#!/usr/bin/env python3
r"""Decode BimmerCode iOS backups (.cod = NSKeyedArchiver plist) against cafd_defs.sqlite.

A .cod holds one ECU's coding session: VIN, ECU address, CAFD id+version, and the raw coding data
blocks (blockAddress = datagroup address, e.g. 12288 = 0x3000). There are no parameter names inside.

    python ocm\cod_import.py [backups]             # timeline of changes per ECU
    python ocm\cod_import.py [backups] --sheet     # also write workspace\bimmercode_changes.tsv to fill in
    python ocm\cod_import.py --apply               # filled-in sheet -> workspace\my_names.tsv
    python ocm\cod_import.py [backups] --ncd OUTDIR  # also write each backup as an E-Sys .ncd
    python ocm\cod_import.py [backups] --fp FP.xml   # also: values that differ from FA coding

[backups] defaults to bimmercode_backups_dir from config.json.
If you know which BimmerCode option you toggled between two backups, the diff is the parameter
behind it. Write the option name into the sheet and --apply it. Rows in my_names.tsv you are sure of
can then be moved into the matching names\<ECU>.tsv file and contributed.
"""
import argparse
import datetime
import glob
import os
import plistlib
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402
from defdb import extract, load_fp, predict  # noqa: E402

_P = config.paths()
SHEET = _P.bc_sheet
NAMES = _P.my_names
SHEET_COLS = ["when", "ecu", "cafd_id", "address", "start", "end", "mask", "old_hex", "new_hex",
              "old_label", "new_label", "bimmercode_option", "old_state", "new_state"]


def read_cod(path):
    d = plistlib.load(open(path, "rb"))
    objs = d["$objects"]

    def o(u):
        return objs[u.data]
    root = o(d["$top"]["root"])
    ecu = o(root["codingECU"])
    cd = o(root["codingData"])
    ident = o(cd["CAFDIdentifier"])  # e.g. 00007083225508 = id + main/sub/patch (hex bytes)
    cid = ident[:8].upper()
    ver = "%03d_%03d_%03d" % (int(ident[8:10], 16), int(ident[10:12], 16), int(ident[12:14], 16))
    blocks = {}
    for b in o(cd["mutableCodingDataBlocks"])["NS.objects"]:
        b = o(b)
        blocks["%04X" % b["blockAddress"]] = bytearray(o(b["data"]))
    m = re.search(r"-(\d{9,})\.cod$", path)
    return dict(path=path, ecu_id=o(ecu["identifier"]), diag=ecu["address"], cafd=cid, ver=ver,
                vin=o(root["VIN"]), blocks=blocks, ts=int(m.group(1)) if m else 0)


