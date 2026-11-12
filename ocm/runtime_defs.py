#!/usr/bin/env python3
r"""Definitions on demand: no "Build definitions" needed to code with E-Sys.

When E-Sys (started by OpenLauncher) opens a CAFD that has no definition file yet, esys\agent.js saves that CAFD
as E-Sys loaded it from your psdzdata to workspace\runtime_cafd\<series>\cafd_<id>_<ver>.xml.gz and runs this
script. It labels the CAFD with exactly the same code as a full build (rules -> value labels, reference vehicle,
names\*.tsv, BMW untrimmed names, contributed definitions, cheat sheets, my_names.tsv) and writes
CAFD_<id>_<ver>.xml to the workspace's definitions folder. The agent then relabels the open tree.

    python ocm\runtime_defs.py <cafd_*.xml.gz> ...   build definitions for these CAFDs
    python ocm\runtime_defs.py --all                 rebuild every CAFD in runtime_cafd (after Apply names)
    python ocm\runtime_defs.py --prepare             FA->FP mapping (option-code labels) + FP_<series>.xml,
                                                     run by OpenLauncher before E-Sys starts
"""
import argparse
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_defdb as B  # noqa: E402
import cheat_import  # noqa: E402
import config  # noqa: E402
import export_defs  # noqa: E402


def runtime_dir(P):
    return os.path.join(P.workspace, "runtime_cafd", P.cfg["series"])


def ensure_fafp(P, out=print):
    """The FA->FP mapping turns FP criteria into option codes (SA5AC, ...). A full build decrypts it with the
    CAFDs; without one, decrypt only the mapping (a few seconds)."""
    if os.path.exists(P.fafp) or not P.cfg.get("esys_dir"):
        return
    import esys
    os.makedirs(P.logs, exist_ok=True)
    tmp = os.path.join(P.workspace, "cafd_cache", "_fafp_only")
    out("Reading the FA->FP mapping from your psdzdata (once)...")
    esys.run_script(os.path.join(config.ESYS_SCRIPTS, "decrypt.js"), [tmp, P.cfg["series"], P.fafp, 0, "-"],
                    cfg=P.cfg, log=os.path.join(P.logs, "decrypt.log"), on_line=out)
    shutil.rmtree(tmp, ignore_errors=True)  # only held decrypt.js's (empty) manifest


def new_db():
    con = sqlite3.connect(":memory:")
    con.executescript(B.SCHEMA)
    return con


def build(P, a, files, write_fp=False):
    """Label files (cache_files rows) exactly like a full build and export their definitions."""
    crits = B.load_crits(a)
    con = new_db()
    con.executemany("INSERT INTO meta VALUES(?,?)", [("series", a.series)])
    B.ingest(con, files, a.series, progress=False)
    con.executescript(B.INDEXES)
    # cheat sheets are checked against the CAFDs in this DB, like cheat_import.py does against the full DB
    rows = B.load_all_names(a)
    sheets = cheat_import.sheet_files(P.cfg.get("cheat_sheets_dir") or "")
    if sheets:
        crow, _ = cheat_import.collect(con, sheets)
        # same shape as load_user_names(names_cheats.tsv): empty value_hex / comment -> None
        rows += [(cid, addr, st, en, mask or None, vhex or None, name, comment or None, "cheatsheet")
                 for cid, addr, st, en, mask, vhex, name, comment in crow]
    con.executemany("INSERT INTO name_override VALUES(?,?,?,?,?,?,?,?,?)", rows)
    ctx = B.load_fp(a.context) if a.context and a.context != "none" else None
    B.refresh_ecus(con, a)
    B.relabel(con, crits, a.series, ctx, a.cache)
    os.makedirs(P.generated, exist_ok=True)
    n = 0
    for cpk, cid, ver, ecu in con.execute("SELECT pk, cafd_id, version, ecu FROM cafd ORDER BY pk").fetchall():
        n += export_defs.export_one(con, cpk, cid, ver, ecu, P.generated, None)
        print(f"wrote CAFD_{cid}_{ver}.xml")
    if write_fp:
        export_defs.write_fp_definition(con, a.series, P.generated)
        print(f"wrote FP_{a.series}.xml")
    con.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--prepare", action="store_true")
    o = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    P = config.paths()
    rdir = runtime_dir(P)
    # the same inputs as build_defdb; cheat sheets are read directly (collect) instead of names_cheats.tsv
    a = B.parse_args(["--cache", rdir, "--cheat-names", ""])
    t0 = time.time()
    if o.prepare:
        ensure_fafp(P)
        if not os.path.exists(os.path.join(P.generated, f"FP_{a.series}.xml")):
            build(P, a, [], write_fp=True)
        return
    files = B.cache_files(rdir)
    if not o.all:
        want = {os.path.normcase(os.path.abspath(f)) for f in o.files}
        files = [f for f in files if os.path.normcase(os.path.abspath(f[4])) in want]
    if not files:
        sys.exit("nothing to do: no matching CAFDs in " + rdir)
    nf = build(P, a, files, write_fp=not os.path.exists(os.path.join(P.generated, f"FP_{a.series}.xml")))
    print(f"{len(files)} CAFD version(s), {nf} functions in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
