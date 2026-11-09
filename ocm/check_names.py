#!/usr/bin/env python3
r"""Check contributions: names\*.tsv and definitions\**\*.xml. Runs without any BMW data, so it also
runs on GitHub.

    python ocm\check_names.py            format checks (errors -> exit code 1)
    python ocm\check_names.py --db       also report rows that match no function in your local DB

Row format (tab separated): cafd_id address start end mask value_hex name comment
  group name     cafd_id address . . . . name [comment]
  function name  cafd_id address start end mask . name [comment]
  value name     cafd_id address start end mask value_hex name [comment]
  comment only   cafd_id address start end mask . (empty name) comment
"""
import argparse
import glob
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

HEX8 = re.compile(r"^[0-9A-F]{8}$")
ADDR = re.compile(r"^[0-9A-F]{4}$")
MASK = re.compile(r"^[01]{1,64}b$")
HEXV = re.compile(r"^([0-9A-F]{2})+$")


def check_file(path, errors, warnings, keys):
    rel = os.path.relpath(path, config.ROOT)
    for ln, raw in enumerate(open(path, encoding="utf-8"), 1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        where = f"{rel}:{ln}"
        if len(f) > 8:
            errors.append(f"{where}: {len(f)} columns (max 8: cafd_id address start end mask value_hex name comment)")
            continue
        f = [x.strip() for x in f] + [""] * (8 - len(f))
        cid, addr, st, en, mask, vhex, name, cmt = f
        if not HEX8.match(cid):
            errors.append(f"{where}: cafd_id '{cid}' must be 8 upper-case hex digits")
        if not ADDR.match(addr):
            errors.append(f"{where}: address '{addr}' must be 4 upper-case hex digits (e.g. 3221)")
        if not name and not cmt:
            errors.append(f"{where}: needs a name or a comment")
        group_row = not (st or en or mask or vhex)
        if not group_row:
            if not (st.isdigit() and (en == "" or en.isdigit())):
                errors.append(f"{where}: start/end must be byte numbers")
            elif en and int(en) < int(st):
                errors.append(f"{where}: end < start")
            if not MASK.match(mask):
                errors.append(f"{where}: mask '{mask}' must be binary with a trailing b (e.g. 11111111b)")
            if vhex and not HEXV.match(vhex):
                errors.append(f"{where}: value_hex '{vhex}' must be upper-case hex bytes")
            if not name and vhex:
                errors.append(f"{where}: comment-only rows must be function rows (no value_hex)")
        elif not name:
            errors.append(f"{where}: a group row needs a name")
        if name and ("\t" in name or len(name) > 120):
            errors.append(f"{where}: name too long")
        if name and not cmt and not vhex:  # value rows usually share the function's source
            warnings.append(f"{where}: no comment - please say where the name comes from")
        key = (cid, addr, st, en or st, mask, vhex)
        if name:
            prev = keys.get(key)
            if prev and prev[0] != name and not rel.startswith(os.path.join("names", "legacy_")):
                errors.append(f"{where}: '{name}' conflicts with '{prev[0]}' at {prev[1]}")
            keys.setdefault(key, (name, where))


VIN = re.compile(r"\b(?=[A-HJ-NPR-Z0-9]*[G-HJ-NPR-Z])[A-HJ-NPR-Z0-9]{17}\b")
DEF_NAME = re.compile(r"^CAFD_([0-9A-F]{8})(?:_(\d{3})_(\d{3})_(\d{3}))?\.xml$", re.I)


def check_xml(path, errors, warnings):
    """definitions\\**\\*.xml: a CafdDefinition (our definition format) or an FDL cheat sheet."""
    import xml.etree.ElementTree as ET
    rel = os.path.relpath(path, config.ROOT)
    raw = open(path, "rb").read()
    text = raw.decode("utf-8", "replace")
    if "bmw.com/2005/psdz" in text:
        errors.append(f"{rel}: looks like a decrypted psdzdata file (BMW's own file) - only definition XMLs or "
                      "cheat sheets, please")
        return
    for m in VIN.finditer(text):
        errors.append(f"{rel}: contains something that looks like a VIN ({m.group(0)[:3]}...): remove it")
        break
    if len(raw) > 10_000_000:
        warnings.append(f"{rel}: {len(raw) // 1_000_000} MB - very large for a definition file")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        errors.append(f"{rel}: not well-formed XML ({e})")
        return
    tag = root.tag.split("}")[-1]
    n = 0
    if tag == "CafdDefinition":
        cid = (root.get("cafdId") or "").upper()
        if not HEX8.match(cid):
            errors.append(f"{rel}: cafdId '{cid}' must be 8 hex digits")
        m = DEF_NAME.match(os.path.basename(path))
        ver = "_".join(root.get(k) or "" for k in ("mainVersion", "subVersion", "patchVersion"))
        if not m or m.group(1).upper() != cid or (m.group(2) and "_".join(m.group(2, 3, 4)) != ver):
            errors.append(f"{rel}: name it CAFD_{cid}_{ver}.xml (or CAFD_{cid}.xml for every version) so "
                          "E-Sys can find it")
        for g in root.iter("Group"):
            if not ADDR.match((g.get("id") or "").upper()):
                errors.append(f"{rel}: Group id '{g.get('id')}' must be 4 hex digits")
            for f in g.iter("Function"):
                n += 1
                try:
                    s, e = int(f.get("start")), int(f.get("end", f.get("start")))
                    if e < s:
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append(f"{rel}: {g.get('id')}/{f.get('name')}: start/end must be byte numbers")
                mk = (f.get("mask") or "").strip()
                if mk and not (MASK.match(mk.lower()) or re.fullmatch(r"[0-9A-Fa-f]{1,2}", mk)):
                    errors.append(f"{rel}: {g.get('id')}/{f.get('name')}: mask '{mk}' (use 11111111b)")
                for v in f.iter("Value"):
                    c = (v.get("code") or "").upper()
                    if c and not HEXV.match(c):
                        errors.append(f"{rel}: {g.get('id')}/{f.get('name')}: value code '{c}' is not hex bytes")
        if not root.get("source") and not root.get("author"):
            warnings.append(f"{rel}: add source=\"...\" (or author) on <CafdDefinition> - where do the names come from?")
    elif tag == "FDL":
        for c in root.iter("cafd"):
            if not re.fullmatch(r"[0-9A-Fa-f]{1,8}", c.get("id") or ""):
                errors.append(f"{rel}: cafd id '{c.get('id')}' is not hex")
            for g in c.iter("group"):
                for f in g.iter("function"):
                    n += 1
                    if not (f.get("start") or "").isdigit():
                        errors.append(f"{rel}: cafd {c.get('id')} group {g.get('id')}: function without start")
    else:
        errors.append(f"{rel}: root <{tag}> - expected <CafdDefinition> or <FDL> (cheat sheet)")
    return n


def db_report(keys, db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    miss = []
    for (cid, addr, st, en, mask, vhex), (name, where) in keys.items():
        if not st:
            q = con.execute("SELECT 1 FROM datagroup d JOIN cafd c ON c.pk=d.cafd_pk WHERE c.cafd_id=? AND d.address=? "
                            "LIMIT 1", (cid, addr))
        elif not vhex:
            q = con.execute("SELECT 1 FROM func f JOIN cafd c ON c.pk=f.cafd_pk WHERE c.cafd_id=? AND f.address=? AND "
                            "f.start=? AND f.end=? AND f.mask=? LIMIT 1", (cid, addr, int(st), int(en), mask))
        else:
            q = con.execute("SELECT 1 FROM val v JOIN func f ON f.pk=v.func_pk JOIN cafd c ON c.pk=f.cafd_pk WHERE "
                            "c.cafd_id=? AND f.address=? AND f.start=? AND f.end=? AND f.mask=? AND v.hex=? LIMIT 1",
                            (cid, addr, int(st), int(en), mask, vhex))
        if not q.fetchone():
            miss.append(f"{where}: '{name}' matches nothing in your DB (other series, or a typo?)")
    return miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", nargs="?", const=True, default=None,
                    help="also check rows against the local definition DB (path optional)")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    files = a.files or (sorted(glob.glob(os.path.join(config.NAMES, "*.tsv")))
                        + sorted(glob.glob(os.path.join(config.CONTRIB, "**", "*.xml"), recursive=True)))
    errors, warnings, keys = [], [], {}
    n_xml = n_xml_fn = 0
    for p in files:
        if p.lower().endswith(".xml"):
            n_xml += 1
            n_xml_fn += check_xml(p, errors, warnings) or 0
        else:
            check_file(p, errors, warnings, keys)
    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("ERROR:", e)
    print(f"{len(files) - n_xml} names files ({len(keys)} names), {n_xml} definition XMLs ({n_xml_fn} functions): "
          f"{len(errors)} errors, {len(warnings)} warnings")
    if a.db:
        db = a.db if isinstance(a.db, str) else config.paths().db
        if os.path.exists(db):
            miss = db_report(keys, db)
            for m in miss:
                print("not found:", m)
            print(f"{len(keys) - len(miss)}/{len(keys)} names match your DB ({os.path.basename(db)})")
        else:
            print(f"no DB at {db}; skipped the DB check")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
