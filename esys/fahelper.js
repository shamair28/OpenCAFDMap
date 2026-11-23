// FA / VO Helper (OpenLauncher section in E-Sys): edit a vehicle order (FA) with option, HO-word and E-word lists, checked
// as you go against Open CAFD Map's shipped tables (data\fa\) and E-Sys's own PsdZ:
//   - the type key exists (tables; PsdZ for your psdzdata);
//   - each option is offered for the type key and the build date (ISTA-based lists, or KIS class lists and windows);
//   - no two options from the same class (one transmission, one HiFi, ...);
//   - Check CAFDs: the target SVT PsdZ calculates for the FA, and which of its CAFDs your psdzdata doesn't have.
// Apply hands the edited FA to E-Sys's FA editor (E-Sys marks it changed); saving it and writing it to the car stay in
// E-Sys, as always. Nothing here writes to the car. Loaded by tools.js.
(function (h) {
  var System = java.lang.System, File = java.io.File, Sw = javax.swing, Awt = java.awt;
  var log = h.log, cls = h.cls;
  var DATA = new File(h.dataDir, "fa");
  var FA_DIR = h.esysDataDir ? new File(h.esysDataDir, "FA") : null;
  var RED = new Awt.Color(0xB4, 0x23, 0x18), ORANGE = new Awt.Color(0xB5, 0x5A, 0x00), GREEN = new Awt.Color(0x0B, 0x7A, 0x3E),
      GREY = new Awt.Color(0x80, 0x80, 0x88), BLUE = new Awt.Color(0x2A, 0x5D, 0xA8);
  var FLAG = { S: "standard", O: "optional", T: "with type key", C: "allowed (class)", V: "available" };

  // ------------------------------------------------------------------ shipped tables
  function gzReader(name) {
    return new java.io.BufferedReader(new java.io.InputStreamReader(
      new java.util.zip.GZIPInputStream(new java.io.FileInputStream(new File(DATA, name)), 65536), "UTF-8"), 65536);
  }
  var D = null;
  function tables() {
    if (D) return D;
    var d = { types: {}, codes: {}, series: {}, lists: {} }, r, line;
    r = gzReader("typekeys.tsv.gz"); r.readLine();
    while ((line = r.readLine()) !== null) {
      var f = String(line).split("\t");
      d.types[f[0]] = { tk: f[0], main: f[1] ? f[1].split(",") : [], series: f[2], model: f[3], body: f[4], market: f[5],
                        drive: f[6], engine: f[7], group: f[8], list: f[9], sources: f[10] || "" };
    }
    r.close();
    r = gzReader("codes.tsv.gz"); r.readLine();
    while ((line = r.readLine()) !== null) {
      var c = String(line).split("\t");
      d.codes[c[0]] = { kind: c[1], title: c[2] || "", classes: (c[3] ? c[3].split(",") : []).map(function (x) {
        var p = x.split(":"); return { group: p[0], cls: p[1], strict: p[2] !== "soft" };
      }) };
    }
    r.close();
    r = gzReader("series.tsv.gz"); r.readLine();
    while ((line = r.readLine()) !== null) {
      var s = String(line).split("\t");
      (d.series[s[0]] = d.series[s[0]] || {})[s[1]] = s[2] ? s[2].split(" ") : [];
    }
    r.close();
    D = d;
    log("FA Helper: tables loaded (" + Object.keys(d.types).length + " type keys, " + Object.keys(d.codes).length + " codes)");
    return d;
  }
  // option list of a type key: code -> [{flag, from, to}] (lists are stored in id order: stop after the id)
  function optionList(id) {
    var d = tables();
    if (!id) return null;
    if (d.lists[id]) return d.lists[id];
    var out = {}, want = parseInt(id, 10), r = gzReader("options.tsv.gz"), line;
    r.readLine();
    while ((line = r.readLine()) !== null) {
      var s = String(line), tab = s.indexOf("\t"), n = parseInt(s.substring(0, tab), 10);
      if (n < want) continue;
      if (n > want) break;
      var f = s.split("\t");
      (out[f[1]] = out[f[1]] || []).push({ flag: f[2], from: f[3], to: f[4] });
    }
    r.close();
    return (d.lists[id] = out);
  }
  function describe(t) {
    if (!t) return "";
    return [t.series, t.model, t.body, t.market, t.drive, t.engine].filter(function (x) { return x; }).join(" · ");
  }
  function ymOf(tc) {          // FA timeCriteria MMYY -> YYYYMM
    var m = /^(\d\d)(\d\d)$/.exec(String(tc || ""));
    return m ? (parseInt(m[2], 10) < 80 ? "20" : "19") + m[2] + m[1] : "";
  }
  function fmtYm(s) { return s ? s.substring(4, 6) + "/" + s.substring(0, 4) : "…"; }
  function inWindow(row, ym) { return !ym || ((!row.from || row.from <= ym) && (!row.to || ym <= row.to)); }

  // ------------------------------------------------------------------ checks (tables only; fast, run on every change)
  // st = { series, tk, ym, sa:{}, ho:{}, e:{} } -> { items: [{level, text}], code: {CODE: {level, note}} }
  function check(st) {
    var d = tables(), items = [], per = {};
    function mark(code, level, note) {
      var cur = per[code];
      var rank = { bad: 3, warn: 2, info: 1 };
      if (!cur || rank[level] > rank[cur.level]) per[code] = { level: level, note: note };
    }
    var t = d.types[st.tk];
    if (!st.tk) items.push({ level: "warn", text: "No type key in the FA." });
    else if (!t) items.push({ level: "bad", text: "Type key " + st.tk + " isn't in Open CAFD Map's tables (" +
                                             "not a known BMW type key, or newer than the tables: Check for updates)." });
    else {
      items.push({ level: "ok", text: "Type key " + st.tk + ": " + describe(t) + (t.main.length ? "  (main series " + t.main.join(", ") + ")" : "") });
      if (st.series && t.main.length && t.main.indexOf(st.series) < 0)
        items.push({ level: "warn", text: "The FA says main series " + st.series + ", but type key " + st.tk + " belongs to " + t.main.join(", ") + "." });
    }
    if (!st.ym) items.push({ level: "warn", text: "No build date (time criteria, MMYY) in the FA." });

    var opts = t ? optionList(t.list) : null, ista = t && t.sources.indexOf("I") >= 0;
    // build date against the type key's production run (its standard options' windows)
    if (opts && ista && st.ym) {
      var first = "", last = "";
      Object.keys(opts).forEach(function (o) {
        opts[o].forEach(function (r) {
          if (r.flag !== "S") return;
          if (r.from && (!first || r.from < first)) first = r.from;
          if (r.to && (!last || r.to > last)) last = r.to;
        });
      });
      if ((first && st.ym < first) || (last && st.ym > last))
        items.push({ level: "warn", text: "Build date " + fmtYm(st.ym) + " is outside " + st.tk + "'s production (" + fmtYm(first) + "–" + fmtYm(last) + ")." });
    }
    var selected = Object.keys(st.sa);
    // validity per option
    selected.forEach(function (code) {
      var c = d.codes[code], rows = opts ? opts[code] : null;
      var knownSeries = st.series && d.series[st.series] && (d.series[st.series].SA || []).indexOf(code) >= 0;
      if (!c && !knownSeries && !rows) { mark(code, "bad", "unknown option code"); return; }
      if (opts && ista) {
        if (!rows) { mark(code, "bad", "not offered for " + st.tk); return; }
        if (st.ym && !rows.some(function (r) { return inWindow(r, st.ym); })) {
          mark(code, "warn", "not available in " + fmtYm(st.ym) + " (" + rows.map(function (r) { return fmtYm(r.from) + "–" + fmtYm(r.to); }).join(", ") + ")");
        }
      } else if (opts) {
        // KIS lists: class lists say which options of a class the type key allows; windows say when
        var group = t.group;
        (c ? c.classes : []).forEach(function (k) {
          if (k.group !== group) return;
          var allowed = Object.keys(opts).filter(function (o) {
            return (opts[o] || []).some(function (r) { return r.flag === "C"; }) &&
                   d.codes[o] && d.codes[o].classes.some(function (x) { return x.group === group && x.cls === k.cls; });
          });
          if (allowed.length && allowed.indexOf(code) < 0) mark(code, "bad", "not allowed for " + st.tk + " (" + k.cls + ": " + allowed.join(", ") + ")");
        });
        var win = (rows || []).filter(function (r) { return r.flag === "V"; });
        if (st.ym && win.length && !win.some(function (r) { return inWindow(r, st.ym); }))
          mark(code, "warn", "not available in " + fmtYm(st.ym));
      }
    });
    // conflicts: two options from one class (of this type key's KIS group)
    var byClass = {};
    selected.forEach(function (code) {
      var c = d.codes[code];
      (c ? c.classes : []).forEach(function (k) {
        if (t && t.group && k.group !== t.group) return;
        var key = k.cls;
        (byClass[key] = byClass[key] || { strict: k.strict, codes: [] });
        if (byClass[key].codes.indexOf(code) < 0) byClass[key].codes.push(code);
        byClass[key].strict = byClass[key].strict && k.strict;
      });
    });
    Object.keys(byClass).forEach(function (k) {
      var b = byClass[k];
      if (b.codes.length < 2) return;
      var names = b.codes.map(function (x) { return x + (d.codes[x] && d.codes[x].title ? " (" + d.codes[x].title + ")" : ""); });
      items.push({ level: b.strict ? "bad" : "warn", text: "Conflict: only one " + k + " option at a time, but the FA has " + names.join(" and ") + "." });
      b.codes.forEach(function (x) { mark(x, b.strict ? "bad" : "warn", "conflicts with " + b.codes.filter(function (y) { return y !== x; }).join(", ")); });
    });
    // standard options of this type key missing from the FA
    if (opts && ista) {
      var missing = Object.keys(opts).filter(function (o) {
        return !st.sa[o] && opts[o].some(function (r) { return r.flag === "S" && inWindow(r, st.ym); });
      });
      if (missing.length) items.push({ level: "info", text: "Standard for " + st.tk + " but not in the FA: " + missing.join(" ") + "." });
    } else if (t && !ista) {
      items.push({ level: "info", text: "Only KIS class lists for " + st.tk + ": options outside those classes can't be checked against the type key." });
    }
    // HO / E words this main series doesn't know
    ["ho", "e"].forEach(function (kind) {
      var list = st.series && d.series[st.series] ? d.series[st.series][kind === "ho" ? "HO" : "E"] || [] : [];
      Object.keys(st[kind]).forEach(function (w) {
        if (list.length && list.indexOf(w) < 0 && !d.codes[w]) mark(w, "warn", "not a known " + (kind === "ho" ? "HO" : "E") + "-word for " + st.series);
      });
    });
    var bad = Object.keys(per).filter(function (k) { return per[k].level === "bad"; }).length;
    var warn = Object.keys(per).filter(function (k) { return per[k].level === "warn"; }).length;
    if (bad || warn) items.splice(1, 0, { level: bad ? "bad" : "warn", text: bad + " problem(s), " + warn + " warning(s) in the codes (marked in the lists)." });
    else if (t) items.splice(1, 0, { level: "ok", text: "All codes fit " + st.tk + (st.ym ? " in " + fmtYm(st.ym) : "") + "." });
    return { items: items, code: per };
  }

  // ------------------------------------------------------------------ E-Sys: the FA it has open, PsdZ
  function getField(obj, clazz, name) {
    var c = clazz;
    while (c) {
      try { var f = c.getDeclaredField(name); f.setAccessible(true); return f.get(obj); } catch (e) { c = c.getSuperclass(); }
    }
    return null;
  }
  var FalLogic = cls("com.bmw.esys.domain.fa.FalLogic");
  var fileHandling = new java.util.IdentityHashMap();   // FalLogic -> its editor's DefaultFileHandlingController
  var moduleOf = new java.util.IdentityHashMap();       // FalLogic -> E-Sys module it belongs to (Coding, FA editor, ...)
  function moduleName(lg) {
    var m = String(moduleOf.get(lg) || "");
    return /coding/i.test(m) ? "Coding" : /fa|fal/i.test(m) ? "FA editor" : (m || "E-Sys");
  }
  // every FalLogic E-Sys's modules hold (Coding's vehicle order, the FA editor), with a document loaded first
  function faLogics() {
    var out = [], seen = new java.util.IdentityHashMap(), Tree = cls("com.bmw.esys.gui.AbstractTreeController");
    var inst = getField(null, cls("com.bmw.esys.gui.control.EsysController"), "instance");
    var mods = inst ? getField(inst, cls("com.bmw.esys.gui.control.EsysController"), "cacheModules") : null;
    if (!mods || !FalLogic || !Tree) return out;
    var queue = [], it = mods.entrySet().iterator();
    while (it.hasNext()) { var me = it.next(); queue.push({ o: me.getValue(), d: 0, mod: String(me.getKey()) }); }
    while (queue.length) {
      var e = queue.shift(), o = e.o;
      if (!o || seen.containsKey(o)) continue;
      seen.put(o, true);
      if (Tree.isInstance(o)) {
        var lg = null;
        try { lg = o.getLogic(); } catch (eL) {}
        if (lg && FalLogic.isInstance(lg) && out.indexOf(lg) < 0) {
          out.push(lg);
          var fh = getField(o, Tree, "fileHandling");      // E-Sys's file handling for that editor (shows the model)
          if (fh) fileHandling.put(lg, fh);
          moduleOf.put(lg, e.mod);
        }
      }
      if (e.d >= 3) continue;
      var c = o.getClass();
      while (c && String(c.getName()).indexOf("com.bmw.esys.gui") === 0) {
        var fs = c.getDeclaredFields();
        for (var i = 0; i < fs.length; i++) {
          if (fs[i].getType().isPrimitive() || java.lang.reflect.Modifier.isStatic(fs[i].getModifiers())) continue;
          try { fs[i].setAccessible(true); var v = fs[i].get(o); if (v && String(v.getClass().getName()).indexOf("com.bmw.esys.gui") === 0) queue.push({ o: v, d: e.d + 1, mod: e.mod }); } catch (eF) {}
        }
        c = c.getSuperclass();
      }
    }
    out.sort(function (a, b) { return (b.getModel() ? 1 : 0) - (a.getModel() ? 1 : 0); });
    return out;
  }
  function activeId(logic) {
    try { var id = logic.getActivatedFa(); if (id) return id; } catch (e) {}
    try { var m = logic.getModel(); return m && m.getId().size() ? m.getId().get(0) : null; } catch (e2) { return null; }
  }
  function stateOf(id) {
    var sfa = id && id.getFa() ? id.getFa().getStandardFA() : null;
    var st = { series: "", tk: "", ym: "", tc: "", sa: {}, ho: {}, e: {} };
    if (!sfa) return st;
    st.series = String(sfa.getSeries() || ""); st.tk = String(sfa.getTypeKey() || ""); st.tc = String(sfa.getTimeCriteria() || "");
    st.ym = ymOf(st.tc);
    function put(list, into) { if (list) for (var i = 0; i < list.size(); i++) into[String(list.get(i)).trim()] = true; }
    if (sfa.getSaCodes()) put(sfa.getSaCodes().getSaCode(), st.sa);
    if (sfa.getHoCodes()) put(sfa.getHoCodes().getHoCode(), st.ho);
    if (sfa.getECodes()) put(sfa.getECodes().getECode(), st.e);
    return st;
  }
  // copy of an FA (TFaList) through JAXB, so E-Sys's own document is only replaced on Apply
  var TFaList = cls("com.bmw.esys.domain.jaxb.fal.TFaList");
  // The JAXB API that matches a model class: javax.xml.bind up to E-Sys 25.x, jakarta.xml.bind from E-Sys 26.04 on
  // (its domain classes carry jakarta annotations; the wrong API reads and writes them empty).
  function jaxbFor(cls) {
    var anns = cls.getAnnotations();
    for (var i = 0; i < anns.length; i++) {
      if (String(anns[i].annotationType().getName()).indexOf("jakarta.") === 0) return Packages.jakarta.xml.bind;
    }
    return Packages.javax.xml.bind;
  }
  function JB() { return jaxbFor(TFaList); }
  function copyList(list) {
    var ctx = JB().JAXBContext.newInstance(Java.to([TFaList], "java.lang.Class[]"));
    var bos = new java.io.ByteArrayOutputStream(), qn = new javax.xml.namespace.QName("", "faList");
    ctx.createMarshaller().marshal(new JB().JAXBElement(qn, TFaList, list), bos);
    return ctx.createUnmarshaller().unmarshal(new javax.xml.transform.stream.StreamSource(new java.io.ByteArrayInputStream(bos.toByteArray())), TFaList).getValue();
  }
  // write st into an FA id (in place)
  function applyState(id, st) {
    var sfa = id.getFa().getStandardFA();
    sfa.setTypeKey(st.tk);
    if (st.tc) sfa.setTimeCriteria(st.tc);
    function setList(getter, holderCls, setHolder, listGetter, codes) {
      var holder = getter();
      if (!holder) { holder = new (Java.type(holderCls))(); setHolder(holder); }
      var l = listGetter(holder);
      l.clear();
      Object.keys(codes).sort().forEach(function (c) { l.add(c); });
    }
    setList(function () { return sfa.getSaCodes(); }, "com.bmw.esys.domain.jaxb.fal.TStandardFA$SaCodes",
            function (x) { sfa.setSaCodes(x); }, function (x) { return x.getSaCode(); }, st.sa);
    setList(function () { return sfa.getHoCodes(); }, "com.bmw.esys.domain.jaxb.fal.TStandardFA$HoCodes",
            function (x) { sfa.setHoCodes(x); }, function (x) { return x.getHoCode(); }, st.ho);
    setList(function () { return sfa.getECodes(); }, "com.bmw.esys.domain.jaxb.fal.TStandardFA$ECodes",
            function (x) { sfa.setECodes(x); }, function (x) { return x.getECode(); }, st.e);
  }

  var cafdIndex = null;   // psdzdata CAFDs: id -> [versions]
  function cafds() {
    if (cafdIndex) return cafdIndex;
    var idx = {}, root = Java.type("com.bmw.esys.domain.psdz.PsdzConfiguration").getInstance().getRoot();
    var names = new File(root, "psdzdata\\swe\\cafd").list() || [];
    for (var i = 0; i < names.length; i++) {
      var m = /^cafd_([0-9a-f]{8})\.caf\.(\d{3})_(\d{3})_(\d{3})/i.exec(String(names[i]));
      if (m) (idx[m[1].toLowerCase()] = idx[m[1].toLowerCase()] || []).push(m[2] + "_" + m[3] + "_" + m[4]);
    }
    return (cafdIndex = idx);
  }
  function pad3(v) { var s = String(v); while (s.length < 3) s = "0" + s; return s; }
  // target SVT for an FA id at an I-step: ECU:cafd -> {ecu, id, ver}
  function targetCafds(id, iStep) {
    var pl = Java.type("com.bmw.esys.domain.psdz.PsdzLogic").getInstance(), out = {};
    var svt = pl.generateSvtTarget(id, iStep);
    Java.from(svt.getStandardSVT().getEcu()).forEach(function (e) {
      var svk = e.getStandardSVK();
      (svk ? Java.from(svk.getPartIdentification()) : []).forEach(function (p) {
        if (!/CAFD/i.test(String(p.getProcessClass()))) return;
        var cid = String(p.getId()).toLowerCase();
        out[e.getBaseVariant() + ":" + cid] = { ecu: String(e.getBaseVariant()), id: cid,
          ver: pad3(p.getMainVersion()) + "_" + pad3(p.getSubVersion()) + "_" + pad3(p.getPatchVersion()) };
      });
    });
    return out;
  }
  function possibleISteps(id) {
    var pl = Java.type("com.bmw.esys.domain.psdz.PsdzLogic").getInstance();
    var psdzFa = Java.type("com.bmw.esys.domain.fa.FalLogic").getModelAsPsdz(id);   // static: needs the type, not the Class
    return Java.from(pl.getPossibleIStufe(psdzFa)).map(String);
  }

  // ------------------------------------------------------------------ the view
  function build() {
    var root = new Sw.JPanel(new Awt.BorderLayout(0, 6));
    root.setBorder(Sw.BorderFactory.createEmptyBorder(4, 4, 4, 4));
    var GBC = Awt.GridBagConstraints;
    function gbc(x, y, wx, fill, w) {
      var c = new GBC(); c.gridx = x; c.gridy = y; c.weightx = wx; c.fill = fill || GBC.NONE; c.gridwidth = w || 1;
      c.insets = new Awt.Insets(2, 4, 2, 4); c.anchor = GBC.WEST; return c;
    }
    var S = { logic: null, list: null, id: null, base: null, st: null, result: null, onlyThis: true, iSteps: [] };

    // --- top: source, type key, build date, I-step
    var top = new Sw.JPanel(new Awt.GridBagLayout());
    top.setBorder(Sw.BorderFactory.createTitledBorder("FA / VO Helper (Open CAFD Map)"));
    var src = new Sw.JLabel("No FA yet: read E-Sys's FA (Coding > Vehicle order) or open an FA file.");
    var bRead = new Sw.JButton("Read from E-Sys"), bOpen = new Sw.JButton("Open FA file...");
    var tkBox = new Sw.JComboBox(); tkBox.setEditable(true); tkBox.setPrototypeDisplayValue("8888  G20 · 330i xDrive · SEDAN · USA · AWD · B46 ·····");
    var tkInfo = new Sw.JLabel(" ");
    var dateField = new Sw.JTextField(5); dateField.setToolTipText("Build date as in the FA: MMYY, e.g. 0722 = July 2022");
    var iStepBox = new Sw.JComboBox(); iStepBox.setPrototypeDisplayValue("S18A-26-07-560 ····");
    var only = new Sw.JCheckBox("Only this type key's options", true);
    top.add(src, gbc(0, 0, 1, GBC.HORIZONTAL, 4)); top.add(bRead, gbc(4, 0, 0)); top.add(bOpen, gbc(5, 0, 0));
    top.add(new Sw.JLabel("Type key"), gbc(0, 1, 0)); top.add(tkBox, gbc(1, 1, 0.5, GBC.HORIZONTAL));
    top.add(new Sw.JLabel("Build date (MMYY)"), gbc(2, 1, 0)); top.add(dateField, gbc(3, 1, 0));
    top.add(new Sw.JLabel("I-step"), gbc(4, 1, 0)); top.add(iStepBox, gbc(5, 1, 0));
    top.add(tkInfo, gbc(0, 2, 1, GBC.HORIZONTAL, 4)); top.add(only, gbc(4, 2, 0, GBC.NONE, 2));
    root.add(top, Awt.BorderLayout.NORTH);

    // --- three lists: options (SALAPA), HO-words, E-words
    var COLS = ["", "Code", "Description", "Note"];
    function column(kind, title) {
      var rows = [], search = new Sw.JTextField();
      var label = new Sw.JLabel(title);
      var model = new Sw.table.AbstractTableModel({
        getRowCount: function () { return rows.length; },
        getColumnCount: function () { return 4; },
        getColumnName: function (c) { return COLS[c]; },
        getColumnClass: function (c) { return c === 0 ? java.lang.Boolean.class : java.lang.String.class; },
        isCellEditable: function (r, c) { return c === 0 && !!S.st; },
        getValueAt: function (r, c) {
          var x = rows[r];
          if (c === 0) return java.lang.Boolean.valueOf(!!S.st[kind][x.code]);
          if (c === 1) return x.code;
          if (c === 2) return x.title;
          var p = S.result ? S.result.code[x.code] : null;
          return p && S.st[kind][x.code] ? p.note : x.note;
        },
        setValueAt: function (v, r, c) {
          var code = rows[r].code;
          if (v) S.st[kind][code] = true; else delete S.st[kind][code];
          changed();
        }
      });
      var base = new Sw.table.DefaultTableCellRenderer();
      var renderer = new Sw.table.TableCellRenderer({
        getTableCellRendererComponent: function (t, v, sel, foc, row, col) {
          var comp = base.getTableCellRendererComponent(t, v, sel, foc, row, col), x = rows[row];
          var p = S.result && x && S.st[kind][x.code] ? S.result.code[x.code] : null;
          if (!sel) comp.setForeground(p ? (p.level === "bad" ? RED : p.level === "warn" ? ORANGE : t.getForeground())
                                         : x && x.dim ? GREY : t.getForeground());
          comp.setFont(t.getFont().deriveFont(x && S.st && S.st[kind][x.code] ? Awt.Font.BOLD : Awt.Font.PLAIN));
          comp.setToolTipText(p ? p.note : x ? x.note : null);
          return comp;
        }
      });
      var table = new Sw.JTable(model);
      table.setDefaultRenderer(java.lang.String.class, renderer);
      table.setFillsViewportHeight(true);
      table.setRowHeight(20);
      var cm = table.getColumnModel();
      cm.getColumn(0).setMaxWidth(26); cm.getColumn(1).setPreferredWidth(52); cm.getColumn(1).setMaxWidth(70);
      cm.getColumn(2).setPreferredWidth(260); cm.getColumn(3).setPreferredWidth(140);
      var p = new Sw.JPanel(new Awt.BorderLayout(0, 2));
      var head = new Sw.JPanel(new Awt.BorderLayout(4, 0));
      head.add(label, Awt.BorderLayout.NORTH);
      head.add(new Sw.JLabel("Search"), Awt.BorderLayout.WEST); head.add(search, Awt.BorderLayout.CENTER);
      p.add(head, Awt.BorderLayout.NORTH); p.add(new Sw.JScrollPane(table), Awt.BorderLayout.CENTER);
      var col = { kind: kind, title: title, panel: p, label: label, model: model, table: table, search: search,
                  set: function (r) { rows = r; model.fireTableDataChanged(); } };
      search.getDocument().addDocumentListener(new Sw.event.DocumentListener({
        insertUpdate: function () { fill(); }, removeUpdate: function () { fill(); }, changedUpdate: function () { fill(); }
      }));
      return col;
    }
    var cSa = column("sa", "Options (SALAPA)"), cHo = column("ho", "HO-words"), cE = column("e", "E-words");
    var lists = new Sw.JPanel(new Awt.GridLayout(1, 3, 6, 0));
    lists.add(cSa.panel); lists.add(cHo.panel); lists.add(cE.panel);

    // --- findings
    var findings = new Sw.JEditorPane("text/html", "");
    findings.setEditable(false);
    var split = new Sw.JSplitPane(Sw.JSplitPane.VERTICAL_SPLIT, lists, new Sw.JScrollPane(findings));
    split.setResizeWeight(0.72);
    root.add(split, Awt.BorderLayout.CENTER);

    // --- bottom: PsdZ check, Apply, Save
    var bottom = new Sw.JPanel(new Awt.FlowLayout(Awt.FlowLayout.LEFT, 6, 2));
    var bCafd = new Sw.JButton("Check CAFDs (PsdZ)"), bApply = new Sw.JButton("Apply to E-Sys FA"),
        bSave = new Sw.JButton("Save FA as..."), bRevert = new Sw.JButton("Revert");
    bCafd.setToolTipText("Calculate the target SVT for this FA with E-Sys's PsdZ and list CAFDs your psdzdata doesn't have");
    bApply.setToolTipText("Hand the edited FA to E-Sys's FA editor (E-Sys marks it changed; save or write it in E-Sys as usual)");
    var status = new Sw.JLabel(" ");
    [bCafd, bApply, bSave, bRevert].forEach(function (b) { bottom.add(b); b.setEnabled(false); });
    bottom.add(status);
    root.add(bottom, Awt.BorderLayout.SOUTH);

    var psdzHtml = "";
    function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
    function render() {
      var colorOf = { bad: "#b42318", warn: "#b55a00", ok: "#0b7a3e", info: "#555560" };
      var icon = { bad: "✗", warn: "▲", ok: "✓", info: "•" };
      var html = (S.result ? S.result.items : []).map(function (it) {
        return "<div style='color:" + colorOf[it.level] + "'>" + icon[it.level] + " " + esc(it.text) + "</div>";
      }).join("") + psdzHtml;
      findings.setText("<html><body style='font-family:sans-serif;font-size:11px'>" + (html || "&nbsp;") + "</body></html>");
      findings.setCaretPosition(0);
    }

    function rowsFor(kind) {
      var d = tables(), st = S.st, out = [], seen = {};
      if (!st) return out;
      var t = d.types[st.tk], opts = t ? optionList(t.list) : null;
      var q = String(kind === "sa" ? cSa.search.getText() : kind === "ho" ? cHo.search.getText() : cE.search.getText()).trim().toLowerCase();
      function add(code, note, dim) {
        if (seen[code]) return;
        var c = d.codes[code], title = c ? c.title : "";
        if (q && code.toLowerCase().indexOf(q) < 0 && title.toLowerCase().indexOf(q) < 0) return;
        seen[code] = true;
        out.push({ code: code, title: title, note: note || "", dim: !!dim });
      }
      Object.keys(st[kind]).forEach(function (c) { add(c, kind === "sa" && opts && opts[c] ? noteOf(opts[c]) : ""); });
      if (kind === "sa" && opts) Object.keys(opts).forEach(function (c) { add(c, noteOf(opts[c])); });
      var showAll = !S.onlyThis || kind !== "sa" || !opts;
      if (showAll) {
        var list = st.series && d.series[st.series] ? d.series[st.series][kind === "sa" ? "SA" : kind === "ho" ? "HO" : "E"] || [] : [];
        list.forEach(function (c) { add(c, kind === "sa" && opts ? "not listed for " + st.tk : "", kind === "sa" && !!opts); });
        if (kind === "sa")   // every option code in the tables (the FA->FP mapping only lists options the FP uses)
          Object.keys(d.codes).forEach(function (c) {
            var k = d.codes[c].kind;
            if (c.length === 3 && (!k || k.indexOf("SA") >= 0)) add(c, opts ? "not listed for " + st.tk : "", !!opts);
          });
      }
      out.sort(function (a, b) {
        var sa = st[kind][a.code] ? 0 : 1, sb = st[kind][b.code] ? 0 : 1;
        return sa - sb || (a.code < b.code ? -1 : a.code > b.code ? 1 : 0);
      });
      return out;
    }
    function noteOf(rows) {
      return rows.map(function (r) { return FLAG[r.flag] + (r.from || r.to ? " " + fmtYm(r.from) + "–" + fmtYm(r.to) : ""); }).join("; ");
    }
    function counts() {
      cSa.label.setText("<html><b>Options (SALAPA)</b>  " + Object.keys(S.st.sa).length + " in this FA</html>");
      cHo.label.setText("<html><b>HO-words</b>  " + Object.keys(S.st.ho).length + " in this FA</html>");
      cE.label.setText("<html><b>E-words</b>  " + Object.keys(S.st.e).length + " in this FA</html>");
    }
    function fill() {
      if (!S.st) return;
      cSa.set(rowsFor("sa")); cHo.set(rowsFor("ho")); cE.set(rowsFor("e"));
      counts();
    }
    var timer = new Sw.Timer(250, function () { recheck(); });
    timer.setRepeats(false);
    function changed() { psdzHtml = ""; timer.restart(); bApply.setEnabled(!!S.logic); counts(); }
    function recheck() {
      if (!S.st) return;
      try { S.result = check(S.st); } catch (e) { S.result = { items: [{ level: "bad", text: "Check failed: " + e }], code: {} }; log("FA Helper check: " + e); }
      fill(); render();
    }
    function typeKeyItems(series) {
      var d = tables(), items = [];
      Object.keys(d.types).forEach(function (k) {
        var t = d.types[k];
        if (!series || t.main.indexOf(series) >= 0) items.push(k + "  " + describe(t));
      });
      items.sort();
      return items;
    }
    var loading = false;
    function load(list, id, logic, label) {
      loading = true;
      S.list = list; S.id = id; S.logic = logic; S.base = stateOf(id); S.st = stateOf(id);
      var d = tables(), t = d.types[S.st.tk];
      tkBox.setModel(new Sw.DefaultComboBoxModel(Java.to(typeKeyItems(S.st.series), "java.lang.Object[]")));
      tkBox.setSelectedItem(S.st.tk + (t ? "  " + describe(t) : ""));
      dateField.setText(S.st.tc);
      src.setText("<html><b>" + esc(label) + "</b>  main series " + esc(S.st.series || "?") + "</html>");
      tkInfo.setText(t ? describe(t) + "   ·   " + (t.sources.indexOf("I") >= 0 ? "option list from Open CAFD Map's tables" : "KIS class lists only") : " ");
      [bCafd, bSave, bRevert].forEach(function (b) { b.setEnabled(true); });
      bApply.setEnabled(false);
      psdzHtml = "";
      loading = false;
      recheck();
      // I-steps PsdZ offers for this FA (background: the first PsdZ call can take a moment)
      iStepBox.setModel(new Sw.DefaultComboBoxModel(Java.to(["(working...)"], "java.lang.Object[]")));
      new java.lang.Thread(function () {
        var steps = [];
        try { steps = possibleISteps(id); } catch (e) { log("FA Helper: I-steps: " + e); }
        Sw.SwingUtilities.invokeLater(function () {
          iStepBox.setModel(new Sw.DefaultComboBoxModel(Java.to(steps.length ? steps : ["(none from PsdZ)"], "java.lang.Object[]")));
        });
      }).start();
    }
    function readFromEsys() {
      var ls = faLogics().filter(function (l) { return !!l.getModel(); }), lg = null;
      if (!ls.length) { status.setText("E-Sys has no FA open: read or load one in Coding > Vehicle order (or the FA editor) first."); return; }
      lg = ls[0];
      if (ls.length > 1) {      // FAs open in more than one module: ask which
        var names = ls.map(function (l, i) { return (i + 1) + ". " + moduleName(l) + " (" + (stateOf(activeId(l)).tk || "?") + ")"; });
        var pick = Sw.JOptionPane.showInputDialog(root, "E-Sys has an FA open in more than one place. Which one?", "FA Helper",
                                                  Sw.JOptionPane.QUESTION_MESSAGE, null, Java.to(names, "java.lang.Object[]"), names[0]);
        if (pick === null) return;
        lg = ls[names.indexOf(String(pick))];
      }
      var act = activeId(lg);
      if (!act) { status.setText("E-Sys's FA list has no FA to use."); return; }
      // work on a copy; its FA at the same position as E-Sys's active one
      var list = copyList(lg.getModel()), pos = Math.max(0, lg.getModel().getId().indexOf(act));
      load(list, list.getId().get(Math.min(pos, list.getId().size() - 1)), lg, "E-Sys FA (" + moduleName(lg) + ")");
      status.setText("Read E-Sys's FA (" + (S.st.tk || "?") + ", " + Object.keys(S.st.sa).length + " options). Edits stay here until you Apply.");
      log("FA Helper: read E-Sys's FA " + S.st.series + " " + S.st.tk);
    }
    function openFile() {
      var fc = new Sw.JFileChooser(FA_DIR && FA_DIR.exists() ? FA_DIR : null);
      fc.setFileFilter(new Sw.filechooser.FileNameExtensionFilter("FA files (*.xml)", "xml"));
      if (fc.showOpenDialog(root) !== Sw.JFileChooser.APPROVE_OPTION) return;
      try {
        var f = fc.getSelectedFile();
        var ctx = JB().JAXBContext.newInstance(Java.to([TFaList], "java.lang.Class[]"));
        var list = ctx.createUnmarshaller().unmarshal(new javax.xml.transform.stream.StreamSource(f), TFaList).getValue();
        if (!list.getId().size()) { status.setText("That file has no FA."); return; }
        load(list, list.getId().get(0), null, String(f.getName()));
        var ls = faLogics();
        S.logic = ls.length ? ls[0] : null;          // Apply can still hand it to E-Sys's FA editor
        status.setText("Opened " + f.getName() + ".");
      } catch (e) { status.setText("Could not open the FA: " + e); }
    }
    function current() {
      var t = String(tkBox.getEditor().getItem() || "").trim().split(/\s+/)[0].toUpperCase();
      S.st.tk = t;
      S.st.tc = String(dateField.getText()).trim();
      S.st.ym = ymOf(S.st.tc);
    }
    tkBox.addActionListener(function () {
      if (loading || !S.st) return;
      current();
      var t = tables().types[S.st.tk];
      tkInfo.setText(t ? describe(t) + "   ·   " + (t.sources.indexOf("I") >= 0 ? "option list from Open CAFD Map's tables" : "KIS class lists only") : "Type key not in the tables");
      changed();
    });
    dateField.getDocument().addDocumentListener(new Sw.event.DocumentListener({
      insertUpdate: function () { if (!loading && S.st) { current(); changed(); } },
      removeUpdate: function () { if (!loading && S.st) { current(); changed(); } },
      changedUpdate: function () {}
    }));
    only.addActionListener(function () { S.onlyThis = only.isSelected(); fill(); });
    bRead.addActionListener(function () { try { readFromEsys(); } catch (e) { status.setText("Could not read E-Sys's FA: " + e); log("FA Helper read: " + e); } });
    bOpen.addActionListener(function () { openFile(); });
    bRevert.addActionListener(function () {
      if (!S.base) return;
      S.st = JSON.parse(JSON.stringify(S.base));
      var tb = tables().types[S.st.tk];
      loading = true; dateField.setText(S.st.tc); tkBox.setSelectedItem(S.st.tk + (tb ? "  " + describe(tb) : "")); loading = false;
      psdzHtml = ""; bApply.setEnabled(false); recheck(); status.setText("Back to the FA as read.");
    });

    // Check CAFDs: target SVT for the edited FA; CAFDs not in psdzdata; which added option brought each one in
    bCafd.addActionListener(function () {
      if (!S.st || !S.id) return;
      current();
      var iStep = String(iStepBox.getSelectedItem() || "");
      if (!/^[A-Z0-9]{4}-\d\d-\d\d-\d{3}$/.test(iStep)) { status.setText("No I-step from PsdZ for this FA."); return; }
      bCafd.setEnabled(false); status.setText("Calculating the target SVT with PsdZ (" + iStep + ")...");
      var st = JSON.parse(JSON.stringify(S.st)), base = S.base, list = S.list, pos = list.getId().indexOf(S.id);
      new java.lang.Thread(function () {
        var html = "", note = "";
        try {
          function idWith(s) { var l = copyList(list), id = l.getId().get(Math.max(0, pos)); applyState(id, s); return id; }
          var tgt = targetCafds(idWith(st), iStep), idx = cafds(), miss = [];
          Object.keys(tgt).forEach(function (k) {
            var x = tgt[k], have = idx[x.id];
            if (!have) miss.push({ x: x, why: "CAFD " + x.id.toUpperCase() + " isn't in your psdzdata" });
            else if (have.indexOf(x.ver) < 0) miss.push({ x: x, why: "CAFD " + x.id.toUpperCase() + " version " + x.ver + " isn't in your psdzdata (it has " + have.join(", ") + ")" });
          });
          // attribute: options added compared with the FA as read (up to 15, one PsdZ call each)
          var added = Object.keys(st.sa).filter(function (c) { return !base.sa[c]; }).slice(0, 15), cause = {};
          added.forEach(function (c) {
            var s2 = JSON.parse(JSON.stringify(st)); delete s2.sa[c];
            try {
              var t2 = targetCafds(idWith(s2), iStep);
              Object.keys(tgt).forEach(function (k) { if (!t2[k]) (cause[k] = cause[k] || []).push(c); });
            } catch (eA) {}
          });
          var n = Object.keys(tgt).length;
          html += "<div style='color:#2a5da8'>• PsdZ target SVT at " + esc(iStep) + ": " + n + " CAFDs in " +
                  Object.keys(tgt).map(function (k) { return tgt[k].ecu; }).filter(function (v, i, a) { return a.indexOf(v) === i; }).length + " ECUs.</div>";
          miss.forEach(function (m) {
            var k = m.x.ecu + ":" + m.x.id;
            html += "<div style='color:#b42318'>✗ " + esc(m.x.ecu) + ": " + esc(m.why) +
                    (cause[k] ? " (needed because of " + esc(cause[k].join(", ")) + ")" : "") + ".</div>";
          });
          if (!miss.length) html += "<div style='color:#0b7a3e'>✓ Every CAFD this FA needs is in your psdzdata.</div>";
          else html += "<div style='color:#555560'>• Usually an ECU your psdzdata doesn't carry for this I-step (e.g. coded with NCD 2.0 / newer data).</div>";
          note = miss.length + " CAFD(s) missing from psdzdata.";
          log("FA Helper: target SVT " + st.tk + " @ " + iStep + ": " + n + " CAFDs, " + miss.length + " not in psdzdata");
        } catch (e) {
          var msg = String(e);
          html = "<div style='color:#b42318'>✗ PsdZ couldn't calculate a target SVT: " + esc(msg.replace(/^.*Exception: /, "").substring(0, 300)) + "</div>";
          if (/C019/.test(msg)) html += "<div style='color:#555560'>• C019 usually means your psdzdata doesn't know this type key at this I-step.</div>";
          note = "PsdZ check failed.";
        }
        Sw.SwingUtilities.invokeLater(function () { psdzHtml = html; render(); status.setText(note); bCafd.setEnabled(true); });
      }).start();
    });

    // Apply: E-Sys's FA editor gets a copy of its document with this FA edited (E-Sys marks it changed)
    bApply.addActionListener(function () {
      if (!S.st || !S.logic) return;
      if (h.applyEnabled === false) { status.setText(h.applyOffReason || "Apply is off on this E-Sys version."); return; }
      current();
      try {
        var model = S.logic.getModel(), list = model ? copyList(model) : copyList(S.list);
        var ids = list.getId(), target = null;
        var act = model ? activeId(S.logic) : null, pos = model && act ? model.getId().indexOf(act) : 0;
        target = ids.get(Math.max(0, Math.min(pos, ids.size() - 1)));
        applyState(target, S.st);
        S.logic.setModel(list);
        try { S.logic.activate(target); } catch (eA) {}
        // show it in E-Sys's editor the way E-Sys shows a changed document ("updated, not saved")
        faLogics();
        var fh = fileHandling.get(S.logic);
        if (fh) fh.openUpdated();
        else log("FA Helper apply: no editor view found for the FA; E-Sys has the new FA but may show the old one until reopened");
        S.list = copyList(list); S.id = S.list.getId().get(Math.max(0, Math.min(pos, S.list.getId().size() - 1)));
        S.base = stateOf(S.id);
        bApply.setEnabled(false);
        status.setText("Applied to E-Sys's FA. Save it or write it in E-Sys as usual.");
        log("FA Helper: applied FA " + S.st.tk + " (" + Object.keys(S.st.sa).length + " options) to E-Sys's " + moduleName(S.logic));
      } catch (e) {
        status.setText("Apply failed: " + e + ". Use Save FA as... and load the file in E-Sys instead.");
        log("FA Helper apply failed: " + e);
      }
    });

    bSave.addActionListener(function () {
      if (!S.st) return;
      current();
      var fc = new Sw.JFileChooser(FA_DIR && FA_DIR.exists() ? FA_DIR : null);
      fc.setSelectedFile(new File(FA_DIR && FA_DIR.exists() ? FA_DIR : new File("."), "FA_" + S.st.series + "_" + S.st.tk + "_edited.xml"));
      fc.setFileFilter(new Sw.filechooser.FileNameExtensionFilter("FA files (*.xml)", "xml"));
      if (fc.showSaveDialog(root) !== Sw.JFileChooser.APPROVE_OPTION) return;
      var out = fc.getSelectedFile();
      if (!/\.xml$/i.test(String(out.getName()))) out = new File(out.getPath() + ".xml");
      try {
        var l = copyList(S.list), id = l.getId().get(Math.max(0, S.list.getId().indexOf(S.id)));
        applyState(id, S.st);
        var ctx = JB().JAXBContext.newInstance(Java.to([TFaList], "java.lang.Class[]"));
        var m = ctx.createMarshaller();
        m.setProperty(JB().Marshaller.JAXB_FORMATTED_OUTPUT, java.lang.Boolean.TRUE);
        m.marshal(new JB().JAXBElement(new javax.xml.namespace.QName("", "faList"), TFaList, l), out);
        status.setText("Saved to " + out + ". Load it in E-Sys's FA editor or Coding.");
      } catch (e) { status.setText("Could not save: " + e); }
    });

    try { tables(); } catch (e) {
      status.setText("Open CAFD Map's FA tables are missing (" + DATA + "): use Check for updates in OpenLauncher.");
      log("FA Helper: tables not loaded: " + e);
    }
    render();
    return root;
  }

  return { build: build, check: check, tables: tables, optionList: optionList,
           // for headless tests
           _: { copyList: copyList, applyState: applyState, stateOf: stateOf, targetCafds: targetCafds,
                possibleISteps: possibleISteps, cafds: cafds } };
});
