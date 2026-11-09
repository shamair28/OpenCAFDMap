#!/usr/bin/env python3
r"""Query / evaluate the CAFD definition DB (workspace\cafd_defs_<series>.sqlite).

  python defdb.py ids                                   # CAFD ids, versions, ECUs
  python defdb.py show 000043AF [016_020_000] [--group 3000]
  python defdb.py search 5AC [--cafd 000043AF] [--version latest]
  python defdb.py eval --fp FP.xml 000043AF 016_020_000           # what the FA would code
  python defdb.py diff-ncd --fp FP.xml --ncd CAFD_000043AF_016_020_000.ncd   # coded vs FA

FP files: E-Sys/PsdZ FP XML (<cat val><crit val/>) or the agent's dumped current_fp.xml.
"""
import argparse
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

DB = config.paths().db
from rules import eval_rule  # noqa: E402


def connect(path=DB):
    if not os.path.exists(path):
        sys.exit(f"DB not found: {path} (run build_defdb.py)")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# ------------------------------------------------------------------ FP + rule evaluation
def load_fp(path):
    """Return {category: set(crit numbers)} from a PsdZ FP XML or the agent's FP dump."""
    root = ET.parse(path).getroot()
    fp = {}
    for cat in root.iter():
        if cat.tag.split("}")[-1] in ("cat", "Category") and cat.get("val") is not None:
            s = fp.setdefault(int(cat.get("val")), set())
            for cr in cat:
                if cr.tag.split("}")[-1] in ("crit", "Crit") and cr.get("val") is not None:
                    s.add(int(cr.get("val")))
    return fp


def load_fa(path):
    """paint / upholstery codes from an E-Sys FA xml (rules can read them directly)."""
    if not path:
        return None
    t = open(path, encoding="utf-8").read()
    out = {}
    for k in ("colourCode", "fabricCode"):
        m = re.search(k + r'="([^"]*)"', t)
        if m:
            out[k] = m.group(1)
    return out


def predict(con, func_pk, fp, fa=None):
    """Value the FA/FP would code: first matching rule, else the default, else a fixed value."""
    rows = con.execute("SELECT v.idx, v.hex, v.is_default, r.raw, v.label FROM val v "
                       "LEFT JOIN rule r ON r.id=v.rule_id WHERE v.func_pk=? ORDER BY v.idx", (func_pk,)).fetchall()
    default = fixed = None
    for idx, hx, is_def, raw, label in rows:
        if raw == "__fixed__" and fixed is None:
            fixed = (idx, hx, label, "fixed")
        if is_def and default is None:
            default = (idx, hx, label, "default")
        if not is_def and eval_rule(raw, fp, fa):
            return (idx, hx, label, "rule")
    return default or fixed or (None, None, None, "none")


# ------------------------------------------------------------------ NCD (S-record) decode
def read_ncd(path):
    """Return (cafd_id, version, {group_addr_hex: bytearray})."""
    groups, cid, ver = {}, None, None
    for line in open(path, encoding="ascii", errors="replace"):
        line = line.strip()
        if not line.startswith("S") or len(line) < 4:
            continue
        typ, cnt = line[1], int(line[2:4], 16)
        body = bytes.fromhex(line[4:4 + cnt * 2])
        if typ == "0":
            hdr = body[2:-1]  # binary: CAFD id (4 bytes) + main/sub/patch version (1 byte each)
            if len(hdr) >= 7:
                cid = hdr[:4].hex().upper()
                ver = "%03d_%03d_%03d" % (hdr[4], hdr[5], hdr[6])
            continue
        alen = {"1": 2, "2": 3, "3": 4}.get(typ)
        if not alen:
            continue
        addr = int.from_bytes(body[:alen], "big")
        data = body[alen:-1]
        g, off = "%04X" % (addr >> 8), addr & 0xFF
        buf = groups.setdefault(g, bytearray())
        if len(buf) < off + len(data):
            buf.extend(b"\x00" * (off + len(data) - len(buf)))
        buf[off:off + len(data)] = data
    return cid, ver, groups


def extract(buf, start, end, mask):
    if buf is None or end >= len(buf):
        return None
    raw = buf[start:end + 1]
    m = (mask or "").strip()
    if start == end and m.endswith("b") and m[:-1] and set(m[:-1]) <= {"0", "1"}:
        mi = int(m[:-1], 2)
        if mi != 0xFF:
            shift = (mi & -mi).bit_length() - 1
            return "%02X" % ((raw[0] & mi) >> shift)
    return raw.hex().upper()


# ------------------------------------------------------------------ commands
def resolve_version(con, cid, ver):
    if ver and ver != "latest":
        return ver
    r = con.execute("SELECT version FROM cafd WHERE cafd_id=? ORDER BY main DESC, sub DESC, patch DESC LIMIT 1",
                    (cid,)).fetchone()
    if not r:
        sys.exit(f"CAFD {cid} not in DB")
    return r[0]


