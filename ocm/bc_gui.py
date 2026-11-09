#!/usr/bin/env python3
r"""BimmerCode Backup Editor: browse BimmerCode backups, stage parameter changes, write new backups.

    pythonw ocm\bc_gui.py        (or the "BimmerCode Backup Editor" button in OpenLauncher)

Same engine as bc_tool.py: originals are never written; every new backup is a byte copy of the
selected backup with only the staged coding bytes changed, verified after writing.
"""
import contextlib
import io
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import appicon  # noqa: E402
import bc_tool as bt  # noqa: E402


def scrolled(parent, widget_factory):
    """Put a Treeview/Text in a frame with vertical AND horizontal scrollbars (wide hex values,
    long names and labels can be side-scrolled instead of being cut off)."""
    fr = ttk.Frame(parent)
    w = widget_factory(fr)
    vs = ttk.Scrollbar(fr, orient="vertical", command=w.yview)
    hs = ttk.Scrollbar(fr, orient="horizontal", command=w.xview)
    w.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
    w.grid(row=0, column=0, sticky="nsew")
    vs.grid(row=0, column=1, sticky="ns")
    hs.grid(row=1, column=0, sticky="ew")
    fr.rowconfigure(0, weight=1)
    fr.columnconfigure(0, weight=1)
    return fr, w


def short_hex(h, n=96):
    """Long blocks (light curves, staging data) are shortened in tables; the details pane has them in full."""
    return h if len(h) <= n else h[:n] + "\u2026"


