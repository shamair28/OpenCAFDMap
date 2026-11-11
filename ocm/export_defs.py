#!/usr/bin/env python3
r"""Export agent definition XMLs (CAFD_<id>_<main>_<sub>_<patch>.xml) from cafd_defs.sqlite.

    python ocm\export_defs.py                 # newest 3 versions per CAFD + versions in E-Sys's SVT/CAF/NCD
                                              # folders and in your BimmerCode backups
    python ocm\export_defs.py --all           # every CAFD version of the series (~2400 files for G020)
    python ocm\export_defs.py --ids 000043AF,00006DF0 --latest 0   # all versions of selected ids
    python ocm\export_defs.py --fp FP.xml     # mark the value the vehicle's FA would code with *

Output goes to the workspace's definitions\<series>\ folder, which esys\agent.js reads, plus
FP_<series>.xml (FP category / criteria labels for E-Sys's FP view).
Schema (function-granular, so byte+mask identify each function):
  <CafdDefinition ecu cafdId mainVersion subVersion patchVersion source>
    <Group id name>
      <Function name start end mask comment>
        <Value code label/>
"""
import argparse
import glob
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402
from rules import eval_rule  # noqa: E402


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


def svt_versions(paths):
    out = set()
    for p in paths:
        try:
            s = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for cid, mv, sv, pv in re.findall(r"<processClass>CAFD</processClass>\s*<id>([0-9A-Fa-f]+)</id>\s*"
                                          r"<mainVersion>(\d+)</mainVersion>\s*<subVersion>(\d+)</subVersion>\s*"
                                          r"<patchVersion>(\d+)</patchVersion>", s):
            out.add((cid.upper(), f"{int(mv):03d}_{int(sv):03d}_{int(pv):03d}"))
    return out


NCD_NAME = re.compile(r"^CAFD_([0-9A-Fa-f]{8})_(\d{3})_(\d{3})_(\d{3})\.ncd$")


def esys_data_versions(cfg):
    """CAFD versions you work with in E-Sys: every SVT in its SVT folder and every CAFD_*.ncd under its CAF and
    NCD folders (E-Sys's own Path.Home / Path.NcdRootDir settings, read-only)."""
    props = config.esys_properties(cfg) if cfg.get("esys_dir") else {}
    home, ncd_root = props.get("Path.Home", ""), props.get("Path.NcdRootDir", "")
    svts = glob.glob(os.path.join(home, "SVT", "**", "*.xml"), recursive=True) if home else []
    out = svt_versions(svts)
    for d in {os.path.join(home, "CAF") if home else "", ncd_root}:
        for p in glob.glob(os.path.join(d, "**", "CAFD_*.ncd"), recursive=True) if d and os.path.isdir(d) else []:
            m = NCD_NAME.match(os.path.basename(p))
            if m:
                out.add((m.group(1).upper(), "_".join(m.group(2, 3, 4))))
    return out


def export_one(con, cpk, cid, ver, ecu, out_dir, fp):
    main, sub, patch = ver.split("_")
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             f'<CafdDefinition schemaVersion="2" ecu={quoteattr(ecu or ("CAFD_" + cid))} cafdId="{cid}" '
             f'mainVersion="{main}" subVersion="{sub}" patchVersion="{patch}" source="cafd_defs.sqlite">']
    vals = {}
    for fpk, idx, hx, is_def, label, raw in con.execute(
            "SELECT v.func_pk, v.idx, v.hex, v.is_default, v.label, r.raw FROM val v JOIN func f ON f.pk=v.func_pk "
            "LEFT JOIN rule r ON r.id=v.rule_id WHERE f.cafd_pk=? ORDER BY v.func_pk, v.idx", (cpk,)):
        vals.setdefault(fpk, []).append((idx, hx, is_def, label, raw))
    cur_dg = None
    n_f = 0
    for dpk, addr, gname, fpk, name, st, en, mask, hint, comment in con.execute(
            "SELECT d.pk, d.address, d.name, f.pk, f.name, f.start, f.end, f.mask, f.hint, f.comment "
            "FROM func f JOIN datagroup d ON d.pk=f.dg_pk WHERE f.cafd_pk=? ORDER BY f.pk", (cpk,)):
        if dpk != cur_dg:
            if cur_dg is not None:
                lines.append("  </Group>")
            lines.append(f'  <Group id="{addr}" name={quoteattr(gname or ("Group_" + addr))}>')
            cur_dg = dpk
        vs = vals.get(fpk, [])
        fa_idx = None
        if fp is not None:
            match = next((i for i, hx, d, lab, raw in vs if not d and eval_rule(raw, fp)), None)
            fa_idx = match if match is not None else next((i for i, hx, d, lab, raw in vs if d), None)
        cmt = " | ".join(x for x in (comment, f"driven by {hint}" if hint else "") if x)
        lines.append(f'    <Function name={quoteattr(name)} start="{st}" end="{en}" mask={quoteattr(mask)} '
                     f'comment={quoteattr(cmt)}>')
        for i, hx, d, lab, raw in vs:
            if i == fa_idx:
                lab = "* " + lab
            # idx = condParam position (wert_NN - 1); several werte can share one hex code
            lines.append(f"      <Value idx=\"{i}\" code=\"{hx}\" label={quoteattr(lab)} />")
        lines.append("    </Function>")
        n_f += 1
    if cur_dg is not None:
        lines.append("  </Group>")
    lines.append("</CafdDefinition>")
    path = os.path.join(out_dir, f"CAFD_{cid}_{ver}.xml")
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    return n_f


