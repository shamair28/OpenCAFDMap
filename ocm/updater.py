#!/usr/bin/env python3
r"""Definitions updater: get the latest shared definitions (names\, definitions\, data\) from the
Open CAFD Map GitHub repository without reinstalling.

    python ocm\updater.py            check: installed vs available version
    python ocm\updater.py --update   download, verify and install, then re-apply names

How it works
  - The repo's definitions.json lists the definitions version and a SHA-256 for every shared file
    (bumped automatically on GitHub after each merge).
  - "Check" downloads only that small file. "Update" downloads the branch as a zip, verifies every
    file against the manifest, backs up any of your local edits, and swaps in the three folders.
    Nothing outside names\, definitions\ and data\ is touched (code updates: download a release or
    git pull). A git clone is updated with `git pull --ff-only` instead.
  - Your workspace (DB, my_names.tsv, settings) is never touched; afterwards the launcher re-applies
    names so E-Sys picks up the new definitions.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import defs_manifest as dm  # noqa: E402

DEFAULT_REPO = "shamair28/OpenCAFDMap"   # forks: point this (or "Update source" in the launcher) at yours
DEFAULT_BRANCH = "main"
MAX_ZIP = 300 * 1024 * 1024
UA = {"User-Agent": "OpenCAFDMap-updater"}


class UpdateError(Exception):
    pass


def source(cfg):
    repo = (cfg.get("update_repo") or DEFAULT_REPO).strip().strip("/")
    if repo.startswith("https://github.com/"):
        repo = repo[len("https://github.com/"):].removesuffix(".git")
    if repo.count("/") != 1:
        raise UpdateError("No update source set (GitHub owner/repo). Set it in the launcher settings.")
    return repo, (cfg.get("update_branch") or DEFAULT_BRANCH).strip()


def vtuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def _get(url, limit, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"{url}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"can't reach GitHub ({e})") from e
    if len(data) > limit:
        raise UpdateError(f"{url}: download larger than expected")
    return data


def installed():
    return dm.load()


def latest(cfg):
    repo, branch = source(cfg)
    raw = _get(f"https://raw.githubusercontent.com/{repo}/{branch}/definitions.json", 20 * 1024 * 1024)
    try:
        man = json.loads(raw)
        man["version"], man["files"]
    except (ValueError, KeyError) as e:
        raise UpdateError("the published definitions.json is not valid") from e
    return man


def check(cfg):
    """(installed version, available version, update available?)"""
    cur, new = installed(), latest(cfg)
    return cur.get("version", "0"), new["version"], vtuple(new["version"]) > vtuple(cur.get("version", "0"))


def local_changes():
    """Shared files you changed or added since the installed version (backed up before an update)."""
    have = installed().get("files", {})
    return [p for p, h in dm.scan().items() if have.get(p) != h]


def _safe(rel):
    parts = rel.split("/")
    return parts[0] in dm.FOLDERS and ".." not in parts and not rel.startswith("/") and ":" not in rel


def update(cfg, out=print):
    """Install the latest definitions. Returns the new version (or None if already up to date)."""
    cur_v, new_v, avail = check(cfg)
    if not avail:
        out(f"Definitions {cur_v} are up to date.")
        return None
    if os.path.isdir(os.path.join(config.ROOT, ".git")) and shutil.which("git"):
        out("This install is a git clone: git pull --ff-only")
        r = subprocess.run(["git", "-C", config.ROOT, "pull", "--ff-only"], capture_output=True, text=True)
        out((r.stdout + r.stderr).strip())
        if r.returncode != 0:
            raise UpdateError("git pull failed (local commits or changes?) - resolve it in git, then retry")
        return installed().get("version")

    repo, branch = source(cfg)
    out(f"Downloading definitions {new_v} from github.com/{repo} ({branch}) ...")
    zdata = _get(f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}", MAX_ZIP, timeout=120)
    z = zipfile.ZipFile(io.BytesIO(zdata))
    names = z.namelist()
    prefix = names[0].split("/")[0] + "/" if names else ""
    try:
        man = json.loads(z.read(prefix + "definitions.json"))
    except KeyError as e:
        raise UpdateError("downloaded archive has no definitions.json") from e
    files = man.get("files", {})
    for rel in files:
        if not _safe(rel):
            raise UpdateError(f"refusing unexpected path in manifest: {rel}")

    P = config.paths()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    stage = os.path.join(P.workspace, "updates", f"stage-{stamp}")
    try:
        for rel, h in files.items():                  # extract + verify every file before touching anything
            try:
                data = z.read(prefix + rel)
            except KeyError as e:
                raise UpdateError(f"{rel} listed in the manifest but missing from the download") from e
            if hashlib.sha256(data).hexdigest() != h:
                raise UpdateError(f"{rel}: checksum mismatch - download corrupted, nothing was changed")
            dst = os.path.join(stage, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    changed = local_changes()                         # back up your own edits first
    if changed:
        bdir = os.path.join(P.workspace, "backups", f"definitions-{stamp}")
        for rel in changed:
            dst = os.path.join(bdir, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(config.ROOT, *rel.split("/")), dst)
        out(f"Your {len(changed)} edited/added file(s) were backed up to {bdir}")

    old = os.path.join(P.workspace, "updates", f"previous-{stamp}")
    os.makedirs(old, exist_ok=True)
    for folder in dm.FOLDERS:                          # swap the folders in
        cur_dir, new_dir = os.path.join(config.ROOT, folder), os.path.join(stage, folder)
        if os.path.isdir(cur_dir):
            shutil.move(cur_dir, os.path.join(old, folder))
        if os.path.isdir(new_dir):
            shutil.move(new_dir, cur_dir)
        else:
            os.makedirs(cur_dir, exist_ok=True)
    with open(dm.MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, indent=1)
        f.write("\n")
    shutil.rmtree(stage, ignore_errors=True)
    out(f"Definitions updated {cur_v} -> {man['version']} ({len(files)} files). Previous files kept in {old}")
    return man["version"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()
    cfg = config.load()
    try:
        if a.update:
            if update(cfg):
                import pipeline
                if os.path.exists(config.paths().db):
                    pipeline.build(print, full=False)
        else:
            cur, new, avail = check(cfg)
            print(f"installed {cur}, available {new}: " + ("update available" if avail else "up to date"))
    except UpdateError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