def run_captured(fn, *args):
    """Run a bc_tool function; return (ok, text). bc_tool reports errors via sys.exit(message)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            res = fn(*args)
        return True, buf.getvalue(), res
    except SystemExit as e:
        return False, (buf.getvalue() + "\n" + str(e)).strip(), None
    except Exception as e:  # noqa: BLE001
        return False, (buf.getvalue() + f"\n{type(e).__name__}: {e}").strip(), None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BimmerCode Backup Editor - Open CAFD Map")
        appicon.apply(self)
        self.geometry("1500x850")
        bt.ensure_workspace()
        if not os.path.exists(bt.DB):
            messagebox.showerror("No definition DB", f"{bt.DB} not found.\nBuild the definitions in OpenLauncher first.")
            self.destroy()
            raise SystemExit(1)
        self.con = bt.db()
        self.cod = None          # loaded backup (bt.Cod)
        self.funcs = []          # function dicts of the loaded backup
        self.cur = {}            # func pk -> current hex
        self.staged = {}         # func pk -> new hex
        self.cods = {}           # tree iid -> path
        self._build()
        self.refresh_backups()

    # ---------------------------------------------------------------- layout
    def _build(self):
        top = ttk.Frame(self, padding=4)
        top.pack(fill="x")
        ttk.Button(top, text="Refresh", command=self.refresh_backups).pack(side="left")
        ttk.Button(top, text="Diff 2 selected backups", command=self.diff_selected).pack(side="left", padx=4)
        ttk.Button(top, text="Check originals", command=self.check_originals).pack(side="left")
        ttk.Button(top, text="Open out folder", command=lambda: os.startfile(bt.OUT)).pack(side="left", padx=4)
        ttk.Button(top, text="Import new backups from source folder", command=self.import_new).pack(side="left")
        self.status = tk.StringVar(value="Select a backup on the left.")
        ttk.Label(top, textvariable=self.status).pack(side="left", padx=12)

        pan = ttk.PanedWindow(self, orient="horizontal")
        pan.pack(fill="both", expand=True)

        # backups
        lf = ttk.Frame(pan)
        ttk.Label(lf, text="Backups (ctrl-click two for Diff)").pack(anchor="w")
        fr, self.tb = scrolled(lf, lambda p: ttk.Treeview(p, columns=("src", "diag", "ecu", "cafd", "date"),
                                                          show="headings", selectmode="extended"))
        for c, w in (("src", 40), ("diag", 45), ("ecu", 130), ("cafd", 170), ("date", 115)):
            self.tb.heading(c, text=c)
            self.tb.column(c, width=w, minwidth=w, stretch=False)
        fr.pack(fill="both", expand=True)
        self.tb.bind("<<TreeviewSelect>>", self.on_backup_select)
        pan.add(lf, weight=1)

        # parameters
        mf = ttk.Frame(pan)
        flt = ttk.Frame(mf)
        flt.pack(fill="x")
        ttk.Label(flt, text="Search").pack(side="left")
        self.q = tk.StringVar()
        e = ttk.Entry(flt, textvariable=self.q, width=28)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self.fill_params())
        ttk.Label(flt, text="Group").pack(side="left")
        self.grp = ttk.Combobox(flt, width=26, state="readonly")
        self.grp.pack(side="left", padx=4)
        self.grp.bind("<<ComboboxSelected>>", lambda _e: self.fill_params())
        self.named_only = tk.BooleanVar()
        ttk.Checkbutton(flt, text="named only", variable=self.named_only, command=self.fill_params).pack(side="left")
        self.staged_only = tk.BooleanVar()
        ttk.Checkbutton(flt, text="staged only", variable=self.staged_only, command=self.fill_params).pack(side="left")
        ttk.Button(flt, text="Apply filter", command=self.fill_params).pack(side="left", padx=4)
        fr, self.tp = scrolled(mf, lambda p: ttk.Treeview(p, columns=("grp", "name", "value", "label", "staged"),
                                                          show="headings", selectmode="browse"))
        # fixed column widths (no stretch): wider content side-scrolls instead of being squeezed
        for c, w in (("grp", 190), ("name", 300), ("value", 260), ("label", 380), ("staged", 200)):
            self.tp.heading(c, text=c)
            self.tp.column(c, width=w, minwidth=60, stretch=False)
        self.tp.tag_configure("staged", background="#ffe9a8")
        self.tp.tag_configure("system", foreground="#888888")
        fr.pack(fill="both", expand=True)
        self.tp.bind("<<TreeviewSelect>>", self.on_param_select)
        pan.add(mf, weight=3)

        # details + staging
        rf = ttk.Frame(pan, padding=4)
        fr, self.detail = scrolled(rf, lambda p: tk.Text(p, height=7, wrap="none"))
        fr.pack(fill="x")
        ttk.Label(rf, text="Values (* = current, double-click to stage)").pack(anchor="w")
        fr, self.tv = scrolled(rf, lambda p: ttk.Treeview(p, columns=("hex", "label"), show="headings", height=9,
                                                          selectmode="browse"))
        self.tv.heading("hex", text="hex")
        self.tv.heading("label", text="label")
        self.tv.column("hex", width=200, minwidth=60, stretch=False)
        self.tv.column("label", width=420, minwidth=60, stretch=False)
        fr.pack(fill="x")
        self.tv.bind("<Double-1>", lambda _e: self.stage_selected_value())
        row = ttk.Frame(rf)
        row.pack(fill="x", pady=3)
        ttk.Button(row, text="Stage selected value", command=self.stage_selected_value).pack(side="left")
        ttk.Label(row, text="  custom hex").pack(side="left")
        self.custom = tk.StringVar()
        ttk.Entry(row, textvariable=self.custom, width=14).pack(side="left", padx=3)
        self.allow_custom = tk.BooleanVar()
        ttk.Checkbutton(row, text="allow custom", variable=self.allow_custom).pack(side="left")
        ttk.Button(row, text="Stage custom", command=self.stage_custom).pack(side="left", padx=3)

        ttk.Label(rf, text="Staged changes (for the loaded backup)").pack(anchor="w", pady=(8, 0))
        self.ts = ttk.Treeview(rf, columns=("param", "old", "new"), show="headings", height=8, selectmode="browse")
        for c, w in (("param", 230), ("old", 90), ("new", 90)):
            self.ts.heading(c, text=c)
            self.ts.column(c, width=w)
        self.ts.pack(fill="x")
        row2 = ttk.Frame(rf)
        row2.pack(fill="x", pady=3)
        ttk.Button(row2, text="Remove", command=self.unstage_selected).pack(side="left")
        ttk.Button(row2, text="Clear all", command=self.clear_staged).pack(side="left", padx=3)
        ttk.Label(rf, text="Note (saved in the .txt log next to the new backup)").pack(anchor="w", pady=(6, 0))
        self.note = tk.StringVar()
        ttk.Entry(rf, textvariable=self.note).pack(fill="x")
        ttk.Button(rf, text="Write new backup", command=self.write_backup).pack(fill="x", pady=6)
        ttk.Label(rf, foreground="#a33", wraplength=440, justify="left",
                  text="New backups keep the source backup's signature block. Restore one low-risk change "
                       "first (see docs/BC_EDITOR.md). Originals are never modified.").pack(anchor="w")
        pan.add(rf, weight=2)

    # ---------------------------------------------------------------- backups
    def refresh_backups(self):
        self.tb.delete(*self.tb.get_children())
        self.cods.clear()
        items = []
        for c in bt.all_cods():
            src = "out" if os.path.dirname(c.path) == bt.OUT else "orig"
            items.append((c.diag, c.ts, src, c))
        for diag, ts, src, c in sorted(items, key=lambda x: (x[0], x[1])):
            iid = self.tb.insert("", "end", values=(src, f"0x{c.diag:02X}", bt.ecu_name(self.con, c),
                                                     f"{c.cafd}_{c.ver}", c.when()))
            self.cods[iid] = c.path
        self.status.set(f"{len(items)} backups")

    def import_new(self):
        ok, txt, _ = run_captured(bt.cmd_init, type("A", (), {"folder": None})())
        self.refresh_backups()
        messagebox.showinfo("Import", txt or "done")

    def on_backup_select(self, _e=None):
        sel = self.tb.selection()
        if len(sel) != 1:
            return
        path = self.cods[sel[0]]
        if self.cod and path == self.cod.path:
            return
        if self.staged and not messagebox.askyesno("Discard staged changes?",
                                                   f"{len(self.staged)} staged change(s) belong to the loaded backup. Discard them?"):
            return
        self.load(path)

    def load(self, path):
        self.cod = bt.Cod(path)
        ok, txt, fs = run_captured(bt.functions, self.con, self.cod)
        if not ok:
            messagebox.showerror("Not in definition DB", txt)
            self.cod, self.funcs = None, []
            return
        self.funcs = fs
        blocks = self.cod.blocks
        self.cur = {f["pk"]: bt.extract(blocks.get(f["addr"]), f["start"], f["end"], f["mask"]) for f in fs}
        self.staged.clear()
        groups = sorted({f"{f['addr']} {f['group']}".strip() for f in fs})
        self.grp["values"] = ["(all)"] + groups
        self.grp.set("(all)")
        self.fill_params()
        self.fill_staged()
        self.status.set(f"{bt.ecu_name(self.con, self.cod)} CAFD_{self.cod.cafd}_{self.cod.ver}  {self.cod.when()}  "
                        f"{os.path.basename(path)}  ({len(fs)} parameters)")

    # ---------------------------------------------------------------- parameters
    def fill_params(self):
        self.tp.delete(*self.tp.get_children())
        if not self.cod:
            return
        q = self.q.get().strip().lower()
        g = self.grp.get()
        gaddr = None if g in ("", "(all)") else g.split()[0]
        for f in self.funcs:
            if gaddr and f["addr"] != gaddr:
                continue
            if self.named_only.get() and f["name"].startswith((f["group"] or f["addr"]) + "_"):
                continue
            if self.staged_only.get() and f["pk"] not in self.staged:
                continue
            hx = self.cur.get(f["pk"])
            lab = bt.label_for(self.con, f["pk"], hx) if hx else "?"
            if q and q not in f"{f['name']} {lab} {f['comment']} {f['group']}".lower():
                continue
            st = self.staged.get(f["pk"], "")
            tags = ("staged",) if st else (("system",) if f["dg_class"] == "SYSTEM" else ())
            self.tp.insert("", "end", iid=str(f["pk"]), tags=tags,
                           values=(f"{f['addr']} {f['group']}", f["name"], short_hex(hx or "?"), lab, short_hex(st)))

    def selected_func(self):
        sel = self.tp.selection()
        if not sel:
            return None
        pk = int(sel[0])
        return next(f for f in self.funcs if f["pk"] == pk)

    def on_param_select(self, _e=None):
        f = self.selected_func()
        if not f:
            return
        hx = self.cur.get(f["pk"])
        self.detail.delete("1.0", "end")
        self.detail.insert("end", f"{f['addr']}/{f['name']}   group: {f['group'] or '-'}   CAFD {f.get('cafd', '')}\n"
                                  f"byte {f['start']}{'-' + str(f['end']) if f['end'] != f['start'] else ''}  "
                                  f"mask {f['mask']}   current: {hx}\n")
        if f["dg_class"] == "SYSTEM":
            self.detail.insert("end", "SYSTEM datagroup (signature) - not editable\n")
        if f["comment"]:
            self.detail.insert("end", f"\n{f['comment']}\n")
        if f["hint"]:
            self.detail.insert("end", f"driven by: {f['hint']}\n")
        self.tv.delete(*self.tv.get_children())
        for idx, vhex, lab, d in bt.values(self.con, f["pk"]):
            mark = "* " if vhex == hx else "  "
            self.tv.insert("", "end", iid=f"{idx}", values=(mark + short_hex(vhex), lab))

    # ---------------------------------------------------------------- staging
    def _stage(self, f, hx):
        if f["dg_class"] == "SYSTEM":
            messagebox.showerror("Not editable", "SYSTEM (signature) datagroups can't be edited.")
            return
        try:  # dry-run the write into a scratch copy of the block to validate width / mask
            buf = bytearray(self.cod.blocks[f["addr"]])
            bt.insert(buf, f, hx)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Invalid value", str(e))
            return
        if hx == self.cur.get(f["pk"]):
            self.staged.pop(f["pk"], None)
        else:
            self.staged[f["pk"]] = hx
        self.fill_staged()
        self.refresh_param_row(f)

    def stage_selected_value(self):
        f = self.selected_func()
        sel = self.tv.selection()
        if not f or not sel:
            return
        idx = int(sel[0])
        hx = next(v[1] for v in bt.values(self.con, f["pk"]) if v[0] == idx)
        self._stage(f, hx)

    def stage_custom(self):
        f = self.selected_func()
        if not f:
            return
        h = self.custom.get().strip()
        h = h[2:] if h.lower().startswith("0x") else h
        ok, txt, res = run_captured(bt.resolve_value, self.con, f, h)
        if not ok:
            messagebox.showerror("Value", txt)
            return
        hx, known = res
        if not known and not self.allow_custom.get():
            messagebox.showerror("Value", f"{hx} is not one of {f['name']}'s values. Tick 'allow custom' to stage it anyway.")
            return
        self._stage(f, hx)

    def refresh_param_row(self, f):
        iid = str(f["pk"])
        if self.tp.exists(iid):
            vals = list(self.tp.item(iid, "values"))
            vals[4] = short_hex(self.staged.get(f["pk"], ""))
            self.tp.item(iid, values=vals, tags=("staged",) if f["pk"] in self.staged else ())

    def fill_staged(self):
        self.ts.delete(*self.ts.get_children())
        for pk, hx in self.staged.items():
            f = next(x for x in self.funcs if x["pk"] == pk)
            self.ts.insert("", "end", iid=str(pk), values=(f"{f['addr']}/{f['name']}", (self.cur.get(pk) or "?")[:16], hx[:16]))

    def unstage_selected(self):
        for iid in self.ts.selection():
            pk = int(iid)
            self.staged.pop(pk, None)
            f = next(x for x in self.funcs if x["pk"] == pk)
            self.refresh_param_row(f)
        self.fill_staged()

    def clear_staged(self):
        pks = list(self.staged)
        self.staged.clear()
        for pk in pks:
            self.refresh_param_row(next(x for x in self.funcs if x["pk"] == pk))
        self.fill_staged()

    # ---------------------------------------------------------------- actions
    def write_backup(self):
        if not self.cod or not self.staged:
            messagebox.showinfo("Nothing to write", "Load a backup and stage at least one change.")
            return
        lines = "\n".join(f"{next(x for x in self.funcs if x['pk'] == pk)['name']}: {self.cur.get(pk)} -> {hx}"
                          for pk, hx in self.staged.items())
        if not messagebox.askyesno("Write new backup?",
                                   f"Source: {os.path.basename(self.cod.path)}\n\n{lines}\n\n"
                                   "A NEW .cod is written to out\\ (the source is not changed). Continue?"):
            return
        changes = [(next(x for x in self.funcs if x["pk"] == pk), hx) for pk, hx in self.staged.items()]
        ok, txt, out = run_captured(bt.write_new, self.con, self.cod, changes, self.note.get())
        if not ok:
            messagebox.showerror("Write failed", txt)
            return
        self.staged.clear()
        self.refresh_backups()
        for iid, p in self.cods.items():  # select the new backup
            if p == out:
                self.tb.selection_set(iid)
                self.tb.see(iid)
                self.load(out)
        messagebox.showinfo("New backup written", txt)

    def diff_selected(self):
        sel = self.tb.selection()
        if len(sel) != 2:
            messagebox.showinfo("Diff", "Ctrl-click exactly two backups (same ECU / CAFD version).")
            return
        a = type("A", (), {"a": self.cods[sel[0]], "b": self.cods[sel[1]]})()
        ok, txt, _ = run_captured(bt.cmd_diff, a)
        self.show_text("Diff", txt)

    def check_originals(self):
        ok, txt, _ = run_captured(bt.cmd_check, None)
        messagebox.showinfo("Check originals", txt)

    def show_text(self, title, txt):
        w = tk.Toplevel(self)
        w.title(title)
        fr, t = scrolled(w, lambda p: tk.Text(p, wrap="none", width=140, height=30))
        t.insert("end", txt)
        fr.pack(fill="both", expand=True)


if __name__ == "__main__":
    appicon.set_app_id("OpenCAFDMap.BackupEditor")
    App().mainloop()
