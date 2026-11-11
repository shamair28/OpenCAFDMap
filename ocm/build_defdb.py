#!/usr/bin/env python3
r"""Build the CAFD definition database from decrypted psdzdata CAFDs.

    python build_defdb.py              # full build from the decrypted cache
    python build_defdb.py --relabel    # only recompute labels (after improving crit labels / names)

Inputs (defaults come from config.json via ocm\config.py)
  --cache     cafd_<ID>_<main>_<sub>_<patch>.xml.gz from decrypt_cafds.js
  --fafp      decrypted FA->FP mapping for the series (crit -> SA / HO / E-word / date)
  --ista      decrypted ISTA DiagDocDb (SA option-code titles)
  --verified  fp_crits_verified.tsv  (crits confirmed via E-Sys FP generation)
  --typekey   fp_crits_typekey.tsv   (crits derived from all type keys, label_typekey_crits.py)
  --fpdef     curated FP_<series>.xml (fallback labels; shown with a trailing '?')
  names\*.tsv           community names (version independent; legacy_*.tsv rank lowest)
  workspace\my_names.tsv your private names (highest priority)

Why derived labels: current psdzdata strips every FDL name ("." in E-Sys) and ISTA does not
carry them. Each value's coding rule (XPath over FP criteria) survives, so we decode it into
option codes / vehicle attributes. Real names are layered on from the names files.
"""
import argparse
import glob
import gzip
import html
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
from rules import FIXED, context_from_fp, crit_refs, readable, simplified  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NS = {"c": "http://bmw.com/2005/psdz.data.caf"}
FILE_RE = re.compile(r"cafd_([0-9A-F]{8})_(\d{3})_(\d{3})_(\d{3})\.xml\.gz$", re.I)
SEL_KIND = {"W": "SA", "H": "HO", "K": "E-word", "A": "date", "T": "typekey"}

SCHEMA = """
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE cafd(
  pk INTEGER PRIMARY KEY, cafd_id TEXT NOT NULL, main INT, sub INT, patch INT, version TEXT,
  series TEXT, ecu TEXT, src TEXT, UNIQUE(cafd_id, version));
-- every distinct coding rule once; readable/crits are recomputed by --relabel
CREATE TABLE rule(id INTEGER PRIMARY KEY, raw TEXT UNIQUE, readable TEXT, crits TEXT);
CREATE TABLE datagroup(
  pk INTEGER PRIMARY KEY, cafd_pk INT NOT NULL, address TEXT, dg_class TEXT, units TEXT,
  active_rule INT, name TEXT, name_source TEXT);
CREATE TABLE func(
  pk INTEGER PRIMARY KEY, cafd_pk INT NOT NULL, dg_pk INT NOT NULL, address TEXT, idx INT,
  start INT, end INT, size TEXT, mask TEXT, mask_hex TEXT,
  name TEXT, name_source TEXT, hint TEXT, comment TEXT, default_hex TEXT);
CREATE TABLE val(
  pk INTEGER PRIMARY KEY, func_pk INT NOT NULL, idx INT, hex TEXT,
  is_default INT, rule_id INT, label TEXT, label_source TEXT, used INT);
CREATE TABLE crit(val INT PRIMARY KEY, cat INT, fa_codes TEXT, selector TEXT, label TEXT, short TEXT, source TEXT);
CREATE TABLE ecu_cafd(ecu TEXT, cafd_id TEXT, source TEXT, PRIMARY KEY(ecu, cafd_id));
CREATE TABLE name_override(
  cafd_id TEXT NOT NULL, address TEXT NOT NULL, start INT, end INT, mask TEXT, value_hex TEXT,
  name TEXT NOT NULL, comment TEXT, source TEXT NOT NULL);
"""

