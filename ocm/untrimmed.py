#!/usr/bin/env python3
r"""Untrimmed names, packed by datagroup layout.

A datagroup's *layout* is the ordered list of its functions' start:end:mask (layout_hash; the same hash is computed
in esys\agent.js at runtime). The folders, never mixed:

  definitions\untrimmed\bmw\<id>.tsv.gz          BMW's own names, read from psdzdata old enough to still carry
                                                 them (PSdZData 54.1 and older): pack-caf. They apply to every
                                                 version with the same layout and are shown as they are.
  definitions\untrimmed\<id>.tsv.gz              the compatibility pack for launchers that read only this folder
                                                 (v2, v3.0.0): bmw\ plus transferred\ (BMW's own wins), written by
                                                 compat(). Those launchers can't mark names, so they show the
                                                 realigned names unmarked, on the right fields. This launcher never
                                                 reads it.
  definitions\untrimmed\transferred\raw\         what E-Sys Plus showed for newer, trimmed versions (E-Sys 3.35.3
                                                 dumps: pack-dumps), per dumped layout, with the reference it used
                                                 (S rows). Input only, never applied: E-Sys Plus asks its reference
                                                 for "function #i of group G" (discussion #9), so wherever BMW
                                                 inserted or removed functions, every later name is on the wrong
                                                 field (00001EF6 006_064_014, group 3000: 37 functions off).
  definitions\untrimmed\transferred\raw\byindex\ E-Sys Plus's references read by index for every CAFD version
                                                 (pack-esysplus): H/N/P/A rows. Input only.
  definitions\untrimmed\transferred\<id>.tsv.gz  realign(): each reference's names put back on the layout it was
                                                 made for, then decided field by field for every layout in the
                                                 maintainer's definition DBs (same field, every value code named;
                                                 flags: same rule print and unchanged neighbouring fields). Applied
                                                 by exact layout; unknown layouts borrow multi-value fields by field
                                                 (transfer_match). Always marked UNVERIFIED.
  definitions\untrimmed\transferred\rejected\    realigned rows that contradict BMW's own names (vet()), with the
                                                 reason in a 9th column. Never applied.

Groups whose layout isn't packaged fall back to exact field matches of BMW's own names, also marked (field_match).

Package rows (tab separated):   kind  address  layout_hash  start  end  mask  value_hex  name  [reason]
    G = group name (layout_hash "*" = fallback for any layout), F = function name, V = value name,
    R = rule print of a function (in name), S = E-Sys Plus reference of a raw layout (in name)

    python ocm\untrimmed.py pack-caf <folder>       decrypted psdzdata CAFDs (<caf> XML) that still carry their names
    python ocm\untrimmed.py pack-dumps <folder>     E-Sys 3.35.3 dumps (CafdDefinition XML, e.g. Agent3353) -> raw\
    python ocm\untrimmed.py pack-esysplus <folder>  E-Sys Plus reference reads by function index (TSV, discussion #9)
                                                    -> raw\byindex\ (kinds H/N/P/A: group, function, value name,
                                                    value the reference has no name for)
    python ocm\untrimmed.py pack-db <sqlite> [src..]  names already in a definition DB -> raw\ (sources: untrimmed fwl)
    python ocm\untrimmed.py realign <db.sqlite>.. [id..]  raw\ -> transferred\; the definition DBs must hold every
                                                    version of those CAFDs (e.g. workspace\cafd_defs_G020.sqlite)
    python ocm\untrimmed.py vet [id..]              re-check transferred rows against BMW's own names
    python ocm\untrimmed.py compat [id..]           rewrite the top-level compatibility pack (done after every pack,
                                                    realign and vet)
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

COMPAT_DIR = os.path.join(config.CONTRIB, "untrimmed")
PACK_DIR = os.path.join(COMPAT_DIR, "bmw")
TRANSFERRED_DIR = os.path.join(COMPAT_DIR, "transferred")
REJECTED_DIR = os.path.join(TRANSFERRED_DIR, "rejected")
RAW_DIR = os.path.join(TRANSFERRED_DIR, "raw")
BYINDEX_DIR = os.path.join(RAW_DIR, "byindex")
BYINDEX_HEADER = "# kind\taddress\treference\tfunction_index\tvalue_hex\tname\n"
ESYSPLUS_FILE = re.compile(r"Reference_CAF_([0-9A-Fa-f]{8})_(\d{3}_\d{3}_\d{3})\)")
REFERENCE = re.compile(r"Reference[:_]CAF_[0-9A-Fa-f]{8}_(\d{3}_\d{3}_\d{3})")
HEADER = "# kind\taddress\tlayout_hash\tstart\tend\tmask\tvalue_hex\tname\n"
REJECTED_HEADER = "# kind\taddress\tlayout_hash\tstart\tend\tmask\tvalue_hex\tname\treason\n"
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
            or n.endswith(POS_MARK.strip())   # borrowed by field position: never packed back as a real name
            or bool(re.fullmatch(r"wert_\d+", n)) or bool(re.fullmatch(r"Group_[0-9A-Fa-f]{4}", n)))


# ------------------------------------------------------------------ field fallback (issue #8)
# A datagroup whose layout isn't packaged (e.g. one field split in two) gets no names by layout. Its functions can
# still borrow the name packaged for exactly the same address, start, end and mask, but that is a guess: checked
# against the pack itself (leave one layout out), such a name is the right one for ~86% of fields when only one
# other layout has the field and ~98% when three or more agree; BMW reassigns reserved bits between versions
# without moving them. So borrowed names carry POS_MARK, and a layout only counts when it names no value code the
# function doesn't have (a reserved bit {00} that became a feature {00, 01} is not matched the other way round).
POS_MARK = " (?)"
UNVERIFIED = POS_MARK   # every name not proven for this CAFD version ends in it, whatever its source
POS_NOTE = ("BMW untrimmed name from another version of this CAFD, matched by field position only "
            "(this group's layout isn't packaged): unverified")
TRANSFER_NOTE = ("Name from E-Sys Plus's reference CAFD, matched to this version by field, value codes and coding "
                 "rules; not BMW's own name for this version: unverified, check before coding")
VALUES_NOTE = ("BMW name from a version with the same layout, but this function has value codes that version "
               "doesn't name (the field may have been reused): unverified")


def mark(name):
    """name + UNVERIFIED, once."""
    n = (name or "").strip()
    return n if n.endswith(UNVERIFIED.strip()) else n + UNVERIFIED


def unmark(name):
    n = (name or "").strip()
    return n[:-len(UNVERIFIED.strip())].rstrip() if n.endswith(UNVERIFIED.strip()) else n


def field_index(groups):
    """groups (load()) -> {(address, start, end, mask): [(name, {value hex: value name}), ...]}, one per layout."""
    idx = defaultdict(list)
    for (a, _h), g in groups.items():
        vals = defaultdict(dict)
        for (st, en, mk, vh), vn in g["V"].items():
            vals[(st, en, mk)][vh.upper()] = vn
        for k, n in g["F"].items():
            idx[(a,) + k].append((n, vals.get(k, {})))
    return idx


def field_match(idx, address, start, end, mask, hexes):
    """(name, {value hex: value name}) for a function with these value codes, or None: the packaged layouts with
    exactly this field that name no value code outside `hexes` must all give it the same name. Value names are
    only kept where those layouts agree too."""
    hexes = {h.upper() for h in hexes}
    cand = [(n, v) for n, v in idx.get((address, int(start), int(end), norm_mask(mask)), ()) if set(v) <= hexes]
    if not cand or len({n for n, _ in cand}) > 1:
        return None
    vals, clash = {}, set()
    for _, v in cand:
        for vh, vn in v.items():
            if vals.setdefault(vh, vn) != vn:
                clash.add(vh)
    return cand[0][0], {vh: vn for vh, vn in vals.items() if vh not in clash}


def value_name(label):
    """'wert_02 nicht_aktiv [n/a here]' -> 'nicht_aktiv'; plain names pass through."""
    m = WERT.match(label or "")
    n = (m.group(1) if m else label or "").strip()
    return "" if placeholder(n) else n


# ------------------------------------------------------------------ package files
def entry():
    """One packaged layout: group name, function / value names, rule prints {(st, en, mask): {print}}, reference."""
    return {"G": None, "F": {}, "V": {}, "R": {}, "S": None}


def load(cid, folder=None):
    """{(address, hash): entry()}, {address: group name for any layout}"""
    folder = folder or PACK_DIR
    p = os.path.join(folder, f"{cid.upper()}.tsv.gz")
    groups, fallback = defaultdict(entry), {}
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
        elif kind == "R":
            groups[(addr, h)]["R"].setdefault((int(st), int(en), mk), set()).add(name)
        elif kind == "S":
            groups[(addr, h)]["S"] = name
    return groups, fallback


def save(cid, groups, fallback, folder=None):
    folder = folder or PACK_DIR
    os.makedirs(folder, exist_ok=True)
    rows = [f"G\t{a}\t*\t\t\t\t\t{n}" for a, n in sorted(fallback.items())]
    for (a, h), g in sorted(groups.items()):
        if g.get("S"):
            rows.append(f"S\t{a}\t{h}\t\t\t\t\t{g['S']}")
        if g["G"]:
            rows.append(f"G\t{a}\t{h}\t\t\t\t\t{g['G']}")
        for (st, en, mk), n in sorted(g["F"].items()):
            rows.append(f"F\t{a}\t{h}\t{st}\t{en}\t{mk}\t\t{n}")
        for (st, en, mk, vh), n in sorted(g["V"].items()):
            rows.append(f"V\t{a}\t{h}\t{st}\t{en}\t{mk}\t{vh}\t{n}")
        for (st, en, mk), ps in sorted(g.get("R", {}).items()):
            rows += [f"R\t{a}\t{h}\t{st}\t{en}\t{mk}\t\t{x}" for x in sorted(ps)]
    with gzip.GzipFile(os.path.join(folder, f"{cid.upper()}.tsv.gz"), "wb", compresslevel=9, mtime=0) as f:
        f.write((HEADER + "\n".join(rows) + "\n").encode("utf-8"))
    return len(rows)


def save_rejected(cid, rows, folder=None):
    """rows: [(kind, addr, hash, st, en, mask, value_hex, name, reason)] -> rejected\\<id>.tsv.gz (removed if empty)."""
    folder = folder or REJECTED_DIR
    p = os.path.join(folder, f"{cid.upper()}.tsv.gz")
    if not rows:
        if os.path.exists(p):
            os.remove(p)
        return 0
    os.makedirs(folder, exist_ok=True)
    lines = ["\t".join(str(x) for x in r) for r in sorted(rows, key=lambda r: tuple(str(x) for x in r))]
    with gzip.GzipFile(p, "wb", compresslevel=9, mtime=0) as f:
        f.write((REJECTED_HEADER + "\n".join(lines) + "\n").encode("utf-8"))
    return len(rows)


def index_entry():
    """What E-Sys Plus's reference says for one group, by function index: its group name, function names, value
    names, and values it has no name for (so the reference function doesn't have them)."""
    return {"H": None, "N": {}, "P": defaultdict(dict), "A": defaultdict(set)}


def load_index(cid, folder=None):
    r"""raw\byindex\<id>.tsv.gz -> {(addr, reference): index_entry()}."""
    folder = folder or BYINDEX_DIR
    out = defaultdict(index_entry)
    p = os.path.join(folder, f"{cid.upper()}.tsv.gz")
    if not os.path.exists(p):
        return out
    for line in gzip.open(p, "rt", encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        kind, a, ref, i, vh, name = (line.rstrip("\r\n").split("\t") + [""] * 6)[:6]
        e = out[(a, ref)]
        if kind == "H":
            e["H"] = name
        elif kind == "N":
            e["N"][int(i)] = name
        elif kind == "P":
            e["P"][int(i)][vh] = name
        elif kind == "A":
            e["A"][int(i)].add(vh)
    return out


def save_index(cid, data, folder=None):
    folder = folder or BYINDEX_DIR
    os.makedirs(folder, exist_ok=True)
    rows = []
    for (a, ref), e in sorted(data.items()):
        if e["H"]:
            rows.append(f"H\t{a}\t{ref}\t\t\t{e['H']}")
        rows += [f"N\t{a}\t{ref}\t{i}\t\t{n}" for i, n in sorted(e["N"].items())]
        rows += [f"P\t{a}\t{ref}\t{i}\t{vh}\t{n}" for i, vs in sorted(e["P"].items()) for vh, n in sorted(vs.items())]
        rows += [f"A\t{a}\t{ref}\t{i}\t{vh}\t" for i, vs in sorted(e["A"].items()) for vh in sorted(vs)]
    with gzip.GzipFile(os.path.join(folder, f"{cid.upper()}.tsv.gz"), "wb", compresslevel=9, mtime=0) as f:
        f.write((BYINDEX_HEADER + "\n".join(rows) + "\n").encode("utf-8"))
    return len(rows)


def load_rejected(cid, folder=None):
    folder = folder or REJECTED_DIR
    p = os.path.join(folder, f"{cid.upper()}.tsv.gz")
    if not os.path.exists(p):
        return []
    return [tuple((line.rstrip("\r\n").split("\t") + [""] * 9)[:9])
            for line in gzip.open(p, "rt", encoding="utf-8") if line.strip() and not line.startswith("#")]


# ------------------------------------------------------------------ transferred names: realign + match by field
# E-Sys Plus gives function #i of a group the name of function #i of its reference CAF (discussion #9). Measured over
# the package (2026-11-24): 99.94% of (group, function index) slots carry the same name in every dumped layout,
# wherever the field sits. So the names are right only on the layout the reference was made for, and that layout
# is recognisable: E-Sys Plus names a value only when the reference function has it ('.' otherwise), so there every
# value code named for #i is present and none of the unnamed ones. realign() finds it among the versions in the
# definition DBs and decides every layout field by field: same field (address, start, end, mask), every value code
# named, and, for on/off flags and reserved bits (no more than FLAG_CODES value codes, which can't tell fields
# apart), the same coding-rule print and unchanged fields up to the nearest multi-value field on each side.
# Against BMW's own names (before vet): 25,640 of 25,769 agree, the rest partly BMW renames (*_FUNC / *_TYPE);
# E-Sys Plus by index had misplaced about a fifth of its names.
FLAG_CODES = 2
REF_FIT = 0.95          # share of a layout's functions whose value codes the reference names, to count as its layout


def rule_print(pairs):
    """[(value hex, coding rule text)] of one function -> 12 hex chars."""
    s = ";".join(f"{h.upper()}={r or ''}" for h, r in sorted(pairs))
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def transfer_index(groups):
    """Realigned transferred groups -> {(addr, st, en, mask): [(name, {value hex: value name}, {rule prints})]}."""
    idx = defaultdict(list)
    for (a, _h), g in groups.items():
        vals = defaultdict(dict)
        for (st, en, mk, vh), vn in g["V"].items():
            vals[(st, en, mk)][vh.upper()] = vn
        for k, n in g["F"].items():
            idx[(a,) + k].append((n, vals.get(k, {}), g["R"].get(k, set())))
    return idx


def transfer_match(idx, address, start, end, mask, hexes, rprint=None):
    """(name, {value hex: value name}) for a function with these value codes and rule print, or None. Every
    reference layout naming exactly this field that names all the value codes (and, for a function with no more
    than FLAG_CODES of them, has the same rule print) must give the same name."""
    hexes = {h.upper() for h in hexes}
    cand = [(n, v) for n, v, ps in idx.get((address, int(start), int(end), norm_mask(mask)), ())
            if hexes <= set(v) and (len(hexes) > FLAG_CODES or (rprint is not None and rprint in ps))]
    if not cand or len({n.upper() for n, _ in cand}) > 1:
        return None
    vals = {}
    for _, v in cand:
        for x in hexes:
            vals.setdefault(x, v[x])
    return cand[0][0], vals


def cache_layouts(dbs, ids):
    """Every layout of these CAFD ids in definition DBs -> {cid: {addr: {hash: [(key, codes, {rule prints})]}}}.
    Versions that share a layout can still differ in their rules: each function keeps every print seen."""
    out = defaultdict(lambda: defaultdict(dict))
    ids = sorted({i.upper() for i in ids})
    for db in dbs:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for n in range(0, len(ids), 500):
            chunk = ids[n:n + 500]
            q = ",".join("?" * len(chunk))
            vals = defaultdict(list)
            for fpk, hx, raw in con.execute(
                    "SELECT v.func_pk, upper(v.hex), coalesce(r.raw, '') FROM val v JOIN func f ON f.pk=v.func_pk "
                    f"JOIN cafd c ON c.pk=f.cafd_pk LEFT JOIN rule r ON r.id=v.rule_id WHERE c.cafd_id IN ({q}) "
                    "AND v.hex IS NOT NULL", chunk):
                vals[fpk].append((hx, raw))
            cur, fs = None, []

            def flush():
                if cur is None:
                    return
                h = layout_hash([f[0] for f in fs])
                have = out[cur[0]][cur[1]]
                if h not in have:
                    have[h] = [(k, cs, {p}) for k, cs, p in fs]
                else:
                    for (_k, _c, ps), (_k2, _c2, p) in zip(have[h], fs):
                        ps.add(p)
            for fpk, dpk, cid, addr, st, en, mk in con.execute(
                    "SELECT f.pk, f.dg_pk, c.cafd_id, f.address, f.start, f.end, f.mask FROM func f "
                    f"JOIN cafd c ON c.pk=f.cafd_pk WHERE c.cafd_id IN ({q}) ORDER BY f.dg_pk, f.idx", chunk):
                if cur is None or dpk != cur[2]:
                    flush()
                    cur, fs = (cid.upper(), addr.upper(), dpk), []
                v = vals.get(fpk, [])
                fs.append(((st, en, norm_mask(mk)), frozenset(x for x, _ in v), rule_print(v)))
            flush()
        con.close()
    return out


def realign_cid(cid, layouts):
    """raw\\<id> (E-Sys Plus by index: dumped layouts) + raw\\byindex\\<id> (E-Sys Plus's references read by index)
    + this CAFD id's layouts (cache_layouts) -> transferred\\<id>: each reference's names on the layout it was made
    for. -> Counter of what happened."""
    raw, raw_fb = load(cid, RAW_DIR)
    byidx = load_index(cid)
    out, n = defaultdict(entry), Counter()
    refs = defaultdict(list)                             # addr -> [(ref layout hash, ref, field names, group)]
    buckets = defaultdict(list)                          # (addr, reference) -> [(hash, raw entry)]
    for (a, h), e in raw.items():
        if h in layouts.get(a, {}):
            buckets[(a, e["S"] or "?")].append((h, e))
        elif e["F"]:
            n["raw layouts not in the definition DBs"] += 1
    for (a, ref), x in byidx.items():
        if a in layouts:
            buckets.setdefault((a, ref), [])
        elif x["N"]:
            n["referenced groups not in the definition DBs"] += 1
    for (a, ref), items in sorted(buckets.items()):
        # per reference function #i: its name, the value names E-Sys Plus gave (codes it has), and the codes a
        # dumped function #i had that got no value name (codes it doesn't have)
        names, vals, absent, gname = defaultdict(Counter), defaultdict(dict), defaultdict(set), Counter()
        x = byidx.get((a, ref))
        if x:
            if x["H"]:
                gname[x["H"]] += 1
            for i, nm in x["N"].items():
                names[i][nm] += 1
            for i, vs in x["P"].items():
                vals[i].update(vs)
            for i, vs in x["A"].items():
                absent[i] |= vs
        for h, e in items:
            if e["G"]:
                gname[e["G"]] += 1
            for i, (k, cs, _p) in enumerate(layouts[a][h]):
                if k in e["F"]:
                    names[i][e["F"][k]] += 1
                named = {x for x in cs if e["V"].get(k + (x,))}
                for x in named:
                    vn = e["V"][k + (x,)]
                    if vals[i].setdefault(x, vn) != vn:
                        vals[i][x] = None                # two value names for one code: unusable
                if named:                                # the reference has value names here: the rest are missing
                    absent[i] |= set(cs) - named
        if not names:
            continue
        # the reference's layout: function #i of it has every code named for #i and none of the missing ones
        size, best = max(names) + 1, None
        for h, fs in layouts[a].items():
            if len(fs) < size:
                continue
            ev = [i for i in range(len(fs)) if vals.get(i) or absent.get(i)]
            if not ev:
                # no value names to compare (raw data fields): only safe when the group never changed layout
                if len(layouts[a]) == 1 and len(fs) == size:
                    best = ((1.0, True), h)
                continue
            fit = sum(1 for i in ev if set(vals.get(i, ())) <= fs[i][1] and not (absent.get(i, set()) & fs[i][1]))
            score = (fit / len(ev), len(fs) == size)
            if not best or score > best[0]:
                best = (score, h)
        if best:
            best = (best[0][0], best[1])
        if not best or best[0] < REF_FIT:
            n["groups whose reference layout isn't in the definition DBs"] += 1
            continue
        refs[a].append((best[1], ref, {k: (next(iter(names[i])), {x: vals[i][x] for x in cs}, ps, i)
                                       for i, (k, cs, ps) in enumerate(layouts[a][best[1]])
                                       if len(names.get(i, ())) == 1 and all(vals[i].get(x) for x in cs)},
                        gname.most_common(1)[0][0] if gname else None))
        n["groups realigned"] += 1
    # every version's layout: a function gets the reference's name for the same field when its value codes are all
    # named there and, for an on/off flag or reserved bit, its rule print is the reference's and the fields around it
    # (up to the nearest multi-value fields) are the reference's too: BMW inserting a flag shifts every later flag in
    # the byte without changing any field's shape, values or rules. Measured against BMW's own names: 1 wrong in 1415
    # (4% wrong without the neighbourhood rule).
    for a, rl in refs.items():
        for h, T in layouts[a].items():
            e = out[(a, h)]
            for href, ref, field, gname in rl:
                e["G"] = e["G"] or gname
                if h == href:
                    e["S"] = e["S"] or (None if ref == "?" else ref)
            for t, (k, cs, ps) in enumerate(T):
                got = set()
                for href, _ref, field, _g in rl:
                    c = field.get(k)
                    if not c or not set(cs) <= set(c[1]):
                        continue
                    if len(cs) <= FLAG_CODES and not (ps & c[2] and same_neighbourhood(T, t, layouts[a][href], c[3])):
                        continue
                    got.add((c[0], tuple(sorted((x, c[1][x]) for x in cs))))
                if len({g[0].upper() for g in got}) != 1:
                    if got:
                        n["functions two references name differently"] += 1
                    continue
                name, vs = next(iter(got))
                e["F"][k] = name
                for x, vn in vs:
                    e["V"][k + (x,)] = vn
                n["layout functions named"] += 1
    out = {k: e for k, e in out.items() if e["G"] or e["F"]}
    p = os.path.join(TRANSFERRED_DIR, f"{cid.upper()}.tsv.gz")
    if out or raw_fb:
        save(cid, out, raw_fb, TRANSFERRED_DIR)
    elif os.path.exists(p):
        os.remove(p)
    save_rejected(cid, [])                               # stale: vet() sorts the new rows again
    return n


def same_neighbourhood(T, t, R, r):
    """Are the fields around function t of layout T, up to the nearest multi-value field on each side (or the group's
    edge), the same, in the same order, as those around function r of the reference layout R?"""
    def bounds(seq, i):
        lo = next((j for j in range(i - 1, -1, -1) if len(seq[j][1]) > FLAG_CODES), -1)
        hi = next((j for j in range(i + 1, len(seq)) if len(seq[j][1]) > FLAG_CODES), len(seq))
        return lo, hi
    tl, th = bounds(T, t)
    rl, rh = bounds(R, r)
    seg = lambda seq, lo, hi: [(f[0], f[1]) for f in seq[max(lo, 0):min(hi + 1, len(seq))]]  # noqa: E731
    return t - tl == r - rl and seg(T, tl, th) == seg(R, rl, rh)


def realign(dbs, ids=None):
    ids = [i.upper() for i in ids] if ids else sorted({os.path.basename(p)[:8].upper() for d in (RAW_DIR, BYINDEX_DIR)
                                                      for p in glob.glob(os.path.join(d, "*.tsv.gz"))})
    layouts = cache_layouts(dbs, ids)
    total = Counter()
    for cid in ids:
        total += realign_cid(cid, layouts.get(cid, {}))
        vet(cid)
    print(f"realigned E-Sys Plus names of {len(ids)} CAFD ids: " + ", ".join(f"{v} {k}" for k, v in total.items()))


# ------------------------------------------------------------------ vetting transferred names
# Realigned names checked against BMW's own names (definitions\untrimmed): BMW's own names keep their field shape
# across layouts for 99.8% of rows and never name two functions of one layout the same.
NOT_A_NAME = re.compile(r"^(UNBELEGT|RESERVE[DN]?|RESERVIERT|FREI|NICHT_BELEGT|NOT_USED|UNUSED|PADDING\w*)(_?\d+)?$",
                        re.I)


def shape(st, en, mask):
    """A field's shape: (extra bytes, bits in the mask). The same setting keeps it from version to version."""
    return int(en) - int(st), norm_mask(mask).count("1")


def vet_rows(bmw, transferred):
    """bmw / transferred: load() groups. -> {(addr, hash, (st, en, mask)): reason} for transferred function rows
    that must not be used, and the set of rows that BMW's own package already names identically (redundant)."""
    anchor = defaultdict(set)                       # (addr, name) -> shapes in BMW's own names
    for (a, _h), g in bmw.items():
        for k, n in g["F"].items():
            anchor[(a, n.upper())].add(shape(*k))
    bad, same = {}, set()
    for (a, h), g in transferred.items():
        own = bmw.get((a, h), {}).get("F", {})
        dup = Counter(n.upper() for n in g["F"].values())
        for k, n in g["F"].items():
            key, nu = (a, h, k), n.upper()
            if k in own:
                if own[k].upper() == nu:
                    same.add(key)
                else:
                    bad[key] = f"BMW's own name for this field in this layout is {own[k]}"
            elif NOT_A_NAME.match(n):
                continue                            # reserved bits come in every size
            elif anchor.get((a, nu)) and shape(*k) not in anchor[(a, nu)]:
                bad[key] = "BMW's own {} is a field of another size ({})".format(
                    n, ", ".join(f"{b} bits" if not x else f"{x + 1} bytes" for x, b in sorted(anchor[(a, nu)])))
            elif dup[nu] > 1:
                bad[key] = "the reference names more than one field of this layout so"
    return bad, same


def vet(cid):
    """Re-sort a CAFD id's transferred rows (transferred\\ + rejected\\) into kept and rejected. Rows that BMW's
    own package names identically are dropped as redundant. -> (kept F rows, rejected F rows)."""
    bmw, _ = load(cid)
    pool, fallback = load(cid, TRANSFERRED_DIR)
    for kind, a, h, st, en, mk, vh, name, _reason in load_rejected(cid):
        if kind == "G":
            pool[(a, h)]["G"] = pool[(a, h)]["G"] or name
        elif kind == "F":
            pool[(a, h)]["F"].setdefault((int(st), int(en), mk), name)
        elif kind == "V":
            pool[(a, h)]["V"].setdefault((int(st), int(en), mk, vh), name)
        elif kind == "R":
            pool[(a, h)]["R"].setdefault((int(st), int(en), mk), set()).add(name)
    bad, same = vet_rows(bmw, pool)
    keep, rej = defaultdict(entry), []
    for (a, h), g in pool.items():
        e = keep[(a, h)]
        e["G"], e["S"] = g["G"], g["S"]
        for k, n in g["F"].items():
            if (a, h, k) in bad:
                rej.append(("F", a, h, *k, "", n, bad[(a, h, k)]))
                rej += [("R", a, h, *k, "", p, "its function was rejected") for p in sorted(g["R"].get(k, ()))]
            elif (a, h, k) not in same:
                e["F"][k] = n
                if k in g["R"]:
                    e["R"][k] = g["R"][k]
        for (st, en, mk, vh), n in g["V"].items():
            k = (st, en, mk)
            if (a, h, k) in bad:
                rej.append(("V", a, h, st, en, mk, vh, n, "its function was rejected"))
            elif (a, h, k) not in same:
                e["V"][(st, en, mk, vh)] = n
    keep = {k: e for k, e in keep.items() if e["G"] or e["F"] or e["V"]}
    if keep or fallback:
        save(cid, keep, fallback, TRANSFERRED_DIR)
    elif os.path.exists(os.path.join(TRANSFERRED_DIR, f"{cid.upper()}.tsv.gz")):
        os.remove(os.path.join(TRANSFERRED_DIR, f"{cid.upper()}.tsv.gz"))
    save_rejected(cid, rej)
    compat(cid)
    return sum(len(e["F"]) for e in keep.values()), sum(1 for r in rej if r[0] == "F")


def compat(cid):
    r"""definitions\untrimmed\<id>.tsv.gz for launchers that read only that folder (v2, v3.0.0): BMW's own names
    (bmw\) plus the realigned transferred names (transferred\), BMW's own winning. G/F/V rows only, so those
    launchers read it like before; they show every name unmarked."""
    bmw, bfb = load(cid, PACK_DIR)
    tr, tfb = load(cid, TRANSFERRED_DIR)
    out = defaultdict(entry)
    for src in (bmw, tr):
        for key, e in src.items():
            o = out[key]
            o["G"] = o["G"] or e["G"]
            for k, n in e["F"].items():
                o["F"].setdefault(k, n)
            for k, n in e["V"].items():
                o["V"].setdefault(k, n)
    fallback = dict(tfb)
    fallback.update(bfb)
    out = {k: dict(e, R={}, S=None) for k, e in out.items() if e["G"] or e["F"] or e["V"]}
    p = os.path.join(COMPAT_DIR, f"{cid.upper()}.tsv.gz")
    if out or fallback:
        return save(cid, out, fallback, COMPAT_DIR)
    if os.path.exists(p):
        os.remove(p)
    return 0


def compat_all(ids=None):
    ids = ids or sorted({os.path.basename(p)[:8].upper() for d in (PACK_DIR, TRANSFERRED_DIR)
                         for p in glob.glob(os.path.join(d, "*.tsv.gz"))})
    rows = sum(compat(cid) for cid in ids)
    print(f"compatibility pack for v2 / v3.0.0: {len(ids)} CAFD ids, {rows} rows -> {COMPAT_DIR}")


def vet_all(ids=None):
    ids = ids or sorted({os.path.basename(p)[:8].upper() for d in (TRANSFERRED_DIR, REJECTED_DIR)
                         for p in glob.glob(os.path.join(d, "*.tsv.gz"))})
    kept = rejected = 0
    for cid in ids:
        k, r = vet(cid)
        kept, rejected = kept + k, rejected + r
    print(f"vetted transferred names of {len(ids)} CAFD ids: {kept} function names kept (shown as unverified), "
          f"{rejected} rejected")


def merge(cid, found, folder=None):
    """found: {(addr, hash): entry()} + group-name votes -> merged into the package (existing names win)."""
    folder = folder or PACK_DIR
    groups, fallback = load(cid, folder)
    votes = defaultdict(Counter)
    for (a, h), g in found.items():
        tgt = groups[(a, h)]
        if g["G"] and not tgt["G"]:
            tgt["G"] = g["G"]
        if g["G"]:
            votes[a][g["G"]] += 1
        tgt["S"] = tgt["S"] or g.get("S")
        for k, n in g["F"].items():
            tgt["F"].setdefault(k, n)
        for k, n in g["V"].items():
            tgt["V"].setdefault(k, n)
        for k, ps in g.get("R", {}).items():
            tgt["R"].setdefault(k, set()).update(ps)
    for a, c in votes.items():
        fallback.setdefault(a, c.most_common(1)[0][0])
    return save(cid, groups, fallback, folder)


# ------------------------------------------------------------------ packers
def pack_dumps(folder):
    """XML dumps with every function of the CAFD (names may be '.'), e.g. Agent3353's CAFD_<id>_<ver>.xml.
    E-Sys 3.35.3 shows names only through E-Sys Plus, which takes them by function index from its own reference
    CAFDs (ecu="... (Reference:CAF_<id>_<ver>)"): packed as they are into raw\\ with that reference; realign puts
    them on the right fields."""
    per = defaultdict(lambda: defaultdict(entry))
    for p in sorted(glob.glob(os.path.join(folder, "CAFD_*_*_*_*.xml"))):
        root = ET.parse(p).getroot()
        cid = (root.get("cafdId") or os.path.basename(p)[5:13]).upper()
        ref = REFERENCE.search(root.get("ecu") or "")
        for g in root.iter("Group"):
            addr = (g.get("id") or "").upper()
            fns = list(g.iter("Function"))
            h = layout_hash([(f.get("start"), f.get("end"), f.get("mask")) for f in fns])
            e = per[cid][(addr, h)]
            e["S"] = e["S"] or (ref.group(1) if ref else None)
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
    total = sum(merge(cid, found, RAW_DIR) for cid, found in per.items())
    print(f"{len(per)} CAFD ids from {folder} -> {RAW_DIR} ({total} rows)")
    print("Now run: python ocm\\untrimmed.py realign <definition DBs with every version of these CAFDs> "
          + " ".join(sorted(per)))


def pack_esysplus(folder):
    r"""E-Sys Plus reference reads by function index (discussion #9): TSVs named
    '<ECU> (Reference_CAF_<id>_<ref>)_<...>.tsv' with GROUP_ADDR_HEX GROUP_NAME FUNC_INDEX FUNC_NAME PARAM_INDEX
    PARAM_NAME PARAM_VALUE, one per CAFD version or merged per reference (any folder depth). -> raw\byindex\. A
    value named '.' is one the reference has no name for. Then run realign."""
    per = defaultdict(lambda: defaultdict(index_entry))
    files = clash = 0
    ambiguous = defaultdict(set)                         # (cid, addr, ref) -> {(index, value)} with two names
    for root, _dirs, names in os.walk(folder):
        for fn in sorted(names):
            m = ESYSPLUS_FILE.search(fn)
            if not fn.lower().endswith(".tsv") or not m:
                continue
            files += 1
            cid, ref = m.group(1).upper(), m.group(2)
            with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as f:
                for line in f:
                    p = line.rstrip("\r\n").split("\t")
                    if len(p) < 7 or not p[2].isdigit():
                        continue
                    a, gname, i, fname, _pi, pname, pval = p[:7]
                    e, i, vh = per[cid][(a.upper(), ref)], int(i), pval.replace(" ", "").upper()
                    if gname and not placeholder(gname) and not e["H"]:
                        e["H"] = gname
                    if fname and not placeholder(fname) and e["N"].setdefault(i, fname) != fname:
                        clash += 1
                    if not vh:
                        continue
                    if placeholder(pname):
                        e["A"][i].add(vh)
                    elif e["P"][i].setdefault(vh, pname) != pname:
                        # a function listing one value twice gets a name per param index (aktiv / nicht_aktiv
                        # both on 01): no single name for that value
                        ambiguous[(cid, a.upper(), ref)].add((i, vh))
    for (cid, a, ref), iv in ambiguous.items():
        e = per[cid][(a, ref)]
        for i, vh in iv:
            e["P"][i].pop(vh, None)
            clash += 1
    total = 0
    for cid, found in per.items():
        have = load_index(cid)
        for k, e in found.items():
            h = have[k]
            h["H"] = h["H"] or e["H"]
            for i, nm in e["N"].items():
                h["N"].setdefault(i, nm)
            for i, vs in e["P"].items():
                for vh, nm in vs.items():
                    h["P"][i].setdefault(vh, nm)
            for i, vs in e["A"].items():
                h["A"][i] |= vs
        for e in have.values():                          # a value named somewhere is the reference's after all
            for i in list(e["A"]):
                e["A"][i] -= set(e["P"].get(i, {}))
        total += save_index(cid, have)
    print(f"{files} E-Sys Plus files, {len(per)} CAFD ids -> {BYINDEX_DIR} ({total} rows; {clash} values with two names "
          "dropped)")
    print("Now run: python ocm\\untrimmed.py realign <definition DBs with every version of these CAFDs>")


CAF_NS = {"c": "http://bmw.com/2005/psdz.data.caf"}
CAF_FILE = re.compile(r"(?:cafd_)?([0-9A-Fa-f]{8})[._]")


def pack_caf(folder):
    """Decrypted psdzdata CAFDs (<caf> XML: cafd_<id>.caf.<ver>.xml or <id>_<ver>.xml) from a psdzdata old enough to
    still carry its names. Only the names are read; the files themselves must never be committed."""
    per = defaultdict(lambda: defaultdict(entry))
    clean = lambda n: re.sub(r"\s+", " ", n or "").strip()
    n = 0
    for p in sorted(glob.glob(os.path.join(folder, "*.xml"))):
        m = CAF_FILE.match(os.path.basename(p))
        if not m:
            continue
        try:
            data = ET.parse(p).getroot().find("c:data", CAF_NS)
        except ET.ParseError as e:
            print(f"  skipped {os.path.basename(p)}: {e}", file=sys.stderr)
            continue
        n += 1
        for dg in data.findall("c:datagroup", CAF_NS) if data is not None else ():
            fns = []
            for fn in dg.findall("c:function", CAF_NS):
                si = fn.find("c:storageInfo", CAF_NS)
                st = int(si.findtext("c:start", "0", CAF_NS))
                fns.append((st, int(si.findtext("c:end", str(st), CAF_NS)),
                            (si.findtext("c:mask", "", CAF_NS) or "").strip(), fn))
            e = per[m.group(1).upper()][((dg.get("address") or "").upper(), layout_hash([f[:3] for f in fns]))]
            if not placeholder(clean(dg.get("name"))):
                e["G"] = clean(dg.get("name"))
            for st, en, mk, fn in fns:
                k = (st, en, norm_mask(mk))
                if not placeholder(clean(fn.get("name"))):
                    e["F"][k] = clean(fn.get("name"))
                seen = set()
                for cp in fn.findall("c:condParam", CAF_NS):
                    uvs = cp.find("c:values", CAF_NS)
                    uvs = [] if uvs is None else sorted(uvs.findall("c:unitVal", CAF_NS),
                                                        key=lambda u: int(u.get("position", "0")))
                    hx = "".join((u.text or "").strip().upper() for u in uvs)
                    name = clean(cp.findtext("c:paramName", "", CAF_NS))
                    if hx and hx not in seen and not placeholder(name):   # first name wins when values share a code
                        seen.add(hx)
                        e["V"][k + (hx,)] = name
    total = sum(merge(cid, found) for cid, found in per.items())
    print(f"{n} CAFDs, {len(per)} CAFD ids from {folder} -> {PACK_DIR} ({total} rows)")
    # BMW's own names are the evidence transferred names are checked against: re-vet those ids
    again = [c for c in sorted(per) if any(os.path.exists(os.path.join(d, f"{c}.tsv.gz"))
                                            for d in (TRANSFERRED_DIR, REJECTED_DIR))]
    if again:
        vet_all(again)                                   # also rewrites their compatibility pack
    compat_all(sorted(set(per) - set(again)))


def pack_db(db, sources=("untrimmed", "fwl")):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    src = "(" + ",".join("?" * len(sources)) + ")"
    per = defaultdict(lambda: defaultdict(entry))
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
    # names in a DB can't be traced back to BMW's own file for that version: packed as transferred
    total = sum(merge(cid, found, RAW_DIR) for cid, found in per.items())
    print(f"{len(per)} CAFD ids from {db} -> {RAW_DIR} ({total} rows); now run realign")


def stats():
    for label, folder in (("BMW's own names", PACK_DIR), ("E-Sys Plus as dumped (raw, not applied)", RAW_DIR),
                          ("transferred, realigned (unverified)", TRANSFERRED_DIR), ("rejected", REJECTED_DIR),
                          ("compatibility pack for v2 / v3.0.0", COMPAT_DIR)):
        files = glob.glob(os.path.join(folder, "*.tsv.gz"))
        c = Counter()
        for p in files:
            for line in gzip.open(p, "rt", encoding="utf-8"):
                if not line.startswith("#"):
                    c[line[0]] += 1
        size = sum(os.path.getsize(p) for p in files)
        print(f"{label}: {len(files)} CAFD ids, {c['G']} group / {c['F']} function / {c['V']} value names, "
              f"{size / 1e6:.1f} MB")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "pack-dumps" and len(sys.argv) > 2:
        pack_dumps(sys.argv[2])
    elif cmd == "pack-esysplus" and len(sys.argv) > 2:
        pack_esysplus(sys.argv[2])
    elif cmd == "pack-caf" and len(sys.argv) > 2:
        pack_caf(sys.argv[2])
    elif cmd == "pack-db" and len(sys.argv) > 2:
        pack_db(sys.argv[2], tuple(sys.argv[3:]) or ("untrimmed", "fwl"))
    elif cmd == "realign" and any(a.lower().endswith(".sqlite") for a in sys.argv[2:]):
        realign([a for a in sys.argv[2:] if a.lower().endswith(".sqlite")],
                [a for a in sys.argv[2:] if not a.lower().endswith(".sqlite")] or None)
    elif cmd == "vet":
        vet_all([c.upper() for c in sys.argv[2:]] or None)
    elif cmd == "compat":
        compat_all([c.upper() for c in sys.argv[2:]] or None)
    elif cmd == "stats":
        stats()
    else:
        sys.exit(__doc__)