def cmd_ids(con, a):
    for cid, n, latest, ecu in con.execute(
            "SELECT cafd_id, count(*), max(printf('%03d_%03d_%03d',main,sub,patch)), max(ecu) FROM cafd "
            "GROUP BY cafd_id ORDER BY cafd_id"):
        print(f"{cid}  versions={n:3d}  latest={latest}  ecu={ecu or '-'}")


def cmd_show(con, a):
    cid = a.cafd.upper().zfill(8)
    ver = resolve_version(con, cid, a.version)
    q = ("SELECT address, group_name, start, end, mask, func_name, hint, idx, hex, label, rule FROM v_values "
         "WHERE cafd_id=? AND version=?" + (" AND address=?" if a.group else "") + " ORDER BY func_pk, idx")
    args = [cid, ver] + ([a.group.upper()] if a.group else [])
    last_g = last_f = None
    for addr, gname, st, en, mask, fname, hint, idx, hx, label, rule in con.execute(q, args):
        if addr != last_g:
            print(f"\n[{addr}] {gname or ''}")
            last_g = addr
        key = (addr, st, en, mask, fname)
        if key != last_f:
            print(f"  {fname:<28} byte {st}{'-' + str(en) if en != st else ''} mask {mask}  {('<- ' + hint) if hint else ''}")
            last_f = key
        if a.rules:
            print(f"      {hx:<10} {label}  :: {rule}")
        else:
            print(f"      {hx:<10} {label}")


def cmd_search(con, a):
    like = f"%{a.text}%"
    q = ("SELECT cafd_id, version, ecu, address, func_name, hint, hex, label, rule FROM v_values "
         "WHERE (rule LIKE ? OR label LIKE ? OR func_name LIKE ? OR hint LIKE ? OR group_name LIKE ?)")
    args = [like] * 5
    if a.cafd:
        q += " AND cafd_id=?"
        args.append(a.cafd.upper().zfill(8))
        if a.version:
            q += " AND version=?"
            args.append(resolve_version(con, args[-1], a.version))
    else:
        q += (" AND (cafd_id, version) IN (SELECT cafd_id, max(printf('%03d_%03d_%03d',main,sub,patch)) "
              "FROM cafd GROUP BY cafd_id)")
    q += " LIMIT ?"
    args.append(a.limit)
    for row in con.execute(q, args):
        cid, ver, ecu, addr, fname, hint, hx, label, rule = row
        print(f"{cid}_{ver} {ecu or '':<10} {fname:<26} {hx:<8} {label}  :: {rule}")


def cmd_eval(con, a):
    fp = load_fp(a.fp)
    fa = load_fa(a.fa)
    cid = a.cafd.upper().zfill(8)
    ver = resolve_version(con, cid, a.version)
    funcs = con.execute("SELECT f.pk, f.address, f.name, f.start, f.end, f.mask FROM func f JOIN cafd c ON "
                        "c.pk=f.cafd_pk WHERE c.cafd_id=? AND c.version=? ORDER BY f.pk", (cid, ver)).fetchall()
    for fpk, addr, name, st, en, mask in funcs:
        idx, hx, label, how = predict(con, fpk, fp, fa)
        print(f"{addr} {name:<28} -> {hx or '?':<10} {label or ''} ({how})")


def cmd_diff_ncd(con, a):
    cid, ver, groups = read_ncd(a.ncd)
    cid = (a.cafd or cid or "").upper().zfill(8)
    ver = a.version or ver
    fp = load_fp(a.fp) if a.fp else None
    fa = load_fa(getattr(a, "fa", None))
    funcs = con.execute("SELECT f.pk, f.address, f.name, f.start, f.end, f.mask FROM func f JOIN cafd c ON "
                        "c.pk=f.cafd_pk WHERE c.cafd_id=? AND c.version=? ORDER BY f.pk", (cid, ver)).fetchall()
    if not funcs:
        sys.exit(f"CAFD {cid}_{ver} not in DB")
    n = same = unknown = 0
    for fpk, addr, name, st, en, mask in funcs:
        actual = extract(groups.get(addr), st, en, mask)
        lab = con.execute("SELECT label FROM val WHERE func_pk=? AND hex=? ORDER BY idx LIMIT 1",
                          (fpk, actual)).fetchone() if actual else None
        if fp is None:
            print(f"{addr} {name:<28} = {actual or '?':<10} {lab[0] if lab else '(custom value)'}")
            continue
        idx, hx, plab, how = predict(con, fpk, fp, fa)
        n += 1
        if hx is None or actual is None:
            unknown += 1
            continue
        if hx == actual:
            same += 1
        elif not a.quiet or True:
            print(f"DIFF {addr} {name:<28} coded={actual:<8} {lab[0] if lab else '(custom)'}   "
                  f"FA-would-code={hx} {plab} ({how})")
    if fp is not None:
        print(f"\n{cid}_{ver}: {same}/{n} functions match FA coding, {n - same - unknown} differ, {unknown} n/a")


