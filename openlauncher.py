#!/usr/bin/env python3
"""OpenLauncher (Open CAFD Map): set your paths, build the definitions, start E-Sys with labels, or open
the BimmerCode Backup Editor. Start it with OpenLauncher.bat (or: pythonw openlauncher.py)."""
import glob
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "ocm"))
import config  # noqa: E402
import esys  # noqa: E402
import pipeline  # noqa: E402
import updater  # noqa: E402
import appicon  # noqa: E402

FIELDS = [  # key, label, kind, help
    ("esys_dir", "E-Sys folder", "dir", "contains E-Sys.bat, jre\\ and lib\\"),
    ("psdzdata_dir", "psdzdata folder", "dir", "empty = the one E-Sys is set up with"),
    ("series", "Main series", "text", "G020 = G20/G21/G22/G23/G26/G28/G8x"),
    ("context", "Reference vehicle", "context", "shortens value labels to what matters for this vehicle class"),
    ("workspace", "Workspace", "dir", "DB, decrypted cache, definitions, logs (never committed)"),
    ("bimmercode_backups_dir", "BimmerCode backups", "dir", "optional: folder with your .cod backups"),
    ("cheat_sheets_dir", "Cheat sheets", "dir", "optional: FDL cheat-sheet XMLs (imported locally only)"),
    ("ista_db", "ISTA DiagDocDb", "file", "optional: decrypted DiagDocDb .sqlite (option-code titles)"),
]


def app_version():
    """'v1.0.0' for a release install (VERSION is added by installer/build_release.py), '' otherwise."""
    try:
        return "v" + open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        return ""


def pythonw():
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.exists(w) else exe


class OpenLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"OpenLauncher {app_version()} - Open CAFD Map".replace("  ", " "))
        appicon.apply(self)
        self.geometry("980x720")
        self.cfg = config.load()
        self.vars = {}
        self.busy = False
        self.q = queue.Queue()
        self._build()
        self.refresh_status()
        self.after(100, self._drain)
        self.after(800, lambda: self.check_updates(silent=True))

    # ---------------------------------------------------------------- layout
    def _build(self):
        pf = ttk.LabelFrame(self, text="Paths", padding=6)
        pf.pack(fill="x", padx=8, pady=(8, 4))
        contexts = ["none"] + sorted(os.path.basename(p) for p in glob.glob(os.path.join(config.DATA, "contexts", "*.xml")))
        for r, (key, label, kind, hlp) in enumerate(FIELDS):
            ttk.Label(pf, text=label).grid(row=r, column=0, sticky="w", padx=(0, 6))
            v = self.vars[key] = tk.StringVar(value=str(self.cfg.get(key) or ""))
            if kind == "context":
                w = ttk.Combobox(pf, textvariable=v, values=contexts, state="readonly", width=60)
            else:
                w = ttk.Entry(pf, textvariable=v, width=70)
            w.grid(row=r, column=1, sticky="we")
            if kind in ("dir", "file"):
                ttk.Button(pf, text="Browse", width=8, command=lambda k=key, t=kind: self.browse(k, t)).grid(row=r, column=2, padx=4)
            ttk.Label(pf, text=hlp, foreground="#666").grid(row=r, column=3, sticky="w")
        self.all_versions = tk.BooleanVar(value=bool(self.cfg.get("export_all_versions")))
        ttk.Checkbutton(pf, text="Export every CAFD version (bigger; otherwise newest 3 + the versions in your "
                                  "BimmerCode backups)", variable=self.all_versions).grid(row=len(FIELDS), column=1,
                                                                                          columnspan=3, sticky="w")
        ttk.Button(pf, text="Save settings", command=self.save).grid(row=len(FIELDS) + 1, column=1, sticky="w", pady=4)
        pf.columnconfigure(1, weight=1)

        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, padding=(10, 2), justify="left").pack(fill="x")

        af = ttk.Frame(self, padding=(8, 2))
        af.pack(fill="x")
        b = ttk.LabelFrame(af, text="Definitions", padding=6)
        b.pack(side="left", fill="y")
        self.btns = []
        for text, step, tip in (("Build definitions", "build", "optional for E-Sys; needed for the Backup Editor"),
                                ("Apply names", "names", "after editing names\\*.tsv / my_names.tsv"),
                                ("Check contributions", "check", "names + definition XMLs, before you contribute")):
            row = ttk.Frame(b)  # the row is created first so it doesn't cover its button
            row.pack(fill="x", pady=1)
            w = ttk.Button(row, text=text, width=26, command=lambda s=step: self.run_step(s))
            w.pack(side="left")
            ttk.Label(row, text=tip, foreground="#666").pack(side="left", padx=6)
            self.btns.append(w)
        t = ttk.LabelFrame(af, text="Tools", padding=6)
        t.pack(side="left", fill="y", padx=8)
        self.btn_esys = ttk.Button(t, text="Start E-Sys with Open CAFD Map", width=32, command=self.start_esys)
        self.btn_esys.pack(fill="x", pady=1)
        ttk.Button(t, text="BimmerCode Backup Editor", width=32, command=self.start_bc).pack(fill="x", pady=1)
        o = ttk.LabelFrame(af, text="Folders", padding=6)
        o.pack(side="left", fill="y")
        ttk.Button(o, text="Workspace", width=16, command=lambda: self.open_dir(config.paths().workspace)).pack(pady=1)
        ttk.Button(o, text="Names", width=16, command=lambda: self.open_dir(config.NAMES)).pack(pady=1)
        ttk.Button(o, text="Logs", width=16, command=lambda: self.open_dir(config.paths().logs)).pack(pady=1)
        ttk.Button(o, text="Desktop shortcut", width=16, command=self.make_shortcut).pack(pady=1)

        u = ttk.LabelFrame(self, text="Definition updates", padding=6)
        u.pack(fill="x", padx=8, pady=(4, 0))
        # one button, three states: "Check for updates" -> "Update now" | "Definitions up to date"
        self.btn_update = ttk.Button(u, text="Check for updates", width=26, command=self.update_clicked)
        self.btn_update.pack(side="left")
        self.update_state = "check"
        self.update_label = tk.StringVar()
        ttk.Label(u, textvariable=self.update_label).pack(side="left", padx=8)
        self.show_versions(None)

        lf = ttk.Frame(self, padding=(8, 4, 8, 8))
        lf.pack(fill="both", expand=True)
        self.log = tk.Text(lf, wrap="none", height=14)
        vs = ttk.Scrollbar(lf, orient="vertical", command=self.log.yview)
        hs = ttk.Scrollbar(lf, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)

    # ---------------------------------------------------------------- settings
    def browse(self, key, kind):
        cur = self.vars[key].get()
        if kind == "dir":
            p = filedialog.askdirectory(initialdir=cur or None)
        else:
            p = filedialog.askopenfilename(initialdir=os.path.dirname(cur) if cur else None,
                                           filetypes=[("SQLite", "*.sqlite *.db"), ("All", "*.*")])
        if p:
            self.vars[key].set(os.path.normpath(p))

    def save(self, quiet=False):
        for key, *_ in FIELDS:
            self.cfg[key] = self.vars[key].get().strip()
        self.cfg["export_all_versions"] = self.all_versions.get()
        config.save(self.cfg)
        self.cfg = config.load()
        self.vars["workspace"].set(self.cfg["workspace"])
        self.refresh_status()
        if not quiet:
            self.write("Settings saved to config.json")

    def refresh_status(self):
        P = config.Paths(self.cfg)
        d = self.cfg.get("esys_dir") or ""
        esys_ok = os.path.exists(os.path.join(d, "lib", "esysGui.jar"))
        psdz_ok = bool(P.psdzdata) and os.path.isdir(os.path.join(P.psdzdata, "swe", "cafd"))
        n_cache = len(glob.glob(os.path.join(P.cache, "cafd_*.xml.gz")))
        n_defs = len(glob.glob(os.path.join(P.generated, "CAFD_*.xml")))
        self.status.set(
            f"E-Sys: {'found' if esys_ok else 'NOT FOUND'}    psdzdata: {P.psdzdata or '-'} "
            f"({'ok' if psdz_ok else 'not found'})    decrypted CAFDs: {n_cache}    "
            f"DB: {'yes' if os.path.exists(P.db) else 'no'}    E-Sys definitions: {n_defs}")

    # ---------------------------------------------------------------- definition updates
    def show_versions(self, available):
        cur = updater.installed().get("version", "0")
        self.update_label.set(f"Installed definitions: {cur}" + (f"     Available: {available}" if available else ""))

    def set_update_state(self, state, available=None):
        self.update_state = state
        self.btn_update.configure(text={"check": "Check for updates", "available": "Update now",
                                        "current": "Definitions up to date", "busy": "Working..."}[state])
        self.btn_update.state(["disabled"] if state == "busy" else ["!disabled"])
        self.show_versions(available)

    def update_clicked(self):
        if self.update_state == "available":
            self.do_update()
        else:  # "check" and "current" both (re)check
            self.check_updates()

    def check_updates(self, silent=False):
        if self.busy:
            return
        self.save(quiet=True)
        self.set_update_state("busy")

        def work():
            try:
                cur, new, avail = updater.check(self.cfg)
                self.q.put(lambda: self.set_update_state("available" if avail else "current", new))
                if not silent:
                    self.q.put(f"Definitions: installed {cur}, available {new}" + (" - update available" if avail else ""))
            except updater.UpdateError as e:
                self.q.put(lambda: self.set_update_state("check"))
                if not silent:
                    self.q.put(f"Update check: {e}")
        threading.Thread(target=work, daemon=True).start()

    def do_update(self):
        if self.busy:
            return
        changed = updater.local_changes()
        if changed and not messagebox.askyesno(
                "Update definitions", f"You changed or added {len(changed)} file(s) in names/definitions/data "
                                      f"(e.g. {changed[0]}).\nThey will be backed up to workspace\\backups and "
                                      "replaced by the published versions. Continue?"):
            return
        self.busy = True
        self.set_update_state("busy")
        for w in self.btns + [self.btn_esys]:
            w.state(["disabled"])

        def work():
            try:
                if updater.update(self.cfg, self.q.put) and os.path.exists(config.paths().db):
                    self.q.put("== Applying the new names to your definitions")
                    pipeline.build(self.q.put, full=False)
                self.q.put(lambda: self.set_update_state("current", updater.installed().get("version")))
            except (updater.UpdateError, pipeline.StepFailed) as e:
                self.q.put(f"UPDATE FAILED: {e}")
                self.q.put(lambda: self.set_update_state("check"))
            except Exception as e:  # noqa: BLE001
                self.q.put(f"UPDATE FAILED: {type(e).__name__}: {e}")
                self.q.put(lambda: self.set_update_state("check"))
            self.q.put(None)
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- actions
    def write(self, line):
        self.log.insert("end", line + "\n")
        self.log.see("end")

    def _drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                if callable(item):           # UI change requested by a worker thread
                    item()
                elif item is None:
                    self.busy = False
                    for w in self.btns + [self.btn_esys]:
                        w.state(["!disabled"])
                    self.refresh_status()
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def run_step(self, step):
        if self.busy:
            return
        self.save(quiet=True)
        if step == "build" and not esys_ready(self.cfg):
            messagebox.showerror("E-Sys", "Set the E-Sys folder first (it is used to decrypt your psdzdata).")
            return
        self.busy = True
        for w in self.btns + [self.btn_esys]:
            w.state(["disabled"])

        def work():
            try:
                if step == "build":
                    pipeline.build(self.q.put, full=True)
                elif step == "names":
                    pipeline.build(self.q.put, full=False)
                else:
                    pipeline.check(self.q.put)
            except pipeline.StepFailed as e:
                self.q.put(f"FAILED: {e}")
            except SystemExit as e:
                self.q.put(f"FAILED: {e}")
            except Exception as e:  # noqa: BLE001
                self.q.put(f"FAILED: {type(e).__name__}: {e}")
            self.q.put(None)
        threading.Thread(target=work, daemon=True).start()

    def start_esys(self):
        self.save(quiet=True)
        if esys_running() and not messagebox.askyesno(
                "E-Sys is already running", "An E-Sys window is already open. Two E-Sys instances can conflict "
                                            "(PsdZ). Start another one anyway?"):
            return
        try:
            esys.check_esys(self.cfg)
        except SystemExit as e:
            messagebox.showerror("Start E-Sys", str(e))
            return
        self.btn_esys.state(["disabled"])
        cfg = dict(self.cfg)

        def work():
            try:
                esys.prepare(cfg, self.q.put)
                d = cfg["esys_dir"]
                spec = esys.launch_spec(d)
                self.q.put(f"E-Sys {esys.esys_version(d) or '(unknown version)'}, Java {esys.java_major(d) or '?'}; "
                           f"launch recipe: {spec['source']}")
                pr = esys.launch(cfg)
                self.q.put(f"E-Sys started with Open CAFD Map (pid {pr.pid}). Labels apply when you open a CAFD; "
                           f"one without a definition yet is labelled within seconds. "
                           f"Logs: {config.Paths(cfg).logs} (esys.log, agent.log)")
                self.q.put(lambda: self.btn_esys.state(["!disabled"]))
                # E-Sys that dies while starting: say so here, with the reason from esys.log. Exit code 0 is a
                # normal close (the user closed E-Sys); a failed start exits non-zero (bootstrap.js uses 3).
                for _ in range(90):
                    if pr.poll() is not None:
                        if pr.returncode == 0:
                            self.q.put("E-Sys closed.")
                            break
                        self.q.put(f"E-Sys stopped during startup (exit {pr.returncode}). Last lines of esys.log:")
                        for line in esys.log_tail(cfg):
                            self.q.put("    " + line)
                        break
                    time.sleep(1)
            except SystemExit as e:
                self.q.put(f"FAILED: {e}")
            except Exception as e:  # noqa: BLE001
                self.q.put(f"FAILED: {type(e).__name__}: {e}")
            self.q.put(lambda: self.btn_esys.state(["!disabled"]))
        threading.Thread(target=work, daemon=True).start()

    def start_bc(self):
        self.save(quiet=True)
        if not os.path.exists(config.paths().db):
            messagebox.showerror("BimmerCode Backup Editor", "Build the definitions first.")
            return
        subprocess.Popen([pythonw(), os.path.join(ROOT, "ocm", "bc_gui.py")], cwd=ROOT)
        self.write("BimmerCode Backup Editor started.")

    def make_shortcut(self):
        """OpenLauncher.lnk on the desktop, with the OpenLauncher icon (a .bat can't have one)."""
        ps = ("$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell)"
              ".CreateShortcut((Join-Path $d 'OpenLauncher.lnk')); $s.TargetPath=$env:OCM_T; "
              "$s.Arguments='\"'+$env:OCM_A+'\"'; $s.WorkingDirectory=$env:OCM_W; $s.IconLocation=$env:OCM_I; "
              "$s.Description='OpenLauncher - Open CAFD Map'; $s.Save(); Write-Output (Join-Path $d 'OpenLauncher.lnk')")
        env = dict(os.environ, OCM_T=pythonw(), OCM_A=os.path.join(ROOT, "openlauncher.py"), OCM_W=ROOT,
                   OCM_I=appicon.ICO)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                           capture_output=True, text=True, creationflags=esys.NO_WINDOW)
        if r.returncode == 0:
            self.write(f"Shortcut created: {r.stdout.strip()}")
        else:
            messagebox.showerror("Desktop shortcut", r.stderr.strip() or "could not create the shortcut")

    def open_dir(self, path):
        os.makedirs(path, exist_ok=True)
        os.startfile(path)


def esys_ready(cfg):
    return os.path.exists(os.path.join(cfg.get("esys_dir") or "", "jre", "bin", "java.exe"))


def esys_running():
    try:
        out = subprocess.run(["tasklist", "/V", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq javaw.exe"],
                             capture_output=True, text=True, creationflags=esys.NO_WINDOW, timeout=15).stdout
        return "E-Sys" in out
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    appicon.set_app_id()
    OpenLauncher().mainloop()
