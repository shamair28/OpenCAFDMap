#!/usr/bin/env python3
r"""BMW untrimmed names, packed by datagroup layout (definitions\untrimmed\<CAFD id>.tsv.gz).

A datagroup's *layout* is the ordered list of its functions' start:end:mask. Names found for one CAFD version
apply to every version whose datagroup has the same layout, so one entry covers all versions that share it,
and names never land where bits moved. The same hash is computed in esys\agent.js at runtime.

Package rows (tab separated):   kind  address  layout_hash  start  end  mask  value_hex  name
    G = group name (layout_hash "*" = fallback for any layout), F = function name, V = value name

Packing (merges into existing files, never drops entries):
    python ocm\untrimmed.py pack-dumps <folder>     XML dumps (CafdDefinition with every function, e.g. Agent3353)
    python ocm\untrimmed.py pack-db <sqlite> [src..]  names already in a definition DB (default sources: untrimmed fwl)
    python ocm\untrimmed.py stats
"""
import glob
import gzip
import hashlib
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

PACK_DIR = os.path.join(config.CONTRIB, "untrimmed")
HEADER = "# kind\taddress\tlayout_hash\tstart\tend\tmask\tvalue_hex\tname\n"
WERT = re.compile(r"^\*?\s*wert_\d+\s*(.*?)\s*(\[[^\]]*\])?$")


def norm_mask(m):
    s = (m or "").strip()
    if re.fullmatch(r"[01]+b", s, re.I):
        return s.lower()
    if re.fullmatch(r"[0-9A-Fa-f]{1,2}", s):
        return format(int(s, 16), "08b") + "b"
    return s.lower()


def layout_hash(funcs):
    """funcs: ordered [(start, end, mask)] -> 16 hex chars (same as agent.js layoutHash)."""
    s = ";".join(f"{int(st)}:{int(en)}:{norm_mask(mk)}" for st, en, mk in funcs)
    return hashlib.md5(s.encode("ascii")).hexdigest()[:16]


def placeholder(n):
    n = (n or "").strip()
    return (not n or n in (".", "UNKNOWN", "value", "custom_wert") or n.startswith("UnknowParam")
            or bool(re.fullmatch(r"wert_\d+", n)) or bool(re.fullmatch(r"Group_[0-9A-Fa-f]{4}", n)))


def value_name(label):
    """'wert_02 nicht_aktiv [n/a here]' -> 'nicht_aktiv'; plain names pass through."""
    m = WERT.match(label or "")
    n = (m.group(1) if m else label or "").strip()
    return "" if placeholder(n) else n


