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
import versions  # noqa: E402

HEX8 = re.compile(r"^[0-9A-F]{8}$")
ADDR = re.compile(r"^[0-9A-F]{4}$")
MASK = re.compile(r"^[01]{1,64}b$")
HEXV = re.compile(r"^([0-9A-F]{2})+$")


def relpath(path):
    """Path relative to the repository for messages; as given when it's on another drive (e.g. C: vs H:)."""
    try:
        return os.path.relpath(path, config.ROOT)
    except ValueError:
        return path


def check_file(path, errors, warnings, keys, scoped_keys=None):
    rel = relpath(path)
    scoped = os.path.basename(os.path.dirname(os.path.abspath(path))).lower() == "versioned"
    ncol = 9 if scoped else 8
    for ln, raw in enumerate(open(path, encoding="utf-8"), 1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        where = f"{rel}:{ln}"
        if len(f) > ncol:
            errors.append(f"{where}: {len(f)} columns (max {ncol}: cafd_id address start end mask value_hex name comment"
                          + (" versions)" if scoped else "); names for some CAFD versions only go in names\\versioned\\ "
                             "with a 9th column, versions"))
            continue
        f = [x.strip() for x in f] + [""] * (ncol - len(f))
        cid, addr, st, en, mask, vhex, name, cmt = f[:8]
        rng = None
        if scoped:
            try:
                rng = versions.ranges(f[8]) if f[8] else None
            except ValueError as e:
                errors.append(f"{where}: versions '{f[8]}': {e}")
                continue
            if not rng:
                errors.append(f"{where}: versions is empty: every-version names go in names\\*.tsv (versions: "
                              "062-, -052, 052-061, 062_010_106, ...)")
                continue
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
        if name and scoped:
            # version-scoped rows conflict only where their ranges overlap; they win over every-version rows
            for r in rng:
                for prev_r, prev_name, prev_where in (scoped_keys if scoped_keys is not None else {}).get(key, []):
                    if prev_name != name and versions.overlap(r, prev_r):
                        errors.append(f"{where}: '{name}' conflicts with '{prev_name}' at {prev_where} for "
                                      f"{versions.text(*r)}")
                if scoped_keys is not None:
                    scoped_keys.setdefault(key, []).append((r, name, where))
            continue
        if name:
            prev = keys.get(key)
            # legacy and cheat-sheet names rank below names\*.tsv, so a different name there isn't a conflict
            lower = rel.startswith(os.path.join("names", "legacy_")) or rel.startswith(os.path.join("names", "cheatsheet"))
            if prev and prev[0] != name and not lower:
                errors.append(f"{where}: '{name}' conflicts with '{prev[0]}' at {prev[1]}")
            keys.setdefault(key, (name, where))


# 17 VIN characters with at least one letter and four digits (a real VIN's serial part is digits), so
# German setting names like TEMPERATURWARNUNG aren't mistaken for one
VIN = re.compile(r"\b(?=[A-HJ-NPR-Z0-9]*[G-HJ-NPR-Z])(?=(?:[A-HJ-NPR-Z]*[0-9]){4})[A-HJ-NPR-Z0-9]{17}\b")
DEF_NAME = re.compile(r"^CAFD_([0-9A-F]{8})(?:_(\d{3})_(\d{3})_(\d{3}))?\.xml$", re.I)


def check_untrimmed(path, errors, warnings):
    r"""definitions\untrimmed\[transferred\[rejected\]]<CAFD id>.tsv.gz (ocm\untrimmed.py):
    kind address layout_hash start end mask value_hex name [reason: rejected\ only]."""
    import gzip
    rel = relpath(path)
    if not re.fullmatch(r"[0-9A-F]{8}\.tsv\.gz", os.path.basename(path)):
        errors.append(f"{rel}: name it <8 upper-case hex CAFD id>.tsv.gz")
        return 0
    n = 0
    try:
        for ln, line in enumerate(gzip.open(path, "rt", encoding="utf-8"), 1):
            if line.startswith("#") or not line.strip():
                continue
            n += 1
            f = (line.rstrip("\r\n").split("\t") + [""] * 8)[:8]
            kind, addr, h, st, en, mask, vhex, name = f
            where = f"{rel}:{ln}"
            if kind not in ("G", "F", "V", "R", "S") or not ADDR.match(addr) or not name:
                errors.append(f"{where}: expected G/F/V/R/S, a 4-hex address and a name")
            elif kind == "R" and not re.fullmatch(r"[0-9a-f]{12}", name):
                errors.append(f"{where}: an R row's name is a 12-digit rule print (ocm\\untrimmed.py rule_print)")
            elif kind == "S" and not re.fullmatch(r"\d{3}_\d{3}_\d{3}", name):
                errors.append(f"{where}: an S row's name is the E-Sys Plus reference version (150_130_000)")
            elif kind in ("R", "S"):
                if kind == "R" and (not st.isdigit() or not en.isdigit() or not MASK.match(mask)):
                    errors.append(f"{where}: start/end must be byte numbers and mask like 00000001b")
                continue
            elif not (h == "*" and kind == "G") and not re.fullmatch(r"[0-9a-f]{16}", h):
                errors.append(f"{where}: layout hash must be 16 lower-case hex digits (see ocm\\untrimmed.py)")
            elif kind != "G" and (not st.isdigit() or not en.isdigit() or not MASK.match(mask)):
                errors.append(f"{where}: start/end must be byte numbers and mask like 00000001b")
            elif kind == "V" and not HEXV.match(vhex):
                errors.append(f"{where}: value_hex must be upper-case hex bytes")
            if VIN.search(name):
                errors.append(f"{where}: contains something that looks like a VIN: remove it")
            if len(errors) > 50:
                break
    except (OSError, EOFError) as e:
        errors.append(f"{rel}: not a readable gzip file ({e})")
    return n


def check_byindex(path, errors):
    r"""definitions\untrimmed\transferred\raw\byindex\<CAFD id>.tsv.gz (ocm\untrimmed.py pack-esysplus):
    kind address reference function_index value_hex name; kinds H (group), N (function), P (value), A (value
    the reference has no name for)."""
    import gzip
    rel = relpath(path)
    if not re.fullmatch(r"[0-9A-F]{8}\.tsv\.gz", os.path.basename(path)):
        errors.append(f"{rel}: name it <8 upper-case hex CAFD id>.tsv.gz")
        return 0
    n = 0
    try:
        for ln, line in enumerate(gzip.open(path, "rt", encoding="utf-8"), 1):
            if line.startswith("#") or not line.strip():
                continue
            n += 1
            kind, addr, ref, idx, vhex, name = (line.rstrip("\r\n").split("\t") + [""] * 6)[:6]
            where = f"{rel}:{ln}"
            if kind not in ("H", "N", "P", "A") or not ADDR.match(addr) or not re.fullmatch(r"\d{3}_\d{3}_\d{3}", ref):
                errors.append(f"{where}: expected H/N/P/A, a 4-hex address and a reference like 116_064_000")
            elif kind != "H" and not idx.isdigit():
                errors.append(f"{where}: function_index must be a number")
            elif kind in ("P", "A") and not HEXV.match(vhex):
                errors.append(f"{where}: value_hex must be upper-case hex bytes")
            elif kind != "A" and not name:
                errors.append(f"{where}: missing name")
            elif VIN.search(name):
                errors.append(f"{where}: contains something that looks like a VIN: remove it")
            if len(errors) > 50:
                break
    except (OSError, EOFError) as e:
        errors.append(f"{rel}: not a readable gzip file ({e})")
    return n


def check_xml(path, errors, warnings):
    """definitions\\**\\*.xml: a CafdDefinition (our definition format) or an FDL cheat sheet."""
    import xml.etree.ElementTree as ET
    rel = relpath(path)
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
        if root.get("versions") is not None:
            try:
                versions.ranges(root.get("versions"))
            except ValueError as e:
                errors.append(f"{rel}: versions=\"{root.get('versions')}\": {e}")
    elif tag == "FDL":
        # cheat sheets are often third-party files kept unchanged: a bad entry is skipped by the import,
        # so it's a warning, not a reason to reject the sheet
        for c in root.iter("cafd"):
            if not re.fullmatch(r"[0-9A-Fa-f]{1,8}", c.get("id") or ""):
                warnings.append(f"{rel}: cafd id '{c.get('id')}' is not hex (entry skipped)")
            for g in c.iter("group"):
                for f in g.iter("function"):
                    n += 1
                    if not (f.get("start") or "").isdigit():
                        warnings.append(f"{rel}: cafd {c.get('id')} group {g.get('id')}: function without start "
                                        "(entry skipped)")
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
                        + sorted(glob.glob(os.path.join(config.NAMES, "versioned", "*.tsv")))
                        + sorted(glob.glob(os.path.join(config.NAMES, "cheatsheet", "*.tsv")))
                        + sorted(glob.glob(os.path.join(config.CONTRIB, "**", "*.xml"), recursive=True))
                        + sorted(glob.glob(os.path.join(config.CONTRIB, "untrimmed", "*.tsv.gz")))
                        + sorted(glob.glob(os.path.join(config.CONTRIB, "untrimmed", "bmw", "*.tsv.gz")))
                        + sorted(glob.glob(os.path.join(config.CONTRIB, "untrimmed", "transferred", "*.tsv.gz")))
                        + sorted(glob.glob(os.path.join(config.CONTRIB, "untrimmed", "transferred", "rejected",
                                                        "*.tsv.gz")))
                        + sorted(glob.glob(os.path.join(config.CONTRIB, "untrimmed", "transferred", "raw",
                                                        "*.tsv.gz")))
                        + sorted(glob.glob(os.path.join(config.CONTRIB, "untrimmed", "transferred", "raw",
                                                        "byindex", "*.tsv.gz"))))
    errors, warnings, keys, scoped_keys = [], [], {}, {}
    n_xml = n_xml_fn = n_untr = 0
    for p in files:
        if p.lower().endswith(".tsv.gz") and os.path.basename(os.path.dirname(p)).lower() == "byindex":
            n_untr += check_byindex(p, errors)
        elif p.lower().endswith(".tsv.gz"):
            n_untr += check_untrimmed(p, errors, warnings)
        elif p.lower().endswith(".xml"):
            n_xml += 1
            n_xml_fn += check_xml(p, errors, warnings) or 0
        else:
            check_file(p, errors, warnings, keys, scoped_keys)
    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("ERROR:", e)
    if n_untr:
        print(f"untrimmed packages: {n_untr} names")
    print(f"{sum(1 for p in files if p.lower().endswith('.tsv'))} names files ({len(keys)} names{f', {sum(map(len, scoped_keys.values()))} version-scoped' if scoped_keys else ''}), {n_xml} definition XMLs ({n_xml_fn} functions): "
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
