#!/usr/bin/env python3
r"""Run E-Sys: headless helper scripts, or E-Sys itself with Open CAFD Map's labels.

    python ocm\esys.py launch          start E-Sys with Open CAFD Map (same as the OpenLauncher button)
    python ocm\esys.py run <script.js> [args...]   headless Nashorn script on E-Sys's classpath

How the labelling gets into E-Sys
  Only through OpenLauncher. E-Sys is started in a NEW process with E-Sys's own JRE, classpath and
  VM options (as in E-Sys.bat), but with Java's built-in JavaScript runner as the entry point:
  esys\bootstrap.js loads esys\agent.js and then calls E-Sys's normal main class.
  - nothing is written into the E-Sys folder (no patched .bat, jar, config or -javaagent hook);
  - E-Sys started any other way (E-Sys.exe, E-Sys.bat, another launcher) never sees our code;
  - bootstrap.js and agent.js refuse to run unless OpenLauncher's -Docm.launcher flag is present.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

MAIN_CLASS = "com.bmw.esys.gui.control.Framework"
CP_DIRS = ["3rd-party", "PsdZ", "UUID", "environment", "estcm", "", "fs", "odxchecker",
           os.path.join("odxchecker", "odx201", "lib"), "xml"]
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def check_esys(cfg):
    d = cfg.get("esys_dir") or ""
    need = [os.path.join(d, "jre", "bin", "java.exe"), os.path.join(d, "lib", "esysGui.jar")]
    missing = [p for p in need if not os.path.exists(p)]
    if not d or missing:
        raise SystemExit(f"E-Sys not found in '{d}' (missing {', '.join(missing) or 'folder'}). "
                         "Set the E-Sys folder in OpenLauncher.")
    return d


def classpath(esys_dir):
    lib = os.path.join(esys_dir, "lib")
    return ";".join(os.path.join(lib, d, "*") if d else os.path.join(lib, "*") for d in CP_DIRS)


def env_for(esys_dir):
    env = dict(os.environ)
    jre = os.path.join(esys_dir, "jre")
    env["JAVA_HOME"] = jre
    env["PATH"] = os.pathsep.join([os.path.join(jre, "bin"), env.get("PATH", ""), r"C:\EDIABAS\Bin",
                                   r"C:\EC-Apps\EDIABAS\Bin"])
    for k in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"):
        env.pop(k, None)  # start from a clean JVM: nobody else's agents in our process
    return env


def esys_vm_options(cfg):
    """VM options E-Sys.bat uses (UI scale / HTTP throughput read from E-Sys's own properties)."""
    d = cfg["esys_dir"]
    props = config.esys_properties(cfg)
    cacerts = os.path.join(d, "jre", "lib", "security", "cacerts")
    return ["-Xmx1024m", "-XX:ThreadStackSize=1024", "-Dcom.sun.management.jmxremote", "-XX:+UseG1GC",
            f"-Djavax.net.ssl.trustStore={cacerts}", "-Djavax.net.ssl.trustStoreType=jks",
            "-Dcom.sun.security.enableAIAcaIssuers=true",
            f"-DmaxHttpThroughput={props.get('HttpUpdate.MaxHttpThroughput', '10000000')}",
            "-Dsun.java2d.uiScale.enabled=true", f"-Dsun.java2d.uiScale={props.get('UI.Scale', 'auto')}"]


def pythonw():
    """Python without a console window, for the agent's on-demand definition builds."""
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.exists(w) else exe


def prepare(cfg=None, out=print):
    """Before E-Sys starts: the FA->FP mapping (option-code labels) and FP_<series>.xml, if there's no build yet.
    Quick, and only does anything the first time."""
    cfg = cfg or config.load()
    p = config.Paths(cfg)
    if os.path.exists(p.fafp) and os.path.exists(os.path.join(p.generated, f"FP_{cfg['series']}.xml")):
        return 0
    py = sys.executable
    if py.lower().endswith("pythonw.exe"):
        py = py[:-len("pythonw.exe")] + "python.exe"
    pr = subprocess.run([py, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_defs.py"),
                         "--prepare"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                        env=dict(os.environ, PYTHONIOENCODING="utf-8"), creationflags=NO_WINDOW,
                        stdin=subprocess.DEVNULL)
    for line in (pr.stdout or "").splitlines():
        if line.startswith(("Reading", "PROGRESS", "NOTE", "wrote")):
            out(line)
    if pr.returncode:
        out(f"Preparing labels failed (exit {pr.returncode}); E-Sys starts anyway. {(pr.stderr or '').strip()[-300:]}")
    return pr.returncode


def launch_command(cfg):
    d = check_esys(cfg)
    p = config.Paths(cfg)
    # no build needed: CAFDs without a definition file get one on demand (ocm/runtime_defs.py)
    os.makedirs(p.generated, exist_ok=True)
    os.makedirs(p.logs, exist_ok=True)
    javaw = os.path.join(d, "jre", "bin", "javaw.exe")
    props = [
        "-Docm.launcher=1",                                   # the only switch that enables our code
        f"-Docm.defs={p.generated}",
        f"-Docm.contrib={config.CONTRIB}",                    # contributed definition XMLs (fallback)
        f"-Docm.agent={os.path.join(config.ESYS_SCRIPTS, 'agent.js')}",
        f"-Docm.log={os.path.join(p.logs, 'agent.log')}",
        f"-Docm.series={cfg['series']}",
        f"-Docm.runtime={os.path.join(p.workspace, 'runtime_cafd', cfg['series'])}",  # CAFDs labelled on demand
        f"-Docm.python={pythonw()}",
        f"-Docm.tools={os.path.dirname(os.path.abspath(__file__))}",
        "-Dnashorn.args=--no-deprecation-warning",
    ]
    return [javaw] + esys_vm_options(cfg) + props + [
        "-cp", classpath(d), "-splash:../splash.gif",
        "jdk.nashorn.tools.Shell", os.path.join(config.ESYS_SCRIPTS, "bootstrap.js")]


def launch(cfg=None):
    cfg = cfg or config.load()
    cmd = launch_command(cfg)
    d = cfg["esys_dir"]
    return subprocess.Popen(cmd, cwd=os.path.join(d, "lib", "environment"), env=env_for(d),
                            creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_script(script, args, cfg=None, log=None, on_line=None):
    """Headless Nashorn script with E-Sys's classpath (PsdZ decryption etc.). stderr lines go to
    on_line (progress) and the log; PsdZ's very noisy stdout is discarded. Returns the exit code."""
    cfg = cfg or config.load()
    d = check_esys(cfg)
    java = os.path.join(d, "jre", "bin", "java.exe")
    cmd = [java, "-Xmx2048m", "-Dnashorn.args=--no-deprecation-warning", "-cp", classpath(d),
           "jdk.nashorn.tools.Shell", script, "--"] + [str(a) for a in args]
    lf = open(log, "a", encoding="utf-8") if log else None
    pr = subprocess.Popen(cmd, cwd=os.path.join(d, "lib", "environment"), env=env_for(d), creationflags=NO_WINDOW,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace")
    for line in pr.stderr:
        line = line.rstrip()
        if lf:
            lf.write(line + "\n")
        if on_line and line.startswith(("PROGRESS", "DONE", "ERR", "OK", "RESULT", "SAMPLE", "NOTE")):
            on_line(line)
    rc = pr.wait()
    if lf:
        lf.close()
    return rc


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("launch", "run"):
        sys.exit(__doc__)
    if sys.argv[1] == "launch":
        prepare()
        pr = launch()
        print(f"E-Sys started with Open CAFD Map (pid {pr.pid})")
    else:
        sys.exit(run_script(os.path.abspath(sys.argv[2]), sys.argv[3:], on_line=print))


if __name__ == "__main__":
    main()
