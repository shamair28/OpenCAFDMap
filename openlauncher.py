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
import webbrowser
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "ocm"))
import config  # noqa: E402
import esys  # noqa: E402
import pipeline  # noqa: E402
import updater  # noqa: E402
import appicon  # noqa: E402
import ui_theme as ui  # noqa: E402

FIELDS = [  # key, label, kind, help
    ("esys_dir", "E-Sys folder", "dir", "contains E-Sys.bat, jre\\ and lib\\"),
    ("psdzdata_dir", "psdzdata folder", "dir", "empty = the one E-Sys is set up with"),
    ("series", "Main series", "series", "Pick your car's main series, or type another code"),
    ("context", "Reference vehicle", "context", "shortens value labels to what matters for this vehicle class"),
    ("esys_ui_scale", "E-Sys UI scale", "uiscale", "try 1 if E-Sys's window gets drawn over itself on a scaled display"),
    ("esys_language", "E-Sys language", "esyslang", "E-Sys's menus and messages, for E-Sys started by OpenLauncher"),
    ("workspace", "Workspace", "dir", "DB, decrypted cache, definitions, logs (never committed)"),
    ("bimmercode_backups_dir", "BimmerCode backups", "dir", "optional: folder with your .cod (iOS) or .dat (Android) backups"),
    ("cheat_sheets_dir", "Cheat sheets", "dir", "optional: FDL cheat-sheet XMLs (imported locally only)"),
    ("ista_db", "ISTA DiagDocDb", "file", "optional: decrypted DiagDocDb .sqlite (option-code titles)"),
]


# Main series (as in the CAFDs' FP info) and the cars they cover, for the Settings dropdown. Any other code can be
# typed in; only the code is saved.
MAIN_SERIES = [
    ("G001", "X3, X4 (G01/G02), X3 M, X4 M"),
    ("G005", "X5, X6 (G05/G06), X5 M, X6 M"),
    ("G007", "X7 (G07)"),
    ("G008", "iX3 (G08)"),
    ("G011", "7 Series (G11/G12)"),
    ("G015", "8 Series (G14/G15/G16), M8"),
    ("G020", "3/4 Series, i4, M3/M4 (G20/G21/G22/G23/G26/G28/G8x)"),
    ("G029", "Z4 (G29)"),
    ("G030", "5/6 Series (G30/G31/G32), M5"),
    ("G038", "5 Series long (G38)"),
    ("G042", "2 Series Coupé, M2 (G42/G87)"),
    ("G060", "5 Series (G60)"),
    ("G070", "7 Series (G70)"),
    ("J029", "Supra (J29)"),
    ("U006", "2 Series Active Tourer (U06)"),
    ("F001", "7 Series (F01/F02)"),
    ("F010", "5/6 Series (F10-F13, F06), M5/M6"),
    ("F015", "X5/X6 (F15/F16), X5 M/X6 M"),
    ("F020", "1/2 Series (F20-F23), M2"),
    ("F025", "X3/X4 (F25/F26)"),
    ("F030", "3/4 Series (F30-F36), M3/M4"),
    ("F040", "1 Series (F40), 2 Gran Coupé (F44)"),
    ("F045", "2 Active/Gran Tourer (F45/F46)"),
    ("F056", "MINI (F54-F60)"),
    ("I001", "i3 (I01)"),
    ("I020", "iX (I20)"),
]


def series_label(code):
    name = dict(MAIN_SERIES).get(code)
    return f"{code}   {name}" if name else code


def series_code(text):
    """'G020   3/4 Series, ...' -> 'G020' (or whatever code was typed)."""
    return (text or "").strip().split(" ")[0].upper()


PAGES = {  # key: (nav button, title, subtitle)
    "home": ("⌂   Home", "Home", "Start E-Sys, build definitions, stay up to date"),
    "settings": ("⚙   Settings", "Settings", "Paths and options, saved to config.json"),
    "activity": ("≡   Activity", "Activity", "Everything OpenLauncher did this session"),
}
VERSION = "v" + config.app_version() if config.app_version() else ""   # '' for a source checkout
pythonw = esys.pythonw


class OpenLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"OpenLauncher {VERSION} - Open CAFD Map".replace("  ", " "))
        appicon.apply(self)
        # 1120x780 where it fits; on a smaller screen, as big as the screen allows (the pages scroll)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1120, sw - 40)}x{min(780, sh - 90)}")
        self.minsize(min(980, sw - 40), min(480, sh - 90))
        self.f = ui.apply(self)
        self.cfg = config.load()
        self.vars = {}
        self.busy = False
        self.q = queue.Queue()
        self._build()
        ui.dark_titlebar(self)
        self.refresh_status()
        self.show_page("home")
        self.after(100, self._drain)
        self.after(800, lambda: self.check_updates(silent=True))
        self.after(1200, lambda: self.check_launcher(silent=True))

    # ---------------------------------------------------------------- layout
    def _build(self):
        C = ui.C
        # sidebar ---------------------------------------------------------------
        side = ttk.Frame(self, style="Side.TFrame", width=236)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = ttk.Frame(side, style="Side.TFrame", padding=(18, 22, 18, 18))
        brand.pack(fill="x")
        try:
            small = os.path.join(ROOT, "assets", "openlauncher-40.png")     # pre-scaled, anti-aliased
            self._logo = tk.PhotoImage(file=small) if os.path.exists(small) else tk.PhotoImage(file=appicon.PNG).subsample(8)
            tk.Label(brand, image=self._logo, bg=C["side"], bd=0).pack(side="left", padx=(0, 10))
        except tk.TclError:
            pass
        bt = ttk.Frame(brand, style="Side.TFrame")
        bt.pack(side="left")
        ttk.Label(bt, text="OpenLauncher", style="Side.TLabel").pack(anchor="w")
        ttk.Label(bt, text=(VERSION or "source checkout"), style="SideMuted.TLabel").pack(anchor="w")
        self.nav = {}
        for key, (text, _, _) in PAGES.items():
            b = ttk.Button(side, text=text, style="Nav.TButton", command=lambda k=key: self.show_page(k))
            b.pack(fill="x", padx=10, pady=1)
            self.nav[key] = b
        foot = ttk.Frame(side, style="Side.TFrame", padding=(18, 14))
        foot.pack(side="bottom", fill="x")
        ttk.Label(foot, text="OpenLauncher", style="SideBold.TLabel").pack(anchor="w")
        ttk.Label(foot, text="Part of the Open CAFD Map project", style="SideMuted.TLabel").pack(anchor="w")
        self._side_icons = []

        def side_link(icon, text, url, pady):
            """A small icon + text link in the sidebar footer (red on hover)."""
            link = ttk.Frame(foot, style="Side.TFrame", cursor="hand2")
            link.pack(anchor="w", pady=pady)
            try:
                img = tk.PhotoImage(file=os.path.join(ROOT, "assets", icon))
                self._side_icons.append(img)
                ic = tk.Label(link, image=img, bg=C["side"], bd=0, cursor="hand2")
                ic.pack(side="left", padx=(0, 6))
            except tk.TclError:
                ic = None
            lt = ttk.Label(link, text=text, style="SideLink.TLabel", cursor="hand2")
            lt.pack(side="left")
            for w in filter(None, (link, ic, lt)):
                w.bind("<Button-1>", lambda e: webbrowser.open(url))
                w.bind("<Enter>", lambda e: lt.configure(style="SideLinkHover.TLabel"))
                w.bind("<Leave>", lambda e: lt.configure(style="SideLink.TLabel"))

        side_link("github-16.png", updater.UPDATE_REPO, f"https://github.com/{updater.UPDATE_REPO}", (6, 4))
        side_link("kofi-16.png", "Top up my gas tank on Ko-fi?", "https://ko-fi.com/shamair", (0, 10))
        ttk.Label(foot, text="OpenLauncher is free. If you paid for it, ask for a refund and download the verified "
                             "release from GitHub.", style="SideNote.TLabel", wraplength=196,
                  justify="left").pack(anchor="w")
        tk.Frame(self, bg=C["border"], width=1).pack(side="left", fill="y")

        # main area ---------------------------------------------------------------
        main = ttk.Frame(self, padding=(28, 20, 28, 16))
        main.pack(side="left", fill="both", expand=True)
        head = ttk.Frame(main)
        head.pack(fill="x")
        self.page_title = tk.StringVar()
        ttk.Label(head, textvariable=self.page_title, style="Title.TLabel").pack(side="left")
        self.page_sub = tk.StringVar()
        ttk.Label(head, textvariable=self.page_sub, style="Muted.TLabel").pack(side="left", padx=14, pady=(8, 0))
        slot = tk.Frame(main, bg=ui.C["bg"], height=3)     # fixed-height slot: the bar shows only while busy
        slot.pack(fill="x", pady=(10, 12))
        slot.pack_propagate(False)
        self.progress = ttk.Progressbar(slot, mode="indeterminate", style="Busy.Horizontal.TProgressbar")
        self._progress_on = False
        # Home and Settings scroll when the window is too short for them (small laptop screens); Activity's log
        # scrolls on its own
        body = {k: (ttk.Frame(main) if k == "activity" else ui.scrollable(main)) for k in PAGES}
        self.pages = {k: getattr(f, "outer", f) for k, f in body.items()}
        self._build_activity(body["activity"])     # first: the other pages write to the log
        self._build_settings(body["settings"])
        self._build_home(body["home"])

    def show_page(self, key):
        for k, p in self.pages.items():
            p.pack_forget()
            self.nav[k].configure(style="NavOn.TButton" if k == key else "Nav.TButton")
        self.pages[key].pack(fill="both", expand=True)
        _, title, sub = PAGES[key]
        self.page_title.set(title)
        self.page_sub.set(sub)

    def _build_home(self, page):
        # hero: start E-Sys -------------------------------------------------------
        hero = ui.card(page, padding=(22, 18))
        hero.outer.pack(fill="x")
        left = ttk.Frame(hero, style="Card.TFrame")
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="Launch", style="CardH.TLabel").pack(anchor="w")
        self.launch_sub = tk.StringVar(value="Launch E-Sys or BimmerCode Backup Editor")   # E-Sys version: refresh_status
        ttk.Label(left, textvariable=self.launch_sub, style="CardMuted.TLabel").pack(anchor="w", pady=(2, 12))
        row = ttk.Frame(left, style="Card.TFrame")
        row.pack(anchor="w")
        self.btn_esys = ttk.Button(row, text="▶  Start E-Sys", style="Hero.TButton", command=self.start_esys)
        self.btn_esys.pack(side="left")
        ttk.Button(row, text="BimmerCode Backup Editor", command=self.start_bc).pack(side="left", padx=12)
        self._build_launch_options(left)
        self.esys_info = tk.StringVar()
        ttk.Label(hero, textvariable=self.esys_info, style="CardMuted.TLabel", justify="right").pack(side="right",
                                                                                                   anchor="n")

        # status cards ------------------------------------------------------------
        stats = ttk.Frame(page)
        stats.pack(fill="x", pady=14)
        self.stat = {}
        for i, (key, title) in enumerate((("esys", "E-SYS"), ("psdz", "PSDZDATA"), ("defs", "E-SYS LABELS"),
                                          ("db", "BACKUP EDITOR"))):
            c = ui.card(stats, padding=(16, 12))
            c.outer.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 7, 0 if i == 3 else 7))
            stats.columnconfigure(i, weight=1, uniform="stat")
            ttk.Label(c, text=title, style="CardMuted.TLabel").pack(anchor="w")
            v, d = tk.StringVar(), tk.StringVar()
            ttk.Label(c, textvariable=v, style="Stat.TLabel").pack(anchor="w", pady=(2, 0))
            dl = ttk.Label(c, textvariable=d, style="StatOk.TLabel")
            dl.pack(anchor="w")
            self.stat[key] = (v, d, dl)

        # definitions + updates ---------------------------------------------------
        cols = ttk.Frame(page)
        cols.pack(fill="x")
        cols.columnconfigure(0, weight=1, uniform="col")
        cols.columnconfigure(1, weight=1, uniform="col")
        d = ui.card(cols)
        d.outer.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        ttk.Label(d, text="Definitions", style="CardH.TLabel").pack(anchor="w", pady=(0, 8))
        self.btns = []
        for text, step, tip in (("Build definitions", "build", "Not needed: E-Sys and the BC Backup Editor build on demand"),
                                ("Apply names", "names", "After editing names"),
                                ("Check contributions", "check", "Before you contribute")):
            r = ttk.Frame(d, style="Card.TFrame")
            r.pack(fill="x", pady=3)
            w = ttk.Button(r, text=text, width=20, command=lambda s=step: self.run_step(s))
            w.pack(side="left")
            ttk.Label(r, text=tip, style="CardMuted.TLabel", wraplength=185, justify="left").pack(side="left", padx=10)
            self.btns.append(w)

        u = ui.card(cols)
        u.outer.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        ttk.Label(u, text="Updates", style="CardH.TLabel").pack(anchor="w", pady=(0, 8))
        # each button has three states: "Check for updates" -> "Update now" | "... up to date"
        ttk.Label(u, text="DEFINITIONS (NAMES)", style="CardMuted.TLabel").pack(anchor="w")
        ur = ttk.Frame(u, style="Card.TFrame")
        ur.pack(fill="x", pady=(2, 10))
        self.btn_update = ttk.Button(ur, text="Check for updates", width=22, command=self.update_clicked)
        self.btn_update.pack(side="left")
        self.update_state = "check"
        self.update_label = tk.StringVar()
        ttk.Label(ur, textvariable=self.update_label, style="CardMuted.TLabel", wraplength=230,
                  justify="left").pack(side="left", padx=10)
        self.show_versions(None)
        ttk.Label(u, text="LAUNCHER", style="CardMuted.TLabel").pack(anchor="w")
        lr = ttk.Frame(u, style="Card.TFrame")
        lr.pack(fill="x", pady=(2, 0))
        self.btn_launcher = ttk.Button(lr, text="Check for launcher update", width=22, command=self.launcher_clicked)
        self.btn_launcher.pack(side="left")
        # git clones only: move to origin/main even with local edits/commits (they are saved first)
        self.btn_force = ttk.Button(lr, text="Force update", style="Ghost.TButton", command=self.force_launcher_update)
        if updater.is_git_clone():
            self.btn_force.pack(side="left", padx=(6, 0))
        self.launcher_state = "check"
        self.launcher_new = None
        self.launcher_label = tk.StringVar()
        ttk.Label(u, textvariable=self.launcher_label, style="CardMuted.TLabel", wraplength=380,
                  justify="left").pack(anchor="w", pady=(6, 0))
        # v3 preview (pre-release install or v3-testing clone): the way back to the stable release; a stable
        # install can try the newest preview instead
        sr = ttk.Frame(u, style="Card.TFrame")
        sr.pack(anchor="w", pady=(8, 0))
        if updater.is_preview():
            self.btn_stable = ttk.Button(sr, text="Back to stable", style="Ghost.TButton", command=self.back_to_stable)
            note = "You're on the v3 preview"
        else:
            self.btn_stable = ttk.Button(sr, text="Try the preview", style="Ghost.TButton", command=self.try_preview)
            note = "Test the next v3 version early; Back to stable returns any time"
        self.btn_stable.pack(side="left")
        ttk.Label(sr, text=note, style="CardMuted.TLabel").pack(side="left", padx=10)
        self.set_launcher_state("check")

        # folders + latest activity -----------------------------------------------
        fo = ui.card(page, padding=(16, 10))
        fo.outer.pack(fill="x", pady=(14, 0))
        for text, cmd in (("Workspace", lambda: self.open_dir(config.paths().workspace)),
                          ("Names", lambda: self.open_dir(config.NAMES)),
                          ("Logs", lambda: self.open_dir(config.paths().logs)),
                          ("Desktop shortcut", self.make_shortcut)):
            ttk.Button(fo, text=text, style="Ghost.TButton", command=cmd).pack(side="left", padx=(0, 6))
        # latest log line, shortened to the space left of the folder buttons (full text: Activity page)
        self.last_line = tk.StringVar(value="Ready.")
        self._last_full = "Ready."
        self._last_font = tkfont.Font(font=self.f["small"])
        self._last_box = ttk.Frame(fo, style="Card.TFrame")
        self._last_box.pack(side="left", fill="x", expand=True, padx=(12, 0))
        last = ttk.Label(self._last_box, textvariable=self.last_line, style="CardMuted.TLabel", cursor="hand2",
                         anchor="e")
        last.pack(fill="x")
        last.bind("<Button-1>", lambda e: self.show_page("activity"))
        self._last_box.bind("<Configure>", lambda e: self._fit_last())

    def _build_launch_options(self, parent):
        """"Launch options" under Start E-Sys: a dropdown menu of the OpenLauncher features E-Sys gets (or a clean
        boot), and a one-line summary of what's on."""
        C = ui.C
        self.opt_vars = {k: tk.BooleanVar(value=esys.option(self.cfg, k)) for k in esys.OPTION_LABELS}
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(anchor="w", pady=(12, 0))
        self.btn_opts = ttk.Button(row, text="⚙  Launch options  ▾", style="Ghost.TButton",
                                   command=self._show_launch_menu)
        self.btn_opts.pack(side="left")
        self.opt_summary = ttk.Label(row, style="CardMuted.TLabel")
        self.opt_summary.pack(side="left", padx=10)
        m = self.opt_menu = tk.Menu(self, tearoff=0, bg=C["card2"], fg=C["text"], activebackground=C["accent"],
                                    activeforeground="#ffffff", selectcolor=C["accent"], bd=0, relief="flat",
                                    disabledforeground=C["faint"], font=self.f["body"])
        for key, label, hlp in esys.LAUNCH_OPTIONS:
            m.add_checkbutton(label=f"{label}    –  {hlp}", variable=self.opt_vars[key],
                              command=lambda k=key: self._set_option(k))
        m.add_separator()
        m.add_checkbutton(label="Clean boot (troubleshooting)    –  start your E-Sys.bat as it is, "
                                "no OpenLauncher features", variable=self.opt_vars["launch_clean"],
                          command=lambda: self._set_option("launch_clean"))
        m.add_separator()
        m.add_command(label="Turn all features on", command=self._all_options_on)
        self._sync_options()

    def _show_launch_menu(self):
        b = self.btn_opts
        self.opt_menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + 2)

    def _all_options_on(self):
        for k, v in self.opt_vars.items():
            self.cfg[k] = k != "launch_clean"
            v.set(self.cfg[k])
        config.save(self.cfg)
        self._sync_options()
        self.write("Launch options: all features on (from the next E-Sys start)")

    def _set_option(self, key):
        self.cfg[key] = bool(self.opt_vars[key].get())
        config.save(self.cfg)
        self._sync_options()
        self.write(f"Launch option: {esys.OPTION_LABELS[key]} {'on' if self.cfg[key] else 'off'} "
                   "(from the next E-Sys start)")

    def _sync_options(self):
        """Clean boot greys out the features; labels off greys out 'on demand'. Summary + Start button follow."""
        state = {k: bool(v.get()) for k, v in self.opt_vars.items()}
        clean = state["launch_clean"]
        for i, (key, _, _) in enumerate(esys.LAUNCH_OPTIONS):
            dis = clean or (key == "launch_on_demand" and not state["launch_labels"])
            self.opt_menu.entryconfigure(i, state="disabled" if dis else "normal")
        off = esys.options_off(state)
        if clean:
            self.opt_summary.configure(text="Clean boot: E-Sys.bat as it is, no OpenLauncher features",
                                       style="StatWarn.TLabel")
        elif off:
            self.opt_summary.configure(text="Off: " + ", ".join(off), style="StatWarn.TLabel")
        else:
            self.opt_summary.configure(text="All features on", style="CardMuted.TLabel")
        self.btn_esys.configure(text="▶  Start E-Sys (clean boot)" if clean else "▶  Start E-Sys")

    def _build_settings(self, page):
        s = ui.card(page, padding=(22, 18))
        s.outer.pack(fill="x")
        ttk.Label(s, text="Paths", style="CardH.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        contexts = ["none"] + sorted(os.path.basename(p) for p in glob.glob(os.path.join(config.DATA, "contexts", "*.xml")))
        for i, (key, label, kind, hlp) in enumerate(FIELDS):
            r = 1 + i * 2
            ttk.Label(s, text=label, style="CardBold.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 18))
            v = self.vars[key] = tk.StringVar(value=str(self.cfg.get(key) or ""))
            if kind == "context":
                w = ttk.Combobox(s, textvariable=v, values=contexts, state="readonly")
            elif kind == "series":            # editable: the list, or any other code typed in
                v.set(series_label(v.get()))
                w = ttk.Combobox(s, textvariable=v, values=[series_label(c) for c, _ in MAIN_SERIES], height=16)
            elif kind == "uiscale":           # the choices, or a number typed in
                v.set(esys.ui_scale_label(v.get()))
                w = ttk.Combobox(s, textvariable=v, values=[lbl for _, lbl in esys.UI_SCALES])
            elif kind == "esyslang":
                v.set(esys.esys_language_label(v.get()))
                w = ttk.Combobox(s, textvariable=v, values=[lbl for _, lbl in esys.ESYS_LANGUAGES],
                                 state="readonly")
            else:
                w = ttk.Entry(s, textvariable=v)
            w.grid(row=r, column=1, sticky="we")
            if kind in ("dir", "file"):
                ttk.Button(s, text="Browse", style="Ghost.TButton",
                           command=lambda k=key, t=kind: self.browse(k, t)).grid(row=r, column=2, padx=(8, 0))
            hl = ttk.Label(s, text=hlp[:1].upper() + hlp[1:], style="CardMuted.TLabel")
            hl.grid(row=r + 1, column=1, sticky="w", pady=(1, 8))
            if key == "ista_db":              # live: decrypted (usable) or encrypted / not a DiagDocDb
                self.ista_status = hl
                hl.configure(wraplength=560, justify="left")
                v.trace_add("write", lambda *_: self._ista_status())
                self._ista_status()
        s.columnconfigure(1, weight=1)

        o = ui.card(page, padding=(22, 16))
        o.outer.pack(fill="x", pady=(14, 0))
        ttk.Label(o, text="Options", style="CardH.TLabel").pack(anchor="w", pady=(0, 8))
        self.all_versions = tk.BooleanVar(value=bool(self.cfg.get("export_all_versions")))
        ttk.Checkbutton(o, text="Export every CAFD version (bigger; otherwise the newest 3 plus the versions in your "
                                "BimmerCode backups)", variable=self.all_versions,
                        style="Card.TCheckbutton").pack(anchor="w", pady=2)
        self.try_untested = tk.BooleanVar(value=bool(self.cfg.get("try_untested_esys")))
        ttk.Checkbutton(o, text="Try anyway: allow Cheat Sheet Apply on E-Sys versions OpenLauncher wasn't tested "
                                "with (tested: 25.x)", variable=self.try_untested,
                        style="Card.TCheckbutton").pack(anchor="w", pady=2)
        self.experimental17 = tk.BooleanVar(value=bool(self.cfg.get("experimental_java17")))
        ttk.Checkbutton(o, text=esys.EXPERIMENTAL_LABEL, variable=self.experimental17,
                        style="Card.TCheckbutton").pack(anchor="w", pady=(2, 0))
        ttk.Label(o, text="Starts E-Sys versions on Java 17 with the standalone Nashorn (lib\\nashorn), Apply buttons "
                          "included. Tested on 26.04: labels, on-demand definitions, Cheat Sheet Apply, Build definitions. "
                          "Not yet: FA Helper, DTC and FA/SVT backup with a car.", style="CardMuted.TLabel",
                  wraplength=620, justify="left").pack(anchor="w", padx=(22, 0), pady=(0, 4))
        # under the options, not beside them: on a narrow window it would cut their text off
        ttk.Button(o, text="Save settings", style="Accent.TButton", command=self.save).pack(anchor="e", pady=(10, 0))

    def _build_activity(self, page):
        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="Open logs folder", style="Ghost.TButton",
                   command=lambda: self.open_dir(config.paths().logs)).pack(side="right")
        ttk.Button(bar, text="Clear", style="Ghost.TButton",
                   command=lambda: self.log.delete("1.0", "end")).pack(side="right", padx=6)
        box = tk.Frame(page, bg=ui.C["border"])
        box.pack(fill="both", expand=True)
        inner = tk.Frame(box, bg=ui.C["log"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.log = tk.Text(inner, wrap="none", bg=ui.C["log"], fg="#d6d6dd", insertbackground=ui.C["text"],
                           selectbackground=ui.C["accent"], relief="flat", bd=0, padx=12, pady=10,
                           font=self.f["mono"])
        self.log.tag_configure("err", foreground=ui.C["bad"])
        self.log.tag_configure("head", foreground=ui.C["accent_hi"])
        self.log.tag_configure("ok", foreground=ui.C["ok"])
        vs = ttk.Scrollbar(inner, orient="vertical", command=self.log.yview)
        hs = ttk.Scrollbar(inner, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)

    # ---------------------------------------------------------------- settings
    def _ista_status(self):
        level, text = config.ista_db_status(self.vars["ista_db"].get())
        self.ista_status.configure(text=text, style={"ok": "StatOk.TLabel", "bad": "StatBad.TLabel"}.get(
            level, "CardMuted.TLabel"))

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
        self.cfg["series"] = series_code(self.cfg["series"]) or "G020"
        self.cfg["esys_ui_scale"] = esys.ui_scale_value(self.cfg["esys_ui_scale"])
        self.cfg["esys_language"] = esys.esys_language_value(self.cfg["esys_language"])
        self.cfg["export_all_versions"] = self.all_versions.get()
        self.cfg["try_untested_esys"] = self.try_untested.get()
        self.cfg["experimental_java17"] = self.experimental17.get()
        config.save(self.cfg)
        self.cfg = config.load()
        self.vars["workspace"].set(self.cfg["workspace"])
        self.vars["series"].set(series_label(self.cfg["series"]))
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

    def _set_stat(self, key, value, detail, level="ok"):
        v, d, dl = self.stat[key]
        v.set(value)
        d.set(detail)
        dl.configure(style={"ok": "StatOk.TLabel", "warn": "StatWarn.TLabel", "bad": "StatBad.TLabel",
                            "muted": "CardMuted.TLabel"}[level])

    def refresh_status(self):
        P = config.Paths(self.cfg)
        d = self.cfg.get("esys_dir") or ""
        esys_ok = os.path.exists(os.path.join(d, "lib", "esysGui.jar"))
        psdz_ok = bool(P.psdzdata) and os.path.isdir(os.path.join(P.psdzdata, "swe", "cafd"))
        n_defs = len(glob.glob(os.path.join(P.generated, "CAFD_*.xml")))
        ver = esys.esys_version(d) if esys_ok else ""
        self._set_stat("esys", ver or ("Found" if esys_ok else "Not found"),
                       os.path.basename(d.rstrip("\\/")) if esys_ok else "Set the E-Sys folder in Settings",
                       "ok" if esys_ok else "bad")
        self._set_stat("psdz", "Ready" if psdz_ok else "Not found",
                       (P.psdzdata or "-") if psdz_ok else "Check the psdzdata folder", "ok" if psdz_ok else "bad")
        # E-Sys labels: definitions are optional (built on demand when E-Sys opens a CAFD), so 0 isn't a problem
        if n_defs:
            self._set_stat("defs", f"{n_defs:,} CAFDs", "Others built on demand", "ok")
        else:
            self._set_stat("defs", "On demand", "Built as you open CAFDs", "ok")
        # the Backup Editor builds its own definitions from your backups' CAFD versions (bc_tool.ensure_defs)
        bc_cache = os.path.join(P.workspace, "cafd_cache", "backups")
        if os.path.exists(os.path.join(P.workspace, "cafd_defs_backups.sqlite")):
            n_cache = len(glob.glob(os.path.join(bc_cache, "cafd_*.xml.gz")))
            self._set_stat("db", "Ready", f"{n_cache:,} CAFD versions from your backups" if n_cache
                           else "Definitions built", "ok")
        else:
            self._set_stat("db", "On demand", "Built when you open it", "ok")
        self.esys_info.set(f"E-Sys {ver}\nmain series {self.cfg.get('series', '')}" if esys_ok else "")
        self.launch_sub.set(f"Launch E-Sys {ver} or BimmerCode Backup Editor" if ver
                            else "Launch E-Sys or BimmerCode Backup Editor")

    # ---------------------------------------------------------------- definition updates
    def show_versions(self, available):
        cur = updater.installed().get("version", "0")
        self.update_label.set(f"Installed {cur}" + (f"\nAvailable {available}" if available and available != cur else ""))

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
        self._lock(True)
        self.set_update_state("busy")

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
            self.launcher_new = available       # what "Update launcher" installs (a preview needs its tag)
        self.btn_launcher.configure(text={"check": "Check for launcher update", "available": "Update launcher",
                                          "current": "Launcher up to date", "busy": "Working...",
                                          "blocked": "Launcher updates off"}[state])
        self.btn_launcher.state(["disabled"] if state in ("busy", "blocked") else ["!disabled"])
        self.btn_force.state(["disabled"] if state in ("busy", "blocked") else ["!disabled"])
        self.btn_stable.state(["disabled"] if state in ("busy", "blocked") else ["!disabled"])
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
                    + (f"     Available: v{available}" if available and state == "available" else ""))
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
                        self.q.put(f"Launcher (git clone): {g['branch']} @ {g['head']}, "
                                   f"origin/{g['target']} @ {g['latest']}"
                                   + (f" - {g['behind']} new commit(s) (Updates > Update launcher)" if avail else ""))
                    return
                cur, new, avail = updater.launcher_check()
                self.q.put(lambda: self.set_launcher_state("available" if avail else "current", new))
                if not silent or avail:
                    self.q.put(f"Launcher: installed v{cur}, newest v{new}"
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
        new = self.launcher_new
        self._install_release("Update launcher", f"OpenLauncher v{new}" if new else "the latest release",
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
        """Updates > Try the preview (stable v3 installs): the newest v3 pre-release if it's newer than this
        version, or the v3-testing branch in a git clone."""
        title = "Try the preview"
        if not self._launcher_update_allowed(title):
            return
        back = "\n\nTo come back: Updates > Back to stable (installs the newest stable release)."
        if updater.is_git_clone():
            if messagebox.askyesno(
                    title, f"This switches the git clone in\n{ROOT}\nto the {updater.PREVIEW_BRANCH} branch (v3 "
                           "preview) and restarts OpenLauncher. Uncommitted changes are saved to a git stash and local "
                           "commits to a backup branch first. Your workspace and config.json are not touched."
                           + back + "\n\nSwitch now?"):
                self._git_update(force=True, target=updater.PREVIEW_BRANCH)
            return
        self.set_launcher_state("busy")

        def work():
            try:
                ver = updater.preview_latest()
            except updater.UpdateError as e:
                self.q.put(f"Looking for the preview: {e}")
                self.q.put(lambda: self.set_launcher_state("check"))
                return
            self.q.put(lambda: self.set_launcher_state("check"))
            cur = updater.launcher_installed()
            if not ver or (cur and updater.vtuple(ver) <= updater.vtuple(cur)):
                self.q.put(lambda: messagebox.showinfo(title, "There's no preview newer than this version right now. "
                                                              "Check again later."))
                return
            self.q.put(lambda: self._install_release(title, f"the OpenLauncher v{ver} preview",
                                                     "Install the preview now?", version=ver, extra=back))
        threading.Thread(target=work, daemon=True).start()

    def back_to_stable(self):
        """Updates > Back to stable (v3 preview only): the newest v3 release (the newest v2 one before v3.0.0), or
        the main branch in a clone."""
        title = "Back to stable"
        if not self._launcher_update_allowed(title):
            return
        if updater.is_git_clone():
            if messagebox.askyesno(
                    title, f"This switches the git clone in\n{ROOT}\nback to the {updater.GIT_BRANCH} branch (stable "
                           "launcher) and restarts OpenLauncher. Uncommitted changes are saved to a git stash and local "
                           "commits to a backup branch first. Your workspace and config.json are not touched."
                           "\n\nSwitch now?"):
                self._git_update(force=True, target=updater.GIT_BRANCH)
            return
        self.set_launcher_state("busy")

        def work():
            try:
                ver = updater.stable_target()
            except updater.UpdateError as e:
                self.q.put(f"Looking for the stable release: {e}")
                self.q.put(lambda: self.set_launcher_state("check"))
                return
            self.q.put(lambda: self.set_launcher_state("check"))
            if not ver:
                self.q.put(lambda: messagebox.showinfo(title, "No stable release was found. Check again later."))
                return
            self.q.put(lambda: self._install_release(title, f"OpenLauncher v{ver}", "Go back to stable now?",
                                                     version=ver))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- actions
    def _lock(self, busy):
        """Mark a background task as running (or finished): the task buttons and Start E-Sys follow."""
        self.busy = busy
        for w in self.btns + [self.btn_esys]:
            w.state(["disabled" if busy else "!disabled"])

    def write(self, line):
        up = line.lstrip().upper()
        tag = ("err" if up.startswith(("FAILED", "UPDATE FAILED", "LAUNCHER UPDATE FAILED", "ERROR")) else
               "head" if line.startswith("==") else
               "ok" if up.startswith(("DONE", "E-SYS STARTED", "SETTINGS SAVED", "SHORTCUT CREATED")) else None)
        self.log.insert("end", line + "\n", (tag,) if tag else ())
        self.log.see("end")
        if line.strip():
            self._last_full = line.strip()
            self._fit_last()

    def _fit_last(self):
        """Middle-ellipsis the latest log line to the width it has (paths keep their start and end)."""
        text, width = self._last_full, self._last_box.winfo_width() - 4
        if width <= 20 or self._last_font.measure(text) <= width:
            self.last_line.set(text)
            return
        lo, hi = 0, len(text)
        while lo < hi:                          # longest head+tail that fits around the ellipsis
            mid = (lo + hi + 1) // 2
            cand = text[:mid // 2] + " … " + text[len(text) - (mid - mid // 2):]
            if self._last_font.measure(cand) <= width:
                lo = mid
            else:
                hi = mid - 1
        self.last_line.set(text[:lo // 2] + " … " + text[len(text) - (lo - lo // 2):] if lo else "…")

    def _drain(self):
        if self.busy != self._progress_on:        # thin red bar under the title while something runs
            self._progress_on = self.busy
            if self.busy:
                self.progress.pack(fill="both", expand=True)
                self.progress.start(12)
            else:
                self.progress.stop()
                self.progress.pack_forget()
        try:
            while True:
                item = self.q.get_nowait()
                if callable(item):           # UI change requested by a worker thread
                    item()
                elif item is None:               # a background task finished
                    self._lock(False)
                    self.refresh_status()
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _build_question(self):
        _, titles = config.ista_db_status(self.cfg.get("ista_db"))
        on_demand = esys.option(self.cfg, "launch_labels") and esys.option(self.cfg, "launch_on_demand")
        head, esys_line = (("You don't need a full build anymore.",
                            "\u2022 E-Sys: OpenLauncher names each CAFD the first time you open it.")
                           if on_demand else
                           ("The Backup Editor doesn't need a full build anymore.",
                            "\u2022 E-Sys: Build labels on demand is off in Launch options, so E-Sys only shows names "
                            "for CAFDs a full build has built. Turn it on and you don't need this."))
        return (f"{head}\n\n{esys_line}\n"
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
        self._lock(True)

        def work():
            try:
                if step == "check":
                    pipeline.check(self.q.put)
                else:
                    pipeline.build(self.q.put, full=(step == "build"))
            except (pipeline.StepFailed, SystemExit) as e:
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
        cfg = dict(self.cfg)
        if esys.option(cfg, "launch_clean"):      # troubleshooting: E-Sys.bat as is, nothing from OpenLauncher
            try:
                pr = esys.launch_clean(cfg)
            except SystemExit as e:
                messagebox.showerror("Start E-Sys (clean boot)", str(e))
                return
            self.write(f"E-Sys started with a clean boot: {os.path.join(cfg['esys_dir'], 'E-Sys.bat')} as it is "
                       f"(pid {pr.pid}), without OpenLauncher's features. Turn Clean boot off in Launch options "
                       "for labels and tools.")
            return
        self.btn_esys.state(["disabled"])

        def work():
            try:
                if esys.option(cfg, "launch_labels"):
                    esys.prepare(cfg, self.q.put)       # FA->FP mapping for option-code labels (first start only)
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