def write_ncd(c, out_dir):
    """Motorola S-record NCD like E-Sys writes (S0 header with CAFD id/version, S2 records of 32 bytes)."""
    def rec(typ, addr_bytes, data):
        body = addr_bytes + data
        cnt = len(body) + 1
        return f"S{typ}{cnt:02X}{body.hex().upper()}{(~(cnt + sum(body)) & 0xFF):02X}"
    m, s, p = (int(x) for x in c["ver"].split("_"))
    lines = [rec("0", b"\x00\x00", bytes.fromhex(c["cafd"]) + bytes([m, s, p]))]
    for g, buf in sorted(c["blocks"].items()):
        base = int(g, 16) << 8
        for off in range(0, len(buf), 32):
            lines.append(rec("2", (base + off).to_bytes(3, "big"), bytes(buf[off:off + 32])))
    name = f"CAFD_{c['cafd']}_{c['ver']}_{c['ecu_id']}_{c['ts']}.ncd"
    with open(os.path.join(out_dir, name), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return name


def decode(con, c):
    """{func_pk: (addr, name, hex, start, end, mask)} for one backup. USER datagroups only: SYSTEM ones
    hold the NCD signature, which changes on every write. None if the CAFD version isn't in the DB."""
    from bc_tool import Cod  # multi-CAFD ECUs: every CAFD listed in the backup, not just the header one
    rows = []
    for cid, ver in Cod(c["path"]).cafds:
        rows += con.execute("SELECT f.pk, f.address, f.name, f.start, f.end, f.mask FROM func f JOIN cafd k ON "
                            "k.pk=f.cafd_pk JOIN datagroup d ON d.pk=f.dg_pk WHERE k.cafd_id=? AND k.version=? "
                            "AND coalesce(d.dg_class,'USER')!='SYSTEM' ORDER BY f.pk", (cid, ver)).fetchall()
    rows = [r for r in rows if r[1] in c["blocks"]]
    if not rows:
        return None
    return {pk: (addr, name, extract(c["blocks"].get(addr), st, en, mask), st, en, mask)
            for pk, addr, name, st, en, mask in rows}


def label(con, fpk, hx):
    r = con.execute("SELECT v.label FROM val v WHERE v.func_pk=? AND v.hex=? ORDER BY v.idx LIMIT 1",
                    (fpk, hx)).fetchone()
    return r[0] if r else "(custom value)"


def short(h, n=20):
    h = h or "?"
    return h if len(h) <= n else h[:n] + "\u2026"


def apply_sheet(sheet=SHEET, names=NAMES):
    """Rows with a bimmercode_option become a function name (+ value labels from old/new_state)."""
    lines = [l.rstrip("\r\n").split("\t") for l in open(sheet, encoding="utf-8")
             if l.strip() and not l.startswith("#")]
    hdr, rows = lines[0], lines[1:]
    ix = {k: i for i, k in enumerate(hdr)}
    existing = open(names, encoding="utf-8").read() if os.path.exists(names) else ""
    out, seen = [], set()
    for r in rows:
        r = r + [""] * (len(hdr) - len(r))
        opt = r[ix["bimmercode_option"]].strip()
        if not opt:
            continue
        key = tuple(r[ix[k]] for k in ("cafd_id", "address", "start", "end", "mask"))
        base = "\t".join(key)
        if key not in seen:
            seen.add(key)
            out.append(f"{base}\t\tBC: {opt}\tfrom BimmerCode backup diff {r[ix['when']]}")
        for hx_col, st_col in (("old_hex", "old_state"), ("new_hex", "new_state")):
            st = r[ix[st_col]].strip()
            if st and r[ix[hx_col]]:
                out.append(f"{base}\t{r[ix[hx_col]]}\t{st}\tBimmerCode state")
    new = [l for l in out if l not in existing]
    if new:
        with open(names, "a", encoding="utf-8", newline="\n") as f:
            f.write("# --- imported from bimmercode_changes.tsv ---\n" + "\n".join(new) + "\n")
    print(f"{len(new)} new lines appended to {names}; now press 'Apply names' in the launcher")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--db", default=_P.db)
    ap.add_argument("--sheet", action="store_true", help="write the change list to bimmercode_changes.tsv")
    ap.add_argument("--apply", action="store_true", help="import the filled-in sheet into names_user.tsv")
    ap.add_argument("--ncd", help="write each backup as .ncd into this folder")
    ap.add_argument("--fp", help="vehicle FP: also list values that differ from FA coding (first backup)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.apply:
        apply_sheet()
        return
    a.folder = a.folder or _P.cfg.get("bimmercode_backups_dir")
    if not a.folder:
        ap.error("backup folder required (or set bimmercode_backups_dir in the launcher)")
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    cods = [read_cod(p) for p in sorted(glob.glob(os.path.join(a.folder, "*.cod")))]
    if a.ncd:
        os.makedirs(a.ncd, exist_ok=True)
        for c in cods:
            write_ncd(c, a.ncd)
        print(f"wrote {len(cods)} .ncd files to {a.ncd}")
    fp = load_fp(a.fp) if a.fp else None
    by = {}
    for c in cods:
        by.setdefault((c["ecu_id"], c["diag"], c["cafd"], c["ver"]), []).append(c)
    sheet_rows = []
    for (eid, diag, cid, ver), lst in sorted(by.items(), key=lambda kv: kv[0][1]):
        lst.sort(key=lambda c: c["ts"])
        r = con.execute("SELECT ecu FROM cafd WHERE cafd_id=? LIMIT 1", (cid,)).fetchone()
        ecu = r[0] if r and r[0] else f"diag_0x{diag:02X}"
        print(f"\n=== diag 0x{diag:02X} {ecu} CAFD_{cid}_{ver}: {len(lst)} backup(s)")
        prev = None
        for c in lst:
            cur = decode(con, c)
            when = datetime.datetime.fromtimestamp(c["ts"]).strftime("%Y-%m-%d %H:%M") if c["ts"] else "?"
            if cur is None:
                print(f"  {when}: CAFD version not in DB")
                continue
            if prev is None:
                print(f"  {when}: first backup ({os.path.basename(c['path'])})")
                if fp is not None:
                    diffs = []
                    for fpk, (addr, name, hx, st, en, mask) in cur.items():
                        p = predict(con, fpk, fp)
                        if hx and p[1] and hx != p[1]:
                            diffs.append((fpk, name, hx, p))
                    print(f"    {len(diffs)} functions differ from FA coding:")
                    for fpk, name, hx, p in diffs:
                        print(f"      {name:<24} car={short(hx)} {label(con, fpk, hx)}  FA={short(p[1])} {p[2]}")
            else:
                ch = [(fpk, cur[fpk][1], prev[fpk][2], cur[fpk][2]) for fpk in cur
                      if fpk in prev and prev[fpk][2] != cur[fpk][2]]
                if ch:
                    print(f"  {when}: {len(ch)} change(s) ({os.path.basename(c['path'])})")
                    for fpk, name, old, new in ch:
                        lo, ln = label(con, fpk, old), label(con, fpk, new)
                        print(f"      {name:<24} {short(old)} {lo}  ->  {short(new)} {ln}")
                        addr, _, _, st, en, mask = cur[fpk]
                        sheet_rows.append([when, ecu, cid, addr, str(st), str(en), mask, old or "", new or "",
                                           lo, ln, "", "", ""])
                else:
                    print(f"  {when}: no change")
            prev = cur
    if a.sheet:
        if os.path.exists(SHEET):
            sys.exit(f"{SHEET} exists; not overwriting your notes (delete it to regenerate)")
        with open(SHEET, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Fill in bimmercode_option (and optionally old_state / new_state, e.g. 'not active' / 'active')\n"
                    "# for each change you recognise, then run: python tools\\cod_import.py --apply\n")
            f.write("\t".join(SHEET_COLS) + "\n")
            for r in sheet_rows:
                f.write("\t".join(r) + "\n")
        print(f"\nwrote {len(sheet_rows)} changes to {SHEET}")


if __name__ == "__main__":
    main()
