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
import re
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
    """Comparable version: '2.3.3' < '3.0.0-beta.1' < '3.0.0-beta.2' < '3.0.0' (a pre-release sorts before its
    release). Anything unreadable sorts first."""
    core, _, pre = str(v).strip().lstrip("v").partition("-")
    try:
        nums = tuple(int(x) for x in core.split("."))
    except ValueError:
        return (0,), (1,)
    if not pre:
        return nums, (1,)
    return nums, (0,) + tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))


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

# Release channels, by tag (fixed in code like UPDATE_REPO):
#   v3.x.y        OpenLauncher v3, the current version: GitHub's "latest" release, built from main
#   v3.x.y-rc.N   v3 previews: GitHub pre-releases from the v3-testing branch, followed only by preview installs
#   v2.x.y        OpenLauncher v2 maintenance releases from the v2 branch, never "latest"
# An install follows the newest release of its own major version (CHANNEL), never GitHub's "latest" release, so
# v2 stays on v2 and v3 on v3. Setup is always given the exact tag to install. Names (definitions.json) come from
# main for every version. This is OpenLauncher v2: it follows v2 releases; Try OpenLauncher v3 moves it to v3.
CHANNEL = 2
PREVIEW_BRANCH = "v3-testing"                   # v3 previews (git clones: Try OpenLauncher v3 before v3.0.0)
LEGACY_PREVIEW_BRANCHES = ("testing",)          # the v3 preview branch's name before v3.0.0
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$")   # the only tags setup is given


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


def releases():
    """[(tag, pre-release?)] of the repository's published releases (newest 100) with a valid tag."""
    raw = _get(f"https://api.github.com/repos/{UPDATE_REPO}/releases?per_page=100", 8 * 1024 * 1024)
    try:
        data = json.loads(raw)
        if not isinstance(data, list):          # e.g. {"message": "API rate limit exceeded ..."}
            raise ValueError(str(data.get("message") if isinstance(data, dict) else data)[:200])
        rels = [(str(r["tag_name"]), bool(r.get("prerelease"))) for r in data if not r.get("draft")]
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise UpdateError("GitHub's release list was not understood") from e
    return [(t, p) for t, p in rels if TAG_RE.match(t)]


def newest(major, prerelease=False, rels=None):
    """Newest release of a major version as '3.0.1', or with prerelease=True its newest pre-release
    ('3.1.0-rc.1'); None if there is none."""
    rels = releases() if rels is None else rels
    tags = [t for t, p in rels if p == prerelease and t.startswith(f"v{major}.")]
    return max(tags, key=vtuple).lstrip("v") if tags else None


def launcher_latest(rels=None):
    """Newest v2 release (this install's channel), or None."""
    return newest(CHANNEL, False, rels)


def preview_latest(rels=None):
    """Newest v3 preview (GitHub pre-release, tag v3.*-*) as '3.0.0-rc.6', or None if there is none."""
    return newest(3, True, rels)


def v3_target(rels=None):
    """What Try OpenLauncher v3 installs: the newest v3 release, or the newest v3 preview before v3.0.0."""
    rels = releases() if rels is None else rels
    return newest(3, False, rels) or newest(3, True, rels)


def is_preview():
    """True on the v3 preview: a pre-release install (VERSION like 3.0.0-rc.6) or a clone on the v3-testing
    branch."""
    if is_git_clone():
        try:
            return _git("rev-parse", "--abbrev-ref", "HEAD", check=False) in (PREVIEW_BRANCH,) + LEGACY_PREVIEW_BRANCHES
        except (OSError, subprocess.SubprocessError):          # git not on PATH (launcher_blocker says so)
            return False
    return "-" in (launcher_installed() or "")


def launcher_check():
    """(installed version, newest version for this install, update available?). A v2 install follows the newest
    v2 release, whatever GitHub calls "latest" (that is v3). A v3 preview on this code (a v3-testing clone) also
    follows v3 previews."""
    cur, rels = launcher_installed(), releases()
    found = [v for v in (launcher_latest(rels), preview_latest(rels) if is_preview() else None) if v]
    new = max(found, key=vtuple) if found else None
    return cur, new or cur, bool(cur and new and vtuple(new) > vtuple(cur))


def stable_target():
    """The newest v2 release (v2 has no preview of its own to come back from)."""
    return newest(CHANNEL, False)


def launcher_update(cfg=None, out=print, version=None):
    """Download the newest setup script and start it for this install folder, in its own console, to install
    exactly `version` ('2.3.11', '3.0.0'; default: the newest v2 release), which setup gets as OCM_SETUP_TAG.
    Never "whatever GitHub calls latest". The caller must exit right after: setup replaces the program files
    (workspace\\ and config.json are kept) and then starts OpenLauncher again."""
    why = launcher_blocker(cfg)
    if why:
        raise UpdateError(why)
    version = version or launcher_latest()
    if not version:
        raise UpdateError("there's no OpenLauncher v2 release to install")
    tag = f"v{version.lstrip('v')}"
    if not TAG_RE.match(tag):
        raise UpdateError(f"not a release tag: {tag}")
    data = _get(SETUP_URL, 1024 * 1024, timeout=60)
    text = data.decode("utf-8", "replace")
    if not text.lstrip().lower().startswith("@echo off") or "#PS-START" not in text or f"'{UPDATE_REPO}'" not in text:
        raise UpdateError("the downloaded setup script doesn't look like OpenLauncher-Setup.bat; nothing was changed")
    if "OCM_SETUP_TAG" not in text:
        raise UpdateError("the current setup script can't install a chosen release; nothing was changed")
    tmp = os.path.join(os.environ.get("TEMP") or config.ROOT, "OpenLauncher-update")
    os.makedirs(tmp, exist_ok=True)
    bat = os.path.join(tmp, "OpenLauncher-Setup.bat")
    with open(bat, "wb") as f:
        f.write(data)
    env = dict(os.environ, OCM_SETUP_DIR=config.ROOT, OCM_SETUP_QUIET="1", OCM_SETUP_NO_SHORTCUTS="1")
    for k in ("OCM_SETUP_ZIP", "OCM_SETUP_NO_LAUNCH", "OCM_SETUP_TAG"):
        env.pop(k, None)
    if tag:
        env["OCM_SETUP_TAG"] = tag
    # wait ~2 s for OpenLauncher to close, run setup; keep the window open only if it failed
    # (a string, not a list: cmd /s /c strips only the outer quotes, Python's list quoting would break it)
    cmd = f'cmd /s /c "ping -n 3 127.0.0.1 >nul & call "{bat}" || pause"'
    subprocess.Popen(cmd, env=env, cwd=tmp, creationflags=subprocess.CREATE_NEW_CONSOLE)
    out(f"Installing OpenLauncher {tag or 'latest release'} in {config.ROOT}; it restarts when setup is done.")


