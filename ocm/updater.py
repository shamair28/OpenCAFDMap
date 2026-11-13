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
  - Your workspace (DB, my_names.tsv, settings) is never touched; afterwards OpenLauncher re-applies
    names so E-Sys picks up the new definitions.
  - Updates only ever come from UPDATE_REPO below (github.com, HTTPS); it can't be changed in settings.
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

# The update source is fixed in code on purpose: it is not a setting, so nothing in config.json (or a
# tampered settings file) can point OpenLauncher at someone else's repository. Forks change these lines.
UPDATE_REPO = "shamair28/OpenCAFDMap"
UPDATE_BRANCH = "main"
MAX_ZIP = 300 * 1024 * 1024
UA = {"User-Agent": "OpenCAFDMap-updater"}


class UpdateError(Exception):
    pass


def source(cfg=None):
    """Where updates come from (cfg is ignored: the source is not configurable)."""
    return UPDATE_REPO, UPDATE_BRANCH


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


def backup_local_changes(changed, kind, stamp=None):
    """Copy your edited/added shared files to workspace\\backups\\<kind>-<time>; returns that folder."""
    stamp = stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(config.paths().workspace, "backups", f"{kind}-{stamp}")
    for rel in changed:
        dst = os.path.join(bdir, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(config.ROOT, *rel.split("/")), dst)
    return bdir


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
        bdir = backup_local_changes(changed, "definitions", stamp)
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


# ------------------------------------------------------------------ launcher (code) updates
# The launcher itself is updated by the release's own OpenLauncher-Setup.bat, run for THIS install's folder
# (OCM_SETUP_DIR), so an install in a custom folder is upgraded in place and never duplicated into the default.
SETUP_URL = f"https://github.com/{UPDATE_REPO}/releases/latest/download/OpenLauncher-Setup.bat"
KEEP = ("workspace", "config.json")             # what setup keeps on an upgrade (must match OpenLauncher-Setup.bat)


def launcher_installed():
    """'2.3.0' for a release install (VERSION file), None for a source checkout."""
    try:
        return open(os.path.join(config.ROOT, "VERSION"), encoding="utf-8").read().strip().lstrip("v")
    except OSError:
        return None


def is_git_clone():
    return os.path.isdir(os.path.join(config.ROOT, ".git"))


def launcher_blocker(cfg=None):
    """Why this install can't be updated from inside OpenLauncher, or None."""
    if is_git_clone():
        return None if shutil.which("git") else "this is a git clone, but git isn't on PATH"
    if launcher_installed() is None:
        return "source checkout without a VERSION file: install a release to get launcher updates"
    ws = os.path.normcase(os.path.abspath(config.Paths(cfg or config.load()).workspace))
    root = os.path.normcase(os.path.abspath(config.ROOT))
    if ws.startswith(root + os.sep) and ws != os.path.join(root, "workspace"):
        # setup replaces everything in the install folder except workspace\ and config.json
        return (f"your workspace ({ws}) is inside the install folder but not named 'workspace'; an update would "
                "remove it. Move it (Workspace setting) or rename it to 'workspace' first")
    return None


def launcher_latest():
    raw = _get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest", 1024 * 1024)
    try:
        tag = json.loads(raw)["tag_name"]
    except (ValueError, KeyError) as e:
        raise UpdateError("GitHub's latest-release answer was not understood") from e
    return str(tag).lstrip("v")


def launcher_check():
    """(installed version, latest release version, update available?)"""
    cur, new = launcher_installed(), launcher_latest()
    return cur, new, cur is not None and vtuple(new) > vtuple(cur)


