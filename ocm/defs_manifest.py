#!/usr/bin/env python3
r"""Definitions manifest (definitions.json at the repo root): the version of the shared definitions
(names\, definitions\, data\) plus a SHA-256 of every file. OpenLauncher's updater compares it with
the latest one on GitHub and verifies every downloaded file against it.

    python ocm\defs_manifest.py            show the installed definitions version
    python ocm\defs_manifest.py --bump     recompute hashes; new version if anything changed
    python ocm\defs_manifest.py --check    exit 1 if the manifest doesn't match the files

--bump runs automatically on GitHub after every merge to main (.github/workflows/definitions-version.yml),
so contributors never edit definitions.json themselves.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

MANIFEST = os.path.join(config.ROOT, "definitions.json")
FOLDERS = ("names", "definitions", "data")   # the only folders the updater ever writes


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(root=config.ROOT):
    """{posix relative path: sha256} for every file in the shared folders."""
    out = {}
    for folder in FOLDERS:
        for p in glob.glob(os.path.join(root, folder, "**", "*"), recursive=True):
            if os.path.isfile(p) and "__pycache__" not in p:
                out[os.path.relpath(p, root).replace(os.sep, "/")] = sha256(p)
    return dict(sorted(out.items()))


def load(path=MANIFEST):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"version": "0", "files": {}}


def next_version(old, today=None):
    """YYYY.MM.DD.N - N counts releases on the same day."""
    today = today or datetime.date.today().strftime("%Y.%m.%d")
    parts = (old or "").split(".")
    if len(parts) == 4 and ".".join(parts[:3]) == today and parts[3].isdigit():
        return f"{today}.{int(parts[3]) + 1}"
    return f"{today}.1"


def bump(path=MANIFEST):
    cur = load(path)
    files = scan()
    if files == cur.get("files"):
        print(f"definitions unchanged: {cur.get('version')}")
        return False
    new = {"version": next_version(cur.get("version")),
           "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "files": files}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(new, f, indent=1)
        f.write("\n")
    print(f"definitions {cur.get('version')} -> {new['version']} ({len(files)} files)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bump", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.bump:
        bump()
    elif a.check:
        cur = load()
        if cur.get("files") != scan():
            sys.exit(f"definitions.json ({cur.get('version')}) does not match the files: run --bump")
        print(f"definitions.json matches the files ({cur.get('version')})")
    else:
        cur = load()
        print(f"definitions {cur.get('version')} ({len(cur.get('files', {}))} files, {cur.get('date', '-')})")


if __name__ == "__main__":
    main()