# ------------------------------------------------------------------ git clone updates
# A v2 clone follows its origin's v2 branch; on main (v3) or v3-testing (v3 preview) it follows that branch. "Update" is a
# fast-forward pull (never loses anything). "Force update" also works when that's impossible (local edits or
# commits): it first saves everything, uncommitted changes to a git stash, local commits to a backup branch, then
# moves the branch to its origin. workspace\ and config.json are git-ignored, so git never touches them.
GIT_BRANCH = "v2"
GIT_BRANCHES = (GIT_BRANCH, "main", PREVIEW_BRANCH)     # the only branches OpenLauncher follows or switches to


def _git(*args, check=True, timeout=120):
    r = subprocess.run(["git", "-C", config.ROOT] + list(args), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check and r.returncode != 0:
        raise UpdateError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout.strip()


def git_target():
    """The branch this clone follows: the one it's on if that's v2, main or v3-testing (also for a clone still on
    the old testing branch), otherwise v2."""
    b = _git("rev-parse", "--abbrev-ref", "HEAD", check=False)
    if b in LEGACY_PREVIEW_BRANCHES:
        return PREVIEW_BRANCH
    return b if b in GIT_BRANCHES else GIT_BRANCH


def _git_fetch(branch):
    # explicit refspec: also creates origin/<branch> in a single-branch clone
    _git("fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}", timeout=180)


def git_state(fetch=True, target=None):
    """{branch, target, head, latest, behind, ahead, dirty} relative to origin/<target> (fetches first)."""
    target = target or git_target()
    if fetch:
        _git_fetch(target)
    up = f"origin/{target}"
    dirty = [l for l in _git("status", "--porcelain", "--untracked-files=no").splitlines() if l.strip()]
    return {"branch": _git("rev-parse", "--abbrev-ref", "HEAD"), "target": target,
            "head": _git("rev-parse", "--short", "HEAD"), "latest": _git("rev-parse", "--short", up),
            "behind": int(_git("rev-list", "--count", f"HEAD..{up}") or 0),
            "ahead": int(_git("rev-list", "--count", f"{up}..HEAD") or 0), "dirty": len(dirty)}


def git_update(force=False, out=print, target=None):
    """Update the clone to origin/<target> (default: the branch it follows; v2, main or v3-testing to switch).
    Returns True if anything changed."""
    if target is not None and target not in GIT_BRANCHES:
        raise UpdateError(f"OpenLauncher only follows {' or '.join(GIT_BRANCHES)}, not {target}")
    s = git_state(target=target)
    t = s["target"]
    if s["branch"] in LEGACY_PREVIEW_BRANCHES and t == PREVIEW_BRANCH:
        force = True                    # the preview branch was renamed: switch the safe way (nothing is lost)
    if not force:
        if s["branch"] != t:
            raise UpdateError(f"the clone is on branch '{s['branch']}', not {t}: use Force update, "
                              "or switch branches in git")
        if s["behind"] == 0:
            out(f"Launcher is up to date ({s['head']}).")
            return False
        out(_git("pull", "--ff-only", "origin", t, timeout=300) or "git pull done")
        return True
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if s["dirty"] or _git("status", "--porcelain"):
        _git("stash", "push", "--include-untracked", "-m", f"OpenLauncher force update {stamp}")
        out(f"Your uncommitted changes were saved: git stash list (\"OpenLauncher force update {stamp}\")")
    saved = set()
    for ref, label in (("HEAD", s["branch"]), (t, t)):
        sha = _git("rev-parse", "--verify", "--quiet", ref, check=False)
        if not sha or sha in saved or not int(_git("rev-list", "--count", sha, "--not", "--remotes=origin",
                                                   "--branches=openlauncher-backup-*") or 0):
            continue                                   # missing, already saved, or nothing only-local / unsaved on it
        saved.add(sha)
        name = f"openlauncher-backup-{stamp}" + ("" if not saved - {sha} else f"-{label}")
        _git("branch", name, sha)
        out(f"Your local commits on {label} were saved to branch {name}")
    _git("checkout", "-B", t, f"origin/{t}")
    _git("branch", f"--set-upstream-to=origin/{t}", t, check=False)     # so plain git pull follows it too
    out(f"Launcher moved to origin/{t} ({_git('rev-parse', '--short', 'HEAD')})")
    return True


def git_switch(target, out=print):
    """Switch the clone to v2, main (v3) or v3-testing (v3 preview) the safe way: Force update's path, so uncommitted changes
    go to a git stash and local commits to a backup branch first."""
    return git_update(force=True, out=out, target=target)


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
