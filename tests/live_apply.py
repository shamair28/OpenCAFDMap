"""Live test of Cheat Sheet Apply in a real E-Sys window (not part of the unit tests: needs E-Sys and its psdzdata).

    python tests\\live_apply.py <E-Sys folder> <NCD file>

Starts E-Sys exactly like OpenLauncher (ocm/esys.py launch_command), but with esys/test_apply.js in place of
bootstrap.js: it opens the NCD in E-Sys's FDL editor (the CAFD comes from E-Sys's psdzdata), runs the Cheat Sheet's
own Apply steps on it, checks E-Sys took them and closes E-Sys without saving. Prints the results; exit code 0 = pass."""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ocm"))
import config  # noqa: E402
import esys  # noqa: E402


def main():
    esys_dir, ncd = sys.argv[1], os.path.abspath(sys.argv[2])
    cfg = config.load()
    cfg["esys_dir"] = esys_dir
    cfg["try_untested_esys"] = True                 # Apply is gated on untested E-Sys versions
    spec = esys.launch_spec(esys_dir)
    cmd = esys.launch_command(cfg, spec)
    out = os.path.join(tempfile.gettempdir(), "ocm-test-apply.txt")
    if os.path.exists(out):
        os.remove(out)
    boot = os.path.join(config.ESYS_SCRIPTS, "bootstrap.js")
    i = cmd.index(boot)
    cmd[i] = os.path.join(config.ESYS_SCRIPTS, "test_apply.js")
    cmd[i - 1:i - 1] = [f"-Docm.test.ncd={ncd}", f"-Docm.test.out={out}"]   # system properties go before the main class
    rc = subprocess.run(cmd, cwd=spec["cwd"], env=esys.env_for(esys_dir), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600).returncode
    print(open(out, encoding="utf-8").read() if os.path.exists(out) else "(no result file)")
    print("exit", rc)
    sys.exit(rc)


if __name__ == "__main__":
    main()
