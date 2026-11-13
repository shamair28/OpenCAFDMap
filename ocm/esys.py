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
import datetime
import functools
import os
import re
import shlex
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

# Built-in launch recipe = E-Sys 25.10's E-Sys.bat. Only used when the user's own E-Sys.bat can't be read.
MAIN_CLASS = "com.bmw.esys.gui.control.Framework"
CP_DIRS = ["3rd-party", "PsdZ", "UUID", "environment", "estcm", "", "fs", "odxchecker",
           os.path.join("odxchecker", "odx201", "lib"), "xml"]
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
NASHORN_LAST = 14          # Java's JavaScript runner (Nashorn) was removed in Java 15
TESTED_ESYS = ("25.",)     # E-Sys versions the in-E-Sys tools were tested on (others get read-only tools)


def check_esys(cfg):
    d = cfg.get("esys_dir") or ""
    need = [os.path.join(d, "jre", "bin", "java.exe"), os.path.join(d, "lib", "esysGui.jar")]
    missing = [p for p in need if not os.path.exists(p)]
    if not d or missing:
        raise SystemExit(f"E-Sys not found in '{d}' (missing {', '.join(missing) or 'folder'}). "
                         "Set the E-Sys folder in OpenLauncher.")
    return d


def builtin_classpath(esys_dir):
    lib = os.path.join(esys_dir, "lib")
    return [os.path.join(lib, d, "*") if d else os.path.join(lib, "*") for d in CP_DIRS]


def classpath(esys_dir):
    return ";".join(launch_spec(esys_dir)["classpath"])


# ------------------------------------------------------------------ versions
@functools.lru_cache(maxsize=None)
def java_major(esys_dir):
    """Major version of E-Sys's bundled JRE (8, 11, ...), or 0 if unknown."""
    jre = os.path.join(esys_dir, "jre")
    try:
        m = re.search(r'JAVA_VERSION="([^"]+)"', open(os.path.join(jre, "release"), encoding="latin-1").read())
        if m:
            return _major(m.group(1))
    except OSError:
        pass
    if os.path.exists(os.path.join(jre, "lib", "rt.jar")):
        return 8                                   # only Java 8 and older ship rt.jar
    try:
        pr = subprocess.run([os.path.join(jre, "bin", "java.exe"), "-version"], capture_output=True, text=True,
                            errors="replace", creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, timeout=30,
                            env=env_for(esys_dir))
        m = re.search(r'version "([^"]+)"', pr.stderr or "")
        return _major(m.group(1)) if m else 0
    except (OSError, subprocess.SubprocessError):
        return 0


def _major(v):
    parts = v.split(".")
    return int(parts[1]) if parts[0] == "1" and len(parts) > 1 else int(re.match(r"\d+", parts[0]).group())


@functools.lru_cache(maxsize=None)
def esys_version(esys_dir):
    """E-Sys version from esysGui.jar's manifest (e.g. '25.10.03', '3.35.3'), or '' if unknown."""
    try:
        with zipfile.ZipFile(os.path.join(esys_dir, "lib", "esysGui.jar")) as z:
            m = re.search(r"^Implementation-Version:\s*(\S+)", z.read("META-INF/MANIFEST.MF").decode("latin-1"), re.M)
            if m:
                return m.group(1)
    except (OSError, KeyError, zipfile.BadZipFile):
        pass
    for f in os.listdir(esys_dir) if os.path.isdir(esys_dir) else []:
        m = re.match(r"RELEASENOTES_(\d+(?:\.\d+)+)", f, re.I)
        if m:
            return m.group(1)
    return ""


def nashorn_options(esys_dir):
    """-Dnashorn.args only where the JRE knows the flag (Java 11-14); Java 8 refuses to start with it."""
    return ["-Dnashorn.args=--no-deprecation-warning"] if java_major(esys_dir) >= 11 else []


def check_java(esys_dir):
    j = java_major(esys_dir)
    if j > NASHORN_LAST:
        raise SystemExit(f"This E-Sys ({esys_version(esys_dir) or 'unknown version'}) uses Java {j}. OpenLauncher "
                         f"starts E-Sys through Java's JavaScript runner, which only exists up to Java {NASHORN_LAST}, "
                         "so this E-Sys version is not supported yet.")
    return j


# ------------------------------------------------------------------ E-Sys.bat
_VAR = re.compile(r"%([^%\s]+?)%")
KEEP_PREFIXES = ("-X", "-D")                       # VM options taken from E-Sys.bat; everything else is dropped


def _expand(s, env, esys_dir):
    s = re.sub(r"%~dp0", lambda m: esys_dir.rstrip("\\/") + "\\", s, flags=re.I)
    return _VAR.sub(lambda m: env.get(m.group(1).split(":", 1)[0].upper(), ""), s)


