#!/usr/bin/env python3
r"""Import BMW setting names from FDL cheat-sheet XMLs you have locally into workspace\names_cheats.tsv.

Cheat sheet entry:  <cafd id><code description><group id><function start end mask comment>VALUE</function>
Comments in many sheets start with BMW's FDL name, e.g.
  "INBAND_RINGING (default: nicht_aktiv)"  or  "PERMANENTER_PROFILWECHSEL (value: aktiv)".

Every entry is checked against the definition DB: the function (group, start, end, mask) must exist in
at least one version of that CAFD; a hex value must be one of its values. Names are version
independent, so they apply to every version with that function.

    python ocm\cheat_import.py [cheat sheet folder]     (default: cheat_sheets_dir from config.json)

Also reads cheat sheets contributed to the repo (definitions\**\*.xml with an <FDL> root).
Priority when sheets disagree: contributed sheets first, then the first sheet in file-name order wins.
The names\*.tsv files and your my_names.tsv always win over this file (see build_defdb.py).
Licences: cheat sheets carry their own licence (community sheets are often GPL-3.0). Sheets in
definitions\ were contributed under their licence (see definitions\README.md); the imported
names_cheats.tsv is a build product and stays in your workspace.
"""
import glob
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

_P = config.paths()
SRC = sys.argv[1] if len(sys.argv) > 1 else _P.cfg.get("cheat_sheets_dir", "")
DB = _P.db
OUT = _P.cheat_names
HEX = re.compile(r"^[0-9A-Fa-f]+$")
# BMW names are identifiers: ALL_CAPS (INBAND_RINGING) or CamelCase (FesSportWorldMode1). The comment
# must be just the identifier, or the identifier followed by "(" / " -" (never a sentence).
CAPS_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,})\b")                       # INBAND_RINGING ... (anything may follow)
CAMEL_RE = re.compile(r"^\s*([A-Za-z]+[a-z][A-Z][A-Za-z0-9_]*)\s*(?:$|\(|-\s)")  # FesSportWorldMode1 (standing alone)
_NOT_NAMES = {"default", "value", "this", "level", "left", "right", "note", "only", "enable", "disable"}
VAL_RE = re.compile(r"\((?:value|wert)\s*:\s*([^)]+)\)", re.I)
DEF_RE = re.compile(r"\(default\s*:\s*([^)]+)\)", re.I)


