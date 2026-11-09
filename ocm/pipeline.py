#!/usr/bin/env python3
r"""Open CAFD Map build steps (the launcher's buttons, also usable from a terminal).

    python ocm\pipeline.py build      decrypt your psdzdata, build the DB, apply names, export definitions
    python ocm\pipeline.py names      re-apply names (after editing names\*.tsv or my_names.tsv) + export
    python ocm\pipeline.py check      check the names files

Paths come from config.json (edit them in the launcher).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402
import esys  # noqa: E402


class StepFailed(Exception):
    pass


def _py(script, args, out):
    """Run one of our Python tools as a child process, streaming its output to out()."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    cmd = [exe, "-u", os.path.join(HERE, script)] + list(args)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                          text=True, encoding="utf-8", errors="replace", env=env,
                          creationflags=esys.NO_WINDOW)
    for line in pr.stdout:
        out(line.rstrip())
    if pr.wait() != 0:
        raise StepFailed(f"{script} failed (exit {pr.returncode})")


def decrypt(P, out):
    out(f"== Decrypting {P.cfg['series']} CAFDs from your psdzdata with E-Sys's PsdZ (first run: can take a while)")
    os.makedirs(P.logs, exist_ok=True)
    rc = esys.run_script(os.path.join(config.ESYS_SCRIPTS, "decrypt.js"), [P.cache, P.cfg["series"], P.fafp],
                         cfg=P.cfg, log=os.path.join(P.logs, "decrypt.log"), on_line=out)
    if rc != 0:
        raise StepFailed(f"decrypt failed (exit {rc}); see {os.path.join(P.logs, 'decrypt.log')}")


def has_cheats(P):
    return bool(P.cfg.get("cheat_sheets_dir")) or bool(P.contributed_xmls("cheatsheet"))


def cheats(P, out):
    if has_cheats(P):
        out("== Importing cheat sheets (contributed + your local folder)")
        _py("cheat_import.py", [P.cfg.get("cheat_sheets_dir") or ""], out)


def build(out, full=True):
    P = config.paths()
    if full:
        decrypt(P, out)
        out("== Building the definition DB (includes the names files)")
        _py("build_defdb.py", [], out)
    if not full or has_cheats(P):
        cheats(P, out)  # needs the DB: cheat-sheet entries are checked against it
        out("== Applying names")
        _py("build_defdb.py", ["--relabel"], out)
    out("== Exporting E-Sys definitions")
    _py("export_defs.py", [], out)
    out("Done.")


def check(out):
    _py("check_names.py", [], out)


def main():
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        if step == "build":
            build(print, full=True)
        elif step == "names":
            build(print, full=False)
        elif step == "check":
            check(print)
        else:
            sys.exit(__doc__)
    except StepFailed as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