def backup_versions(folders):
    """(cafd_id, version) of every CAFD listed in BimmerCode backups (your car's actual versions)."""
    from bc_tool import Cod
    out = set()
    for d in folders:
        for p in glob.glob(os.path.join(d, "*.cod")) if d and os.path.isdir(d) else []:
            try:
                out.update(Cod(p).cafds)
            except Exception:  # noqa: BLE001 - not a readable backup: skip
                pass
    return out


def write_fp_definition(con, series, out_dir):
    """FP_<series>.xml for the agent's FP view: category names + criteria labels from the crit table."""
    cats = {}
    path = os.path.join(config.DATA, "fp_categories.tsv")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if not line.startswith("#") and "\t" in line:
                k, v = line.rstrip("\r\n").split("\t")[:2]
                cats[int(k)] = v
    by = {}
    for val, cat, label in con.execute("SELECT val, cat, label FROM crit ORDER BY cat, val"):
        by.setdefault(cat, []).append((val, label))
    lines = ['<?xml version="1.0" encoding="utf-8"?>', f'<FpDefinition mainSeries="{series}" source="cafd_defs">']
    for cat in sorted(set(by) | set(cats)):
        lines.append(f'  <Category val="{cat}" name={quoteattr(cats.get(cat, ""))}>')
        for val, label in by.get(cat, []):
            lines.append(f'    <Crit val="{val}" name={quoteattr(label or "")} />')
        lines.append("  </Category>")
    lines.append("</FpDefinition>")
    with open(os.path.join(out_dir, f"FP_{series}.xml"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")


def main():
    P = config.paths()
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=P.db)
    ap.add_argument("--out", default=P.generated)
    ap.add_argument("--latest", type=int, default=3, help="newest N versions per id (0 = all)")
    ap.add_argument("--all", action="store_true", default=bool(P.cfg.get("export_all_versions")))
    ap.add_argument("--ids", help="comma list of CAFD ids")
    ap.add_argument("--svt", nargs="*", default=[], help="SVT XMLs: also export the CAFD versions they list")
    ap.add_argument("--fp", help="vehicle FP; marks the value the FA would code with '* '")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        sys.exit(f"no DB at {a.db}: build it first")
    os.makedirs(a.out, exist_ok=True)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    fp = load_fp(a.fp) if a.fp else None
    ids = {x.strip().upper().zfill(8) for x in a.ids.split(",")} if a.ids else None

    rows = con.execute("SELECT pk, cafd_id, version, ecu FROM cafd ORDER BY cafd_id, main, sub, patch").fetchall()
    by = {}
    for r in rows:
        by.setdefault(r[1], []).append(r)
    want = set(svt_versions(a.svt))
    # versions you open in E-Sys: SVTs and NCDs in E-Sys's data folders (e.g. a FakeCar's SVT)
    want |= esys_data_versions(P.cfg)
    # versions your car actually runs: every CAFD listed in your BimmerCode backups
    want |= backup_versions([P.cfg.get("bimmercode_backups_dir"), os.path.join(P.bc, "originals"),
                             os.path.join(P.bc, "out")])
    pick = []
    for cid, lst in by.items():
        if ids and cid not in ids:
            continue
        sel = lst if (a.all or a.latest == 0) else lst[-a.latest:]
        sel = sel + [r for r in lst if (cid, r[2]) in want and r not in sel]
        pick.extend(sel)
    total = 0
    for n, (cpk, cid, ver, ecu) in enumerate(pick, 1):
        total += export_one(con, cpk, cid, ver, ecu, a.out, fp)
        if n % 100 == 0:
            print(f"  {n}/{len(pick)}")
    series = (con.execute("SELECT v FROM meta WHERE k='series'").fetchone() or [P.cfg["series"]])[0]
    write_fp_definition(con, series, a.out)
    print(f"exported {len(pick)} CAFD versions ({total} functions) + FP_{series}.xml -> {a.out}")


if __name__ == "__main__":
    main()
