"""Open CAFD Map settings: one config.json next to OpenLauncher (not committed; see config.example.json).

Every tool reads its paths from here, so nothing is tied to one machine's drive layout.
"""
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT, "config.json")
DATA = os.path.join(ROOT, "data")
NAMES = os.path.join(ROOT, "names")
CONTRIB = os.path.join(ROOT, "definitions")   # contributed XMLs: CafdDefinition files and FDL cheat sheets
ESYS_SCRIPTS = os.path.join(ROOT, "esys")

DEFAULTS = {
    "esys_dir": "",              # E-Sys install folder (contains E-Sys.bat, jre\, lib\)
    "psdzdata_dir": "",          # optional override; default = Path.PsdzHome from E-Sys's Esys.properties
    "series": "G020",            # main series to build (G020 = G20/G21/G22/G23/G26/G28/G8x)
    "workspace": "",             # where the DB, decrypted cache, definitions, logs go (default: <repo>\workspace)
    "context": "G20_LCI_US_330ixDrive_89FF.xml",  # data\contexts\ reference vehicle for shorter labels; "none"
    "try_untested_esys": False,    # True: Cheat Sheet Apply also on E-Sys versions OpenLauncher wasn't tested with
    "experimental_java17": False,  # True: start E-Sys on Java 15+ (E-Sys 26.x: Java 17) with the standalone Nashorn
                                   # (lib\nashorn), Apply buttons included; off: such an E-Sys isn't started
    "esys_ui_scale": "",           # "" = E-Sys's own UI.Scale; "1", "1.25", ...: passed as -Dsun.java2d.uiScale
    "esys_language": "",           # "" = E-Sys's own language=; "en" / "de": E-Sys's language for that start
    # Launch options (OpenLauncher > Launch options): what E-Sys gets when started by OpenLauncher
    "launch_labels": True, "launch_on_demand": True, "launch_tools": True, "launch_backup": True,
    "launch_clean": False,         # True: start E-Sys.bat exactly as it is (troubleshooting), no OpenLauncher features
    "export_all_versions": False,  # False: newest 3 versions per CAFD + versions found in your BimmerCode backups
    "cheat_sheets_dir": "",      # optional: folder of FDL cheat-sheet XMLs to import (kept local, never committed)
    "ista_db": "",               # optional: decrypted ISTA DiagDocDb sqlite (SA option titles in labels)
    "bimmercode_backups_dir": "",  # optional: folder with BimmerCode .cod / .dat backups for the backup editor
}


def app_version():
    """'2.3.0' for a release install (VERSION is added by installer/build_release.py), '' for a source checkout."""
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            return f.read().strip().lstrip("v")
    except OSError:
        return ""


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    if not cfg["esys_dir"]:
        cfg["esys_dir"] = guess_esys_dir()
    if not cfg["workspace"]:
        cfg["workspace"] = os.path.join(ROOT, "workspace")
    return cfg


def save(cfg):
    out = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    if os.path.normcase(os.path.abspath(out["workspace"] or "")) == os.path.normcase(os.path.join(ROOT, "workspace")):
        out["workspace"] = ""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


def ista_db_problem(path):
    """Why this file can't be used as the optional ISTA DiagDocDb ('' when it can). ISTA ships its DiagDocDb
    encrypted; only a decrypted copy is a plain SQLite database that Python can read."""
    if not path:
        return ""
    if not os.path.isfile(path):
        return "file not found"
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError as e:
        return f"can't be read ({e.strerror or e})"
    if head != b"SQLite format 3\x00":
        return "not a plain SQLite database: ISTA's own DiagDocDb is encrypted, and only a decrypted copy works"
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            ok = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='XEP_SALAPAS'").fetchone()
        finally:
            con.close()
    except sqlite3.Error as e:
        return f"can't be opened ({e})"
    return "" if ok else "doesn't have ISTA's option table (XEP_SALAPAS): not a DiagDocDb"