def parse_bat(esys_dir):
    """Read the launch recipe from E-Sys's own E-Sys.bat: classpath, working folder, VM options, main class.

    Only a small, safe subset is interpreted: `set`, `cd`, the `findstr` property lookups and the line that
    starts javaw. Anything else is ignored, and from the javaw line we keep only -X/-D VM options, the
    classpath and the main class (no -javaagent or other launcher additions). Returns None if the bat is
    missing or doesn't look like an E-Sys launcher."""
    bat = os.path.join(esys_dir, "E-Sys.bat")
    if not os.path.exists(bat):
        return None
    props = config.esys_properties({"esys_dir": esys_dir})
    env = {k.upper(): v for k, v in os.environ.items()}
    env.pop("CLASSPATH", None)
    cwd, dropped = os.path.join(esys_dir, "lib", "environment"), []
    for raw in open(bat, encoding="latin-1"):
        line = raw.strip()
        low = line.lower()
        if not line or low.startswith(("rem", "::", "@", "echo")):
            continue
        m = re.match(r'for\s+/f\s.*findstr\s+/r\s+"\^([^"=]+?)="\s.*\bdo\s+set\s+"?(\w+)=([^"%]*)%%a"?\s*$', line, re.I)
        if m:                                       # for /f ... findstr "^Key=" ... do set "VAR=prefix%%a"
            key = m.group(1).replace("\\.", ".")
            if key in props:
                env[m.group(2).upper()] = m.group(3) + props[key]
            continue
        if low.startswith(("if ", "for ", "goto", ":", "pushd", "popd", "exit")):
            continue                                # conditionals (e.g. the optional TLS -javaagent) are skipped
        m = re.match(r'set\s+"?(\w+)=(.*?)"?\s*$', line, re.I)
        if m:
            env[m.group(1).upper()] = _expand(m.group(2), env, esys_dir)
            continue
        m = re.match(r"cd\s+(?:/d\s+)?(.+)$", line, re.I)
        if m:
            cwd = os.path.normpath(_expand(m.group(1).strip('"'), env, esys_dir))
            continue
        if re.search(r'(^|\s|")javaw(\.exe)?"?(\s|$)', low):
            toks = shlex.split(_expand(line, env, esys_dir).replace("\\", "/"), posix=True)
            i = next(n for n, t in enumerate(toks) if re.search(r"javaw(\.exe)?$", t, re.I))
            opts, cp, main, rest = [], None, None, toks[i + 1:]
            k = 0
            while k < len(rest):
                t = rest[k]
                if t in ("-cp", "-classpath") and k + 1 < len(rest):
                    cp = rest[k + 1].split(";")
                    k += 2
                    continue
                if t.startswith("-splash:"):
                    pass                            # we show our own splash
                elif t.startswith(KEEP_PREFIXES):
                    opts.append(t.replace("/", "\\"))
                elif t.startswith("-"):
                    dropped.append(t)
                else:
                    main = t
                    break
                k += 1
            if cp is None:
                cp = env.get("CLASSPATH", "").split(";")
            cp = [os.path.normpath(c) for c in cp if c.strip()]
            if not main or not cp:
                return None
            return {"source": "E-Sys.bat", "classpath": cp, "cwd": cwd, "vm_options": opts, "main": main,
                    "dropped": dropped}
    return None


def builtin_spec(esys_dir):
    props = config.esys_properties({"esys_dir": esys_dir})
    cacerts = os.path.join(esys_dir, "jre", "lib", "security", "cacerts")
    opts = ["-Xmx1024m", "-XX:ThreadStackSize=1024", "-Dcom.sun.management.jmxremote", "-XX:+UseG1GC",
            f"-Djavax.net.ssl.trustStore={cacerts}", "-Djavax.net.ssl.trustStoreType=jks",
            "-Dcom.sun.security.enableAIAcaIssuers=true",
            f"-DmaxHttpThroughput={props.get('HttpUpdate.MaxHttpThroughput', '10000000')}",
            "-Dsun.java2d.uiScale.enabled=true", f"-Dsun.java2d.uiScale={props.get('UI.Scale', 'auto')}"]
    return {"source": "built-in (E-Sys 25.10 layout)", "classpath": builtin_classpath(esys_dir),
            "cwd": os.path.join(esys_dir, "lib", "environment"), "vm_options": opts, "main": MAIN_CLASS, "dropped": []}


def launch_spec(esys_dir):
    """How this E-Sys is started: from its own E-Sys.bat, or the built-in 25.10 recipe as a fallback."""
    try:
        spec = parse_bat(esys_dir)
    except Exception as e:  # noqa: BLE001 - a bat we don't understand must never block the launch
        spec = None
        print(f"NOTE could not read E-Sys.bat ({type(e).__name__}: {e}); using the built-in recipe")
    if spec and not os.path.isdir(spec["cwd"]):
        spec = None
    return spec or builtin_spec(esys_dir)


