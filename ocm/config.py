"""Open CAFD Map settings: one config.json next to the launcher (not committed; see config.example.json).

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
    "export_all_versions": False,  # False: newest 3 versions per CAFD + versions found in your BimmerCode backups
    "cheat_sheets_dir": "",      # optional: folder of FDL cheat-sheet XMLs to import (kept local, never committed)
    "ista_db": "",               # optional: decrypted ISTA DiagDocDb sqlite (SA option titles in labels)
    "bimmercode_backups_dir": "",  # optional: folder with BimmerCode .cod backups for the backup editor
    "update_repo": "",           # GitHub owner/repo the definitions updater pulls from ("" = project default)
    "update_branch": "main",
}


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