def svt_cafds(path):
    """[(ecu, cafd_id, version)] from an SVT xml."""
    ns = "{http://bmw.com/2005/psdz.data.svt}"
    out = []
    for e in ET.parse(path).getroot().iter(ns + "ecu"):
        for pi in e.iter(ns + "partIdentification"):
            if pi.findtext(ns + "processClass") == "CAFD":
                v = "_".join("%03d" % int(pi.findtext(ns + x)) for x in ("mainVersion", "subVersion", "patchVersion"))
                out.append((e.get("baseVariant"), pi.findtext(ns + "id").upper(), v))
    return out


def cmd_compare(con, a):
    """Factory coding of vehicle A vs vehicle B: every function whose FA-coded value differs."""
    fa, fb = load_fp(a.fp), load_fp(a.fp2)
    faa, fab = load_fa(a.fa), load_fa(a.fa2)
    targets = []
    if a.svt:
        targets = svt_cafds(a.svt)
    if a.cafd:
        ids = {x.strip().upper().zfill(8) for x in a.cafd.split(",")}
        targets = [t for t in targets if t[1] in ids] or [(None, i, None) for i in sorted(ids)]
    if not targets:
        sys.exit("give --svt and/or --cafd")
    total = 0
    for ecu, cid, ver in targets:
        have = con.execute("SELECT 1 FROM cafd WHERE cafd_id=? AND version=?", (cid, ver)).fetchone() if ver else None
        v = ver if have else resolve_version(con, cid, None) if con.execute(
            "SELECT 1 FROM cafd WHERE cafd_id=?", (cid,)).fetchone() else None
        if not v:
            continue
        funcs = con.execute("SELECT f.pk, f.address, f.name, f.hint FROM func f JOIN cafd c ON c.pk=f.cafd_pk "
                            "WHERE c.cafd_id=? AND c.version=? ORDER BY f.pk", (cid, v)).fetchall()
        rows = []
        for fpk, addr, name, hint in funcs:
            pa, pb = predict(con, fpk, fa, faa), predict(con, fpk, fb, fab)
            if pa[1] != pb[1]:
                rows.append((addr, name, hint, pa, pb))
        if rows:
            print(f"\n== {ecu or ''} CAFD_{cid}_{v}{'' if have or not ver else ' (SVT version ' + ver + ' not in DB, using latest)'}"
                  f": {len(rows)} functions differ")
            for addr, name, hint, pa, pb in rows:
                # stored labels are simplified for the build context; show the full rule here instead
                def full(p, fpk_name=name, addr=addr):
                    r = con.execute("SELECT r.readable FROM val v JOIN func f ON f.pk=v.func_pk JOIN cafd c ON "
                                    "c.pk=f.cafd_pk LEFT JOIN rule r ON r.id=v.rule_id WHERE c.cafd_id=? AND "
                                    "c.version=? AND f.name=? AND v.idx=?", (cid, v, fpk_name, p[0])).fetchone()
                    tag = f"wert_{p[0] + 1:02d}" if p[0] is not None else "?"
                    return f"{tag} ({p[3]}) :: {r[0] if r and r[0] else 'default'}"
                print(f"  {name:<24} A={trim(pa[1])} {full(pa)}\n  {'':<24} B={trim(pb[1])} {full(pb)}")
            total += len(rows)
    print(f"\n{total} functions differ between A ({a.fp}) and B ({a.fp2})")


def trim(h, n=16):
    h = h or "?"
    return h if len(h) <= n else h[:n] + "…"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DB)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("ids")
    p = sp.add_parser("show")
    p.add_argument("cafd")
    p.add_argument("version", nargs="?")
    p.add_argument("--group")
    p.add_argument("--rules", action="store_true", help="print full readable rule per value")
    p = sp.add_parser("search")
    p.add_argument("text")
    p.add_argument("--cafd")
    p.add_argument("--version")
    p.add_argument("--limit", type=int, default=200)
    p = sp.add_parser("eval")
    p.add_argument("--fp", required=True)
    p.add_argument("--fa", help="FA xml: lets rules on paint/upholstery evaluate")
    p.add_argument("cafd")
    p.add_argument("version", nargs="?")
    p = sp.add_parser("diff-ncd")
    p.add_argument("--ncd", required=True)
    p.add_argument("--fp")
    p.add_argument("--fa", help="FA xml: lets rules on paint/upholstery evaluate")
    p.add_argument("--cafd")
    p.add_argument("--version")
    p.add_argument("--quiet", action="store_true")
    p = sp.add_parser("compare", help="what the factory codes differently for vehicle A vs B")
    p.add_argument("--fp", required=True, help="FP of vehicle A")
    p.add_argument("--fp2", required=True, help="FP of vehicle B")
    p.add_argument("--fa", help="FA of vehicle A (paint/upholstery rules)")
    p.add_argument("--fa2", help="FA of vehicle B")
    p.add_argument("--svt", help="SVT whose CAFD versions to compare (e.g. tools/contexts/svt/...)")
    p.add_argument("--cafd", help="comma list of CAFD ids (limits --svt, or used alone with latest versions)")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    con = connect(a.db)
    {"ids": cmd_ids, "show": cmd_show, "search": cmd_search, "eval": cmd_eval, "diff-ncd": cmd_diff_ncd,
     "compare": cmd_compare}[a.cmd](con, a)


if __name__ == "__main__":
    main()