# ------------------------------------------------------------------ package files
def load(cid, folder=PACK_DIR):
    """{(address, hash): {"G": name, "F": {(st, en, mask): name}, "V": {(st, en, mask, hex): name}}}, {address: name}"""
    p = os.path.join(folder, f"{cid.upper()}.tsv.gz")
    groups, fallback = defaultdict(lambda: {"G": None, "F": {}, "V": {}}), {}
    if not os.path.exists(p):
        return groups, fallback
    for line in gzip.open(p, "rt", encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        kind, addr, h, st, en, mk, vh, name = (line.rstrip("\r\n").split("\t") + [""] * 8)[:8]
        if kind == "G":
            if h == "*":
                fallback[addr] = name
            else:
                groups[(addr, h)]["G"] = name
        elif kind == "F":
            groups[(addr, h)]["F"][(int(st), int(en), mk)] = name
        elif kind == "V":
            groups[(addr, h)]["V"][(int(st), int(en), mk, vh)] = name
    return groups, fallback


def save(cid, groups, fallback, folder=PACK_DIR):
    os.makedirs(folder, exist_ok=True)
    rows = [f"G\t{a}\t*\t\t\t\t\t{n}" for a, n in sorted(fallback.items())]
    for (a, h), g in sorted(groups.items()):
        if g["G"]:
            rows.append(f"G\t{a}\t{h}\t\t\t\t\t{g['G']}")
        for (st, en, mk), n in sorted(g["F"].items()):
            rows.append(f"F\t{a}\t{h}\t{st}\t{en}\t{mk}\t\t{n}")
        for (st, en, mk, vh), n in sorted(g["V"].items()):
            rows.append(f"V\t{a}\t{h}\t{st}\t{en}\t{mk}\t{vh}\t{n}")
    with gzip.GzipFile(os.path.join(folder, f"{cid.upper()}.tsv.gz"), "wb", compresslevel=9, mtime=0) as f:
        f.write((HEADER + "\n".join(rows) + "\n").encode("utf-8"))
    return len(rows)


def merge(cid, found):
    """found: {(addr, hash): {"G", "F", "V"}} + group-name votes -> merged into the package (existing names win)."""
    groups, fallback = load(cid)
    votes = defaultdict(Counter)
    for (a, h), g in found.items():
        tgt = groups[(a, h)]
        if g["G"] and not tgt["G"]:
            tgt["G"] = g["G"]
        if g["G"]:
            votes[a][g["G"]] += 1
        for k, n in g["F"].items():
            tgt["F"].setdefault(k, n)
        for k, n in g["V"].items():
            tgt["V"].setdefault(k, n)
    for a, c in votes.items():
        fallback.setdefault(a, c.most_common(1)[0][0])
    return save(cid, groups, fallback)


# ------------------------------------------------------------------ packers
def pack_dumps(folder):
    """XML dumps with every function of the CAFD (names may be '.'), e.g. Agent3353's CAFD_<id>_<ver>.xml."""
    per = defaultdict(lambda: defaultdict(lambda: {"G": None, "F": {}, "V": {}}))
    for p in sorted(glob.glob(os.path.join(folder, "CAFD_*_*_*_*.xml"))):
        root = ET.parse(p).getroot()
        cid = (root.get("cafdId") or os.path.basename(p)[5:13]).upper()
        for g in root.iter("Group"):
            addr = (g.get("id") or "").upper()
            fns = list(g.iter("Function"))
            h = layout_hash([(f.get("start"), f.get("end"), f.get("mask")) for f in fns])
            e = per[cid][(addr, h)]
            if not placeholder(g.get("name")):
                e["G"] = g.get("name")
            for f in fns:
                k = (int(f.get("start")), int(f.get("end")), norm_mask(f.get("mask")))
                if not placeholder(f.get("name")):
                    e["F"][k] = f.get("name")
                for v in f.iter("Value"):
                    n = v.get("label") or ""
                    if not placeholder(n):
                        e["V"][k + ((v.get("code") or "").upper(),)] = n
    total = sum(merge(cid, found) for cid, found in per.items())
    print(f"{len(per)} CAFD ids from {folder} -> {PACK_DIR} ({total} rows)")


def pack_db(db, sources=("untrimmed", "fwl")):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    src = "(" + ",".join("?" * len(sources)) + ")"
    per = defaultdict(lambda: defaultdict(lambda: {"G": None, "F": {}, "V": {}}))
    for (cid,) in con.execute("SELECT DISTINCT cafd_id FROM cafd ORDER BY cafd_id").fetchall():
        for cpk, in con.execute("SELECT pk FROM cafd WHERE cafd_id=?", (cid,)).fetchall():
            fl = defaultdict(list)
            for dpk, addr, st, en, mk in con.execute(
                    "SELECT d.pk, d.address, f.start, f.end, f.mask FROM func f JOIN datagroup d ON d.pk=f.dg_pk "
                    "WHERE f.cafd_pk=? ORDER BY d.address, f.idx", (cpk,)):
                fl[(dpk, addr)].append((st, en, mk))
            hashes = {dpk: (addr, layout_hash(l)) for (dpk, addr), l in fl.items()}
            for dpk, name in con.execute(f"SELECT pk, name FROM datagroup WHERE cafd_pk=? AND name_source IN {src}",
                                         (cpk, *sources)):
                if dpk in hashes and not placeholder(name):
                    per[cid][hashes[dpk]]["G"] = name
            for dpk, st, en, mk, name in con.execute(
                    f"SELECT dg_pk, start, end, mask, name FROM func WHERE cafd_pk=? AND name_source IN {src}",
                    (cpk, *sources)):
                if dpk in hashes and not placeholder(name):
                    per[cid][hashes[dpk]]["F"].setdefault((st, en, norm_mask(mk)), name)
            for dpk, st, en, mk, hx, label in con.execute(
                    f"SELECT f.dg_pk, f.start, f.end, f.mask, v.hex, v.label FROM val v JOIN func f ON f.pk=v.func_pk "
                    f"WHERE f.cafd_pk=? AND v.label_source IN {src}", (cpk, *sources)):
                n = value_name(label)
                if dpk in hashes and n:
                    per[cid][hashes[dpk]]["V"].setdefault((st, en, norm_mask(mk), hx.upper()), n)
    total = sum(merge(cid, found) for cid, found in per.items())
    print(f"{len(per)} CAFD ids from {db} -> {PACK_DIR} ({total} rows)")


def stats():
    files = glob.glob(os.path.join(PACK_DIR, "*.tsv.gz"))
    c = Counter()
    for p in files:
        for line in gzip.open(p, "rt", encoding="utf-8"):
            if not line.startswith("#"):
                c[line[0]] += 1
    size = sum(os.path.getsize(p) for p in files)
    print(f"{len(files)} CAFD ids, {c['G']} group / {c['F']} function / {c['V']} value names, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "pack-dumps" and len(sys.argv) > 2:
        pack_dumps(sys.argv[2])
    elif cmd == "pack-db" and len(sys.argv) > 2:
        pack_db(sys.argv[2], tuple(sys.argv[3:]) or ("untrimmed", "fwl"))
    elif cmd == "stats":
        stats()
    else:
        sys.exit(__doc__)