def norm_mask(m):
    m = (m or "").strip()
    if re.fullmatch(r"[01]+b", m, re.I):
        return m.lower()
    if HEX.fullmatch(m) and len(m) <= 2:
        return format(int(m, 16), "08b") + "b"
    return m


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    known = defaultdict(set)  # (cid, addr, start, end, mask) -> set of value hex (all versions)
    defaults = defaultdict(set)
    for cid, addr, st, en, mask, hx, isd in con.execute(
            "SELECT c.cafd_id, f.address, f.start, f.end, f.mask, v.hex, v.is_default FROM val v "
            "JOIN func f ON f.pk=v.func_pk JOIN cafd c ON c.pk=f.cafd_pk"):
        k = (cid, addr, st, en, mask.lower())
        known[k].add(hx)
        if isd:
            defaults[k].add(hx)

    known_ids = {k[0] for k in known}
    # contributed sheets (definitions\**\*.xml with an <FDL> root) first, then your local folder
    files = _P.contributed_xmls("cheatsheet")
    if SRC and os.path.isdir(SRC):
        files += sorted(glob.glob(os.path.join(SRC, "*.xml")))
    if not files:
        sys.exit(f"no cheat sheets: local folder not set or missing ({SRC!r}) and none in definitions\\")
    funcs = {}            # key -> (name, comment, source)
    vals = {}             # (key, hex) -> (label, source)
    stats = defaultdict(lambda: [0, 0, 0, 0])  # file -> [entries, matched, named, mismatched]
    for p in files:
        src = os.path.basename(p)[:-4]
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError as e:
            print(f"skip {p}: {e}")
            continue
        for cafd in root.iter("cafd"):
            cid = (cafd.get("id") or "").upper().zfill(8)
            for code in cafd.iter("code"):
                desc = (code.get("description") or "").strip()
                for g in code.iter("group"):
                    addr = (g.get("id") or "").upper()
                    for f in g.iter("function"):
                        st = stats[src]
                        st[0] += 1
                        try:
                            s, e = int(f.get("start")), int(f.get("end"))
                        except (TypeError, ValueError):
                            continue
                        k = (cid, addr, s, e, norm_mask(f.get("mask")))
                        if k not in known:
                            if cid in known_ids:
                                st[3] += 1  # CAFD is G020 but no such function in any version
                            continue
                        st[1] += 1
                        cmt = (f.get("comment") or "").strip()
                        txt = (f.text or "").strip()
                        m = CAPS_RE.match(cmt) or CAMEL_RE.match(cmt)
                        fdl = m.group(1) if m else None
                        if fdl and fdl.lower() not in _NOT_NAMES:
                            st[2] += 1
                            if k not in funcs:
                                funcs[k] = (fdl, desc, src)
                        elif k not in funcs and desc:
                            funcs.setdefault(("desc",) + k, (None, desc, src))
                        # value naming
                        vname = None
                        mv = VAL_RE.search(cmt)
                        if mv:
                            vname = mv.group(1).strip()
                        if HEX.fullmatch(txt or "x"):
                            hx = txt.upper().zfill(len(next(iter(known[k]))) if known[k] else len(txt))
                            if hx not in known[k]:
                                st[3] += 1
                                continue
                            if vname and (k, hx) not in vals:
                                vals[(k, hx)] = (vname, src)
                        elif txt and len(known[k]) == 2 and len(defaults[k]) == 1:
                            # value given by name on a 2-value function: pin it via the default named in the comment
                            md = DEF_RE.search(cmt)
                            d_hex = next(iter(defaults[k]))
                            o_hex = next(iter(known[k] - defaults[k]))
                            if md and md.group(1).strip() != txt:
                                vals.setdefault((k, d_hex), (md.group(1).strip(), src))
                                vals.setdefault((k, o_hex), (txt, src))
    # Only BMW setting names become function names. Community descriptions without one go in as
    # comment-only rows (empty name): the function keeps its BMW-style <group>_<start>_<end>_<mask> name.
    rows = []
    for k, (name, desc, src) in funcs.items():
        if k[0] == "desc":
            k = k[1:]
            if k in funcs or not desc:
                continue
            name = ""
        cid, addr, s, e, mask = k
        rows.append((cid, addr, s, e, mask, "", name, f"{desc} [{src}]" if desc else f"[{src}]"))
    for (k, hx), (lab, src) in vals.items():
        cid, addr, s, e, mask = k
        rows.append((cid, addr, s, e, mask, hx, lab, f"[{src}]"))
    rows.sort()
    with open(OUT, "w", encoding="utf-8", newline="\n") as fo:
        fo.write("# Generated by tools\\cheat_import.py from E-Sys MAX / community cheat sheets (GPL-3.0). Do not edit:\n"
                 "# put corrections in names_user.tsv, which wins.\n")
        fo.write("cafd_id\taddress\tstart\tend\tmask\tvalue_hex\tname\tcomment\n")
        for r in rows:
            fo.write("\t".join("" if x is None else str(x) for x in r) + "\n")
    nf = sum(1 for r in rows if not r[5] and r[6])
    nc = sum(1 for r in rows if not r[5] and not r[6])
    print(f"{nf} BMW function names + {nc} description-only comments + "
          f"{sum(1 for r in rows if r[5])} value names -> {OUT}")
    for src, (n, m, named, bad) in stats.items():
        if m:
            print(f"  {src:<20} {n:5} entries, {m:4} match the DB, {named:4} with BMW name, "
                  f"{bad} rejected (function/value not in any version)")


if __name__ == "__main__":
    main()