def ista_db_status(path):
    """(level, text) for Settings and the Build definitions dialog: level is 'none', 'ok' or 'bad'."""
    if not (path or "").strip():
        return "none", "Optional: not set, the shipped option titles are used"
    bad = ista_db_problem(path)
    if not bad:
        return "ok", "\u2713 Decrypted DiagDocDb: option titles come from ISTA"
    if bad.startswith("not a plain SQLite"):
        what = "Encrypted (ISTA's own file) or not a database"
    elif bad == "file not found":
        what = "File not found"
    elif bad.startswith("doesn't have"):
        what = "Not ISTA's DiagDocDb"
    else:
        what = "Can't be read"
    return "bad", f"\u2717 {what}: the shipped option titles are used"


def guess_esys_dir():
    for pat in (r"C:\EC-Apps\ESG\E-Sys", r"C:\EC-Apps\E-Sys*", r"C:\ESG\E-Sys*", r"C:\Program Files*\E-Sys*"):
        for d in sorted(glob.glob(pat), reverse=True):
            if os.path.exists(os.path.join(d, "E-Sys.bat")) and os.path.isdir(os.path.join(d, "lib")):
                return d
    return ""


def esys_properties(cfg):
    """Read-only view of E-Sys's own config\\Esys.properties (we never write it)."""
    p = os.path.join(cfg["esys_dir"], "config", "Esys.properties")
    props = {}
    if os.path.exists(p):
        for line in open(p, encoding="latin-1"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip().replace("\\:", ":").replace("\\\\", "\\")
    return props


def psdzdata_dir(cfg):
    if cfg.get("psdzdata_dir"):
        return cfg["psdzdata_dir"]
    home = esys_properties(cfg).get("Path.PsdzHome", "")
    return os.path.join(home, "psdzdata") if home else ""


class Paths:
    """Everything the pipeline writes lives under the workspace."""

    def __init__(self, cfg=None):
        self.cfg = cfg = cfg or load()
        ws = self.workspace = cfg["workspace"]
        s = cfg["series"]
        self.cache = os.path.join(ws, "cafd_cache", s)
        self.db = os.path.join(ws, f"cafd_defs_{s}.sqlite")
        self.generated = os.path.join(ws, "definitions", s)
        self.fafp = os.path.join(ws, "cafd_cache", f"fafp_{s}.xml")
        self.logs = os.path.join(ws, "logs")
        self.dumps = os.path.join(ws, "dumps")
        self.my_names = os.path.join(ws, "my_names.tsv")          # your private names (highest priority)
        self.cheat_names = os.path.join(ws, "names_cheats.tsv")   # imported cheat sheets (local only)
        self.bc_sheet = os.path.join(ws, "bimmercode_changes.tsv")
        self.bc = os.path.join(ws, "bimmercode")                  # backup editor workspace
        self.psdzdata = psdzdata_dir(cfg)
        self.context = (os.path.join(DATA, "contexts", cfg["context"])
                        if cfg.get("context") and cfg["context"] != "none" else None)

    def name_files(self):
        """Community name files: names\\*.tsv (legacy_*.tsv rank below cheat sheets)."""
        return sorted(glob.glob(os.path.join(NAMES, "*.tsv")))

    def versioned_name_files(self):
        """names\\versioned\\*.tsv: community names for some CAFD versions only (a 9th column, versions). A
        separate folder, so launchers that don't know the column never apply these to every version."""
        return sorted(glob.glob(os.path.join(NAMES, "versioned", "*.tsv")))

    def sheet_name_files(self):
        """names\\cheatsheet\\*.tsv: names taken from cheat sheets whose files can't be shipped."""
        return sorted(glob.glob(os.path.join(NAMES, "cheatsheet", "*.tsv")))

    def contributed_xmls(self, kind):
        """Contributed XMLs under definitions\\ by root element: kind 'definition' (CafdDefinition)
        or 'cheatsheet' (FDL)."""
        want = {"definition": "CafdDefinition", "cheatsheet": "FDL"}[kind]
        return [p for p in sorted(glob.glob(os.path.join(CONTRIB, "**", "*.xml"), recursive=True))
                if xml_root(p) == want]


def xml_root(path):
    """Name of an XML file's root element (cheap: reads only the start of the file)."""
    import re
    try:
        with open(path, "rb") as f:
            head = f.read(4096).decode("utf-8", "replace")
    except OSError:
        return None
    head = re.sub(r"<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>", "", head, flags=re.S)
    m = re.search(r"<([A-Za-z_][\w.:-]*)", head)
    return m.group(1).split(":")[-1] if m else None


def paths():
    return Paths(load())