def launcher_update(cfg=None, out=print):
    """Download the latest release's setup script and start it for this install folder, in its own console.
    The caller must exit right after: setup replaces the program files (workspace\\ and config.json are
    kept) and then starts OpenLauncher again."""
    why = launcher_blocker(cfg)
    if why:
        raise UpdateError(why)
    data = _get(SETUP_URL, 1024 * 1024, timeout=60)
    text = data.decode("utf-8", "replace")
    if not text.lstrip().lower().startswith("@echo off") or "#PS-START" not in text or f"'{UPDATE_REPO}'" not in text:
        raise UpdateError("the downloaded setup script doesn't look like OpenLauncher-Setup.bat; nothing was changed")
    tmp = os.path.join(os.environ.get("TEMP") or config.ROOT, "OpenLauncher-update")
    os.makedirs(tmp, exist_ok=True)
    bat = os.path.join(tmp, "OpenLauncher-Setup.bat")
    with open(bat, "wb") as f:
        f.write(data)
    env = dict(os.environ, OCM_SETUP_DIR=config.ROOT, OCM_SETUP_QUIET="1", OCM_SETUP_NO_SHORTCUTS="1")
    env.pop("OCM_SETUP_ZIP", None)
    env.pop("OCM_SETUP_NO_LAUNCH", None)
    # wait ~2 s for OpenLauncher to close, run setup; keep the window open only if it failed
    # (a string, not a list: cmd /s /c strips only the outer quotes, Python's list quoting would break it)
    cmd = f'cmd /s /c "ping -n 3 127.0.0.1 >nul & call "{bat}" || pause"'
    subprocess.Popen(cmd, env=env, cwd=tmp, creationflags=subprocess.CREATE_NEW_CONSOLE)
    out(f"Updating OpenLauncher in {config.ROOT}; it restarts when setup is done.")


# ------------------------------------------------------------------ git clone updates
# A clone follows its origin's main branch. "Update" is a fast-forward pull (never loses anything).
# "Force update" also works when that's impossible (local edits or commits): it first saves everything,
# uncommitted changes to a git stash, local commits to a backup branch, then moves main to origin/main.
# workspace\ and config.json are git-ignored, so git never touches them.
GIT_BRANCH = "main"


def _git(*args, check=True, timeout=120):
    r = subprocess.run(["git", "-C", config.ROOT] + list(args), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check and r.returncode != 0:
        raise UpdateError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout.strip()


def git_state(fetch=True):
    """{branch, head, behind, ahead, dirty} relative to origin/main (fetches first)."""
    if fetch:
        _git("fetch", "--quiet", "origin", GIT_BRANCH, timeout=180)
    up = f"origin/{GIT_BRANCH}"
    dirty = [l for l in _git("status", "--porcelain", "--untracked-files=no").splitlines() if l.strip()]
    return {"branch": _git("rev-parse", "--abbrev-ref", "HEAD"), "head": _git("rev-parse", "--short", "HEAD"),
            "latest": _git("rev-parse", "--short", up),
            "behind": int(_git("rev-list", "--count", f"HEAD..{up}") or 0),
            "ahead": int(_git("rev-list", "--count", f"{up}..HEAD") or 0), "dirty": len(dirty)}


def git_update(force=False, out=print):
    """Update the clone to origin/main. Returns True if anything changed."""
    s = git_state()
    if not force:
        if s["branch"] != GIT_BRANCH:
            raise UpdateError(f"the clone is on branch '{s['branch']}', not {GIT_BRANCH}: use Force update, "
                              "or switch branches in git")
        if s["behind"] == 0:
            out(f"Launcher is up to date ({s['head']}).")
            return False
        out(_git("pull", "--ff-only", "origin", GIT_BRANCH, timeout=300) or "git pull done")
        return True
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if s["dirty"] or _git("status", "--porcelain"):
        _git("stash", "push", "--include-untracked", "-m", f"OpenLauncher force update {stamp}")
        out(f"Your uncommitted changes were saved: git stash list (\"OpenLauncher force update {stamp}\")")
    saved = set()
    for ref, label in (("HEAD", s["branch"]), (GIT_BRANCH, GIT_BRANCH)):
        sha = _git("rev-parse", "--verify", "--quiet", ref, check=False)
        if not sha or sha in saved or not int(_git("rev-list", "--count", f"origin/{GIT_BRANCH}..{sha}") or 0):
            continue                                   # missing, already saved, or nothing local on it
        saved.add(sha)
        name = f"openlauncher-backup-{stamp}" + ("" if not saved - {sha} else f"-{label}")
        _git("branch", name, sha)
        out(f"Your local commits on {label} were saved to branch {name}")
    _git("checkout", "-B", GIT_BRANCH, f"origin/{GIT_BRANCH}")
    out(f"Launcher moved to origin/{GIT_BRANCH} ({_git('rev-parse', '--short', 'HEAD')})")
    return True


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
