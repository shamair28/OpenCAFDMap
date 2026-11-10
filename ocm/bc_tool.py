#!/usr/bin/env python3
r"""Edit BimmerCode backups (.cod) parameter-by-parameter and repackage them as NEW backups.

Workspace: <Open CAFD Map workspace>/bimmercode (set paths in OpenLauncher)
  originals\   copies of your BimmerCode backups. Never modified; `init` copies, `check` verifies.
  decoded\     readable dump per backup (.txt) + raw blocks (.json)
  edits\       edit files for `apply` (see edits\EXAMPLE.tsv)
  out\         new .cod files, one per edit run, ready to copy back to BimmerCode

  python bc_tool.py init  [backup folder]       copy backups into originals\ and decode them
  python bc_tool.py list                        backups: ECU, CAFD, date, file
  python bc_tool.py show  <cod> [--group 3221] [--grep Sport]   parameters with names + current value
  python bc_tool.py values <cod> <param>        every value a parameter can take
  python bc_tool.py set   <cod> <param>=<value> [...] [--note text]
  python bc_tool.py apply <cod> <edits.tsv>     several edits from a file
  python bc_tool.py diff  <cod A> <cod B>       parameter-level differences
  python bc_tool.py check                       verify originals\ still match the source backups

<cod>    a file name (or unique part of one) from originals\ or out\, or "latest:<ECU>",
         e.g. latest:BDC_BODY3, latest:0x63, latest:HU_MGU.
<param>  BMW / DB name (FesSportWorldMode1, PfFesMaster_9_9_255), optionally "group/name",
         or a raw position "3221:9:9:11111111b".
<value>  hex (02, 0x02), a value label as shown (aktiv, "mode Sport+", wert_02), or a mode name.

The coding data blocks are changed in place; everything else in the backup, including the SYSTEM
(signature) block, is copied unchanged. See docs/BC_EDITOR.md for what that means on restore.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import plistlib
import re
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402
from defdb import extract  # noqa: E402

_P = config.paths()
WS = _P.bc                                           # workspace\bimmercode
SRC = _P.cfg.get("bimmercode_backups_dir") or ""     # where your BimmerCode backups are copied from
DB = _P.db
ORIG, DEC, OUT, EDITS = (os.path.join(WS, d) for d in ("originals", "decoded", "out", "edits"))
FN_RE = re.compile(r"^(?P<ecu>[0-9A-F]+)-(?P<vin>[A-Z0-9]{17})-(?P<ts>\d{9,})(?:-(?P<tag>[^.]*))?\.cod$", re.I)


# ------------------------------------------------------------------ .cod model
class Cod:
    """NSKeyedArchiver plist of a BimmerCode CodingSession; keeps the raw plist for faithful rewriting."""

    def __init__(self, path):
        self.path = path
        self.plist = plistlib.load(open(path, "rb"))
        o = self.objs = self.plist["$objects"]
        root = o[self.plist["$top"]["root"].data]
        ecu = o[root["codingECU"].data]
        cd = o[root["codingData"].data]
        ident = o[cd["CAFDIdentifier"].data]
        self.vin = o[root["VIN"].data]
        self.ecu_id = o[ecu["identifier"].data]
        self.diag = ecu["address"]
        self.cafd = ident[:8].upper()   # header CAFD (BimmerCode's primary)
        self.ver = "%03d_%03d_%03d" % (int(ident[8:10], 16), int(ident[10:12], 16), int(ident[12:14], 16))
        # Multi-CAFD ECUs (BDC: 7, ACSM: 6, FZD: 2): the backup lists every CAFD in identifiers.CAFD and
        # holds the blocks of all of them. Each datagroup address belongs to exactly one of these CAFDs.
        self.cafds = []
        try:
            arr = o[o[ecu["identifiers"].data]["CAFD"].data]
            items = [o[u.data] for u in arr["NS.objects"]] if "NS.objects" in arr else [arr]
            for x in items:
                cv = ("%08X" % x["identifier"], "%03d_%03d_%03d" % (x["mainVersion"], x["subVersion"], x["patchVersion"]))
                if cv not in self.cafds:
                    self.cafds.append(cv)
        except (KeyError, AttributeError, TypeError):
            pass
        if (self.cafd, self.ver) not in self.cafds:
            self.cafds.insert(0, (self.cafd, self.ver))
        self.block_obj = {}  # "3221" -> index of the bytes object in $objects
        for b in o[cd["mutableCodingDataBlocks"].data]["NS.objects"]:
            b = o[b.data]
            self.block_obj["%04X" % b["blockAddress"]] = b["data"].data
        m = FN_RE.match(os.path.basename(path))
        self.ts = int(m.group("ts")) if m else 0

    @property
    def blocks(self):
        return {g: bytearray(self.objs[i]) for g, i in self.block_obj.items()}

    def when(self):
        return datetime.datetime.fromtimestamp(self.ts).strftime("%Y-%m-%d %H:%M") if self.ts else "?"

    def save_as(self, path, blocks):
        """Copy the original file and overwrite only the changed data bytes in place (same length),
        so the new backup is byte-identical to the source except for the edited coding bytes."""
        raw = bytearray(open(self.path, "rb").read())
        locs = bplist_data_locations(bytes(raw))
        for g, data in blocks.items():
            i = self.block_obj[g]
            old = self.objs[i]
            if len(data) != len(old):
                raise ValueError(f"block {g} changed length")
            if bytes(data) == old:
                continue
            start, length = locs[i]
            assert length == len(old) and raw[start:start + length] == old, f"block {g}: data not where expected"
            raw[start:start + length] = data
        with open(path, "wb") as f:
            f.write(raw)
        # the result must parse to exactly the source plist with only these data objects replaced
        expect = plistlib.load(open(self.path, "rb"))
        for g, data in blocks.items():
            expect["$objects"][self.block_obj[g]] = bytes(data)
        if plistlib.load(open(path, "rb")) != expect:
            os.remove(path)
            raise RuntimeError("patched backup does not round-trip; nothing written")


def bplist_data_locations(raw):
    """Map index in the NSKeyedArchiver $objects array -> (file offset, length) of data (0x4n) objects."""
    if raw[:8] != b"bplist00":
        raise ValueError("not a binary plist")
    off_size, ref_size = raw[-26], raw[-25]
    n_obj = int.from_bytes(raw[-24:-16], "big")
    top = int.from_bytes(raw[-16:-8], "big")
    table = int.from_bytes(raw[-8:], "big")
    offs = [int.from_bytes(raw[table + k * off_size: table + (k + 1) * off_size], "big") for k in range(n_obj)]

    def length_at(pos, low):
        if low != 0xF:
            return low, pos + 1
        m = raw[pos + 1]  # int marker 0x1k
        nb = 1 << (m & 0xF)
        return int.from_bytes(raw[pos + 2: pos + 2 + nb], "big"), pos + 2 + nb

    def refs(pos, count):
        return [int.from_bytes(raw[pos + k * ref_size: pos + (k + 1) * ref_size], "big") for k in range(count)]

    def ascii_at(objnum):
        p = offs[objnum]
        mk = raw[p]
        if mk >> 4 != 0x5:
            return None
        n, s = length_at(p, mk & 0xF)
        return raw[s:s + n].decode("ascii")

    p = offs[top]
    mk = raw[p]
    assert mk >> 4 == 0xD, "top object is not a dict"
    n, s = length_at(p, mk & 0xF)
    keys, vals = refs(s, n), refs(s + n * ref_size, n)
    objs_ref = vals[[ascii_at(k) for k in keys].index("$objects")]
    p = offs[objs_ref]
    mk = raw[p]
    assert mk >> 4 == 0xA, "$objects is not an array"
    n, s = length_at(p, mk & 0xF)
    out = {}
    for i, objnum in enumerate(refs(s, n)):
        q = offs[objnum]
        if raw[q] >> 4 == 0x4:
            ln, ds = length_at(q, raw[q] & 0xF)
            out[i] = (ds, ln)
    return out


# ------------------------------------------------------------------ DB helpers
def db():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def functions(con, cod):
    """Functions of every CAFD listed in the backup, restricted to datagroups the backup actually holds."""
    out, missing = [], []
    for cid, ver in cod.cafds:
        rows = con.execute(
            "SELECT f.pk, f.address, d.name, d.dg_class, f.name, f.start, f.end, f.mask, f.comment, f.hint "
            "FROM func f JOIN datagroup d ON d.pk=f.dg_pk JOIN cafd c ON c.pk=f.cafd_pk "
            "WHERE c.cafd_id=? AND c.version=? ORDER BY f.pk", (cid, ver)).fetchall()
        if not rows:
            missing.append(f"{cid}_{ver}")
            continue
        out += [dict(pk=r[0], addr=r[1], group=r[2] or "", dg_class=r[3], name=r[4], start=r[5], end=r[6], mask=r[7],
                     comment=r[8] or "", hint=r[9] or "", cafd=f"{cid}_{ver}") for r in rows if r[1] in cod.block_obj]
    if not out:
        sys.exit(f"none of this backup's CAFDs are in the definition DB: {', '.join(missing)}")
    if missing:
        print(f"note: CAFD(s) not in the DB, their blocks are shown unnamed/not editable: {', '.join(missing)}",
              file=sys.stderr)
    out.sort(key=lambda f: (f["addr"], f["start"], f["pk"]))
    return out


def values(con, fpk):
    return con.execute("SELECT idx, hex, label, is_default FROM val WHERE func_pk=? ORDER BY idx", (fpk,)).fetchall()


def label_for(con, fpk, hx):
    r = con.execute("SELECT label FROM val WHERE func_pk=? AND hex=? ORDER BY idx LIMIT 1", (fpk, hx)).fetchone()
    return r[0] if r else "(custom value)"


def ecu_name(con, cod):
    r = con.execute("SELECT ecu FROM cafd WHERE cafd_id=? LIMIT 1", (cod.cafd,)).fetchone()
    return (r[0] if r and r[0] else f"diag_0x{cod.diag:02X}").split(",")[0]


def insert(buf, f, hx):
    """Write value hx into buf at function f (inverse of defdb.extract)."""
    st, en, m = f["start"], f["end"], f["mask"]
    if st == en and m.endswith("b") and int(m[:-1], 2) != 0xFF:
        mi = int(m[:-1], 2)
        shift = (mi & -mi).bit_length() - 1
        v = int(hx, 16)
        if (v << shift) & ~mi & 0xFF:
            raise ValueError(f"value {hx} does not fit mask {m}")
        buf[st] = (buf[st] & ~mi & 0xFF) | ((v << shift) & mi)
    else:
        data = bytes.fromhex(hx)
        if len(data) != en - st + 1:
            raise ValueError(f"value needs {en - st + 1} bytes, got {len(data)}")
        buf[st:en + 1] = data


# ------------------------------------------------------------------ resolving user input
def all_cods():
    return [Cod(p) for p in sorted(glob.glob(os.path.join(ORIG, "*.cod")) + glob.glob(os.path.join(OUT, "*.cod")))]


def find_cod(spec, con=None):
    if spec.lower().startswith("latest:"):
        key = spec.split(":", 1)[1].upper()
        con = con or db()
        cand = []
        for c in (Cod(p) for p in glob.glob(os.path.join(ORIG, "*.cod"))):
            names = {ecu_name(con, c).upper(), f"0X{c.diag:02X}", c.ecu_id.upper(), c.cafd}
            if key in names or any(n.startswith(key) for n in names):
                cand.append(c)
        if not cand:
            sys.exit(f"no backup for {spec}")
        return max(cand, key=lambda c: c.ts)
    if os.path.isfile(spec):
        return Cod(spec)
    hits = [p for p in glob.glob(os.path.join(ORIG, "*.cod")) + glob.glob(os.path.join(OUT, "*.cod"))
            if spec.lower() in os.path.basename(p).lower()]
    if len(hits) != 1:
        sys.exit(f"'{spec}' matches {len(hits)} backups" + (": " + ", ".join(map(os.path.basename, hits[:8])) if hits else ""))
    return Cod(hits[0])


def find_func(fs, spec):
    m = re.fullmatch(r"([0-9A-Fa-f]{4}):(\d+):(\d+):([01]+b)", spec.strip())
    if m:
        hit = [f for f in fs if f["addr"] == m.group(1).upper() and f["start"] == int(m.group(2))
               and f["end"] == int(m.group(3)) and f["mask"] == m.group(4)]
    else:
        grp, _, nm = spec.rpartition("/")
        hit = [f for f in fs if f["name"].lower() == nm.lower()
               and (not grp or grp.upper() in (f["addr"], f["group"].upper()))]
    if len(hit) != 1:
        alts = [f"{f['addr']}/{f['name']}" for f in fs if spec.split('/')[-1].lower() in f["name"].lower()][:10]
        sys.exit(f"parameter '{spec}' matches {len(hit)} functions" + (f"; similar: {', '.join(alts)}" if alts else ""))
    return hit[0]


def pasted_hex(s):
    """Hex from a pasted value, or None if it isn't hex.
    "01000F" / "0x01000F": the digits as given.
    "01, 00, 0F" / "0x01 0x0 F" / "0, 00, 0F" (comma, space, ; or line separated): one byte per item,
    so a single-digit item is that byte ("0" -> "00", "F" -> "0F"). Items longer than 2 digits are
    refused, so nothing can shift."""
    s = s.strip()
    if not s:
        return None
    if re.search(r"[\s,;]", s):
        items = [re.sub(r"^0[xX]", "", t) for t in re.split(r"[\s,;]+", s) if t]
        if not items or not all(re.fullmatch(r"[0-9A-Fa-f]{1,2}", t) for t in items):
            return None
        return "".join(t.zfill(2) for t in items)
    s = re.sub(r"^0[xX]", "", s)
    return s if re.fullmatch(r"[0-9A-Fa-f]+", s) else None


def resolve_value(con, f, spec):
    vals = values(con, f["pk"])
    s = re.sub(r"^\s*(werte|values?)\s*[=:]\s*", "", spec.strip(), flags=re.I)  # E-Sys "Werte=01, 65, ..."
    nbytes = f["end"] - f["start"] + 1
    h = pasted_hex(s)
    if h is not None:
        if nbytes > 1:
            # multi-byte: exact length only - never pad, so bytes can't shift
            if len(h) != 2 * nbytes:
                hint = ""
                extra = h[2 * nbytes:]
                if len(h) > 2 * nbytes and len(extra) % 2 == 0 and set(extra) <= {"0"}:
                    hint = (f" The {len(extra) // 2} extra byte(s) at the end are all 00 (a template for a longer "
                            "layout?) - remove them if the rest is right.")
                sys.exit(f"{f['name']} is {nbytes} bytes; the pasted value has {len(h) / 2:g} bytes. "
                         f"Nothing staged.{hint}")
            hx = h.upper()
        elif len(h) <= 2:
            hx = h.upper().zfill(2)
        else:
            sys.exit(f"{f['name']} is a single byte/bit field; '{spec}' is too long")
        known = any(v[1] == hx for v in vals)
        return hx, known
    low = s.lower()
    for idx, hx, lab, d in vals:  # wert_NN
        if low == f"wert_{idx + 1:02d}":
            return hx, True
    exact = [v for v in vals if re.sub(r"^wert_\d+\s*", "", v[2] or "").split(" [")[0].strip().lower() == low]
    if not exact:
        exact = [v for v in vals if low in (v[2] or "").lower()]
    if len({v[1] for v in exact}) == 1:
        return exact[0][1], True
    opts = "; ".join(f"{v[1]}={v[2]}" for v in vals)
    sys.exit(f"value '{spec}' for {f['name']} is ambiguous or unknown. Options: {opts}")


# ------------------------------------------------------------------ commands
def decode_one(con, cod, folder=DEC):
    fs = functions(con, cod)
    blocks = cod.blocks
    base = os.path.splitext(os.path.basename(cod.path))[0]
    with open(os.path.join(folder, base + ".json"), "w", encoding="utf-8") as fj:
        json.dump(dict(file=os.path.basename(cod.path), vin=cod.vin, ecu=ecu_name(con, cod), diag=f"0x{cod.diag:02X}",
                       cafd=cod.cafd, version=cod.ver, cafds=[f"{c}_{v}" for c, v in cod.cafds], date=cod.when(),
                       blocks={g: b.hex().upper() for g, b in blocks.items()}), fj, indent=1)
    with open(os.path.join(folder, base + ".txt"), "w", encoding="utf-8") as ft:
        ft.write(f"{ecu_name(con, cod)} diag 0x{cod.diag:02X}  {cod.when()}  {cod.vin}\n"
                 f"CAFDs: {', '.join(f'{c}_{v}' for c, v in cod.cafds)}\n")
        grp = None
        for f in fs:
            if f["addr"] != grp:
                grp = f["addr"]
                ft.write(f"\n[{grp}] {f['group']}  (CAFD {f['cafd']})"
                         f"{'  (SYSTEM - signature, not editable)' if f['dg_class'] == 'SYSTEM' else ''}\n")
            hx = extract(blocks.get(f["addr"]), f["start"], f["end"], f["mask"])
            lab = label_for(con, f["pk"], hx) if hx else "?"
            ft.write(f"  {f['name']:<40} = {(hx or '?')[:24]:<24} {lab}\n")
    return base


def cmd_init(a):
    con = db()
    src = a.folder or SRC
    if not src or not os.path.isdir(src):
        sys.exit("BimmerCode backups folder not set or missing: set it in OpenLauncher (or pass a folder)")
    n = 0
    for p in glob.glob(os.path.join(src, "*.cod")):
        dst = os.path.join(ORIG, os.path.basename(p))
        if not os.path.exists(dst):
            shutil.copy2(p, dst)
            n += 1
    print(f"{n} new backups copied to {ORIG}")
    for p in sorted(glob.glob(os.path.join(ORIG, "*.cod"))):
        try:
            decode_one(con, Cod(p))
        except SystemExit as e:
            print(f"  skip {os.path.basename(p)}: {e}")
    print(f"decoded into {DEC}")


def cmd_list(a):
    con = db()
    for c in sorted(all_cods(), key=lambda c: (c.diag, c.ts)):
        where = "out" if os.path.dirname(c.path) == OUT else "orig"
        print(f"{where:4} 0x{c.diag:02X} {ecu_name(con, c):<16} CAFD_{c.cafd}_{c.ver}  {c.when()}  {os.path.basename(c.path)}")


def cmd_show(a):
    con = db()
    cod = find_cod(a.cod, con)
    fs = functions(con, cod)
    blocks = cod.blocks
    print(f"{ecu_name(con, cod)} 0x{cod.diag:02X} CAFD_{cod.cafd}_{cod.ver} {cod.when()} ({os.path.basename(cod.path)})")
    for f in fs:
        if a.group and a.group.upper() not in (f["addr"], f["group"].upper()):
            continue
        hx = extract(blocks.get(f["addr"]), f["start"], f["end"], f["mask"])
        lab = label_for(con, f["pk"], hx) if hx else "?"
        line = f"{f['addr']}/{f['name']:<40} = {(hx or '?')[:20]:<20} {lab}"
        if a.grep and a.grep.lower() not in (line + f["comment"]).lower():
            continue
        print(line + (f"   // {f['comment'][:80]}" if a.comments and f["comment"] else ""))


def cmd_values(a):
    con = db()
    cod = find_cod(a.cod, con)
    f = find_func(functions(con, cod), a.param)
    cur = extract(cod.blocks.get(f["addr"]), f["start"], f["end"], f["mask"])
    print(f"{f['addr']}/{f['name']}  byte {f['start']}-{f['end']} mask {f['mask']}  current={cur}")
    if f["comment"]:
        print("  " + f["comment"])
    for idx, hx, lab, d in values(con, f["pk"]):
        print(f"  {'*' if hx == cur else ' '} {hx:<10} {lab}")


def write_new(con, cod, changes, note):
    """changes: list of (func, new_hex). Writes a new .cod in out\\ and returns its path."""
    blocks = cod.blocks
    log = []
    for f, hx in changes:
        if f["dg_class"] == "SYSTEM":
            sys.exit(f"{f['addr']}/{f['name']} is in the SYSTEM (signature) block - refusing to edit")
        old = extract(blocks[f["addr"]], f["start"], f["end"], f["mask"])
        insert(blocks[f["addr"]], f, hx)
        new = extract(blocks[f["addr"]], f["start"], f["end"], f["mask"])
        assert new == hx.upper(), (new, hx)
        log.append((f, old, new))
    ts = int(datetime.datetime.now().timestamp())
    m = FN_RE.match(os.path.basename(cod.path))
    ecu_id, vin = (m.group("ecu"), m.group("vin")) if m else (cod.ecu_id, cod.vin)
    out = os.path.join(OUT, f"{ecu_id}-{vin}-{ts}.cod")
    while os.path.exists(out):
        ts += 1
        out = os.path.join(OUT, f"{ecu_id}-{vin}-{ts}.cod")
    Cod(cod.path).save_as(out, blocks)  # fresh copy of the source, so nothing leaks between runs
    # verify: re-read, only the intended functions changed
    fs = functions(con, cod)
    before, after = cod.blocks, Cod(out).blocks
    changed = {(f["addr"], f["start"], f["end"], f["mask"]) for f, _, _ in log}
    for f in fs:
        k = (f["addr"], f["start"], f["end"], f["mask"])
        b = extract(before.get(f["addr"]), *k[1:])
        n = extract(after.get(f["addr"]), *k[1:])
        if b != n and k not in changed:
            # a function sharing bytes with an edited one may legitimately change
            if not any(f["addr"] == g["addr"] and f["start"] <= g["end"] and g["start"] <= f["end"] for g, _, _ in log):
                os.remove(out)
                sys.exit(f"verification failed: {f['addr']}/{f['name']} changed unexpectedly")
    with open(out + ".txt", "w", encoding="utf-8") as fl:
        fl.write(f"source: {os.path.basename(cod.path)} ({cod.when()})\nnote: {note or ''}\n")
        for f, old, new in log:
            fl.write(f"{f['addr']}/{f['name']}: {old} {label_for(con, f['pk'], old)} -> {new} {label_for(con, f['pk'], new)}\n")
    print(f"new backup: {out}")
    for f, old, new in log:
        print(f"  {f['addr']}/{f['name']}: {old} [{label_for(con, f['pk'], old)}] -> {new} [{label_for(con, f['pk'], new)}]")
    print("  signature block copied unchanged from the source backup (see README)")
    return out


def cmd_set(a):
    con = db()
    cod = find_cod(a.cod, con)
    fs = functions(con, cod)
    changes = []
    for item in a.assign:
        if "=" not in item:
            sys.exit(f"expected param=value, got '{item}'")
        p, v = item.split("=", 1)
        f = find_func(fs, p)
        hx, known = resolve_value(con, f, v)
        if not known and not a.allow_custom:
            sys.exit(f"{hx} is not one of {f['name']}'s values (use --allow-custom to write it anyway)")
        changes.append((f, hx))
    write_new(con, cod, changes, a.note)


def cmd_apply(a):
    con = db()
    cod = find_cod(a.cod, con)
    fs = functions(con, cod)
    changes = []
    for ln, line in enumerate(open(a.edits, encoding="utf-8"), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [x.strip() for x in re.split(r"\t|\s*=\s*", line, maxsplit=1)]
        if len(parts) != 2:
            sys.exit(f"{a.edits}:{ln}: expected 'param = value'")
        f = find_func(fs, parts[0])
        hx, known = resolve_value(con, f, parts[1])
        if not known and not a.allow_custom:
            sys.exit(f"{a.edits}:{ln}: {hx} is not a listed value of {f['name']} (use --allow-custom)")
        changes.append((f, hx))
    write_new(con, cod, changes, a.note or os.path.basename(a.edits))


def cmd_diff(a):
    con = db()
    x, y = find_cod(a.a, con), find_cod(a.b, con)
    if sorted(x.cafds) != sorted(y.cafds):
        sys.exit(f"different CAFD sets: {x.cafds} vs {y.cafds}")
    bx, by = x.blocks, y.blocks
    n = 0
    for f in functions(con, x):
        vx = extract(bx.get(f["addr"]), f["start"], f["end"], f["mask"])
        vy = extract(by.get(f["addr"]), f["start"], f["end"], f["mask"])
        if vx != vy:
            n += 1
            tag = "  (signature)" if f["dg_class"] == "SYSTEM" else ""
            print(f"{f['addr']}/{f['name']}: {(vx or '?')[:20]} [{label_for(con, f['pk'], vx)}] -> "
                  f"{(vy or '?')[:20]} [{label_for(con, f['pk'], vy)}]{tag}")
    print(f"{n} parameter(s) differ")


def cmd_check(a):
    if not SRC or not os.path.isdir(SRC):
        sys.exit("BimmerCode backups folder not set: nothing to compare the originals with")
    bad = 0
    for p in glob.glob(os.path.join(ORIG, "*.cod")):
        s = os.path.join(SRC, os.path.basename(p))
        if os.path.exists(s) and hashlib.sha256(open(p, "rb").read()).digest() != hashlib.sha256(open(s, "rb").read()).digest():
            print("MODIFIED:", os.path.basename(p))
            bad += 1
    print("originals OK" if not bad else f"{bad} original(s) differ from {SRC}")


def ensure_workspace():
    for d in (ORIG, DEC, OUT, EDITS):
        os.makedirs(d, exist_ok=True)
    ex = os.path.join(config.ROOT, "docs", "EXAMPLE_edits.tsv")
    if os.path.exists(ex) and not os.path.exists(os.path.join(EDITS, "EXAMPLE.tsv")):
        shutil.copy(ex, os.path.join(EDITS, "EXAMPLE.tsv"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("init"); p.add_argument("folder", nargs="?")
    sp.add_parser("list")
    p = sp.add_parser("show"); p.add_argument("cod"); p.add_argument("--group"); p.add_argument("--grep")
    p.add_argument("--comments", action="store_true")
    p = sp.add_parser("values"); p.add_argument("cod"); p.add_argument("param")
    p = sp.add_parser("set"); p.add_argument("cod"); p.add_argument("assign", nargs="+")
    p.add_argument("--note"); p.add_argument("--allow-custom", action="store_true")
    p = sp.add_parser("apply"); p.add_argument("cod"); p.add_argument("edits")
    p.add_argument("--note"); p.add_argument("--allow-custom", action="store_true")
    p = sp.add_parser("diff"); p.add_argument("a"); p.add_argument("b")
    sp.add_parser("check")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ensure_workspace()
    globals()["cmd_" + a.cmd](a)


if __name__ == "__main__":
    main()