def env_for(esys_dir):
    env = dict(os.environ)
    jre = os.path.join(esys_dir, "jre")
    env["JAVA_HOME"] = jre
    env["PATH"] = os.pathsep.join([os.path.join(jre, "bin"), env.get("PATH", ""), r"C:\EDIABAS\Bin",
                                   r"C:\EC-Apps\EDIABAS\Bin"])
    for k in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"):
        env.pop(k, None)  # start from a clean JVM: nobody else's agents in our process
    return env


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


def splash_image():
    """OpenLauncher's animated splash (assets/openlauncher.gif); E-Sys's own splash.gif if it's missing."""
    own = os.path.join(config.ROOT, "assets", "openlauncher.gif")
    return own if os.path.exists(own) else "../splash.gif"


def launch_command(cfg, spec=None):
    d = check_esys(cfg)
    check_java(d)
    spec = spec or launch_spec(d)
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
        f"-Docm.cafdir={os.path.join(config.esys_properties(cfg).get('Path.Home', ''), 'CAF')}",  # Compare NCD default
        f"-Docm.cheatdir={cfg.get('cheat_sheets_dir') or ''}",  # your local cheat sheets (Cheat Sheet window)
        f"-Docm.main={spec['main']}",                         # E-Sys's main class (from E-Sys.bat)
        f"-Docm.esysVersion={esys_version(d)}",
        f"-Docm.testedEsys={','.join(TESTED_ESYS)}",
    ] + nashorn_options(d)
    return [javaw] + spec["vm_options"] + props + [
        "-cp", ";".join(spec["classpath"]), f"-splash:{splash_image()}",
        "jdk.nashorn.tools.Shell", os.path.join(config.ESYS_SCRIPTS, "bootstrap.js")]


def esys_log(cfg):
    return os.path.join(config.Paths(cfg).logs, "esys.log")


def launch(cfg=None):
    """Start E-Sys. Its console output goes to logs\\esys.log (the previous run's is kept as esys.prev.log)."""
    cfg = cfg or config.load()
    d = check_esys(cfg)
    spec = launch_spec(d)
    cmd = launch_command(cfg, spec)
    log = esys_log(cfg)
    if os.path.exists(log):
        try:
            os.replace(log, log[:-4] + ".prev.log")
        except OSError:
            pass
    lf = open(log, "w", encoding="utf-8")
    ver = ""
    try:
        ver = open(os.path.join(config.ROOT, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        pass
    lf.write(f"# OpenLauncher {ver or '(source checkout)'} starting E-Sys {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
             f"# E-Sys {esys_version(d) or '?'} in {d}, Java {java_major(d) or '?'}\n"
             f"# launch recipe: {spec['source']}; main class {spec['main']}; working folder {spec['cwd']}\n"
             + (f"# options in E-Sys.bat not used: {' '.join(spec['dropped'])}\n" if spec["dropped"] else "")
             + f"# command: {subprocess.list2cmdline(cmd)}\n\n")
    lf.flush()
    try:
        return subprocess.Popen(cmd, cwd=spec["cwd"], env=env_for(d), creationflags=NO_WINDOW,
                                stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT)
    finally:
        lf.close()   # the child keeps its own handle


def log_tail(cfg, n=15):
    """Last lines of E-Sys's console output (for "E-Sys stopped during startup" messages)."""
    try:
        lines = [l.rstrip() for l in open(esys_log(cfg), encoding="utf-8", errors="replace")
                 if l.strip() and not l.startswith("#")]
        return lines[-n:]
    except OSError:
        return []


def run_script(script, args, cfg=None, log=None, on_line=None):
    """Headless Nashorn script with E-Sys's classpath (PsdZ decryption etc.). stderr lines go to
    on_line (progress) and the log; PsdZ's very noisy stdout is discarded. Returns the exit code."""
    cfg = cfg or config.load()
    d = check_esys(cfg)
    check_java(d)
    spec = launch_spec(d)
    java = os.path.join(d, "jre", "bin", "java.exe")
    dopts = [o for o in spec["vm_options"] if o.startswith("-D")]   # e.g. java.endorsed.dirs on E-Sys 3.x
    cmd = [java, "-Xmx2048m"] + dopts + nashorn_options(d) + ["-cp", ";".join(spec["classpath"]),
                                                               "jdk.nashorn.tools.Shell", script, "--"] + [str(a) for a in args]
    lf = open(log, "a", encoding="utf-8") if log else None
    pr = subprocess.Popen(cmd, cwd=spec["cwd"], env=env_for(d), creationflags=NO_WINDOW,
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
