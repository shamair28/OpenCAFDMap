// Cheat Sheet window (OpenLauncher section in E-Sys): E-Sys's own FDL Editor on the left, community and local cheat
// sheets on the right, each entry checked against the CAFD version open in the editor.
//
//   Go to   selects the entry's function in the FDL tree.
//   Apply   picks the entry's value in the FDL editor through E-Sys's own FDLController.setValue, exactly like choosing it
//           from the value list by hand. Only the editor's open NCD changes; saving and coding stay with E-Sys and you.
//
// Loaded by tools.js. The FDL Editor is E-Sys's real module (same view object); this file only docks a pane next to it.
(function (h) {
  var File = java.io.File;
  var Sw = javax.swing;
  var Awt = java.awt;
  var log = h.log;

  var GREEN = new Awt.Color(0x0B, 0x7A, 0x3E), BLUE = new Awt.Color(0x2A, 0x5D, 0xA8),
      ORANGE = new Awt.Color(0xB5, 0x5A, 0x00), RED = new Awt.Color(0xB4, 0x23, 0x18);
  var STATUS = {
    ok:      { text: "✓ compatible", color: GREEN },
    set:     { text: "● already set", color: BLUE },
    partial: { text: "◐ partly compatible", color: ORANGE },
    custom:  { text: "✎ custom value", color: ORANGE },
    nofunc:  { text: "✗ not in this version", color: RED },
    noval:   { text: "✗ value not in this version", color: RED },
    bad:     { text: "✗ not for this version", color: RED }
  };

  // ------------------------------------------------------------------ cheat sheets
  var sheetsByCafd = null;   // cafd id -> [code]; code = {sheet, kind, author, desc, steps: [{addr,start,end,mask,value,comment}]}
  var sheetCount = 0;

  function normMask(m) {
    var s = String(m || "").trim();
    if (/^[01]+b$/i.test(s)) return s.toLowerCase();
    if (/^[0-9a-f]{1,2}$/i.test(s)) { var b = parseInt(s, 16).toString(2); while (b.length < 8) b = "0" + b; return b + "b"; }
    return s.toLowerCase();
  }

  function xmlFiles(dir) {
    var out = [], d = new File(dir);
    var kids = d.listFiles();
    if (!kids) return out;
    java.util.Arrays.sort(kids);
    for (var i = 0; i < kids.length; i++) if (kids[i].isFile() && /\.xml$/i.test(String(kids[i].getName()))) out.push(kids[i]);
    return out;
  }

  function allXml(dir) {
    var out = [], stack = [new File(dir)];
    while (stack.length) {
      var d = stack.pop(), kids = d.listFiles();
      if (!kids) continue;
      java.util.Arrays.sort(kids);
      for (var i = 0; i < kids.length; i++) {
        if (kids[i].isDirectory()) { if (String(kids[i].getName()) !== "untrimmed") stack.push(kids[i]); }
        else if (/\.xml$/i.test(String(kids[i].getName()))) out.push(kids[i]);
      }
    }
    return out;
  }

  function loadSheets() {
    sheetsByCafd = {};
    sheetCount = 0;
    var sources = [];
    if (h.contribDir) allXml(h.contribDir).forEach(function (f) {
      var community = String(f.getParentFile().getName()) === "community-cheatsheets";
      sources.push({ f: f, kind: community ? "community (GPL-3.0)" : "contributed" });
    });
    if (h.localDir) xmlFiles(h.localDir).forEach(function (f) { sources.push({ f: f, kind: "local" }); });
    var dbf = javax.xml.parsers.DocumentBuilderFactory.newInstance();
    for (var i = 0; i < sources.length; i++) {
      try {
        var doc = dbf.newDocumentBuilder().parse(sources[i].f);
        var root = doc.getDocumentElement();
        if (String(root.getTagName()) !== "FDL") continue;
        sheetCount++;
        var sheet = String(sources[i].f.getName()).replace(/\.xml$/i, "");
        var cafds = root.getElementsByTagName("cafd");
        for (var c = 0; c < cafds.getLength(); c++) {
          var ce = cafds.item(c);
          var cid = String(ce.getAttribute("id")).toUpperCase();
          while (cid.length < 8) cid = "0" + cid;
          if (!/^[0-9A-F]{8}$/.test(cid)) continue;
          var codes = ce.getElementsByTagName("code");
          for (var k = 0; k < codes.getLength(); k++) {
            var code = codes.item(k);
            var steps = [];
            var groups = code.getElementsByTagName("group");
            for (var g = 0; g < groups.getLength(); g++) {
              var ge = groups.item(g);
              var addr = String(ge.getAttribute("id")).toUpperCase();
              var fns = ge.getElementsByTagName("function");
              for (var f = 0; f < fns.getLength(); f++) {
                var fe = fns.item(f);
                var st = parseInt(fe.getAttribute("start"), 10), en = parseInt(fe.getAttribute("end"), 10);
                if (isNaN(st)) continue;
                steps.push({ addr: addr, start: st, end: isNaN(en) ? st : en, mask: normMask(fe.getAttribute("mask") || "FF"),
                             value: String(fe.getTextContent()).trim(), comment: String(fe.getAttribute("comment") || "") });
              }
            }
            if (!steps.length) continue;
            (sheetsByCafd[cid] || (sheetsByCafd[cid] = [])).push({
              sheet: sheet, kind: sources[i].kind, author: String(ce.getAttribute("author") || sheet),
              desc: String(code.getAttribute("description") || "(no description)"), steps: steps });
          }
        }
      } catch (e) {
        log("Cheat Sheet: skipped " + sources[i].f.getName() + ": " + e);
      }
    }
    log("Cheat Sheet: " + sheetCount + " sheets loaded (" + Object.keys(sheetsByCafd).length + " CAFD ids)");
  }

  // ------------------------------------------------------------------ the CAFD open in the FDL editor
  function tvalueHex(tval) {
    if (!tval) return "";
    var uvs = tval.getUnitVal();
    if (!uvs) return "";
    var s = "";
    for (var i = 0; i < uvs.size(); i++) {
      var b = uvs.get(i).getValue();
      if (!b) continue;
      for (var j = 0; j < b.length; j++) { var x = (b[j] & 0xFF).toString(16).toUpperCase(); s += x.length < 2 ? "0" + x : x; }
    }
    return s;
  }
  function padHex(s, n) { s = String(s || "").toUpperCase(); while (s.length < n) s = "0" + s; return s; }
  function cn(o) { return o ? String(o.getClass().getName()) : ""; }
  function uoOf(node) { try { return node.getUserObjectNoWrapper(); } catch (e) { return node.getUserObject(); } }

  // BMW / community value name from a label: "wert_02 nicht_aktiv [default]" -> "nicht_aktiv"
  function valueName(label) {
    var m = /^\*?\s*wert_\d+\s+(.*?)(\s*\[[^\]]*\])?$/.exec(String(label || ""));
    return m ? m[1] : String(label || "").replace(/\s*\[[^\]]*\]$/, "");
  }
  function normName(s) { return String(s || "").toLowerCase().replace(/[\s_]+/g, "_").replace(/^_|_$/g, ""); }

  function findTree(comp) {
    if (!comp) return null;
    if (comp instanceof Sw.JTree) {
      try { var r = comp.getModel().getRoot(); if (r && /caf1\.Caf1$/.test(cn(uoOf(r)))) return comp; } catch (e) {}
    }
    if (comp instanceof Awt.Container) {
      var kids = comp.getComponents();
      for (var i = 0; i < kids.length; i++) { var t = findTree(kids[i]); if (t) return t; }
    }
    return null;
  }

  // Index the open CAFD: "ADDR|start|end|mask" -> function tree node.
  function indexCaf(tree) {
    var root = tree.getModel().getRoot();
    var caf = uoOf(root);
    var ident = caf.getSweHeader() ? caf.getSweHeader().getCafIdent() : null;
    var res = { caf: caf, cid: ident ? padHex(ident.getId(), 8) : "?", ver: ident ? padHex(ident.getMainVersion(), 3) + "_" +
                padHex(ident.getSubVersion(), 3) + "_" + padHex(ident.getPatchVersion(), 3) : "?",
                ecu: caf.getSweHeader() ? String(caf.getSweHeader().getEcuName()) : "", fns: {} };
    for (var i = 0; i < root.getChildCount(); i++) {
      var data = root.getChildAt(i);
      if (!/caf1\.TData$/.test(cn(uoOf(data)))) continue;
      for (var d = 0; d < data.getChildCount(); d++) {
        var dgn = data.getChildAt(d), dg = uoOf(dgn);
        if (!dg || !dg.getAddress) continue;
        var addr = String(dg.getAddress()).toUpperCase();
        for (var c = 0; c < dgn.getChildCount(); c++) {
          var list = dgn.getChildAt(c);
          for (var f = 0; f < list.getChildCount(); f++) {
            var fnode = list.getChildAt(f), fn = uoOf(fnode);
            if (!fn || !fn.getStorageInfo) continue;
            var si = fn.getStorageInfo();
            var st = si && si.getStart() ? si.getStart().intValue() : 0;
            var en = si && si.getEnd() ? si.getEnd().intValue() : st;
            var mk = si && si.getMask() && si.getMask().getValue() ? normMask(si.getMask().getValue()) : "11111111b";
            res.fns[addr + "|" + st + "|" + en + "|" + mk] = { node: fnode, fn: fn, addr: addr, group: String(dg.getName() || "") };
          }
        }
      }
    }
    return res;
  }

  // One cheat-sheet step against the open CAFD.
  // Cheat-sheet value as hex: "07", "0x07", or a byte list like "04, 52, 44" / "04 52 44" (whole records such as Lmm1).
  // Anything else (aktiv, nicht_aktiv) is a value name. Returns null for names.
  function sheetHex(v) {
    var t = String(v || "").trim();
    if (/^[0-9A-Fa-f]{1,2}([\s,;]+[0-9A-Fa-f]{1,2})+$/.test(t))
      return t.split(/[\s,;]+/).map(function (b) { return b.length < 2 ? "0" + b : b; }).join("").toUpperCase();
    t = t.replace(/^0x/i, "");
    return /^[0-9A-Fa-f]+$/.test(t) ? t.toUpperCase() : null;
  }
  // Long values (whole records) are shown shortened: "04 52 44 64 00 00 ... (252 bytes)"
  function showHex(hx) {
    hx = String(hx || "");
    if (hx.length <= 16) return hx;
    return hx.substring(0, 12).replace(/(..)/g, "$1 ") + "... (" + (hx.length / 2) + " bytes)";
  }

  function checkStep(ix, s) {
    var hit = ix.fns[s.addr + "|" + s.start + "|" + s.end + "|" + s.mask];
    var r = { step: s, hit: hit, cp: null, status: "nofunc", fname: "", current: "", target: s.value };
    if (!hit) return r;
    r.fname = String(hit.fn.getName() || "");
    var interp = hit.fn.getInterpretation();
    var curHex = interp ? tvalueHex(interp.getValues()) : "";
    r.current = showHex(curHex) + (interp && interp.getParamName() ? "  " + interp.getParamName() : "");
    var cps = hit.fn.getCondParam(), hexv = sheetHex(s.value), want = normName(s.value);
    for (var i = 0; cps && i < cps.size(); i++) {
      var cp = cps.get(i), hx = tvalueHex(cp.getValues()), pn = String(cp.getParamName() || "");
      if ((hexv !== null && padHex(hexv, hx.length) === hx) ||
          (hexv === null && (normName(valueName(pn)) === want || normName(pn) === want))) {
        r.cp = cp; r.cpHex = hx;
        break;
      }
    }
    if (r.cp) {
      r.target = showHex(r.cpHex) + "  " + r.cp.getParamName();
      r.status = r.cpHex === curHex ? "set" : "ok";
      return r;
    }
    if (hexv === null) { r.status = "noval"; return r; }
    // a raw value that isn't one of the function's werte: applied through E-Sys's raw value field (Werte=)
    var nBytes = s.end - s.start + 1;
    if (s.end > s.start && hexv.length !== nBytes * 2) {
      r.status = "noval";
      r.target = showHex(hexv) + "  (" + hexv.length / 2 + " bytes, the function has " + nBytes + ")";
      return r;
    }
    r.customHex = s.end > s.start ? hexv : padHex(hexv, Math.max(2, curHex.length || 2));
    r.target = showHex(r.customHex) + "  (custom value)";
    r.status = r.customHex === curHex ? "set" : "custom";
    return r;
  }

  function checkCode(ix, code) {
    var rs = code.steps.map(function (s) { return checkStep(ix, s); });
    var n = rs.length, set = 0, bad = 0, custom = 0;
    rs.forEach(function (r) {
      if (r.status === "set") set++;
      else if (r.status === "custom") custom++;
      else if (r.status !== "ok") bad++;
    });
    var st = bad === n ? "bad" : set === n ? "set" : bad > 0 ? "partial" : (custom > 0 && custom === n - set) ? "custom" : "ok";
    return { code: code, results: rs, status: st };
  }

  // ------------------------------------------------------------------ FDL editor access (E-Sys's own controller)
  function fdlController() {
    var ctl = h.cls("com.bmw.esys.gui.control.EsysController");
    var inst = h.field(null, ctl, "instance").get(null);
    var mc = inst.getCachedModules().get("FDLEditor");
    return mc ? mc.getFDLController() : null;
  }

  function goTo(tree, node) {
    var path = new Sw.tree.TreePath(node.getPath());
    tree.expandPath(path);
    tree.setSelectionPath(path);
    tree.scrollPathToVisible(path);
  }

  // Choose the step's value in the FDL editor: select the "Ausgelesen" value node and call FDLController.setValue with the
  // werte name, the same call E-Sys makes when the value is picked from its drop-down list.
  function applyStep(tree, r) {
    var fnode = r.hit.node, enumNode = null;
    for (var i = 0; i < fnode.getChildCount() && !enumNode; i++) {
      var c = fnode.getChildAt(i);
      if (!/TInterpretations$/.test(cn(uoOf(c)))) continue;
      for (var j = 0; j < c.getChildCount(); j++) {
        var w = c.getChildAt(j).getUserObject();
        if (/AttributeWrapper$/.test(cn(w)) && /SimpleEnumerationFormat$/.test(cn(w.getFormat()))) { enumNode = c.getChildAt(j); break; }
      }
    }
    if (!enumNode) throw "no value selector for " + r.fname;
    var name = String(r.cp.getParamName());
    var cps = r.hit.fn.getCondParam();
    for (var k = 0; k < cps.size(); k++)
      if (cps.get(k) !== r.cp && String(cps.get(k).getParamName()) === name && tvalueHex(cps.get(k).getValues()) !== r.cpHex)
        throw "two values of " + r.fname + " share the name '" + name + "'; pick it by hand";
    var path = new Sw.tree.TreePath(enumNode.getPath());
    tree.expandPath(path.getParentPath());
    tree.setSelectionPath(path);
    fdlController().setValue(enumNode.getUserObject(), name);
    tree.getModel().nodeChanged(enumNode);
    tree.scrollPathToVisible(path);
  }

  // Custom value: set the "Ausgelesen" raw value (Werte=) with a TValue parsed by E-Sys's own TValueFormat, through
  // FDLController.setValue like an edit by hand. The parsed value is checked byte for byte before it's set.
  function applyCustom(tree, r) {
    var fnode = r.hit.node, wrapNode = null;
    for (var i = 0; i < fnode.getChildCount() && !wrapNode; i++) {
      var c = fnode.getChildAt(i);
      if (!/TInterpretations$/.test(cn(uoOf(c)))) continue;
      for (var j = 0; j < c.getChildCount(); j++) {
        var w = c.getChildAt(j).getUserObject();
        if (/AttributeWrapper$/.test(cn(w)) && /TValueFormat$/.test(cn(w.getFormat()))) { wrapNode = c.getChildAt(j); break; }
      }
    }
    if (!wrapNode) throw "no raw value field for " + r.fname;
    var wrapper = wrapNode.getUserObject(), fmt = wrapper.getFormat();
    // write the value the way this format writes values (same separator between bytes)
    var sample = String(fmt.format(wrapper.getValue()) || "");
    var sepM = /[0-9A-Fa-f]{2}([^0-9A-Fa-f]+)[0-9A-Fa-f]{2}/.exec(sample);
    var sep = sepM ? sepM[1] : "";
    var bytes = r.customHex.match(/../g);
    // FDLController.setValue only takes text (what you'd type); E-Sys parses it with this same format
    var text = null, tried = [bytes.join(sep), bytes.join(" "), bytes.join(""), bytes.join(", ")];
    for (var t = 0; t < tried.length && text === null; t++) {
      try { if (tvalueHex(fmt.parseObject(tried[t])) === r.customHex) text = tried[t]; } catch (eP) {}
    }
    if (text === null) throw "E-Sys did not accept the value for " + r.fname;
    var path = new Sw.tree.TreePath(wrapNode.getPath());
    tree.expandPath(path.getParentPath());
    tree.setSelectionPath(path);
    fdlController().setValue(wrapper, text);
    if (tvalueHex(r.hit.fn.getInterpretation().getValues()) !== r.customHex) throw "E-Sys did not take the value for " + r.fname;
    var parent = wrapNode.getParent();
    for (var k = 0; k < parent.getChildCount(); k++) tree.getModel().nodeChanged(parent.getChildAt(k));
    tree.getModel().nodeChanged(parent);
    tree.scrollPathToVisible(path);
  }

  // ------------------------------------------------------------------ the pane
  var ui = null;

  function buildPane() {
    var p = new Sw.JPanel(new Awt.BorderLayout(0, 4));
    p.setBorder(Sw.BorderFactory.createEmptyBorder(4, 4, 4, 4));
    p.setMinimumSize(new Awt.Dimension(320, 100));

    var head = new Sw.JPanel(new Awt.GridBagLayout());
    head.setBorder(Sw.BorderFactory.createTitledBorder("Cheat sheets (Open CAFD Map)"));
    var GBC = Awt.GridBagConstraints;
    function gbc(x, y, wx, w) {
      var c = new GBC(); c.gridx = x; c.gridy = y; c.weightx = wx; c.gridwidth = w || 1;
      c.fill = wx ? GBC.HORIZONTAL : GBC.NONE; c.anchor = GBC.WEST; c.insets = new Awt.Insets(2, 4, 2, 4); return c;
    }
    var info = new Sw.JLabel("Load an NCD (or CAF) in the FDL editor on the left.");
    var show = new Sw.JComboBox(Java.to(["Compatible", "All for this CAFD"], "java.lang.Object[]"));
    var sheetBox = new Sw.JComboBox();
    var search = new Sw.JTextField();
    var refresh = new Sw.JButton("Refresh");
    head.add(info, gbc(0, 0, 1, 4));
    head.add(new Sw.JLabel("Show:"), gbc(0, 1, 0));
    head.add(show, gbc(1, 1, 0));
    head.add(new Sw.JLabel("Sheet:"), gbc(2, 1, 0));
    head.add(sheetBox, gbc(3, 1, 1));
    head.add(new Sw.JLabel("Search:"), gbc(0, 2, 0));
    head.add(search, gbc(1, 2, 1, 2));
    head.add(refresh, gbc(3, 2, 0));
    p.add(head, Awt.BorderLayout.NORTH);

    var state = { ix: null, tree: null, codes: [], view: [], sel: null };
    var codeCols = ["Status", "Description", "Sheet"];
    var codeModel = new Sw.table.AbstractTableModel({
      getRowCount: function () { return state.view.length; },
      getColumnCount: function () { return 3; },
      getColumnName: function (c) { return codeCols[c]; },
      getValueAt: function (r, c) {
        var x = state.view[r];
        return c === 0 ? STATUS[x.status].text : c === 1 ? x.code.desc : x.code.sheet;
      }
    });
    var stepCols = ["Status", "Group", "Function", "Byte / mask", "Current", "Target", "Sheet comment"];
    var stepModel = new Sw.table.AbstractTableModel({
      getRowCount: function () { return state.sel ? state.sel.results.length : 0; },
      getColumnCount: function () { return stepCols.length; },
      getColumnName: function (c) { return stepCols[c]; },
      getValueAt: function (r, c) {
        var x = state.sel.results[r], s = x.step;
        switch (c) {
          case 0: return STATUS[x.status].text;
          case 1: return s.addr + (x.hit && x.hit.group ? "  " + x.hit.group : "");
          case 2: return x.fname || "-";
          case 3: return s.start + (s.end > s.start ? "-" + s.end : "") + "  " + s.mask;
          case 4: return x.current;
          case 5: return x.target;
          default: return s.comment;
        }
      }
    });
    function statusRenderer(getStatus) {
      var base = new Sw.table.DefaultTableCellRenderer();
      return new Sw.table.TableCellRenderer({
        getTableCellRendererComponent: function (t, v, sel, foc, row, col) {
          var c = base.getTableCellRendererComponent(t, v, sel, foc, row, col);
          if (!sel) c.setForeground(col === 0 ? STATUS[getStatus(row)].color : t.getForeground());
          return c;
        }
      });
    }
    var codeTable = new Sw.JTable(codeModel);
    codeTable.setDefaultRenderer(java.lang.Object.class, statusRenderer(function (r) { return state.view[r].status; }));
    codeTable.setSelectionMode(Sw.ListSelectionModel.SINGLE_SELECTION);
    codeTable.getColumnModel().getColumn(0).setPreferredWidth(130);
    codeTable.getColumnModel().getColumn(1).setPreferredWidth(330);
    codeTable.getColumnModel().getColumn(2).setPreferredWidth(110);
    var stepTable = new Sw.JTable(stepModel);
    stepTable.setDefaultRenderer(java.lang.Object.class, statusRenderer(function (r) { return state.sel.results[r].status; }));
    stepTable.setSelectionMode(Sw.ListSelectionModel.SINGLE_SELECTION);
    var detail = new Sw.JLabel(" ");
    var bGo = new Sw.JButton("Go to"), bApply = new Sw.JButton("Apply"), status = new Sw.JLabel(" ");
    bGo.setToolTipText("Select this function in the FDL tree (the selected step, or the first one)");
    bApply.setToolTipText("Pick these values in the FDL editor (only the open NCD changes; save and code in E-Sys as usual)");
    var actions = new Sw.JPanel(new Awt.FlowLayout(Awt.FlowLayout.RIGHT, 4, 0));
    actions.add(bGo); actions.add(bApply);
    var stepHead = new Sw.JPanel(new Awt.BorderLayout(4, 2));
    stepHead.add(detail, Awt.BorderLayout.CENTER);
    stepHead.add(actions, Awt.BorderLayout.EAST);
    stepHead.add(status, Awt.BorderLayout.SOUTH);
    var stepPanel = new Sw.JPanel(new Awt.BorderLayout());
    stepPanel.add(stepHead, Awt.BorderLayout.NORTH);
    stepPanel.add(new Sw.JScrollPane(stepTable), Awt.BorderLayout.CENTER);
    var vsplit = new Sw.JSplitPane(Sw.JSplitPane.VERTICAL_SPLIT, new Sw.JScrollPane(codeTable), stepPanel);
    vsplit.setResizeWeight(0.6);
    p.add(vsplit, Awt.BorderLayout.CENTER);


    function filterView() {
      var mode = show.getSelectedIndex(), sh = sheetBox.getSelectedItem(), q = String(search.getText() || "").toLowerCase();
      state.view = state.codes.filter(function (x) {
        if (mode === 0 && x.status === "bad") return false;
        if (sh && String(sh) !== "All sheets" && x.code.sheet !== String(sh)) return false;
        if (q && (x.code.desc + " " + x.code.sheet + " " + x.results.map(function (r) { return r.fname + " " + r.step.comment; }).join(" ")).toLowerCase().indexOf(q) < 0) return false;
        return true;
      });
      codeModel.fireTableDataChanged();
      state.sel = null; stepModel.fireTableDataChanged(); detail.setText(" ");
    }
    function recheck(keepSel) {
      if (!state.ix) return;
      var oldDesc = keepSel && state.sel ? state.sel.code : null;
      state.ix = indexCaf(state.tree);
      state.codes = (sheetsByCafd[state.ix.cid] || []).map(function (code) { return checkCode(state.ix, code); });
      var order = { ok: 0, custom: 1, partial: 2, set: 3, bad: 4 };
      state.codes.sort(function (a, b) { return (order[a.status] - order[b.status]) || (a.code.desc < b.code.desc ? -1 : 1); });
      var n = state.codes.filter(function (x) { return x.status !== "bad"; }).length;
      info.setText("<html><b>" + state.ix.ecu + "  CAFD_" + state.ix.cid + "_" + state.ix.ver + "</b>: " + state.codes.length +
                   " cheat sheet entries, " + n + " fit this version</html>");
      var sheets = ["All sheets"];
      state.codes.forEach(function (x) { if (sheets.indexOf(x.code.sheet) < 0) sheets.push(x.code.sheet); });
      var cur = sheetBox.getSelectedItem();
      sheetBox.setModel(new Sw.DefaultComboBoxModel(Java.to(sheets, "java.lang.Object[]")));
      if (cur && sheets.indexOf(String(cur)) >= 0) sheetBox.setSelectedItem(cur);
      filterView();
      if (oldDesc) for (var i = 0; i < state.view.length; i++) if (state.view[i].code === oldDesc) { codeTable.setRowSelectionInterval(i, i); break; }
    }
    function load(tree) {
      state.tree = tree;
      status.setText(" ");
      state.ix = tree ? indexCaf(tree) : null;
      if (!state.ix) { info.setText("Load an NCD (or CAF) in the FDL editor on the left."); state.codes = []; filterView(); return; }
      if (sheetsByCafd === null) loadSheets();
      recheck(false);
    }
    codeTable.getSelectionModel().addListSelectionListener(function (e) {
      if (e.getValueIsAdjusting()) return;
      var i = codeTable.getSelectedRow();
      state.sel = i >= 0 ? state.view[i] : null;
      stepModel.fireTableDataChanged();
      detail.setText(state.sel ? "<html><b>" + state.sel.code.desc + "</b>  (" + state.sel.code.sheet + ", " + state.sel.code.kind + ")</html>" : " ");
    });
    show.addActionListener(function () { filterView(); });
    sheetBox.addActionListener(function () { filterView(); });
    search.addActionListener(function () { filterView(); });
    refresh.addActionListener(function () { if (sheetsByCafd !== null) loadSheets(); ui.reload(); });
    bGo.addActionListener(function () {
      if (!state.sel) { status.setText("Pick an entry first."); return; }
      var i = Math.max(0, stepTable.getSelectedRow()), r = state.sel.results[i];
      if (!r || !r.hit) { status.setText("That function isn't in this CAFD version."); return; }
      goTo(state.tree, r.hit.node);
      status.setText(" ");
    });
    bApply.addActionListener(function () {
      if (!state.sel) { status.setText("Pick an entry first."); return; }
      var done = 0, skipped = [], errs = [];
      state.sel.results.forEach(function (r) {
        if (r.status === "ok" || r.status === "custom") {
          try { if (r.status === "ok") applyStep(state.tree, r); else applyCustom(state.tree, r); done++; }
          catch (e) { errs.push(r.fname + ": " + e); }
        } else if (r.status !== "set") skipped.push((r.fname || r.step.addr + "/" + r.step.start) + " (" + STATUS[r.status].text + ")");
      });
      log("Cheat Sheet: applied '" + state.sel.code.desc + "' (" + state.sel.code.sheet + "): " + done + " value(s)" +
          (skipped.length ? ", skipped " + skipped.join(", ") : "") + (errs.length ? ", errors " + errs.join("; ") : ""));
      recheck(true);
      status.setText(done + " value(s) set in the FDL editor" + (skipped.length ? ", " + skipped.length + " skipped" : "") +
                     (errs.length ? ", " + errs.join("; ") : "") + (done ? ". Save and code in E-Sys as usual." : ""));
    });
    return { panel: p, load: load, recheck: recheck, state: state };
  }

  // ------------------------------------------------------------------ window = FDL Editor + pane
  var split = null, lastCaf = null;

  function open() {
    var ctl = Java.type("com.bmw.esys.gui.control.EsysController");
    var inst = h.field(null, h.cls("com.bmw.esys.gui.control.EsysController"), "instance").get(null);
    if (String(inst.getCurrentModule()) !== "FDLEditor") {
      ctl.openModule("FDLEditor", "module.section.editors", new java.util.HashMap());   // E-Sys's own FDL Editor module
    }
    var frame = h.esysFrame();
    var curField = h.field(null, h.cls("com.bmw.esys.gui.control.EsysFrame"), "currentView");
    var view = curField.get(frame);
    if (!ui) {
      var pane = buildPane();
      ui = { pane: pane, reload: function () { lastCaf = null; } };
    }
    if (!split) {
      split = new Sw.JSplitPane(Sw.JSplitPane.HORIZONTAL_SPLIT);
      split.setResizeWeight(0.6);
      split.setRightComponent(ui.pane.panel);
      split.putClientProperty("ocmCheatSheet", java.lang.Boolean.TRUE);
    }
    if (view !== split) {
      // a Cheat Sheet split from an earlier agent instance (hot reload) is unwrapped: keep only E-Sys's FDL view
      var inner = (view instanceof Sw.JSplitPane && view.getClientProperty("ocmCheatSheet")) ? view.getLeftComponent() : view;
      Java.type("com.bmw.esys.gui.AbstractView").replace(view, split);
      split.setLeftComponent(inner);
      inner.setVisible(true);
      curField.set(frame, split);
      split.setDividerLocation(0.6);
    }
    split.setVisible(true);   // E-Sys hides the view it switches away from (EsysFrame.setCurrentViewVisible)
    h.selectOurBar();
    lastCaf = null;
    var w = Sw.SwingUtilities.getWindowAncestor(split);
    if (w) { w.validate(); w.repaint(); }
  }

  // While the window is showing: follow what the FDL editor has open. A newly opened CAFD is checked one tick later (after
  // the agent has labelled it, so value names like "aktiv" resolve) and once more ~5 s later (labels from a definition
  // that was built on demand).
  var pending = null, recheckAt = 0, ticks = 0;
  function tick() {
    ticks++;
    if (!split || !split.isShowing() || !ui) return;
    var tree = findTree(split.getLeftComponent());
    var caf = tree ? uoOf(tree.getModel().getRoot()) : null;
    if (caf !== lastCaf) {
      if (pending !== caf) { pending = caf; return; }
      lastCaf = caf; pending = null; recheckAt = ticks + 5;
      try { ui.pane.load(tree); } catch (e) { log("Cheat Sheet: " + e); }
      return;
    }
    if (recheckAt && ticks >= recheckAt) {
      recheckAt = 0;
      try { ui.pane.recheck(true); } catch (e2) {}
    }
  }

  return { open: open, tick: tick };
});
