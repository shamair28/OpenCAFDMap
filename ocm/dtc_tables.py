#!/usr/bin/env python3
r"""Build the DTC tool's shipped fault texts (data\dtc\) on the maintainer's machine. Users never run this:
OpenLauncher only needs E-Sys and these tables.

    python ocm\dtc_tables.py --ista <DiagDocDb.sqlite> --key <encryption_key>

Writes (deterministic gzip, so an unchanged rebuild gives identical files):
  data\dtc\faults.tsv.gz   diagnostic address (hex), fault code (hex, as the ECU reports it), then up to 3 texts
                           "English|German", most common first. ISTA ties a code to an ECU variant; the variants are
                           grouped by diagnostic address, so address + code narrows the text down without knowing the
                           variant. Where ISTA has several texts for one address and code (different ECU generations),
                           all of them are kept, up to 3.
  data\dtc\modules.tsv.gz  diagnostic address (hex) -> module title "English|German" (the most common ECU variant
                           title at that address), shown when E-Sys has no SVT with the module's name
  data\dtc\SOURCES.txt     the ISTA version the tables were built from
"""
import apsw
import argparse
import collections
import gzip
import io
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

OUT = os.path.join(config.DATA, "dtc")
MAX_TEXTS = 3


def clean(s):
    return " ".join((s or "").replace("|", "/").split())


def build(ista, key):
    params = urllib.parse.urlencode({
        "cipher": "rc4",
        "key": key
    })
    con = apsw.Connection(f"file:{ista}?{params}", 
                          flags = apsw.SQLITE_OPEN_URI | apsw.SQLITE_OPEN_READONLY)
    q = ("SELECT g.DIAGNOSTIC_ADDRESS, f.CODE, l.TITLE_ENGB, l.TITLE_DEDE "
         "FROM XEP_FAULTCODES f JOIN XEP_ECUVARIANTS v ON v.ID = f.ECUVARIANTID "
         "JOIN XEP_ECUGROUPS g ON g.ID = v.ECUGROUPID "
         "JOIN XEP_REFFAULTLABELS r ON r.ID = f.ID JOIN XEP_FAULTLABELS l ON l.ID = r.LABELID")
    per = collections.defaultdict(collections.Counter)
    for addr, code, en, de in con.execute(q):
        en, de = clean(en), clean(de)
        if addr is None or not str(code or "").isdigit() or not (en or de):
            continue
        a, c = int(addr), int(code)
        if not (0 <= a <= 0xFF and 0 <= c <= 0xFFFFFF):
            continue
        per[(a, c)][(en, de)] += 1
    # a module title per diagnostic address (the most common ECU variant title there), for modules without an SVT name
    mods = collections.defaultdict(collections.Counter)
    for addr, en, de in con.execute("SELECT g.DIAGNOSTIC_ADDRESS, v.TITLE_ENGB, v.TITLE_DEDE FROM XEP_ECUVARIANTS v "
                                    "JOIN XEP_ECUGROUPS g ON g.ID = v.ECUGROUPID"):
        en, de = clean(en), clean(de)
        if addr is not None and 0 <= int(addr) <= 0xFF and (en or de):
            mods[int(addr)][(en, de)] += 1
    modules = [f"{a:02X}\t{t[0]}|{t[1]}" for a, cnt in sorted(mods.items())
               for t in [sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]]]
    try:
        ver = con.execute("SELECT VERSION FROM RG_VERSION WHERE SUBSYSTEM = 'DATABASE'").fetchone()
        ver = ver[0] if ver else None
    except apsw.Error:
        ver = None
    con.close()
    rows = []
    for (a, c), cnt in sorted(per.items()):
        texts = [t for t, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))][:MAX_TEXTS]
        rows.append(f"{a:02X}\t{c:06X}\t" + "\t".join(f"{en}|{de}" for en, de in texts))
    return rows, modules, ver


def write_gz(path, text):
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as g:
        g.write(text.encode("utf-8"))
    with open(path, "wb") as f:
        f.write(raw.getvalue())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ista", required=True, help="ISTA DiagDocDb.sqlite (maintainer only)")
    ap.add_argument("--key", required=True, help="Encryption key (maintainer only)")
    a = ap.parse_args()
    rows, modules, ver = build(a.ista, a.key)
    os.makedirs(OUT, exist_ok=True)
    write_gz(os.path.join(OUT, "faults.tsv.gz"), "addr\tcode\ttexts (English|German, most common first)\n" +
             "\n".join(rows) + "\n")
    write_gz(os.path.join(OUT, "modules.tsv.gz"), "addr\ttitle (English|German)\n" + "\n".join(modules) + "\n")
    with open(os.path.join(OUT, "SOURCES.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("DTC fault texts (built by ocm/dtc_tables.py)\n")
        f.write(f"ISTA database {ver or '?'}\n")
    print(f"{len(rows)} address + code pairs, {len(modules)} module titles -> {OUT}")


if __name__ == "__main__":
    main()
