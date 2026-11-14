// FA / SVT auto-backup: every FA or SVT that E-Sys reads from the vehicle is saved as a copy in E-Sys's data folder
// (Path.Home, e.g. H:\Data):  FA\FABak\<series>_<last 7 of VIN>.xml  and  SVT\SVTBak\<series>_<last 7 of VIN>.xml
// (E-Sys's own faList / svt XML; the same naming other tools use). If a backup with different content already exists,
// it is kept as <name>_<yyyyMMdd-HHmmss>.xml first. Loaded by agent.js, only in an E-Sys started by OpenLauncher.
//
// How it works: E-Sys keeps an FA or SVT document in a logic object (FalLogic / SvtLogic) that fires a PropertyChange
// when the document is replaced. We listen to that; when it fires, the matching file handling tells whether the new
// document came from a file (skipped) or was read from the vehicle (backed up). Nothing in E-Sys is modified.
// Watched: FA and SVT editors (any module), and the Coding module's "SVT Actual". The calculated SVT target isn't read
// from the vehicle and isn't backed up.
(function (api) {
  var System = java.lang.System;
  var File = java.io.File;
  var log = api.log;
  var DATA_DIR = api.dataDir ? new File(api.dataDir) : null;

  function cls(name) { try { return java.lang.Class.forName(name); } catch (e) { return null; } }
  var C = {
    esys: cls("com.bmw.esys.gui.control.EsysController"),
    tree: cls("com.bmw.esys.gui.AbstractTreeController"),
    svtCmp: cls("com.bmw.esys.gui.svt.SvtCompareController"),
    fal: cls("com.bmw.esys.domain.fa.FalLogic"),
    svt: cls("com.bmw.esys.domain.svt.SvtLogic"),
    fh: cls("com.bmw.esys.gui.DefaultFileHandlingController")
  };
  // Tested on E-Sys 25.x (the same versions as the Cheat Sheet window); other versions keep E-Sys unchanged.
  var ESYS_VER = String(System.getProperty("ocm.esysVersion") || "");
  var TESTED = String(System.getProperty("ocm.testedEsys") || "25.").split(",").some(function (p) {
    return p && ESYS_VER.indexOf(p) === 0;
  });
  if (!DATA_DIR || !TESTED || !C.esys || !C.tree || !C.fal || !C.svt || !C.fh) {
    log("FA/SVT backup off: " + (!DATA_DIR ? "E-Sys data folder (Path.Home) unknown"
        : !TESTED ? "not tested on E-Sys " + (ESYS_VER || "(unknown version)") : "this E-Sys has no FA/SVT editors we know"));
    return { tick: function () {} };
  }

  function getField(obj, clazz, name) {
    var c = clazz;
    while (c) {
      try { var f = c.getDeclaredField(name); f.setAccessible(true); return f.get(obj); } catch (e) { c = c.getSuperclass(); }
    }
    return null;
  }

  // Listeners survive hot reloads of this script: they call through a System property to the current code.
  var props = System.getProperties();
  var hooked = props.get("ocm.backupHooked");
  if (!hooked) { hooked = java.util.Collections.synchronizedMap(new java.util.WeakHashMap()); props.put("ocm.backupHooked", hooked); }
  var pending = props.get("ocm.backupPending");
  if (!pending) { pending = new java.util.concurrent.ConcurrentHashMap(); props.put("ocm.backupPending", pending); }
  // logic -> its file handling, refreshed by every scan (listeners only report the logic, so reloads can't go stale)
  var fhOf = props.get("ocm.backupFh");
  if (!fhOf) { fhOf = java.util.Collections.synchronizedMap(new java.util.WeakHashMap()); props.put("ocm.backupFh", fhOf); }

  function kindOf(logic) { return C.fal.isInstance(logic) ? "FA" : C.svt.isInstance(logic) ? "SVT" : null; }

  // ------------------------------------------------------------------ find what to watch: (logic, file handling)
  function modules() {
    var inst = getField(null, C.esys, "instance");
    return inst ? getField(inst, C.esys, "cacheModules") : null;
  }

  function targets() {
    var out = [], seen = new java.util.IdentityHashMap(), mods = modules();
    if (!mods) return out;
    var queue = [];
    var it = mods.values().iterator();
    while (it.hasNext()) queue.push({ o: it.next(), d: 0 });
    while (queue.length) {
      var e = queue.shift(), o = e.o;
      if (!o || seen.containsKey(o)) continue;
      seen.put(o, true);
      if (C.svtCmp && C.svtCmp.isInstance(o)) {
        out.push({ logic: getField(o, C.svtCmp, "logicSvtActual"), fh: getField(o, C.svtCmp, "fileHandlingActual"),
                   where: "SVT Actual" });
      } else if (C.tree.isInstance(o)) {
        var lg = null;
        try { lg = o.getLogic(); } catch (eL) {}
        if (lg && kindOf(lg)) out.push({ logic: lg, fh: getField(o, C.tree, "fileHandling"), where: o.getClass().getSimpleName() });
      }
      if (e.d >= 3) continue;
      var c = o.getClass();
      while (c && String(c.getName()).indexOf("com.bmw.esys.gui") === 0) {
        var fs = c.getDeclaredFields();
        for (var i = 0; i < fs.length; i++) {
          if (fs[i].getType().isPrimitive() || java.lang.reflect.Modifier.isStatic(fs[i].getModifiers())) continue;
          try {
            fs[i].setAccessible(true);
            var v = fs[i].get(o);
            if (v && String(v.getClass().getName()).indexOf("com.bmw.esys.gui") === 0) queue.push({ o: v, d: e.d + 1 });
          } catch (eF) {}
        }
        c = c.getSuperclass();
      }
    }
    return out;
  }

  var faLogics = [];   // FA logics seen, for the VIN of an SVT (an SVT doesn't carry one)

  function hook() {
    var ts = targets();
    faLogics = ts.filter(function (t) { return t.logic && C.fal.isInstance(t.logic); }).map(function (t) { return t.logic; });
    for (var i = 0; i < ts.length; i++) {
      var t = ts[i];
      if (!t.logic || !t.fh || !kindOf(t.logic)) continue;
      fhOf.put(t.logic, t.fh);
      if (hooked.containsKey(t.logic)) continue;
      hooked.put(t.logic, true);
      (function (lg) {
        lg.getChangeSupport().addPropertyChangeListener(new java.beans.PropertyChangeListener({
          propertyChange: function (ev) {
            var f = System.getProperties().get("ocm.backupOnChange");
            if (f) f(lg);
          }
        }));
      })(t.logic);
      log("FA/SVT backup: watching " + kindOf(t.logic) + " (" + t.where + ")");
    }
  }

  props.put("ocm.backupOnChange", function (logic) {
    pending.put(logic, System.currentTimeMillis());
  });

  // ------------------------------------------------------------------ decide + write
  function source(fh) {
    var proxy = getField(fh, C.fh, "currentFile");
    // FileProxy is a non-public inner class: read its fields, its getters aren't callable from here
    var file = proxy ? getField(proxy, proxy.getClass(), "file") : null;
    var desc = proxy ? getField(proxy, proxy.getClass(), "description") : null;
    return { file: file, desc: desc ? String(desc) : "" };
  }

  function connection() {
    var inst = getField(null, C.esys, "instance");
    var cp = inst ? getField(inst, C.esys, "currentConnection") : null;
    if (!cp) return {};
    var vin = null, series = null;
    try { vin = cp.getGatewayVin(); } catch (e) {}
    try { series = cp.getSeries(); } catch (e2) {}
    return { vin: vin ? String(vin) : null, series: series ? String(series) : null };
  }

  function faIdentity(model) {
    try {
      var ids = model.getId();
      for (var i = 0; i < ids.size(); i++) {
        var fa = ids.get(i).getFa();
        if (!fa) continue;
        var vin = fa.getHeader() ? fa.getHeader().getVinLong() : null;
        var ser = fa.getStandardFA() ? fa.getStandardFA().getSeries() : null;
        if (vin) return { vin: String(vin), series: ser ? String(ser) : null };
      }
    } catch (e) {}
    return {};
  }

  // Same document? Compare by meaning: element local names, attributes in any order, text; the header (read date and
  // time) and E-Sys's list bookkeeping (noActivated, no) are ignored, so a re-read of an unchanged car isn't a new copy.
  var IGNORE_ATTR = { noActivated: 1, no: 1 };
  function canon(xml) {
    var dbf = javax.xml.parsers.DocumentBuilderFactory.newInstance();
    dbf.setNamespaceAware(true);
    var doc = dbf.newDocumentBuilder().parse(new org.xml.sax.InputSource(new java.io.StringReader(xml)));
    var sb = new java.lang.StringBuilder();
    (function walk(n) {
      var name = String(n.getLocalName() || n.getNodeName());
      if (name === "header") return;
      sb.append("<").append(name);
      var at = n.getAttributes(), list = [];
      for (var i = 0; i < at.getLength(); i++) {
        var a = at.item(i), an = String(a.getLocalName() || a.getNodeName());
        if (/^xmlns/.test(String(a.getNodeName())) || IGNORE_ATTR[an]) continue;
        list.push(an + "=" + a.getNodeValue());
      }
      sb.append(list.sort().join(" ")).append(">");
      var kids = n.getChildNodes();
      for (var k = 0; k < kids.getLength(); k++) {
        var c = kids.item(k);
        if (c.getNodeType() === 1) walk(c);
        else if (c.getNodeType() === 3 && String(c.getNodeValue()).trim()) sb.append(String(c.getNodeValue()).trim());
      }
      sb.append("</>");
    })(doc.getDocumentElement());
    return String(sb.toString());
  }

  function sameDocument(file, xml) {
    try {
      var old = String(new java.lang.String(java.nio.file.Files.readAllBytes(file.toPath()), "UTF-8"));
      return canon(old) === canon(xml);
    } catch (e) {
      return false;
    }
  }

  function write(kind, logic) {
    var model = logic.getModel();
    if (!model) return;
    var id = connection();
    if (kind === "FA") {
      var fid = faIdentity(model);
      id = { vin: fid.vin || id.vin, series: fid.series || id.series };
    } else if (!id.vin || !id.series) {
      // SVT: the vehicle's FA as E-Sys has it loaded (connecting by VIN leaves the gateway VIN empty)
      for (var i = 0; i < faLogics.length && (!id.vin || !id.series); i++) {
        var m = null;
        try { m = faLogics[i].getModel(); } catch (eM) {}
        var f2 = m ? faIdentity(m) : {};
        id = { vin: id.vin || f2.vin, series: id.series || f2.series };
      }
    }
    if (!id.vin || id.vin.length < 7) { log("FA/SVT backup: " + kind + " read, but no VIN is known - not saved"); return; }
    var xml = String(logic.marshal());
    var dir = new File(DATA_DIR, kind === "FA" ? "FA\\FABak" : "SVT\\SVTBak");
    dir.mkdirs();
    var name = (id.series || "UNKNOWN") + "_" + id.vin.substring(id.vin.length - 7);
    var target = new File(dir, name + ".xml");
    if (target.exists()) {
      if (sameDocument(target, xml)) { log("FA/SVT backup: " + kind + " unchanged since the last backup (" + target + ")"); return; }
      var stamp = new java.text.SimpleDateFormat("yyyyMMdd-HHmmss").format(new java.util.Date(target.lastModified()));
      var old = new File(dir, name + "_" + stamp + ".xml");
      if (target.renameTo(old)) log("FA/SVT backup: previous " + kind + " backup kept as " + old.getName());
      else log("FA/SVT backup: could not keep the previous " + target.getName());
    }
    var tmp = new File(dir, name + ".xml.tmp");
    java.nio.file.Files.write(tmp.toPath(), new java.lang.String(xml).getBytes("UTF-8"));
    java.nio.file.Files.move(tmp.toPath(), target.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING);
    log("FA/SVT backup: saved " + kind + " read from the vehicle to " + target);
  }

  var ticks = 0;
  function tick() {
    if (ticks++ % 5 === 0) {
      try { hook(); } catch (eH) { log("FA/SVT backup: " + eH); }
    }
    var now = System.currentTimeMillis();
    var it = pending.entrySet().iterator();
    while (it.hasNext()) {
      var en = it.next();
      if (now - en.getValue() < 500) continue;       // let E-Sys finish setting the file handling
      it.remove();
      var logic = en.getKey(), kind = kindOf(logic);
      try {
        var fh = fhOf.get(logic);
        if (!fh || !C.fh.isInstance(fh)) { log("FA/SVT backup: " + kind + " replaced, file handling unknown - not saved"); continue; }
        var src = source(fh);
        if (src.file !== null && src.file.exists()) continue;   // opened from (or saved to) a file: not a vehicle read
        if (!logic.getModel()) continue;                         // closed / cleared
        write(kind, logic);
      } catch (eW) {
        log("FA/SVT backup failed for " + kind + ": " + eW);
      }
    }
  }

  log("FA/SVT backup on: " + DATA_DIR);
  return { tick: tick };
});