INDEXES = """
CREATE INDEX ix_cafd_id ON cafd(cafd_id);
CREATE INDEX ix_dg_cafd ON datagroup(cafd_pk, address);
CREATE INDEX ix_func_cafd ON func(cafd_pk, address, start, end, mask);
CREATE INDEX ix_func_dg ON func(dg_pk);
CREATE INDEX ix_val_func ON val(func_pk);
CREATE INDEX ix_override ON name_override(cafd_id, address, start, end, mask, value_hex);
CREATE VIEW v_values AS
  SELECT c.cafd_id, c.version, c.ecu, f.address, d.name AS group_name, f.start, f.end, f.mask,
         f.name AS func_name, f.name_source, f.hint, f.comment, v.idx, v.hex, v.is_default, v.used,
         v.label, v.label_source, r.readable AS rule, r.raw AS rule_raw, f.pk AS func_pk, v.pk AS val_pk
    FROM val v JOIN func f ON f.pk = v.func_pk JOIN datagroup d ON d.pk = f.dg_pk
    JOIN cafd c ON c.pk = f.cafd_pk LEFT JOIN rule r ON r.id = v.rule_id;
"""


def mask_to_hex(mask):
    m = (mask or "").strip()
    if m.endswith("b") and m[:-1] and set(m[:-1]) <= {"0", "1"}:
        bits = m[:-1]
        return "%0*X" % ((len(bits) + 3) // 4, int(bits, 2))
    return m


# ------------------------------------------------------------------ crit labels
def load_tsv_crits(path, source, crits):
    if not path or not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\r\n").split("\t")
        cv, cat, label = int(f[0]), int(f[1]), f[2]
        short = f[3] if source == "curated_unverified" and len(f) > 3 and f[3] else label
        if cat < 128 and cv not in crits:
            crits[cv] = dict(cat=cat, fa_codes="", selector="", label=label, short=short, source=source)


def load_crits(a):
    """Priority: fafp (SA/HO/E-word/date) > E-Sys verified > type-key derived > curated."""
    crits = {}
    if a.fafp and os.path.exists(a.fafp):
        s = open(a.fafp, encoding="utf-8").read()
        rows = re.findall(r'<faElement code="([^"]*)" selector="([^"]*)"/><fpElement categoryVal="(\d+)" '
                          r'criteriaVal="(\d+)"', s)
        by = defaultdict(list)
        for code, sel, cat, cv in rows:
            by[int(cv)].append((code, sel, int(cat)))
        sa_titles = {}
        if a.ista and os.path.exists(a.ista):
            con = sqlite3.connect(f"file:{a.ista}?mode=ro", uri=True)
            # One code can have a car title (PRODUCT_TYPE P) and a motorcycle title (M), e.g. 255 =
            # sport leather steering wheel / special model Dark White: take the car title first.
            for name, en, de in con.execute("SELECT NAME, TITLE_ENGB, TITLE_DEDE FROM XEP_SALAPAS "
                                            "ORDER BY PRODUCT_TYPE <> 'P', ID"):
                if name and name not in sa_titles:
                    sa_titles[name] = en or de
            con.close()
        for cv, lst in by.items():
            # Rules reference crits as //cat[@val<"128"]; cat>=128 entries (build dates, cat 255) reuse
            # the same numbers (2103 = date 03/21 AND engine B58 in cat 13), so they must not label them.
            lst = [x for x in lst if x[2] < 128]
            if not lst:
                continue
            cat = lst[0][2]
            sels = sorted({x[1] for x in lst})
            codes = [x[0] for x in lst]
            if sels == ["T"]:
                label = f"typekey group ({len(codes)} keys e.g. {','.join(sorted(codes)[:4])})"
                short = f"TK#{cv}"
            else:
                parts, shorts = [], []
                for code, sel, _ in lst:
                    if sel == "W":
                        t = sa_titles.get(code)
                        parts.append(f"SA{code}" + (f" {t}" if t else ""))
                        shorts.append(f"SA{code}")
                    elif sel == "A":
                        d = f"built>={code[2:]}-{code[:2]}" if len(code) == 4 else f"date {code}"
                        parts.append(d)
                        shorts.append(d)
                    else:
                        parts.append(f"{SEL_KIND.get(sel, sel)} {code}")
                        shorts.append(code)
                label, short = " / ".join(parts), "/".join(shorts)
            crits[cv] = dict(cat=cat, fa_codes=",".join(codes), selector=",".join(sels), label=label,
                             short=short, source="fafp")
    load_tsv_crits(a.verified, "esys_verified", crits)
    load_tsv_crits(a.typekey, "esys_typekey", crits)
    load_tsv_crits(a.curated, "curated_unverified", crits)
    if a.fpdef and os.path.exists(a.fpdef):
        root = ET.parse(a.fpdef).getroot()
        for cat in root.iter("Category"):
            if int(cat.get("val")) >= 128:
                continue  # rules only reference cat<128 by number; numbers collide across >=128
            for cr in cat.iter("Crit"):
                cv = int(cr.get("val"))
                if cv not in crits and cr.get("name"):
                    lab = cr.get("name")
                    crits[cv] = dict(cat=int(cat.get("val")), fa_codes="", selector="", label=lab,
                                     short=lab.split(" - ")[0].split(" ")[0] + "?", source="curated_unverified")
    return crits


def trim(s, n=60):
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def value_label(idx, is_default, rd, series):
    tag = f"wert_{idx + 1:02d}"
    if rd == "fixed":
        return f"{tag} [fixed]"
    if is_default and rd in ("always", "never", None, ""):
        return f"{tag} [default]"
    if is_default:
        return f"{tag} [default; {trim(rd)}]"
    if rd in ("never", None, ""):
        return f"{tag} [n/a {series}]"
    if rd == "always":
        return f"{tag} [always]"
    return f"{tag} [{trim(rd)}]"


# ------------------------------------------------------------------ CAFD parse
def unit_hex(values_el):
    if values_el is None:
        return ""
    uvs = sorted(values_el.findall("c:unitVal", NS), key=lambda e: int(e.get("position", "0")))
    return "".join((u.text or "").strip().upper() for u in uvs)


def series_rule(el, series, tag="c:bool"):
    bs = el.findall(tag, NS)
    if not bs:
        return FIXED, False
    for b in bs:
        if b.get("mainSeries") == series:
            return html.unescape(b.text or "").strip(), b.get("isDefault") == "true"
    return None, False


def parse_cafd(path, series):
    root = ET.parse(gzip.open(path)).getroot()
    data = root.find("c:data", NS)
    out = []
    if data is None:
        return out
    for dg in data.findall("c:datagroup", NS):
        act = dg.find("c:activatingCondition", NS)
        act_raw = None
        if act is not None:
            act_raw, _ = series_rule(act, series, "c:boolExpression")
            if act_raw == FIXED:
                act_raw = "true()"
        rec = dict(address=(dg.get("address") or "").upper(), dg_class=dg.get("datagroupClass"),
                   units=dg.get("units"), active_raw=act_raw, funcs=[])
        for fi, fn in enumerate(dg.findall("c:function", NS)):
            si = fn.find("c:storageInfo", NS)
            start = int(si.findtext("c:start", "0", NS))
            vals = []
            for vi, cp in enumerate(fn.findall("c:condParam", NS)):
                raw, is_def = series_rule(cp, series)
                vals.append((vi, unit_hex(cp.find("c:values", NS)), is_def, raw))
            mask = (si.findtext("c:mask", "", NS) or "").strip()
            rec["funcs"].append(dict(idx=fi, start=start, end=int(si.findtext("c:end", str(start), NS)),
                                     size=si.findtext("c:size", "", NS), mask=mask, mask_hex=mask_to_hex(mask),
                                     vals=vals, default_hex=next((v[1] for v in vals if v[2]), None)))
        out.append(rec)
    return out


# ------------------------------------------------------------------ side inputs
def load_svt_ecus(paths):
    m = {}
    ns = "{http://bmw.com/2005/psdz.data.svt}"
    for p in paths:
        try:
            root = ET.parse(p).getroot()
        except Exception:
            continue
        for e in root.iter(ns + "ecu"):
            for pi in e.iter(ns + "partIdentification"):
                if pi.findtext(ns + "processClass") == "CAFD":
                    m.setdefault(pi.findtext(ns + "id").upper(), set()).add(e.get("baseVariant"))
    return m


def load_mapping_ecus(mapping_dir):
    m = {}
    for p in glob.glob(os.path.join(mapping_dir, "*_CAFD.xml")):
        ecu = os.path.basename(p)[: -len("_CAFD.xml")].replace("__", "_").strip("_")
        try:
            s = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for cid in set(re.findall(r"<processClass>CAFD</processClass>\s*<id>([0-9A-Fa-f]+)</id>", s)):
            m.setdefault(cid.upper().zfill(8), set()).add(ecu)
    return m


def load_ecu_tsv(path, default_source):
    rows = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            f = line.rstrip("\r\n").split("\t")
            if line.startswith("#") or len(f) < 2:
                continue
            rows.append((f[0], f[1].upper().zfill(8), default_source))
    return rows


def refresh_ecus(con, a):
    r"""ECU names per CAFD id: data\ecu_cafd.tsv (from KIS target SVTs) + data\ecu_extra.tsv +
    psdzdata\mapping on this machine + any SVT XMLs passed with --svt."""
    con.execute("DELETE FROM ecu_cafd")
    rows = load_ecu_tsv(os.path.join(config.DATA, "ecu_cafd.tsv"), "svt")
    for cid, ecus in load_svt_ecus(a.svt or []).items():
        rows += [(e, cid, "svt") for e in ecus]
    if a.mapping and os.path.isdir(a.mapping):
        for cid, ecus in load_mapping_ecus(a.mapping).items():
            rows += [(e, cid, "psdz_mapping") for e in ecus]
    rows += load_ecu_tsv(os.path.join(config.DATA, "ecu_extra.tsv"), "manual")
    con.executemany("INSERT OR IGNORE INTO ecu_cafd VALUES(?,?,?)", rows)
    con.execute("UPDATE cafd SET ecu=(SELECT group_concat(ecu, ',') FROM (SELECT DISTINCT ecu FROM ecu_cafd e "
                "WHERE e.cafd_id=cafd.cafd_id ORDER BY ecu))")
    print(f"  ECU names for {con.execute('SELECT count(DISTINCT cafd_id) FROM cafd WHERE ecu IS NOT NULL').fetchone()[0]}"
          f"/{con.execute('SELECT count(DISTINCT cafd_id) FROM cafd').fetchone()[0]} CAFD ids")


def load_user_names(path, source="community"):
    """names TSV: cafd_id address start end mask value_hex name comment
    (start/end/mask empty -> group name; value_hex empty -> function name)."""
    rows = []
    if not path or not os.path.exists(path):
        return rows
    for ln, line in enumerate(open(path, encoding="utf-8"), 1):
        if line.startswith("#") or not line.strip():
            continue
        f = [x.strip() for x in (line.rstrip("\r\n").split("\t") + [""] * 8)[:8]]
        if f[0].lower() in ("cafd_id", "cafd"):
            continue
        cid, addr, start, end, mask, vhex, name, comment = f
        if not (cid and addr and (name or comment)):
            print(f"  {os.path.basename(path)}:{ln}: need cafd_id, address and a name or comment", file=sys.stderr)
            continue
        if not name and (vhex or not start):
            print(f"  {os.path.basename(path)}:{ln}: comment-only rows must be function rows", file=sys.stderr)
            continue
        rows.append((cid.upper().zfill(8), addr.upper(), int(start) if start else None,
                     int(end) if end else (int(start) if start else None), mask or None,
                     vhex.upper() or None, name, comment or None, source))
    return rows


# ------------------------------------------------------------------ labelling pass
def load_fp(path):
    root = ET.parse(path).getroot()
    fp = {}
    for cat in root.iter():
        if cat.tag.split("}")[-1] in ("cat", "Category") and cat.get("val") is not None:
            s = fp.setdefault(int(cat.get("val")), set())
            for cr in cat:
                if cr.tag.split("}")[-1] in ("crit", "Crit") and cr.get("val") is not None:
                    s.add(int(cr.get("val")))
    return fp


def relabel(con, crits, series, context=None):
    """context: FP dict of a reference vehicle. Its vehicle-class criteria (series, body, market,
    lifecycle, engine, build date...) are substituted into every rule so labels only show the
    option codes that matter for that class. rule.readable always keeps the full rule."""
    con.execute("DELETE FROM crit")
    con.executemany("INSERT INTO crit VALUES(?,?,?,?,?,?,?)",
                    [(k, v["cat"], v["fa_codes"], v["selector"], v["label"], v["short"], v["source"])
                     for k, v in crits.items()])
    known = context_from_fp(context, crits) if context else None
    rules, full = {}, []
    for rid, raw in con.execute("SELECT id, raw FROM rule").fetchall():
        rd = readable(raw, crits)
        full.append((rd, ",".join(map(str, crit_refs(raw))), rid))
        rules[rid] = simplified(raw, crits, known) if known else (rd, crit_refs(raw))
    con.executemany("UPDATE rule SET readable=?, crits=? WHERE id=?", full)
    print(f"  {len(rules)} rules rendered")

    # value labels + function hints (top option codes driving the function)
    upd_val, upd_func = [], []
    cur_f, cnt = None, Counter()

    def flush():
        if cur_f is not None:
            tops = [crits[c]["short"] if c in crits else f"#{c}" for c, _ in cnt.most_common(4)]
            upd_func.append((",".join(tops), cur_f))

    for vpk, fpk, idx, is_def, rid in con.execute(
            "SELECT pk, func_pk, idx, is_default, rule_id FROM val ORDER BY func_pk, idx"):
        if fpk != cur_f:
            flush()
            cur_f, cnt = fpk, Counter()
        rd, cl = rules.get(rid, (None, []))
        cnt.update(set(cl))
        upd_val.append((value_label(idx, is_def, rd, series if not known else "here"),
                        int(bool(is_def) or rd not in (None, "never", "")), vpk))
        if len(upd_val) >= 200000:
            con.executemany("UPDATE val SET label=?, label_source='derived', used=? WHERE pk=?", upd_val)
            upd_val.clear()
    flush()
    con.executemany("UPDATE val SET label=?, label_source='derived', used=? WHERE pk=?", upd_val)
    con.execute("UPDATE func SET name = address || '_B' || start || CASE WHEN end != start THEN '-' || end ELSE '' END"
                " || '_' || mask_hex, name_source='derived', comment=NULL")
    con.execute("UPDATE datagroup SET name=NULL, name_source=NULL")
    con.executemany("UPDATE func SET hint=? WHERE pk=?", upd_func)
    print(f"  {len(upd_func)} functions hinted")
    apply_overrides(con)


PRIO = ("CASE o.source WHEN 'local' THEN 0 WHEN 'community' THEN 1 WHEN 'contributed' THEN 2 "
        "WHEN 'cheatsheet' THEN 3 ELSE 4 END")
PLACEHOLDER_FN = re.compile(r"^(\.|Group_[0-9A-F]+|[0-9A-F]{4}_B\d.*|.*_Byte_\d+(_\d+)?|.+_\d+_\d+_\d+)$")
WERT = re.compile(r"^wert_\d+(?:\s+(.*?))?(?:\s*\[[^\]]*\])?$")


def norm_mask(m):
    m = (m or "").strip()
    if re.fullmatch(r"[01]+b", m, re.I):
        return m.lower()
    if re.fullmatch(r"[0-9A-Fa-f]{1,2}", m):
        return format(int(m, 16), "08b") + "b"
    return m


def load_definition_xmls(paths):
    """Names from contributed CafdDefinition XMLs (definitions\\**\\*.xml): group, function and value
    names keyed version-independently like the names files. Placeholders and derived labels
    (Group_3000, PfFesMaster_9_9_255, 'wert_02 [SA5AC]') are skipped; 'wert_02 aktiv [SA5AC]' -> 'aktiv'."""
    rows = []
    for p in paths:
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError as e:
            print(f"  skip {p}: {e}", file=sys.stderr)
            continue
        cid = (root.get("cafdId") or "").upper().zfill(8)
        src_note = root.get("source") or root.get("author") or os.path.basename(p)
        for g in root.iter("Group"):
            gid = (g.get("id") or "").upper()
            gname = (g.get("name") or "").strip()
            if gname and not PLACEHOLDER_FN.match(gname):
                rows.append((cid, gid, None, None, None, None, gname, f"[{src_note}]", "contributed"))
            for f in g.iter("Function"):
                try:
                    st = int(f.get("start"))
                    en = int(f.get("end", st))
                except (TypeError, ValueError):
                    continue
                mask = norm_mask(f.get("mask") or "FF")
                fname = (f.get("name") or "").strip()
                cmt = " | ".join(x for x in (f.get("comment") or "").split(" | ")
                                 if x and not x.startswith("driven by")).strip()
                if fname and not PLACEHOLDER_FN.match(fname):
                    rows.append((cid, gid, st, en, mask, None, fname,
                                 f"{cmt} [{src_note}]" if cmt else f"[{src_note}]", "contributed"))
                elif cmt:
                    rows.append((cid, gid, st, en, mask, None, "", f"{cmt} [{src_note}]", "contributed"))
                for v in f.iter("Value"):
                    code = (v.get("code") or "").upper()
                    lab = (v.get("label") or "").strip().lstrip("* ").strip()
                    if not code or not lab or lab in ("Stock NCD Value", "custom_wert", "."):
                        continue
                    m = WERT.match(lab)
                    if m:
                        lab = (m.group(1) or "").strip()
                    if lab:
                        rows.append((cid, gid, st, en, mask, code, lab, f"[{src_note}]", "contributed"))
    return rows


def load_all_names(a):
    rows = []
    for p in a.names:
        src = "legacy" if os.path.basename(p).lower().startswith("legacy_") else "community"
        rows += load_user_names(p, src)
    rows += load_definition_xmls(a.definitions)
    return rows + load_user_names(a.cheat_names, "cheatsheet") + load_user_names(a.my_names, "local")


def apply_overrides(con):
    r"""Resolve name_override rows (version independent keys).
    Priority: local (my_names.tsv) > community (names\*.tsv) > cheatsheet > legacy (names\legacy_*.tsv)."""
    con.executescript(("""
    UPDATE datagroup SET (name, name_source) = (SELECT o.name, o.source FROM name_override o JOIN cafd c ON c.cafd_id=o.cafd_id
         WHERE c.pk=datagroup.cafd_pk AND o.address=datagroup.address AND o.start IS NULL
         ORDER BY PRIO LIMIT 1)
     WHERE EXISTS (SELECT 1 FROM name_override o JOIN cafd c ON c.cafd_id=o.cafd_id
         WHERE c.pk=datagroup.cafd_pk AND o.address=datagroup.address AND o.start IS NULL);
    -- names and comments resolve separately: a comment-only row (name '') never hides a real name
    UPDATE func SET (name, name_source) = (SELECT o.name, o.source
         FROM name_override o JOIN cafd c ON c.cafd_id=o.cafd_id
         WHERE c.pk=func.cafd_pk AND o.address=func.address AND o.start=func.start AND o.end=func.end
           AND o.mask=func.mask AND o.value_hex IS NULL AND o.name != ''
         ORDER BY PRIO LIMIT 1)
     WHERE EXISTS (SELECT 1 FROM name_override o JOIN cafd c ON c.cafd_id=o.cafd_id
         WHERE c.pk=func.cafd_pk AND o.address=func.address AND o.start=func.start AND o.end=func.end
           AND o.mask=func.mask AND o.value_hex IS NULL AND o.name != '');
    UPDATE func SET comment = (SELECT o.comment
         FROM name_override o JOIN cafd c ON c.cafd_id=o.cafd_id
         WHERE c.pk=func.cafd_pk AND o.address=func.address AND o.start=func.start AND o.end=func.end
           AND o.mask=func.mask AND o.value_hex IS NULL AND coalesce(o.comment, '') != ''
         ORDER BY PRIO LIMIT 1)
     WHERE EXISTS (SELECT 1 FROM name_override o JOIN cafd c ON c.cafd_id=o.cafd_id
         WHERE c.pk=func.cafd_pk AND o.address=func.address AND o.start=func.start AND o.end=func.end
           AND o.mask=func.mask AND o.value_hex IS NULL AND coalesce(o.comment, '') != '');
    UPDATE val SET (label, label_source) = (SELECT
           -- cheat-sheet value names keep the derived rule hint: "wert_02 aktiv [SA5AC]"
           CASE WHEN o.source = 'cheatsheet' OR (o.source IN ('local', 'community', 'contributed') AND o.name NOT LIKE 'wert%') THEN 'wert_' || printf('%02d', val.idx + 1) || ' ' || o.name ||
                CASE WHEN instr(val.label, '[') > 0 THEN ' ' || substr(val.label, instr(val.label, '[')) ELSE '' END
                ELSE o.name END, o.source FROM name_override o
         JOIN func f ON f.pk=val.func_pk JOIN cafd c ON c.pk=f.cafd_pk
         WHERE c.cafd_id=o.cafd_id AND o.address=f.address AND o.start=f.start AND o.end=f.end
           AND o.mask=f.mask AND o.value_hex=val.hex ORDER BY PRIO LIMIT 1)
     WHERE func_pk IN (SELECT f.pk FROM func f JOIN cafd c ON c.pk=f.cafd_pk JOIN name_override o
         ON o.cafd_id=c.cafd_id AND o.address=f.address AND o.start=f.start AND o.end=f.end AND o.mask=f.mask
         AND o.value_hex IS NOT NULL)
       AND EXISTS (SELECT 1 FROM name_override o JOIN func f ON f.pk=val.func_pk JOIN cafd c ON c.pk=f.cafd_pk
         WHERE c.cafd_id=o.cafd_id AND o.address=f.address AND o.start=f.start AND o.end=f.end
           AND o.mask=f.mask AND o.value_hex=val.hex);
    """).replace("PRIO", PRIO))
    n = con.execute("SELECT count(*) FROM name_override").fetchone()[0]
    print(f"  {n} name overrides applied")
    bmw_placeholders(con)


def bmw_placeholders(con):
    """Functions without a known name get BMW's trimmed-function convention:
    <GroupName>_<start>_<end>_<mask as decimal>, e.g. PfFesMaster_9_9_255 (address if the group is unnamed)."""
    upd = []
    for fpk, addr, gname, st, en, mask in con.execute(
            "SELECT f.pk, f.address, d.name, f.start, f.end, f.mask FROM func f JOIN datagroup d ON d.pk=f.dg_pk "
            "WHERE f.name_source='derived'"):
        m = mask or ""
        md = str(int(m[:-1], 2)) if m.endswith("b") and set(m[:-1]) <= {"0", "1"} and m[:-1] else m
        upd.append((f"{gname or addr}_{st}_{en}_{md}", fpk))
    con.executemany("UPDATE func SET name=? WHERE pk=?", upd)
    print(f"  {len(upd)} functions named <group>_<start>_<end>_<mask>")


# ------------------------------------------------------------------ main
def main():
    P = config.paths()
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=P.cache)
    ap.add_argument("--out", default=P.db)
    ap.add_argument("--series", default=P.cfg["series"])
    ap.add_argument("--fafp", default=P.fafp)
    ap.add_argument("--ista", default=P.cfg.get("ista_db") or None)
    ap.add_argument("--verified", default=os.path.join(config.DATA, "fp_crits_verified.tsv"))
    ap.add_argument("--typekey", default=os.path.join(config.DATA, "fp_crits_typekey.tsv"))
    ap.add_argument("--curated", default=os.path.join(config.DATA, "fp_crits_curated.tsv"))
    ap.add_argument("--fpdef", default=None, help="optional curated FP definition XML (fallback crit labels)")
    ap.add_argument("--names", nargs="*", default=P.name_files(), help="community name files (names\\*.tsv)")
    ap.add_argument("--definitions", nargs="*", default=P.contributed_xmls("definition"),
                    help="contributed CafdDefinition XMLs (definitions/**/*.xml)")
    ap.add_argument("--my-names", default=P.my_names)
    ap.add_argument("--cheat-names", default=P.cheat_names,
                    help="from cheat_import.py (local cheat sheets); names files win")
    ap.add_argument("--svt", nargs="*", default=[], help="extra SVT XMLs for ECU names")
    ap.add_argument("--mapping", default=os.path.join(P.psdzdata, "mapping") if P.psdzdata else None)
    ap.add_argument("--relabel", action="store_true", help="recompute labels/names in an existing DB")
    ap.add_argument("--context", default=P.context or "none",
                    help="FP xml of a reference vehicle: simplify labels for its vehicle class "
                         "(series/body/market/LCI/engine/date); 'none' for full rules")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    crits = load_crits(a)
    print(f"{len(crits)} FP criteria labelled: " +
          ", ".join(f"{k}={v}" for k, v in Counter(c['source'] for c in crits.values()).items()))
    overrides = load_all_names(a)
    print(f"{len(overrides)} name rows from {len(a.names)} names files, {len(a.definitions)} definition XMLs"
          + (", cheat sheets" if os.path.exists(a.cheat_names or "") else "")
          + (", my_names.tsv" if os.path.exists(a.my_names or "") else ""))

    if a.relabel:
        if not os.path.exists(a.out):
            sys.exit(f"no DB at {a.out}: run a full build first")
        con = sqlite3.connect(a.out)
        con.execute("DELETE FROM name_override")
        con.executemany("INSERT INTO name_override VALUES(?,?,?,?,?,?,?,?,?)", overrides)
        ctx = load_fp(a.context) if a.context and a.context != "none" else None
        con.execute("INSERT OR REPLACE INTO meta VALUES('context', ?)", (a.context or "none",))
        refresh_ecus(con, a)
        relabel(con, crits, a.series, ctx)
        con.commit()
        con.close()
        print("relabelled", a.out, "context:", a.context or "none")
        return

    files = []
    for p in glob.glob(os.path.join(a.cache, "cafd_*.xml.gz")):
        m = FILE_RE.search(os.path.basename(p))
        if m:
            files.append((m.group(1).upper(), int(m.group(2)), int(m.group(3)), int(m.group(4)), p))
    files.sort()
    if not files:
        sys.exit(f"no decrypted CAFDs in {a.cache}: run the decrypt step first")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    print(f"{len(files)} CAFD files, {len({f[0] for f in files})} ids")

    tmp = a.out + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO meta VALUES(?,?)", [("series", a.series), ("cache", a.cache), ("fafp", a.fafp)])


    rule_ids = {}

    def rid(raw):
        if raw is None:
            return None
        i = rule_ids.get(raw)
        if i is None:
            i = rule_ids[raw] = len(rule_ids) + 1
            con.execute("INSERT INTO rule(id, raw) VALUES(?,?)", (i, raw))
        return i

    for n, (cid, mv, sv, pv, path) in enumerate(files, 1):
        ver = f"{mv:03d}_{sv:03d}_{pv:03d}"
        ecu = None
        cpk = con.execute("INSERT INTO cafd(cafd_id,main,sub,patch,version,series,ecu,src) VALUES(?,?,?,?,?,?,?,?)",
                          (cid, mv, sv, pv, ver, a.series, ecu, os.path.basename(path))).lastrowid
        try:
            dgs = parse_cafd(path, a.series)
        except Exception as e:
            print(f"  parse error {path}: {e}", file=sys.stderr)
            continue
        for dg in dgs:
            dpk = con.execute("INSERT INTO datagroup(cafd_pk,address,dg_class,units,active_rule) VALUES(?,?,?,?,?)",
                              (cpk, dg["address"], dg["dg_class"], dg["units"], rid(dg["active_raw"]))).lastrowid
            for f in dg["funcs"]:
                fpk = con.execute(
                    "INSERT INTO func(cafd_pk,dg_pk,address,idx,start,end,size,mask,mask_hex,default_hex) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (cpk, dpk, dg["address"], f["idx"], f["start"], f["end"], f["size"], f["mask"],
                     f["mask_hex"], f["default_hex"])).lastrowid
                con.executemany("INSERT INTO val(func_pk,idx,hex,is_default,rule_id) VALUES(?,?,?,?,?)",
                                [(fpk, vi, hx, int(d), rid(raw)) for vi, hx, d, raw in f["vals"]])
        if n % 200 == 0:
            con.commit()
            print(f"  {n}/{len(files)}")
    con.commit()
    con.executescript(INDEXES)
    con.executemany("INSERT INTO name_override VALUES(?,?,?,?,?,?,?,?,?)", overrides)
    ctx = load_fp(a.context) if a.context and a.context != "none" else None
    con.execute("INSERT OR REPLACE INTO meta VALUES('context', ?)", (a.context or "none",))
    refresh_ecus(con, a)
    relabel(con, crits, a.series, ctx)
    con.commit()
    con.execute("VACUUM")
    con.close()
    if os.path.exists(a.out):
        os.remove(a.out)
    os.replace(tmp, a.out)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
