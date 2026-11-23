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
    ("esys_ui_scale", "E-Sys UI scale", "uiscale", "try 1 if E-Sys's window gets drawn over itself on a scaled display"),
    ("esys_language", "E-Sys language", "esyslang", "E-Sys's menus and messages, for E-Sys started by OpenLauncher"),
    ("workspace", "Workspace", "dir", "DB, decrypted cache, definitions, logs (never committed)"),
    ("bimmercode_backups_dir", "BimmerCode backups", "dir", "optional: folder with your .cod (iOS) or .dat (Android) backups"),
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
        self.after(1200, lambda: self.check_launcher(silent=True))

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
            elif kind == "uiscale":           # the choices, or a number typed in
                v.set(esys.ui_scale_label(v.get()))
                w = ttk.Combobox(pf, textvariable=v, values=[lbl for _, lbl in esys.UI_SCALES], width=60)
            elif kind == "esyslang":
                v.set(esys.esys_language_label(v.get()))
                w = ttk.Combobox(pf, textvariable=v, values=[lbl for _, lbl in esys.ESYS_LANGUAGES],
                                 state="readonly", width=60)
            else:
                w = ttk.Entry(pf, textvariable=v, width=70)
            w.grid(row=r, column=1, sticky="we")
            if kind in ("dir", "file"):
                ttk.Button(pf, text="Browse", width=8, command=lambda k=key, t=kind: self.browse(k, t)).grid(row=r, column=2, padx=4)
            hl = ttk.Label(pf, text=hlp, foreground="#666")
            hl.grid(row=r, column=3, sticky="w")
            if key == "ista_db":              # live: decrypted (usable) or encrypted / not a DiagDocDb
                self.ista_status = hl
                v.trace_add("write", lambda *_: self._ista_status())
                self._ista_status()
        self.all_versions = tk.BooleanVar(value=bool(self.cfg.get("export_all_versions")))
        ttk.Checkbutton(pf, text="Export every CAFD version (bigger; otherwise newest 3 + the versions in your "
                                  "BimmerCode backups)", variable=self.all_versions).grid(row=len(FIELDS), column=1,
                                                                                          columnspan=3, sticky="w")
        self.try_untested = tk.BooleanVar(value=bool(self.cfg.get("try_untested_esys")))
        ttk.Checkbutton(pf, text="Try anyway: allow Cheat Sheet Apply on E-Sys versions OpenLauncher wasn't tested with "
                                  "(tested: 25.x)", variable=self.try_untested).grid(row=len(FIELDS) + 1, column=1,
                                                                                    columnspan=3, sticky="w")
        ttk.Button(pf, text="Save settings", command=self.save).grid(row=len(FIELDS) + 2, column=1, sticky="w", pady=4)
        pf.columnconfigure(1, weight=1)

        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, padding=(10, 2), justify="left").pack(fill="x")

        af = ttk.Frame(self, padding=(8, 2))
        af.pack(fill="x")
        b = ttk.LabelFrame(af, text="Definitions", padding=6)
        b.pack(side="left", fill="y")
        self.btns = []
        for text, step, tip in (("Build definitions", "build", "not needed: E-Sys and the Backup Editor build what they need"),
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

        u = ttk.LabelFrame(self, text="Updates", padding=6)
        u.pack(fill="x", padx=8, pady=(4, 0))
        # each button has three states: "Check for updates" -> "Update now" | "... up to date"
        ur = ttk.Frame(u)
        ur.pack(fill="x", pady=1)
        self.btn_update = ttk.Button(ur, text="Check for updates", width=26, command=self.update_clicked)
        self.btn_update.pack(side="left")
        self.update_state = "check"
        self.update_label = tk.StringVar()
        ttk.Label(ur, textvariable=self.update_label).pack(side="left", padx=8)
        self.show_versions(None)
        lr = ttk.Frame(u)
        lr.pack(fill="x", pady=1)
        self.btn_launcher = ttk.Button(lr, text="Check for launcher update", width=26, command=self.launcher_clicked)
        self.btn_launcher.pack(side="left")
        # git clones only: move to the followed origin branch even with local edits/commits (they are saved first)
        self.btn_force = ttk.Button(lr, text="Force update", width=14, command=self.force_launcher_update)
        if updater.is_git_clone():
            self.btn_force.pack(side="left", padx=(4, 0))
        self.launcher_state = "check"
        self.launcher_label = tk.StringVar()
        ttk.Label(lr, textvariable=self.launcher_label).pack(side="left", padx=8)
        # OpenLauncher v3: its newest release (or preview before v3.0.0); this v2 install otherwise stays on v2
        pr = ttk.Frame(u)
        pr.pack(fill="x", pady=1)
        self.btn_preview = ttk.Button(pr, text="Try OpenLauncher v3", width=26, command=self.try_preview)
        self.btn_preview.pack(side="left")
        ttk.Label(pr, text="New design, FA Helper and DTC. This v2 launcher only updates to newer v2 releases "
                           "until you switch.", foreground="#666").pack(side="left", padx=8)
        self.set_launcher_state("check")

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
    def _ista_status(self):
        level, text = config.ista_db_status(self.vars["ista_db"].get())
        self.ista_status.configure(text=text, foreground={"ok": "#1a7f37", "bad": "#b42318"}.get(level, "#666"))

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
        self.cfg["esys_ui_scale"] = esys.ui_scale_value(self.cfg["esys_ui_scale"])
        self.cfg["esys_language"] = esys.esys_language_value(self.cfg["esys_language"])
        self.cfg["export_all_versions"] = self.all_versions.get()
        self.cfg["try_untested_esys"] = self.try_untested.get()
        config.save(self.cfg)
        self.cfg = config.load()
        self.vars["workspace"].set(self.cfg["workspace"])
        self.vars["esys_ui_scale"].set(esys.ui_scale_label(self.cfg["esys_ui_scale"]))
        self.vars["esys_language"].set(esys.esys_language_label(self.cfg["esys_language"]))
        self.refresh_status()
        if not quiet:
            self.write("Settings saved to config.json")
            bad = config.ista_db_problem(self.cfg.get("ista_db"))
            if bad:
                self.write(f"ISTA DiagDocDb: {bad}. Builds use the shipped option titles instead.")
                messagebox.showwarning(
                    "ISTA DiagDocDb",
                    f"This file can't be used as the ISTA DiagDocDb:\n{self.cfg['ista_db']}\n\n{bad}.\n\n"
                    "It's optional: Build definitions uses the option titles that ship with Open CAFD Map "
                    "instead. Clear the field if you don't have a decrypted copy.")

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

    # ---------------------------------------------------------------- launcher updates
    def set_launcher_state(self, state, available=None, git=None):
        why = updater.launcher_blocker(self.cfg)
        if why:
            state = "blocked"
        self.launcher_state = state
        if state == "available":
            self.launcher_new = available       # the exact version "Update launcher" installs
        self.btn_launcher.configure(text={"check": "Check for launcher update", "available": "Update launcher",
                                          "current": "Launcher up to date", "busy": "Working...",
                                          "blocked": "Launcher updates off"}[state])
        self.btn_launcher.state(["disabled"] if state in ("busy", "blocked") else ["!disabled"])
        self.btn_force.state(["disabled"] if state in ("busy", "blocked") else ["!disabled"])
        self.btn_preview.state(["disabled"] if state in ("busy", "blocked") else ["!disabled"])
        if updater.is_git_clone():
            g = git or {}
            text = "Installed launcher: git clone" + (f", {g['branch']} @ {g['head']}" if g else "")
            if g:
                text += f"     origin/{g['target']} @ {g['latest']}" + (f" ({g['behind']} new commit(s))" if g["behind"] else "")
                local = [f"{g['dirty']} edited file(s)"] * bool(g["dirty"]) + [f"{g['ahead']} local commit(s)"] * bool(g["ahead"])
                if local:
                    text += f"     local: {', '.join(local)}"
        else:
            cur = updater.launcher_installed()
            text = (f"Installed launcher: {('v' + cur) if cur else 'source checkout'}"
                    + (f"     Available: v{available}" if available else ""))
        self.launcher_label.set(text + (f"     ({why})" if why else ""))

    def launcher_clicked(self):
        if self.launcher_state == "available":
            self.do_launcher_update()
        else:
            self.check_launcher()

    def check_launcher(self, silent=False):
        if updater.launcher_blocker(self.cfg):
            return
        self.set_launcher_state("busy")

        def work():
            try:
                if updater.is_git_clone():
                    g = updater.git_state()
                    avail = g["behind"] > 0
                    self.q.put(lambda: self.set_launcher_state("available" if avail else "current", git=g))
                    if not silent or avail:
                        self.q.put(f"Launcher (git clone): {g['branch']} @ {g['head']}, origin/{g['target']} @ {g['latest']}"
                                   + (f" - {g['behind']} new commit(s) (Updates > Update launcher)" if avail else ""))
                    return
                cur, new, avail = updater.launcher_check()
                self.q.put(lambda: self.set_launcher_state("available" if avail else "current", new))
                if not silent or avail:
                    self.q.put(f"Launcher: installed v{cur}, newest v2 release v{new}"
                               + (" - update available (Updates > Update launcher)" if avail else ""))
            except (updater.UpdateError, OSError, subprocess.SubprocessError) as e:
                self.q.put(lambda: self.set_launcher_state("check"))
                if not silent:
                    self.q.put(f"Launcher update check: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _launcher_update_allowed(self, title):
        if self.busy:
            messagebox.showinfo(title, "Wait for the current task to finish first.")
            return False
        if esys_running():
            messagebox.showwarning(title, "Close E-Sys first: it runs Open CAFD Map's files from this "
                                          "install, and the update replaces them.")
            return False
        return True

    def _git_update(self, force, target=None):
        """Pull (or force-move, or switch to target) the clone in the background, then restart OpenLauncher on the
        new code."""
        self.busy = True
        self.set_launcher_state("busy")

        def work():
            try:
                if updater.git_update(force, self.q.put, target=target):
                    self.q.put("Restarting OpenLauncher on the updated code...")
                    self.q.put(self.restart)
                    return
                self.q.put(lambda: self.set_launcher_state("current"))
            except (updater.UpdateError, OSError, subprocess.SubprocessError) as e:
                self.q.put(f"LAUNCHER UPDATE FAILED: {e}")
                if not force:
                    self.q.put("If the clone has local edits or commits, Force update saves them and updates anyway.")
                self.q.put(lambda: self.set_launcher_state("check"))
            self.q.put(None)
        threading.Thread(target=work, daemon=True).start()

    def restart(self):
        subprocess.Popen([pythonw(), os.path.join(ROOT, "openlauncher.py")], cwd=ROOT)
        self.after(200, self.destroy)

    def force_launcher_update(self):
        if not self._launcher_update_allowed("Force update"):
            return
        if not messagebox.askyesno(
                "Force update", f"This moves the git clone in\n{ROOT}\nto origin/{updater.git_target()}, even if you changed files "
                                "or made commits. Nothing is lost:\n\n"
                                "  - uncommitted changes are saved to a git stash (git stash list)\n"
                                "  - local commits are saved to a branch named openlauncher-backup-<time>\n\n"
                                "Your workspace and config.json are not touched. Force update now?"):
            return
        self._git_update(force=True)

    def do_launcher_update(self):
        if not self._launcher_update_allowed("Update launcher"):
            return
        if updater.is_git_clone():
            self._git_update(force=False)
            return
        new = getattr(self, "launcher_new", None)
        self._install_release("Update launcher", f"OpenLauncher v{new}" if new else "the newest v2 release",
                              "Update now?", version=new)

    def _install_release(self, title, what, question, version=None, extra=""):
        """Confirm, back up edited shared files, then run setup for this folder (latest release, or version)."""
        changed = updater.local_changes()
        note = (f"\n\nYou changed or added {len(changed)} file(s) in names/definitions/data (e.g. {changed[0]}); "
                "setup replaces those folders, so they are backed up to workspace\\backups first." if changed else "")
        if not messagebox.askyesno(
                title, f"OpenLauncher closes and {what} is installed into\n{ROOT}\n"
                       "Your workspace and settings are kept. OpenLauncher starts again when it's done."
                       + extra + note + "\n\n" + question):
            return
        try:
            if changed:
                bdir = updater.backup_local_changes(changed, "launcher-update")
                self.write(f"Backed up {len(changed)} edited file(s) to {bdir}")
            updater.launcher_update(self.cfg, self.write, version=version)
        except (updater.UpdateError, OSError) as e:
            messagebox.showerror(title, str(e))
            return
        self.after(300, self.destroy)

    def try_preview(self):
        """Updates > Try OpenLauncher v3: the newest v3 release (the newest v3 preview before v3.0.0), or the main /
        v3-testing branch in a git clone."""
        title = "Try OpenLauncher v3"
        why = updater.launcher_blocker(self.cfg)
        if why:
            messagebox.showinfo(title, f"This install can't switch launchers from here: {why}.")
            return
        if not self._launcher_update_allowed(title):
            return
        self.set_launcher_state("busy")

        def work():
            try:
                ver = updater.v3_target()
            except updater.UpdateError as e:
                self.q.put(f"Looking for OpenLauncher v3: {e}")
                self.q.put(lambda: self.set_launcher_state("check"))
                return
            self.q.put(lambda: self.set_launcher_state("check"))
            if not ver:
                self.q.put(lambda: messagebox.showinfo(title, "There's no OpenLauncher v3 to try yet. Check again later."))
                return
            self.q.put(lambda: self._switch_to_v3(title, ver))
        threading.Thread(target=work, daemon=True).start()

    def _switch_to_v3(self, title, ver):
        preview = "-" in ver
        back = ("\n\nTo come back to v2: in the v3 preview, Updates > Back to stable." if preview else
                "\n\nAfter this, the launcher follows v3 updates. To come back to v2, run the setup from a v2 release "
                f"(github.com/{updater.UPDATE_REPO}/releases).")
        if updater.is_git_clone():
            branch = updater.PREVIEW_BRANCH if preview else "main"
            if messagebox.askyesno(
                    title, f"This switches the git clone in\n{ROOT}\nto the {branch} branch (OpenLauncher v3"
                           + (" preview" if preview else "") + ") and restarts OpenLauncher. Uncommitted changes are "
                           "saved to a git stash and local commits to a backup branch first. Your workspace and "
                           "config.json are not touched." + back + "\n\nSwitch now?"):
                self._git_update(force=True, target=branch)
            return
        self._install_release(title, f"OpenLauncher v{ver}" + (" (preview)" if preview else ""),
                              "Install it now?", version=ver, extra=back)

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

    def _build_question(self):
        _, titles = config.ista_db_status(self.cfg.get("ista_db"))
        return ("You don't need a full build anymore.\n\n"
                "\u2022 E-Sys: OpenLauncher names each CAFD the first time you open it.\n"
                "• BimmerCode Backup Editor: when you open it or import backups, it builds definitions for only "
                "the CAFDs in your backups, usually in under a minute.\n\n"
                "A full build reads every CAFD in your psdzdata for one main series and can take half an hour or "
                "more. It's only needed for the command-line search tools.\n\n"
                f"Option titles: {titles}.\n\nRun a full build anyway?")

    def run_step(self, step):
        if self.busy:
            return
        self.save(quiet=True)
        if step == "build" and not esys_ready(self.cfg):
            messagebox.showerror("E-Sys", "Set the E-Sys folder first (it is used to decrypt your psdzdata).")
            return
        if step == "build" and not messagebox.askyesno("Build definitions", self._build_question(), icon="question"):
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
