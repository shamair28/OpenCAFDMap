// Open CAFD Map tools inside E-Sys: an "OpenLauncher" section in E-Sys's left navigation bar with our utilities.
// Loaded by agent.js (only in an E-Sys started by OpenLauncher). Read-only: nothing here writes coding data or NCDs.
//
//   Compare NCD   two NCD files side by side, with BMW / community names; differences in red; Save diff (TSV)
//
// How it attaches: E-Sys's left bar is an OutlookBarPanel (public addBar). A tool's view is shown the way E-Sys shows a
// module: AbstractView.replace(currentView, ourView) and EsysFrame.currentView = ourView, so clicking any E-Sys section
// afterwards swaps it out normally.
(function (api) {
  var System = java.lang.System;
  var File = java.io.File;
  var Sw = javax.swing;
  var Awt = java.awt;
  var BAR_NAME = "OpenLauncher";
  var log = api.log;

  var DEF_DIR = api.defDir;
  var CAF_DIR = System.getProperty("ocm.cafdir") ? String(System.getProperty("ocm.cafdir")) : null;

  // ------------------------------------------------------------------ E-Sys plumbing
  function cls(name) { try { return java.lang.Class.forName(name); } catch (e) { return null; } }

  function field(obj, clazz, name) {
    var f = clazz.getDeclaredField(name);
    f.setAccessible(true);
    return f;
  }

  function esysFrame() {
    var ctl = cls("com.bmw.esys.gui.control.EsysController");
    var inst = field(null, ctl, "instance").get(null);
    var m = ctl.getDeclaredMethod("getEsysFrame");
    m.setAccessible(true);
    return m.invoke(inst);
  }

  // Show a tool's view in E-Sys's content area, exactly as E-Sys swaps module views.
  function showView(view) {
    var frame = esysFrame();
    var fc = cls("com.bmw.esys.gui.control.EsysFrame");
    var cur = field(frame, fc, "currentView");
    var old = cur.get(frame);
    Java.type("com.bmw.esys.gui.AbstractView").replace(old, view);
    cur.set(frame, view);
    view.setVisible(true);   // E-Sys hides the view it switches away from (EsysFrame.setCurrentViewVisible)
    var w = Sw.SwingUtilities.getWindowAncestor(view);
    if (w) { w.validate(); w.repaint(); }
  }

  function findOutlook(comp, OBP) {
    if (!comp) return null;
    if (OBP.isInstance(comp)) return comp;
    if (comp instanceof Awt.Container) {
      var kids = comp.getComponents();
      for (var i = 0; i < kids.length; i++) {
        var r = findOutlook(kids[i], OBP);
        if (r) return r;
      }
    }
    return null;
  }

  // ------------------------------------------------------------------ icons (drawn, so nothing is shipped or borrowed)
  function compareIcon(size) {
    var img = new Awt.image.BufferedImage(size, size, Awt.image.BufferedImage.TYPE_INT_ARGB);
    var g = img.createGraphics();
    g.setRenderingHint(Awt.RenderingHints.KEY_ANTIALIASING, Awt.RenderingHints.VALUE_ANTIALIAS_ON);
    var s = size / 32;
    function page(x, y, lines) {
      g.setColor(Awt.Color.WHITE);
      g.fillRoundRect(x * s, y * s, 15 * s, 20 * s, 3 * s, 3 * s);
      g.setColor(new Awt.Color(0x55, 0x55, 0x55));
      g.setStroke(new Awt.BasicStroke(1.2 * s));
      g.drawRoundRect(x * s, y * s, 15 * s, 20 * s, 3 * s, 3 * s);
      for (var i = 0; i < lines.length; i++) {
        g.setColor(lines[i]);
        g.setStroke(new Awt.BasicStroke(2 * s));
        g.drawLine((x + 3) * s, (y + 5 + i * 4) * s, (x + 12) * s, (y + 5 + i * 4) * s);
      }
    }
    var grey = new Awt.Color(0x9a, 0x9a, 0x9a), red = new Awt.Color(0xd9, 0x30, 0x25), blue = new Awt.Color(0x2a, 0x78, 0xd6);
    page(2, 3, [grey, blue, grey, grey]);
    page(15, 9, [grey, red, grey, red]);
    g.dispose();
    return new Sw.ImageIcon(img);
  }

  function sheetIcon(size) {
    var img = new Awt.image.BufferedImage(size, size, Awt.image.BufferedImage.TYPE_INT_ARGB);
    var g = img.createGraphics();
    g.setRenderingHint(Awt.RenderingHints.KEY_ANTIALIASING, Awt.RenderingHints.VALUE_ANTIALIAS_ON);
    var s = size / 32;
    g.setColor(Awt.Color.WHITE);
    g.fillRoundRect(5 * s, 3 * s, 20 * s, 26 * s, 3 * s, 3 * s);
    g.setColor(new Awt.Color(0x55, 0x55, 0x55));
    g.setStroke(new Awt.BasicStroke(1.2 * s));
    g.drawRoundRect(5 * s, 3 * s, 20 * s, 26 * s, 3 * s, 3 * s);
    var green = new Awt.Color(0x0B, 0x7A, 0x3E), grey = new Awt.Color(0x9a, 0x9a, 0x9a);
    for (var i = 0; i < 4; i++) {
      var y = (9 + i * 5.5) * s;
      g.setColor(green);
      g.setStroke(new Awt.BasicStroke(1.8 * s));
      g.drawLine(8 * s, y, 9.5 * s, y + 1.5 * s);
      g.drawLine(9.5 * s, y + 1.5 * s, 12 * s, y - 1.5 * s);
      g.setColor(grey);
      g.drawLine(14 * s, y, 22 * s, y);
    }
    g.dispose();
    return new Sw.ImageIcon(img);
  }

  // ------------------------------------------------------------------ NCD + definitions
  function parseNcd(file) {
    var res = { cid: null, ver: null, groups: {} };
    var lines = java.nio.file.Files.readAllLines(file.toPath(), java.nio.charset.StandardCharsets.ISO_8859_1);
    for (var i = 0; i < lines.size(); i++) {
      var line = String(lines.get(i)).trim();
      if (line.charAt(0) !== "S" || line.length < 4) continue;
      var typ = line.charAt(1), cnt = parseInt(line.substr(2, 2), 16);
      var body = [];
      for (var k = 0; k < cnt; k++) body.push(parseInt(line.substr(4 + 2 * k, 2), 16));
      if (typ === "0") {
        var h = body.slice(2, body.length - 1);
        if (h.length >= 7) {
          res.cid = hex(h.slice(0, 4));
          res.ver = pad3(h[4]) + "_" + pad3(h[5]) + "_" + pad3(h[6]);
        }
        continue;
      }
      var alen = { "1": 2, "2": 3, "3": 4 }[typ];
      if (!alen) continue;
      var addr = 0;
      for (var a = 0; a < alen; a++) addr = addr * 256 + body[a];
      var data = body.slice(alen, body.length - 1);
      var g = hex4(Math.floor(addr / 256)), off = addr % 256;
      var buf = res.groups[g] || (res.groups[g] = []);
      for (var d = 0; d < data.length; d++) buf[off + d] = data[d];
    }
    if (!res.cid) throw "not an NCD file (no S0 header with the CAFD id): " + file;
    return res;
  }
  function pad3(n) { var s = String(n); while (s.length < 3) s = "0" + s; return s; }
  function hx(b) { var s = (b & 0xFF).toString(16).toUpperCase(); return s.length < 2 ? "0" + s : s; }
  function hex(arr) { var s = ""; for (var i = 0; i < arr.length; i++) s += hx(arr[i]); return s; }
  function hex4(n) { var s = n.toString(16).toUpperCase(); while (s.length < 4) s = "0" + s; return s; }

  // Same as defdb.extract: a masked single-byte function gives its shifted field value, otherwise the raw bytes.
  function extract(buf, st, en, mask) {
    if (!buf || en >= buf.length) return null;
    for (var i = st; i <= en; i++) if (buf[i] === undefined) return null;
    var m = String(mask || "");
    if (st === en && /^[01]+b$/i.test(m)) {
      var mi = parseInt(m.substring(0, m.length - 1), 2);
      if (mi !== 0xFF) {
        var shift = 0;
        while (((mi >> shift) & 1) === 0) shift++;
        return hx((buf[st] & mi) >> shift);
      }
    }
    return hex(buf.slice(st, en + 1));
  }

  // Definition for a CAFD version: the workspace's file, else build it (decrypt with E-Sys's PsdZ + runtime_defs.py).
  function definition(cid, ver, status) {
    var tag = "CAFD_" + cid + "_" + ver;
    var f = new File(DEF_DIR, tag + ".xml");
    if (!f.exists() && api.runtimeDir && api.python && api.tools) {
      status("Building the definition for " + tag + " ...");
      try {
        var gz = new File(api.runtimeDir, "cafd_" + cid + "_" + ver + ".xml.gz");
        if (!gz.exists()) {
          var cfg = Java.type("com.bmw.esys.domain.psdz.PsdzConfiguration").getInstance();
          var src = new File(cfg.getRoot(), "psdzdata\\swe\\cafd\\cafd_" + cid.toLowerCase() + ".caf." + ver);
          if (!src.exists()) throw "not in your psdzdata: " + src;
          var doc = Java.type("com.bmw.esys.domain.psdz.PsdzLogic").getInstance().decryptVerifyCAF(src);
          gz.getParentFile().mkdirs();
          var os = new java.util.zip.GZIPOutputStream(new java.io.FileOutputStream(gz));
          try {
            javax.xml.transform.TransformerFactory.newInstance().newTransformer().transform(
              new javax.xml.transform.dom.DOMSource(doc), new javax.xml.transform.stream.StreamResult(os));
          } finally { os.close(); }
        }
        var pb = new java.lang.ProcessBuilder(Java.to([api.python, "-u", new File(api.tools, "runtime_defs.py").getPath(),
                                                       gz.getAbsolutePath()], "java.lang.String[]"));
        pb.redirectErrorStream(true);
        pb.redirectOutput(java.lang.ProcessBuilder.Redirect.appendTo(new File(api.logDir, "runtime_defs.log")));
        pb.environment().put("PYTHONIOENCODING", "utf-8");
        var rc = pb.start().waitFor();
        log("Compare NCD: built the definition for " + tag + " (exit " + rc + ")");
      } catch (e) {
        log("Compare NCD: no definition for " + tag + ": " + e);
      }
    }
    if (!f.exists()) f = api.findDef(cid, ver);   // contributed definition (exact or versionless)
    return f ? api.loadXmlDef(f) : null;
  }

  // Function rows of one side: key -> row info. Without a definition, every byte of every datagroup is a row.
  function sideRows(ncd, def) {
    var rows = {};
    if (def) {
      for (var addr in def.groups) {
        var g = def.groups[addr];
        for (var j = 0; j < g.functions.length; j++) {
          var fn = g.functions[j];
          var key = addr + "|" + pad3(fn.start) + "|" + pad3(fn.end) + "|" + fn.mask;
          var v = extract(ncd.groups[addr], fn.start, fn.end, fn.mask);
          rows[key] = { addr: addr, gname: g.name, fname: fn.name, start: fn.start, end: fn.end, mask: fn.mask,
                        hex: v, label: v === null ? "" : (fn.values[v] || "(custom value)"), comment: fn.comment };
        }
      }
    } else {
      for (var ga in ncd.groups) {
        var buf = ncd.groups[ga];
        for (var b = 0; b < buf.length; b++) {
          if (buf[b] === undefined) continue;
          rows[ga + "|" + pad3(b) + "|" + pad3(b) + "|11111111b"] = { addr: ga, gname: "", fname: "byte " + b, start: b,
            end: b, mask: "11111111b", hex: hx(buf[b]), label: "", comment: "" };
        }
      }
    }
    return rows;
  }

  function compare(f1, f2, status) {
    var n1 = parseNcd(f1), n2 = parseNcd(f2);
    var d1 = definition(n1.cid, n1.ver, status);
    var d2 = (n2.cid === n1.cid && n2.ver === n1.ver) ? d1 : definition(n2.cid, n2.ver, status);
    var r1 = sideRows(n1, d1), r2 = sideRows(n2, d2);
    var keys = {};
    for (var k in r1) keys[k] = true;
    for (var k2 in r2) keys[k2] = true;
    var list = Object.keys(keys).sort(function (a, b) {
      var pa = a.split("|"), pb = b.split("|");
      return (parseInt(pa[0], 16) - parseInt(pb[0], 16)) || (+pa[1] - +pb[1]) || (+pa[2] - +pb[2]) || (pa[3] < pb[3] ? -1 : pa[3] > pb[3] ? 1 : 0);
    });
    var rows = [], diff = 0;
    for (var i = 0; i < list.length; i++) {
      var a = r1[list[i]] || null, b = r2[list[i]] || null;
      var st = !a ? "only2" : !b ? "only1" : (a.hex === b.hex ? "same" : "changed");
      if (st !== "same") diff++;
      rows.push({ key: list[i], a: a, b: b, status: st });
    }
    return { n1: n1, n2: n2, d1: d1, d2: d2, rows: rows, diff: diff, f1: f1, f2: f2 };
  }

  // ------------------------------------------------------------------ Compare NCD view
  var RED_BG = new Awt.Color(0xFD, 0xE2, 0xE0), RED_FG = new Awt.Color(0xB4, 0x23, 0x18);

  function buildCompareView() {
    var root = new Sw.JPanel(new Awt.BorderLayout(0, 6));
    root.setBorder(Sw.BorderFactory.createEmptyBorder(4, 4, 4, 4));

    // --- top: file pickers + buttons (E-Sys Plus style)
    var top = new Sw.JPanel(new Awt.GridBagLayout());
    top.setBorder(Sw.BorderFactory.createTitledBorder("Compare Net Coding Data (Open CAFD Map)"));
    var f1 = new Sw.JTextField(), f2 = new Sw.JTextField();
    var GBC = Awt.GridBagConstraints;
    function gbc(x, y, wx, fill) {
      var c = new GBC();
      c.gridx = x; c.gridy = y; c.weightx = wx; c.fill = fill || GBC.NONE;
      c.insets = new Awt.Insets(2, 4, 2, 4); c.anchor = GBC.WEST;
      return c;
    }
    function chooser(target) {
      var dir = target.getText() ? new File(target.getText()).getParentFile() : (CAF_DIR ? new File(CAF_DIR) : null);
      var fc = new Sw.JFileChooser(dir && dir.exists() ? dir : null);
      fc.setFileFilter(new Sw.filechooser.FileNameExtensionFilter("NCD files (*.ncd)", "ncd"));
      if (fc.showOpenDialog(root) === Sw.JFileChooser.APPROVE_OPTION) target.setText(fc.getSelectedFile().getPath());
    }
    var b1 = new Sw.JButton("..."), b2 = new Sw.JButton("...");
    b1.setToolTipText("Pick NCD 1"); b2.setToolTipText("Pick NCD 2");
    b1.addActionListener(function () { chooser(f1); });
    b2.addActionListener(function () { chooser(f2); });
    var bCompare = new Sw.JButton("Compare"), bClear = new Sw.JButton("Clear"), bSave = new Sw.JButton("Save diff");
    var onlyDiff = new Sw.JCheckBox("Differences only", true);
    bSave.setEnabled(false);
    top.add(new Sw.JLabel("NCD File 1:"), gbc(0, 0, 0));
    top.add(f1, gbc(1, 0, 1, GBC.HORIZONTAL));
    top.add(b1, gbc(2, 0, 0));
    top.add(bCompare, gbc(3, 0, 0, GBC.HORIZONTAL));
    top.add(onlyDiff, gbc(4, 0, 0));
    top.add(new Sw.JLabel("NCD File 2:"), gbc(0, 1, 0));
    top.add(f2, gbc(1, 1, 1, GBC.HORIZONTAL));
    top.add(b2, gbc(2, 1, 0));
    top.add(bClear, gbc(3, 1, 0, GBC.HORIZONTAL));
    top.add(bSave, gbc(4, 1, 0, GBC.HORIZONTAL));
    var info = new Sw.JLabel(" ");
    var ic = gbc(0, 2, 1, GBC.HORIZONTAL); ic.gridwidth = 5;
    top.add(info, ic);
    root.add(top, Awt.BorderLayout.NORTH);

    // --- split view: one row model, two tables (NCD 1 left, NCD 2 right)
    var state = { result: null, view: [] };
    var COLS = ["Group", "Function", "Byte / mask", "Hex", "Value"];
    function cell(row, side, col) {
      var r = side === 1 ? row.a : row.b;
      var any = row.a || row.b;
      if (col === 0) return any.addr + (any.gname ? "  " + any.gname : "");
      if (col === 1) return r ? r.fname : (side === 1 ? "(not in NCD 1's CAFD)" : "(not in NCD 2's CAFD)");
      if (col === 2) return any.start + (any.end > any.start ? "-" + any.end : "") + "  " + any.mask;
      if (!r) return "";
      if (col === 3) return r.hex === null ? "(missing)" : r.hex;
      return r.label;
    }
    function model(side) {
      return new Sw.table.AbstractTableModel({
        getRowCount: function () { return state.view.length; },
        getColumnCount: function () { return COLS.length; },
        getColumnName: function (c) { return COLS[c]; },
        getValueAt: function (r, c) { return cell(state.view[r], side, c); }
      });
    }
    var m1 = model(1), m2 = model(2);
    function renderer(side) {
      var base = new Sw.table.DefaultTableCellRenderer();   // delegate: colours are set per cell below
      return new Sw.table.TableCellRenderer({
        getTableCellRendererComponent: function (t, v, sel, foc, row, col) {
          var c = base.getTableCellRendererComponent(t, v, sel, foc, row, col);
          var vr = state.view[row];
          var st = vr ? vr.status : "same";
          var mark = st !== "same" && (side === 2 || st === "only1");
          if (!sel) {
            c.setBackground(mark ? RED_BG : t.getBackground());
            c.setForeground(mark ? RED_FG : t.getForeground());
          }
          var r = vr ? (side === 1 ? vr.a : vr.b) : null;
          c.setToolTipText(r && r.comment ? String(r.comment) : null);
          return c;
        }
      });
    }
    function table(m, side) {
      var t = new Sw.JTable(m);
      t.setDefaultRenderer(java.lang.Object.class, renderer(side));
      t.setAutoCreateRowSorter(false);
      t.setFillsViewportHeight(true);
      var widths = [190, 330, 110, 90, 260];
      for (var i = 0; i < widths.length; i++) t.getColumnModel().getColumn(i).setPreferredWidth(widths[i]);
      return t;
    }
    var t1 = table(m1, 1), t2 = table(m2, 2);
    var sel = new Sw.DefaultListSelectionModel();
    t1.setSelectionModel(sel); t2.setSelectionModel(sel);
    var s1 = new Sw.JScrollPane(t1), s2 = new Sw.JScrollPane(t2);
    s2.getVerticalScrollBar().setModel(s1.getVerticalScrollBar().getModel());   // scroll together
    function titled(sp, title) {
      var p = new Sw.JPanel(new Awt.BorderLayout());
      var l = new Sw.JLabel(title);
      l.setBorder(Sw.BorderFactory.createEmptyBorder(2, 4, 2, 4));
      p.add(l, Awt.BorderLayout.NORTH);
      p.add(sp, Awt.BorderLayout.CENTER);
      return { panel: p, label: l };
    }
    var p1 = titled(s1, "NCD 1"), p2 = titled(s2, "NCD 2");
    var split = new Sw.JSplitPane(Sw.JSplitPane.HORIZONTAL_SPLIT, p1.panel, p2.panel);
    split.setResizeWeight(0.5);
    root.add(split, Awt.BorderLayout.CENTER);

    function refresh() {
      var res = state.result;
      state.view = [];
      if (res) for (var i = 0; i < res.rows.length; i++)
        if (!onlyDiff.isSelected() || res.rows[i].status !== "same") state.view.push(res.rows[i]);
      m1.fireTableDataChanged(); m2.fireTableDataChanged();
    }
    function sideTitle(n, d, f) {
      return "<html><b>" + (d && d.ecu ? d.ecu + "  " : "") + "CAFD_" + n.cid + "_" + n.ver + "</b>  " +
        String(f.getName()) + (d ? "" : "  <font color='#b42318'>(no definition: raw bytes)</font>") + "</html>";
    }
    onlyDiff.addActionListener(function () { refresh(); });
    bClear.addActionListener(function () {
      f1.setText(""); f2.setText(""); state.result = null; refresh();
      info.setText(" "); p1.label.setText("NCD 1"); p2.label.setText("NCD 2"); bSave.setEnabled(false);
    });
    bCompare.addActionListener(function () {
      var a = new File(String(f1.getText()).trim()), b = new File(String(f2.getText()).trim());
      if (!a.isFile() || !b.isFile()) { info.setText("Pick two NCD files first."); return; }
      bCompare.setEnabled(false);
      info.setText("Comparing ...");
      new java.lang.Thread(function () {
        var res = null, err = null;
        try {
          res = compare(a, b, function (msg) { Sw.SwingUtilities.invokeLater(function () { info.setText(msg); }); });
        } catch (e) { err = e; }
        Sw.SwingUtilities.invokeLater(function () {
          bCompare.setEnabled(true);
          if (err) { info.setText("Error: " + err); log("Compare NCD error: " + err); return; }
          state.result = res;
          refresh();
          p1.label.setText(sideTitle(res.n1, res.d1, res.f1));
          p2.label.setText(sideTitle(res.n2, res.d2, res.f2));
          var same = res.n1.cid === res.n2.cid;
          info.setText((same ? "" : "Different CAFDs (" + res.n1.cid + " vs " + res.n2.cid + "): only datagroups both share line up.  ")
            + res.diff + " of " + res.rows.length + " " + (res.d1 || res.d2 ? "functions" : "bytes") + " differ.");
          bSave.setEnabled(true);
          log("Compare NCD: " + a.getName() + " vs " + b.getName() + ": " + res.diff + " difference(s)");
        });
      }).start();
    });
    bSave.addActionListener(function () {
      var res = state.result;
      if (!res) return;
      var name = "NCD_diff_" + res.n1.cid + "_" + res.n1.ver + "_vs_" + (res.n2.cid !== res.n1.cid ? res.n2.cid + "_" : "") + res.n2.ver + ".tsv";
      var fc = new Sw.JFileChooser(res.f1.getParentFile());
      fc.setSelectedFile(new File(res.f1.getParentFile(), name));
      fc.setFileFilter(new Sw.filechooser.FileNameExtensionFilter("Tab-separated text (*.tsv)", "tsv"));
      if (fc.showSaveDialog(root) !== Sw.JFileChooser.APPROVE_OPTION) return;
      var out = fc.getSelectedFile();
      if (!/\.tsv$/i.test(String(out.getName()))) out = new File(out.getPath() + ".tsv");
      try { writeDiff(res, out); info.setText("Saved " + res.diff + " difference(s) to " + out); }
      catch (e) { info.setText("Could not save: " + e); }
    });
    return root;
  }

  // Diff as TSV: '#' header lines (files, CAFDs), then one row per differing function. Opens in Excel / any editor and
  // is easy to read back in (status column + the same cafd/address/start/end/mask keys as names\*.tsv).
  function writeDiff(res, out) {
    function clean(s) { return s === null || s === undefined ? "" : String(s).replace(/[\t\r\n]+/g, " "); }
    var ts = new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm").format(new java.util.Date());
    var L = ["# Open CAFD Map NCD diff (" + ts + ")",
             "# ncd1\t" + res.f1.getPath() + "\tCAFD_" + res.n1.cid + "_" + res.n1.ver + "\t" + clean(res.d1 ? res.d1.ecu : ""),
             "# ncd2\t" + res.f2.getPath() + "\tCAFD_" + res.n2.cid + "_" + res.n2.ver + "\t" + clean(res.d2 ? res.d2.ecu : ""),
             "# status: changed = both have the function, values differ; only1 / only2 = the function exists in one CAFD version only",
             ["status", "cafd_id", "address", "group", "function", "start", "end", "mask", "hex1", "value1", "hex2", "value2"].join("\t")];
    for (var i = 0; i < res.rows.length; i++) {
      var r = res.rows[i];
      if (r.status === "same") continue;
      var x = r.a || r.b;
      L.push([r.status, res.n1.cid, x.addr, clean(x.gname), clean(x.fname), x.start, x.end, x.mask,
              r.a ? clean(r.a.hex) : "", r.a ? clean(r.a.label) : "", r.b ? clean(r.b.hex) : "", r.b ? clean(r.b.label) : ""].join("\t"));
    }
    var w = new java.io.OutputStreamWriter(new java.io.FileOutputStream(out), "UTF-8");
    try { w.write("﻿" + L.join("\r\n") + "\r\n"); } finally { w.close(); }
  }

  // ------------------------------------------------------------------ the OpenLauncher section
  var views = {};
  function openTool(id, build) {
    try {
      if (!views[id]) views[id] = build();
      showView(views[id]);
    } catch (e) {
      log("OpenLauncher tool '" + id + "' failed: " + e);
    }
  }

  // E-Sys's module sections are grey (160,160,163 in light mode); Framework.isDarkMode is E-Sys's own switch.
  function darkMode() {
    try { return !!Java.type("com.bmw.esys.gui.control.Framework").isDarkMode; } catch (e) { return false; }
  }

  var outlook = null;   // E-Sys's OutlookBarPanel, once found
  function selectOurBar() {
    try { outlook.setVisibleBar(field(outlook, cls("com.bmw.esys.gui.outlookbar.OutlookBarPanel"), "bars").get(outlook).size() - 1); }
    catch (e) {}
  }

  // Cheat Sheet window (esys/cheatsheet.js): E-Sys's FDL Editor + cheat sheet pane.
  // Apply edits through E-Sys's FDL Editor internals, so the Cheat Sheet window only runs on E-Sys versions it was
  // tested on. Compare NCD is read-only and stays available everywhere the section can be added.
  var ESYS_VER = String(System.getProperty("ocm.esysVersion") || "");
  var TESTED = String(System.getProperty("ocm.testedEsys") || "25.").split(",").some(function (p) {
    return p && ESYS_VER.indexOf(p) === 0;
  });
  var cheat = null;
  if (!TESTED) log("Cheat Sheet window off: not tested on E-Sys " + (ESYS_VER || "(unknown version)"));
  else try {
    var CONTRIB = System.getProperty("ocm.contrib") ? String(System.getProperty("ocm.contrib")) : null;
    cheat = load(new File(new File(String(System.getProperty("ocm.agent"))).getParentFile(), "cheatsheet.js").getPath())({
      log: log, cls: cls, field: field, esysFrame: esysFrame, selectOurBar: selectOurBar,
      contribDir: CONTRIB,   // every FDL cheat sheet under definitions\ (community-cheatsheets and contributed ones)
      localDir: System.getProperty("ocm.cheatdir") ? String(System.getProperty("ocm.cheatdir")) : null
    });
  } catch (eC) {
    log("Cheat Sheet window not loaded: " + eC);
  }

  function toolButton(text, icon, tip, action) {
    var b = new Sw.JButton(text, icon);
    b.setVerticalTextPosition(Sw.SwingConstants.BOTTOM);
    b.setHorizontalTextPosition(Sw.SwingConstants.CENTER);
    b.setAlignmentX(Awt.Component.CENTER_ALIGNMENT);
    b.setContentAreaFilled(false);
    b.setBorderPainted(false);
    b.setFocusPainted(false);
    b.setToolTipText(tip);
    b.setForeground(darkMode() ? new Awt.Color(0xE0, 0xE0, 0xE0) : Awt.Color.BLACK);
    b.addActionListener(action);
    return b;
  }

  function sectionPanel(bg) {
    var p = new Sw.JPanel();
    p.setLayout(new Sw.BoxLayout(p, Sw.BoxLayout.Y_AXIS));
    p.setBackground(bg);
    p.setOpaque(true);
    p.add(Sw.Box.createVerticalStrut(6));
    p.add(toolButton("Compare NCD", compareIcon(32), "Compare two NCD files side by side (Open CAFD Map)",
                     function () { openTool("compare", buildCompareView); }));
    if (cheat) {
      p.add(Sw.Box.createVerticalStrut(10));
      p.add(toolButton("Cheat Sheet", sheetIcon(32), "FDL Editor with community cheat sheets, checked against the open CAFD",
                       function () { try { cheat.open(); } catch (e) { log("Cheat Sheet failed: " + e); } }));
    }
    p.add(Sw.Box.createVerticalGlue());
    return p;
  }

  var installed = false, gaveUp = false, tries = 0;
  function install() {
    if (installed || gaveUp) return;
    var OBP = cls("com.bmw.esys.gui.outlookbar.OutlookBarPanel");
    if (!OBP) { gaveUp = true; log("OpenLauncher section: this E-Sys has no outlook bar, tools not added"); return; }
    var wins = Awt.Window.getWindows();
    for (var i = 0; i < wins.length; i++) {
      if (!wins[i].isShowing()) continue;
      var ob = findOutlook(wins[i], OBP);
      if (!ob) continue;
      try {
        try { ob.removeBar(BAR_NAME); } catch (eR) {}        // hot reload: replace our earlier bar
        outlook = ob;
        ob.addBar(BAR_NAME, sectionPanel(darkMode() ? new Awt.Color(0x3C, 0x3F, 0x41) : new Awt.Color(0xA0, 0xA0, 0xA3)));
        installed = true;
        log("OpenLauncher section added to E-Sys (Compare NCD" + (cheat ? ", Cheat Sheet" : "") + ")");
      } catch (e) {
        gaveUp = true;
        log("OpenLauncher section could not be added: " + e);
      }
      return;
    }
    if (++tries > 600) gaveUp = true;   // ~9 minutes without a main window: stop looking
  }

  function tick() {
    install();
    if (cheat) cheat.tick();
  }

  return { tick: tick, compare: compare, writeDiff: writeDiff };
});
