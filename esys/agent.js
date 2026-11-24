(function() {
  var File = java.io.File;
  var FileWriter = java.io.FileWriter;
  var BufferedWriter = java.io.BufferedWriter;
  var DocumentBuilderFactory = javax.xml.parsers.DocumentBuilderFactory;
  var Window = java.awt.Window;
  var Container = java.awt.Container;
  var JTree = javax.swing.JTree;
  var SwingUtilities = javax.swing.SwingUtilities;
  var System = java.lang.System;

  // Open CAFD Map labelling agent. Runs only inside an E-Sys started by OpenLauncher
  // (esys/bootstrap.js). It renames E-Sys's in-memory CAFD / FP tree nodes ("." -> BMW / community
  // names and derived value labels) from the definition XMLs. It never changes coding data.
  if (String(System.getProperty("ocm.launcher")) !== "1") return;

  var DEF_DIR = String(System.getProperty("ocm.defs"));
  var LOG_FILE = new File(String(System.getProperty("ocm.log")));
  var SCRIPT_FILE = new File(String(System.getProperty("ocm.agent")));
  var SERIES = String(System.getProperty("ocm.series") || "G020");
  // optional: -Docm.dumps=<folder> writes what was applied per CAFD (for debugging definitions)
  var DUMP_DIR = System.getProperty("ocm.dumps") ? String(System.getProperty("ocm.dumps")) : null;
  if (DUMP_DIR) new File(DUMP_DIR).mkdirs();

  function log(msg) {
    try {
      var ts = new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS").format(new java.util.Date());
      var bw = new BufferedWriter(new FileWriter(LOG_FILE, true));
      bw.write("[" + ts + "] " + msg + "\r\n");
      bw.close();
    } catch (e) {}
  }

  var processedCafs = new java.util.WeakHashMap();
  var processedFps = new java.util.WeakHashMap();

  function padHex(str, len) {
    var s = String(str || "").toUpperCase();
    while (s.length < len) s = "0" + s;
    return s;
  }

  function bytesToHex(bytes) {
    if (!bytes) return "";
    var sb = new java.lang.StringBuilder(bytes.length * 2);
    for (var i = 0; i < bytes.length; i++) {
      var b = bytes[i] & 0xFF;
      if (b < 16) sb.append("0");
      sb.append(java.lang.Integer.toHexString(b).toUpperCase());
    }
    return sb.toString();
  }

  function extractTValueHex(tval) {
    if (!tval) return "";
    var uvs = tval.getUnitVal();
    if (!uvs || uvs.size() === 0) return "";
    var sb = new java.lang.StringBuilder();
    for (var i = 0; i < uvs.size(); i++) {
      var uv = uvs.get(i);
      if (uv && uv.getValue()) {
        sb.append(bytesToHex(uv.getValue()));
      }
    }
    return sb.toString();
  }

  // Definitions come from ocm/export_defs.py: <defs>/CAFD_<id>_<main>_<sub>_<patch>.xml (built from your
  // psdzdata + all names). Fallback for CAFD versions your psdzdata doesn't have: a contributed
  // definition XML in the repo's definitions/ folder (exact version first, then a versionless CAFD_<id>.xml).
  var CONTRIB_DIR = System.getProperty("ocm.contrib") ? String(System.getProperty("ocm.contrib")) : null;
  var contribIndex = null;
  function indexContrib() {
    contribIndex = {};
    if (!CONTRIB_DIR) return;
    var stack = [new File(CONTRIB_DIR)];
    while (stack.length) {
      var d = stack.pop();
      var kids = d.listFiles();
      if (!kids) continue;
      for (var i = 0; i < kids.length; i++) {
        if (kids[i].isDirectory()) { stack.push(kids[i]); continue; }
        var n = String(kids[i].getName()).toUpperCase();
        if (/^CAFD_[0-9A-F]{8}(_\d{3}_\d{3}_\d{3})?\.XML$/.test(n) && !contribIndex[n]) contribIndex[n] = kids[i];
      }
    }
  }
  function findXmlDefFile(cafId, mainVer, subVer, patchVer) {
    var verName = "CAFD_" + cafId + "_" + mainVer + "_" + subVer + "_" + patchVer + ".xml";
    var f = new File(DEF_DIR, verName);
    if (f.exists()) return f;
    if (contribIndex === null) indexContrib();
    return contribIndex[verName.toUpperCase()] || contribIndex[("CAFD_" + cafId + ".xml").toUpperCase()] || null;
  }

  // Definitions on demand (ocm/runtime_defs.py): a CAFD opened without a definition file is saved as E-Sys loaded
  // it (before any renaming) and labelled in the background by the same code as "Build definitions". When the
  // file appears in DEF_DIR the next tick relabels the tree. Nothing is sent anywhere; files stay in the workspace.
  var RUNTIME_DIR = System.getProperty("ocm.runtime") ? String(System.getProperty("ocm.runtime")) : null;
  var PYTHON = System.getProperty("ocm.python") ? String(System.getProperty("ocm.python")) : null;
  var TOOLS = System.getProperty("ocm.tools") ? String(System.getProperty("ocm.tools")) : null;
  var CAF_NS = "http://bmw.com/2005/psdz.data.caf";
  var runtimeState = System.getProperties().get("ocm.runtimeState");
  if (!runtimeState) { runtimeState = new java.util.concurrent.ConcurrentHashMap(); System.getProperties().put("ocm.runtimeState", runtimeState); }
  // objects already renamed by an earlier agent instance (hot reload) must not be saved: their names aren't BMW's
  var seenCafs = System.getProperties().get("ocm.seenCafs");
  if (!seenCafs) { seenCafs = java.util.Collections.synchronizedMap(new java.util.WeakHashMap()); System.getProperties().put("ocm.seenCafs", seenCafs); }
  var runtimeExec = System.getProperties().get("ocm.runtimeExec");
  if (!runtimeExec && RUNTIME_DIR && PYTHON && TOOLS) {
    runtimeExec = java.util.concurrent.Executors.newSingleThreadExecutor(new java.util.concurrent.ThreadFactory({
      newThread: function (r) { var t = new java.lang.Thread(r, "OpenCAFDMap-Definitions"); t.setDaemon(true); return t; }
    }));
    System.getProperties().put("ocm.runtimeExec", runtimeExec);
  }
  var jaxbCtx = null, legacyCtx = null;

  // E-Sys 3.x (Java 8) keeps an open CAFD as com.bmw.esys.domain.base.caf.TCAF (JAXB1) instead of 25.x's
  // jaxb.caf1.Caf1. Same getters and setters, so applyAndDumpCaf labels both; only finding the CAF in the tree
  // and saving it for on-demand definitions differ. Nothing below changes how 25.x CAFs are handled.
  var LEGACY_CAF = "com.bmw.esys.domain.base.caf.TCAF";
  var legacyCafClass = null;
  var legacyInterpClass = null;
  try {
    legacyCafClass = java.lang.Class.forName(LEGACY_CAF);
    legacyInterpClass = java.lang.Class.forName("com.bmw.esys.domain.base.caf.TInterpretations");
  } catch (eNoLegacy) {}
  function isLegacyCaf(obj) { return legacyCafClass !== null && obj !== null && legacyCafClass.isInstance(obj); }

  function saveCafXml(caf, file) {
    file.getParentFile().mkdirs();
    var tmp = new File(file.getPath() + ".tmp");
    var os = new java.util.zip.GZIPOutputStream(new java.io.FileOutputStream(tmp));
    try {
      if (isLegacyCaf(caf)) {
        // JAXB1 objects are their own root element and write the same psdz CAF XML runtime_defs.py reads
        if (!legacyCtx) legacyCtx = javax.xml.bind.JAXBContext.newInstance("com.bmw.esys.domain.base.caf");
        legacyCtx.createMarshaller().marshal(caf, os);
      } else {
        var cls = caf.getClass();
        if (!jaxbCtx) jaxbCtx = javax.xml.bind.JAXBContext.newInstance(Java.to([cls], "java.lang.Class[]"));
        jaxbCtx.createMarshaller().marshal(new javax.xml.bind.JAXBElement(new javax.xml.namespace.QName(CAF_NS, "caf"), cls, caf), os);
      }
    } finally { os.close(); }
    if (file.exists()) file["delete"]();
    if (!tmp.renameTo(file)) throw "could not write " + file;
  }

  function requestDefinition(caf, tag, cafFile) {
    if (!runtimeExec || runtimeState.containsKey(tag)) return;
    if (!cafFile.exists()) {
      if (seenCafs.containsKey(caf)) return;
      try { saveCafXml(caf, cafFile); } catch (eSave) { runtimeState.put(tag, "failed"); log("Could not save " + tag + " for labelling: " + eSave); return; }
    }
    runtimeState.put(tag, "pending");
    log("Building the definition for " + tag + " in the background");
    runtimeExec.submit(new java.lang.Runnable({ run: function () {
      var t0 = System.currentTimeMillis();
      try {
        var pb = new java.lang.ProcessBuilder(Java.to([PYTHON, "-u", new File(TOOLS, "runtime_defs.py").getPath(),
                                                       cafFile.getAbsolutePath()], "java.lang.String[]"));
        pb.redirectErrorStream(true);
        pb.redirectOutput(java.lang.ProcessBuilder.Redirect.appendTo(new File(LOG_FILE.getParentFile(), "runtime_defs.log")));
        pb.environment().put("PYTHONIOENCODING", "utf-8");
        var rc = pb.start().waitFor();
        runtimeState.put(tag, rc === 0 ? "done" : "failed");
        log((rc === 0 ? "Built the definition for " : "Building the definition failed for ") + tag + " ("
            + ((System.currentTimeMillis() - t0) / 1000).toFixed(1) + "s" + (rc === 0 ? "" : ", exit " + rc + ", see runtime_defs.log") + ")");
      } catch (eRun) {
        runtimeState.put(tag, "failed");
        log("Building the definition failed for " + tag + ": " + eRun);
      }
    }}));
  }

  // Untrimmed names packed by datagroup layout (ocm/untrimmed.py), applied at runtime to whatever is still a
  // placeholder, so versions that were never exported get names too:
  //   definitions/untrimmed/<CAFD id>.tsv.gz              BMW's own names (res.groups)
  //   definitions/untrimmed/transferred/<CAFD id>.tsv.gz  names from E-Sys Plus's reference CAFDs, put back on the
  //                                                       layouts they were made for (ocm/untrimmed.py realign),
  //                                                       matched by field (res.tpos, transferMatch); always
  //                                                       marked unverified
  var untrimmedCache = {};
  function readPack(f, groups, fallback) {
    var br = new java.io.BufferedReader(new java.io.InputStreamReader(
      new java.util.zip.GZIPInputStream(new java.io.FileInputStream(f)), "UTF-8"));
    var line;
    while ((line = br.readLine()) !== null) {
      line = String(line);
      if (!line || line.charAt(0) === "#") continue;
      var p = line.split("\t");
      if (p[0] === "G" && p[2] === "*") { if (!fallback.hasOwnProperty(p[1])) fallback[p[1]] = p[7]; continue; }
      var key = p[1] + "|" + p[2];
      var g = groups[key] || (groups[key] = { G: null, F: {}, V: {} });
      if (p[0] === "G") g.G = p[7];
      else if (p[0] === "F") g.F[p[3] + "|" + p[4] + "|" + p[5]] = p[7];
      else if (p[0] === "V") g.V[p[3] + "|" + p[4] + "|" + p[5] + "|" + p[6]] = p[7];
    }
    br.close();
  }
  function loadUntrimmed(cafId) {
    if (untrimmedCache.hasOwnProperty(cafId)) return untrimmedCache[cafId];
    var res = null;
    try {
      var dir = CONTRIB_DIR ? new File(CONTRIB_DIR, "untrimmed") : null;
      var f = dir ? new File(dir, cafId + ".tsv.gz") : null;
      var tf = dir ? new File(new File(dir, "transferred"), cafId + ".tsv.gz") : null;
      if ((f && f.exists()) || (tf && tf.exists())) {
        res = { groups: {}, tgroups: {}, fallback: {} };
        if (f.exists()) readPack(f, res.groups, res.fallback);
        if (tf.exists()) readPack(tf, res.tgroups, res.fallback);   // BMW's own group names come first
        // field indexes: "addr|start|end|mask" -> [{n: name, v: {hex: value name}}], one per layout:
        // res.pos (BMW's own names, field fallback below) and res.tpos (realigned transferred names)
        var fieldIndex = function (groups) {
          var idx = {};
          for (var gk in groups) {
            var ug = groups[gk], ga = gk.split("|")[0], byF = {};
            for (var vk in ug.V) {
              var vp = vk.split("|"), fk = vp[0] + "|" + vp[1] + "|" + vp[2];
              (byF[fk] || (byF[fk] = {}))[vp[3].toUpperCase()] = ug.V[vk];
            }
            for (var fk2 in ug.F) {
              var pk = ga + "|" + fk2;
              (idx[pk] || (idx[pk] = [])).push({ n: ug.F[fk2], v: byF[fk2] || {} });
            }
          }
          return idx;
        };
        res.pos = fieldIndex(res.groups);
        res.tpos = fieldIndex(res.tgroups);
      }
    } catch (eU) {
      log("Error reading untrimmed names for CAFD_" + cafId + ": " + eU);
      res = null;
    }
    untrimmedCache[cafId] = res;
    return res;
  }

  // Field fallback (same rule as ocm/untrimmed.py field_match, issue #8) for groups whose layout isn't packaged:
  // the layouts with exactly this address/start/end/mask that name no value code the function lacks must all agree.
  // A guess (BMW reassigns bits between versions), so the name gets POS_MARK. -> {n, v: {hex: value name}} or null
  var POS_MARK = " (?)";   // = untrimmed.UNVERIFIED: every name not proven for this CAFD version ends in it
  var POS_NOTE = "BMW untrimmed name from another version of this CAFD, matched by field position only "
    + "(this group's layout isn't packaged): unverified";
  var TRANSFER_NOTE = "Name from E-Sys Plus's reference CAFD, matched to this version by field, value codes and coding "
    + "rules; not BMW's own name for this version: unverified, check before coding";
  var VALUES_NOTE = "BMW name from a version with the same layout, but this function has value codes that version "
    + "doesn't name (the field may have been reused): unverified";
  // Realigned transferred name for a field (ocm/untrimmed.py transfer_match), or null. Every reference layout
  // naming exactly this field that names all its value codes must agree. On/off flags and reserved bits (no more
  // than FLAG_CODES value codes) also need the same coding-rule print, which only the definition build can compute:
  // here they stay placeholders until the on-demand definition arrives.
  var FLAG_CODES = 2;
  function transferMatch(uPack, addr, uKey, hexes) {
    var cand = (uPack && uPack.tpos) ? uPack.tpos[addr + "|" + uKey] : null;
    var n = 0;
    for (var h0 in hexes) n++;
    if (!cand || n <= FLAG_CODES) return null;
    var name = null, vals = null;
    for (var i = 0; i < cand.length; i++) {
      var ok = true;
      for (var h in hexes) if (!cand[i].v[h]) { ok = false; break; }
      if (!ok) continue;
      if (name !== null && name.toUpperCase() !== cand[i].n.toUpperCase()) return null;
      if (name === null) { name = cand[i].n; vals = cand[i].v; }
    }
    return name === null ? null : { n: name, v: vals };
  }

  // Does the packaged group g name a value for every code in hexes ({HEX: true})? Same rules as
  // build_defdb.apply_untrimmed: a transferred name needs them all (strict); BMW's own layout only when it names
  // values for this field at all.
  function namesValues(g, uKey, hexes, strict) {
    if (!strict) {
      var any = false;
      for (var vk in g.V) if (vk.indexOf(uKey + "|") === 0) { any = true; break; }
      if (!any) return true;
    }
    for (var h in hexes) if (!g.V[uKey + "|" + h]) return false;
    return true;
  }
  function fieldMatch(uPack, addr, uKey, hexes) {
    var cand = (uPack && uPack.pos) ? uPack.pos[addr + "|" + uKey] : null;
    if (!cand) return null;
    var name = null, vals = {}, clash = {};
    for (var i = 0; i < cand.length; i++) {
      var ok = true;
      for (var h in cand[i].v) if (!hexes[h]) { ok = false; break; }
      if (!ok) continue;
      if (name !== null && name !== cand[i].n) return null;
      name = cand[i].n;
      for (var h2 in cand[i].v) {
        if (vals.hasOwnProperty(h2) && vals[h2] !== cand[i].v[h2]) clash[h2] = true;
        else vals[h2] = cand[i].v[h2];
      }
    }
    if (name === null) return null;
    for (var h3 in clash) delete vals[h3];
    return { n: name, v: vals };
  }

  // Same as ocm/untrimmed.py layout_hash: md5 of "start:end:mask;..." over the group's functions, 16 hex chars.
  function layoutHash(fns) {
    var parts = [];
    for (var j = 0; j < fns.size(); j++) {
      var si = fns.get(j).getStorageInfo();
      var st = (si && si.getStart()) ? si.getStart().intValue() : 0;
      var en = (si && si.getEnd()) ? si.getEnd().intValue() : st;
      var mk = (si && si.getMask() && si.getMask().getValue()) ? String(si.getMask().getValue()) : "11111111b";
      parts.push(st + ":" + en + ":" + normMask(mk));
    }
    var dig = java.security.MessageDigest.getInstance("MD5").digest(
      new java.lang.String(parts.join(";")).getBytes("US-ASCII"));
    return bytesToHex(dig).toLowerCase().substring(0, 16);
  }

  // Names that only stand in for a missing one: E-Sys's ".", our Group_XXXX / *_Byte_* / <group>_<s>_<e>_<mask>
  // fallbacks, and bare value numbers ("wert_02", "wert_02 [SA5AC]").
  function isPlaceholder(n) {
    n = String(n || "");
    return !n || n === "." || n === "custom_wert" || /^Group_[0-9A-Fa-f]{4}$/.test(n) || n.indexOf("_Byte_") >= 0
      || /^.+_\d+_\d+_\d+$/.test(n) || /^\*?\s*wert_\d+(\s*\[[^\]]*\])?$/.test(n);
  }

  function normMask(m) {
    var s = String(m || "").trim();
    if (/^[01]+b$/i.test(s)) return s.toLowerCase();
    if (/^[0-9a-f]{1,2}$/i.test(s)) {  // legacy "FF" style
      var b = parseInt(s, 16).toString(2);
      while (b.length < 8) b = "0" + b;
      return b + "b";
    }
    return s.toLowerCase();
  }

  function loadXmlDef(f) {
    if (!f || !f.exists()) return null;
    var dbf = DocumentBuilderFactory.newInstance();
    var db = dbf.newDocumentBuilder();
    var doc = db.parse(f);
    var root = doc.getDocumentElement();

    var def = {
      file: f.getAbsolutePath(),
      lastModified: f.lastModified(),
      ecu: String(root.getAttribute("ecu") || "ECU"),
      generated: String(root.getAttribute("schemaVersion")) === "2",
      groups: {}
    };

    var grpNodes = root.getElementsByTagName("Group");
    for (var i = 0; i < grpNodes.getLength(); i++) {
      var gEl = grpNodes.item(i);
      var gId = String(gEl.getAttribute("id")).toUpperCase();
      var gName = String(gEl.getAttribute("name"));
      var gObj = { name: gName, functions: [] };

      var fnNodes = gEl.getElementsByTagName("Function");
      for (var j = 0; j < fnNodes.getLength(); j++) {
        var fEl = fnNodes.item(j);
        var fnObj = {
          name: String(fEl.getAttribute("name")),
          start: parseInt(fEl.getAttribute("start"), 10),
          end: parseInt(fEl.getAttribute("end"), 10),
          mask: normMask(fEl.getAttribute("mask") || "FF"),
          comment: String(fEl.getAttribute("comment") || ""),
          values: {},      // hex code -> label (first wins)
          byIdx: {}        // condParam position -> label (generated files carry idx)
        };
        var valNodes = fEl.getElementsByTagName("Value");
        for (var k = 0; k < valNodes.getLength(); k++) {
          var vEl = valNodes.item(k);
          var code = String(vEl.getAttribute("code") || "").toUpperCase();
          var label = String(vEl.getAttribute("label") || "");
          var idxAttr = vEl.getAttribute("idx");
          if (label && idxAttr !== null && String(idxAttr) !== "") {
            fnObj.byIdx[parseInt(idxAttr, 10)] = { code: code, label: label };
          }
          if (code && label && !fnObj.values[code]) {
            fnObj.values[code] = label;
          }
        }
        gObj.functions.push(fnObj);
      }
      def.groups[gId] = gObj;
    }
    return def;
  }

  function xmlEscape(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function applyAndDumpCaf(caf) {
    var hdr = caf.getSweHeader();
    var ident = hdr ? hdr.getCafIdent() : null;
    var cafId = ident ? padHex(ident.getId(), 8) : "00000000";
    var mainVer = ident ? padHex(ident.getMainVersion(), 3) : "000";
    var subVer = ident ? padHex(ident.getSubVersion(), 3) : "000";
    var patchVer = ident ? padHex(ident.getPatchVersion(), 3) : "000";

    var xmlFile = findXmlDefFile(cafId, mainVer, subVer, patchVer);
    var xmlStamp = xmlFile ? xmlFile.lastModified() : -1;
    var stateKey = String(xmlStamp);

    if (processedCafs.containsKey(caf) && String(processedCafs.get(caf)) === stateKey) {
      return false;
    }
    processedCafs.put(caf, stateKey);

    var tag = "CAFD_" + cafId + "_" + mainVer + "_" + subVer + "_" + patchVer;
    if (RUNTIME_DIR && !new File(DEF_DIR, tag + ".xml").exists()) {
      requestDefinition(caf, tag, new File(RUNTIME_DIR, "cafd_" + cafId + "_" + mainVer + "_" + subVer + "_" + patchVer + ".xml.gz"));
    }
    seenCafs.put(caf, true);

    var def = null;
    try {
      def = loadXmlDef(xmlFile);
    } catch (eXml) {
      log("Error loading XML definition for CAFD_" + cafId + ": " + eXml);
    }

    if (hdr) {
      hdr.setEcuName((def && def.ecu) ? def.ecu : ("CAFD_" + cafId));
    }

    var data = caf.getData();
    if (!data) return true;
    var dgs = data.getDatagroup();
    if (!dgs) return true;

    var dumpSb = new java.lang.StringBuilder();
    dumpSb.append('<?xml version="1.0" encoding="utf-8"?>\r\n');
    dumpSb.append('<CafdDefinition schemaVersion="1" ecu="' + xmlEscape(hdr ? hdr.getEcuName() : "ECU") + '" cafdId="' + cafId + '" mainVersion="' + mainVer + '" subVersion="' + subVer + '" patchVersion="' + patchVer + '">\r\n');

    var uPack = loadUntrimmed(cafId);
    var untrimmedHits = 0;
    var posHits = 0;
    var transferHits = 0;
    var fnCounter = 1;
    var grpRenamed = 0;
    var fnRenamed = 0;
    var cpRenamed = 0;
    var interpUpdated = 0;

    for (var i = 0; i < dgs.size(); i++) {
      var dg = dgs.get(i);
      var addr = String(dg.getAddress() || "").toUpperCase();
      var gDef = (def && def.groups[addr]) ? def.groups[addr] : null;

      if (gDef && gDef.name) {
        dg.setName(gDef.name);
        grpRenamed++;
      } else if (!dg.getName() || String(dg.getName()) === ".") {
        dg.setName("Group_" + addr);
        grpRenamed++;
      }

      var uG = null, tG = null;     // tG: a transferred reference layout identical to this one (group name only)
      if (uPack) {
        var uFns = dg.getFunction();
        if (uFns && uFns.size() > 0) {
          var uH = addr + "|" + layoutHash(uFns);
          uG = uPack.groups[uH] || null;
          tG = uPack.tgroups[uH] || null;
        }
        if (isPlaceholder(dg.getName())) {
          var uGName = (uG && uG.G) || (tG && tG.G) || uPack.fallback[addr];
          if (uGName) { dg.setName(uGName); untrimmedHits++; }
        }
      }

      var grpNameForDump = String(dg.getName());
      dumpSb.append('  <Group id="' + addr + '" name="' + xmlEscape(grpNameForDump) + '">\r\n');

      var fns = dg.getFunction();
      if (fns) {
        for (var j = 0; j < fns.size(); j++) {
          var fn = fns.get(j);
          var si = fn.getStorageInfo();
          var start = (si && si.getStart()) ? si.getStart().intValue() : 0;
          var end = (si && si.getEnd()) ? si.getEnd().intValue() : start;
          var maskStr = (si && si.getMask() && si.getMask().getValue()) ? String(si.getMask().getValue()) : "11111111b";

          // Several functions share a byte with different masks: identity is (start, end, mask).
          var matchedFnDef = null;
          if (gDef && gDef.functions) {
            var nm = normMask(maskStr);
            var byPos = [];
            for (var m = 0; m < gDef.functions.length; m++) {
              var cand = gDef.functions[m];
              if (cand.start === start && cand.end === end) {
                if (cand.mask === nm) { matchedFnDef = cand; break; }
                byPos.push(cand);
              }
            }
            // legacy hand-made files (no real masks): accept a unique start/end match.
            // Generated files are exact, so never fall back for them.
            if (!matchedFnDef && !def.generated && byPos.length === 1) {
              matchedFnDef = byPos[0];
            }
            if (!matchedFnDef && fns.size() === 1 && gDef.functions.length === 1) {
              matchedFnDef = gDef.functions[0];
            }
          }

          if (matchedFnDef && matchedFnDef.name) {
            fn.setName(matchedFnDef.name);
            if (matchedFnDef.comment) fn.setComment(matchedFnDef.comment);
            fnRenamed++;
          } else if (!fn.getName() || String(fn.getName()) === ".") {
            var autoName = grpNameForDump + "_Byte_" + start + (end > start ? ("_" + end) : "");
            fn.setName(autoName);
            fnRenamed++;
          }
          var uKey = start + "|" + end + "|" + normMask(maskStr);
          var posMatch = null, vG = uG;     // vG: the package group whose value names this function uses
          var fnHexes = {};
          if (uPack) {
            var cpsH = fn.getCondParam();
            if (cpsH) for (var kh = 0; kh < cpsH.size(); kh++) {
              var hh = extractTValueHex(cpsH.get(kh).getValues());
              if (hh) fnHexes[String(hh).toUpperCase()] = true;
            }
          }
          var addNote = function (note) {
            var oc = fn.getComment() ? String(fn.getComment()) : "";
            fn.setComment(oc ? oc + " | " + note : note);
          };
          if (uG && uG.F[uKey] && isPlaceholder(fn.getName())) {
            if (namesValues(uG, uKey, fnHexes, false)) fn.setName(uG.F[uKey]);
            else { fn.setName(uG.F[uKey] + POS_MARK); addNote(VALUES_NOTE); }
            untrimmedHits++;
          } else if (!(uG && uG.F[uKey]) && isPlaceholder(fn.getName())
                     && (posMatch = transferMatch(uPack, addr, uKey, fnHexes))) {
            fn.setName(posMatch.n + POS_MARK);   // posMatch.v now holds its value names
            addNote(TRANSFER_NOTE);
            vG = null;
            untrimmedHits++;
            transferHits++;
          } else if (uPack && !(uG && uG.F[uKey])) {
            posMatch = fieldMatch(uPack, addr, uKey, fnHexes);
            if (posMatch && isPlaceholder(fn.getName())) {
              fn.setName(posMatch.n + POS_MARK);
              addNote(POS_NOTE);
              untrimmedHits++;
              posHits++;
            } else if (posMatch && String(fn.getName()).toLowerCase() !== posMatch.n.toLowerCase()
                       && String(fn.getName()).toLowerCase() !== (posMatch.n + POS_MARK).toLowerCase()) {
              posMatch = null;  // named otherwise (definition, cheat sheet): keep its values as they are
            }
          }

          var defHex = extractTValueHex(fn.getDefVal());
          var fnIdHex = padHex(java.lang.Integer.toHexString(fnCounter++), 16);
          dumpSb.append('    <Function name="' + xmlEscape(fn.getName()) + '" id="' + fnIdHex + '" start="' + start + '" end="' + end + '" mask="' + xmlEscape(maskStr) + '" comment="' + xmlEscape(fn.getComment() || "") + '">\r\n');
          if (defHex) {
            dumpSb.append('      <Default>' + defHex + '</Default>\r\n');
          }

          var cps = fn.getCondParam();
          var hexToLabel = {};
          if (cps) {
            for (var k = 0; k < cps.size(); k++) {
              var cp = cps.get(k);
              var cpHex = extractTValueHex(cp.getValues());
              var wertIdx = k + 1;
              var wertNum = (wertIdx < 10 ? "0" : "") + wertIdx;
              var defaultWertLabel = "wert_" + wertNum;

              // Prefer the label for this exact condParam position (same hex can appear in several werte),
              // provided the hex still agrees; fall back to the hex lookup. (Custom 6DF0 LED labels now
              // live in names_user.tsv and arrive through the generated definitions.)
              var customLabel = null;
              if (matchedFnDef) {
                var bi = matchedFnDef.byIdx[k];
                if (bi && (!bi.code || !cpHex || bi.code === cpHex)) customLabel = bi.label;
                if (!customLabel && cpHex && matchedFnDef.values[cpHex]) customLabel = matchedFnDef.values[cpHex];
              }

              var finalLabel = customLabel ? customLabel : defaultWertLabel;
              var uVal = (vG && cpHex) ? vG.V[uKey + "|" + cpHex] : null;
              if (!uVal && posMatch && cpHex) uVal = posMatch.v[String(cpHex).toUpperCase()] || null;
              if (uVal && isPlaceholder(finalLabel)) {
                var hintAt = finalLabel.indexOf("[");
                finalLabel = defaultWertLabel + " " + uVal + (hintAt >= 0 ? " " + finalLabel.substring(hintAt) : "");
                untrimmedHits++;
              }
              cp.setParamName(finalLabel);
              cpRenamed++;
              if (cpHex && !hexToLabel[cpHex]) {
                hexToLabel[cpHex] = finalLabel;
              }

              if (cpHex) {
                dumpSb.append('      <Value code="' + cpHex + '" label="' + xmlEscape(finalLabel) + '" />\r\n');
              }
            }
          }

          try {
            var interp = fn.getInterpretation();
            if (interp) {
              var interpHex = extractTValueHex(interp.getValues());
              if (interpHex && hexToLabel[interpHex]) {
                interp.setParamName(hexToLabel[interpHex]);
                interpUpdated++;
              } else if (!interp.getParamName() || String(interp.getParamName()) === ".") {
                interp.setParamName("custom_wert");
                interpUpdated++;
              }
            }
          } catch (eInterp) {}

          dumpSb.append('    </Function>\r\n');
        }
      }
      dumpSb.append('  </Group>\r\n');
    }
    dumpSb.append('</CafdDefinition>\r\n');

    var stats = " (groups=" + grpRenamed + ", fns=" + fnRenamed + ", values=" + cpRenamed + ", read=" + interpUpdated
      + ", untrimmed=" + untrimmedHits + (posHits ? " incl. " + posHits + " by field position" : "")
      + (transferHits ? " incl. " + transferHits + " transferred (unverified)" : "") + ")";
    var pending = runtimeState.get(tag) === "pending" ? "; full labels follow once its definition is built" : "";
    if (!def && untrimmedHits) log("Labelled " + tag + " from untrimmed names only" + stats + pending);
    else if (!def) log("No definition for " + tag + (pending || ": press Build definitions, or see runtime_defs.log"));
    else log("Labelled " + tag + stats);
    if (DUMP_DIR) {
      try {
        var bw = new BufferedWriter(new FileWriter(new File(DUMP_DIR, tag + "_applied.xml")));
        bw.write(dumpSb.toString());
        bw.close();
      } catch (eDump) {
        log("Error writing dump file: " + eDump);
      }
    }

    return true;
  }

  // FP_<main series>.xml: in the configured series' definitions folder, or in that main series' own folder
  // (workspace\definitions\<series>, where an FP of another series is built on demand).
  function fpXmlFile(mainSeries) {
    var f = new File(DEF_DIR, "FP_" + mainSeries + ".xml");
    if (f.exists()) return f;
    var g = new File(new File(new File(DEF_DIR).getParentFile(), mainSeries), "FP_" + mainSeries + ".xml");
    return g.exists() ? g : null;
  }

  // The FP (vehicle profile) of a car whose main series has no FP definition yet: build one in the background with
  // ocm/runtime_defs.py --prepare --series <series> (FA->FP mapping from your psdzdata + criteria labels), using
  // the E-Sys that's running. The next tick relabels the FP view when the file appears.
  function requestFpDefinition(mainSeries) {
    var tag = "FP_" + mainSeries;
    if (!runtimeExec || !PYTHON || !TOOLS || runtimeState.containsKey(tag)) return;
    runtimeState.put(tag, "pending");
    log("Building the vehicle profile labels for main series " + mainSeries + " in the background");
    runtimeExec.submit(new java.lang.Runnable({ run: function () {
      var t0 = System.currentTimeMillis();
      try {
        var esysDir = new File(String(System.getProperty("java.home"))).getParentFile().getPath();   // <E-Sys>\jre -> <E-Sys>
        var pb = new java.lang.ProcessBuilder(Java.to([PYTHON, "-u", new File(TOOLS, "runtime_defs.py").getPath(),
                                                       "--prepare", "--series", mainSeries, "--esys-dir", esysDir],
                                                      "java.lang.String[]"));
        pb.redirectErrorStream(true);
        pb.redirectOutput(java.lang.ProcessBuilder.Redirect.appendTo(new File(LOG_FILE.getParentFile(), "runtime_defs.log")));
        pb.environment().put("PYTHONIOENCODING", "utf-8");
        var rc = pb.start().waitFor();
        runtimeState.put(tag, rc === 0 && fpXmlFile(mainSeries) ? "done" : "failed");
        log((rc === 0 && fpXmlFile(mainSeries) ? "Built the vehicle profile labels for " : "Building the vehicle profile labels failed for ")
            + mainSeries + " (" + ((System.currentTimeMillis() - t0) / 1000).toFixed(1) + "s"
            + (rc === 0 ? "" : ", exit " + rc + ", see runtime_defs.log") + ")");
      } catch (eRun) {
        runtimeState.put(tag, "failed");
        log("Building the vehicle profile labels failed for " + mainSeries + ": " + eRun);
      }
    }}));
  }

  function loadFpXmlDef(mainSeries) {
    var f = fpXmlFile(mainSeries);
    if (!f) return null;

    var dbf = DocumentBuilderFactory.newInstance();
    var db = dbf.newDocumentBuilder();
    var doc = db.parse(f);
    var root = doc.getDocumentElement();

    var def = {
      file: f.getAbsolutePath(),
      lastModified: f.lastModified(),
      categories: {}
    };

    var catNodes = root.getElementsByTagName("Category");
    for (var i = 0; i < catNodes.getLength(); i++) {
      var cEl = catNodes.item(i);
      var cVal = String(cEl.getAttribute("val"));
      var cName = String(cEl.getAttribute("name") || "");
      var cObj = { name: cName, crits: {} };

      var critNodes = cEl.getElementsByTagName("Crit");
      for (var j = 0; j < critNodes.getLength(); j++) {
        var crEl = critNodes.item(j);
        var crVal = String(crEl.getAttribute("val"));
        var crName = String(crEl.getAttribute("name") || "");
        if (crVal && crName) {
          cObj.crits[crVal] = crName;
        }
      }
      def.categories[cVal] = cObj;
    }
    return def;
  }

  function applyAndDumpFp(fp) {
    var hdr = fp.getHeader();
    var mainSeries = (hdr && hdr.getMainSeries()) ? String(hdr.getMainSeries()) : SERIES;
    var fpFile = fpXmlFile(mainSeries);
    var stateKey = fpFile ? String(fpFile.lastModified()) : "none";
    if (!fpFile) requestFpDefinition(mainSeries);

    if (processedFps.containsKey(fp) && String(processedFps.get(fp)) === stateKey) {
      return false;
    }
    processedFps.put(fp, stateKey);

    var sfp = fp.getStandardFP();
    if (!sfp) return false;
    var cats = sfp.getCat();
    if (!cats) return false;

    var fpDef = null;
    try {
      fpDef = loadFpXmlDef(mainSeries);
    } catch (eFpXml) {
      log("Error loading FP XML definition: " + eFpXml);
    }

    var catRenamed = 0;
    var critRenamed = 0;

    var sb = new java.lang.StringBuilder();
    sb.append('<?xml version="1.0" encoding="utf-8"?>\r\n');
    sb.append('<FpDump mainSeries="' + mainSeries + '" series="' + (hdr ? hdr.getSeries() : "") + '" ver="' + (hdr ? (hdr.getMajorVersion() + "_" + hdr.getMinorVersion() + "_" + hdr.getPatchVersion()) : "") + '">\r\n');
    for (var i = 0; i < cats.size(); i++) {
      var cat = cats.get(i);
      var cValStr = String(cat.getVal());
      var cDef = (fpDef && fpDef.categories[cValStr]) ? fpDef.categories[cValStr] : null;
      if (cDef && cDef.name) {
        cat.setName(cDef.name);
        cat.setNameEN(cDef.name);
        catRenamed++;
      }

      sb.append('  <Category val="' + cValStr + '" name="' + xmlEscape(cat.getName()) + '" nameEN="' + xmlEscape(cat.getNameEN()) + '">\r\n');
      var crits = cat.getCrit();
      if (crits) {
        for (var j = 0; j < crits.size(); j++) {
          var crit = crits.get(j);
          var crValStr = String(crit.getVal());
          var crLabel = (cDef && cDef.crits[crValStr]) ? cDef.crits[crValStr] : null;
          if (crLabel) {
            crit.setName(crLabel);
            crit.setNameEN(crLabel);
            critRenamed++;
          }
          sb.append('    <Crit val="' + crValStr + '" value="' + xmlEscape(crit.getValue()) + '" name="' + xmlEscape(crit.getName()) + '" nameEN="' + xmlEscape(crit.getNameEN()) + '" />\r\n');
        }
      }
      sb.append('  </Category>\r\n');
    }
    sb.append('</FpDump>\r\n');

    if (DUMP_DIR) {
      try {
        var bw = new BufferedWriter(new FileWriter(new File(DUMP_DIR, "current_fp.xml")));
        bw.write(sb.toString());
        bw.close();
      } catch (eFpDump) {}
    }
    log("Labelled FP " + mainSeries + " (categories=" + catRenamed + ", criteria=" + critRenamed + ")");
    return true;
  }

  function refreshTreeNodesRecursively(model, node) {
    if (!node) return;
    try {
      var uo = (typeof node.getUserObjectNoWrapper === "function") ? node.getUserObjectNoWrapper() : null;
      if (uo && uo.getClass().getName() === "com.bmw.esys.domain.jaxb.caf1.TInterpretations") {
        if (node.getChildCount() > 0) {
          com.bmw.esys.gui.fdl.Fdl1TreeNode.setInterpretationSelectionList(node.getChildAt(0));
        }
      } else if (uo && legacyInterpClass !== null && legacyInterpClass.isInstance(uo) && node.getChildCount() > 0) {
        // E-Sys 3.x: the "Ausgelesen" (read) value's dropdown still lists the old value names after renaming,
        // and E-Sys then can't draw or expand the node. Rebuild it the same way (3.x's FDLTreeNode).
        com.bmw.esys.gui.coding.FDLTreeNode.setInterpretationSelectionList(node.getChildAt(0));
      }
    } catch (eSetList) {}

    try {
      model.nodeChanged(node);
    } catch (eNC) {}

    try {
      var cnt = node.getChildCount();
      for (var i = 0; i < cnt; i++) {
        refreshTreeNodesRecursively(model, node.getChildAt(i));
      }
    } catch (eChildren) {}
  }

  function scanContainer(comp) {
    if (!comp) return;
    if (comp instanceof JTree) {
      try {
        var model = comp.getModel();
        var root = model ? model.getRoot() : null;
        if (root && typeof root.getUserObjectNoWrapper === "function") {
          var uo = root.getUserObjectNoWrapper();
          if (uo) {
            var clsName = uo.getClass().getName();
            if (clsName === "com.bmw.esys.domain.jaxb.caf1.Caf1") {
              if (applyAndDumpCaf(uo)) {
                refreshTreeNodesRecursively(model, root);
                comp.treeDidChange();
                comp.revalidate();
                comp.repaint();
              }
            } else if (clsName === "com.bmw.esys.domain.jaxb.fp.TFzgProfil") {
              if (applyAndDumpFp(uo)) {
                refreshTreeNodesRecursively(model, root);
                comp.treeDidChange();
                comp.revalidate();
                comp.repaint();
              }
            } else if (isLegacyCaf(uo)) {             // E-Sys 3.x CAF / FDL view
              if (applyAndDumpCaf(uo)) {
                refreshTreeNodesRecursively(model, root);
                comp.treeDidChange();
                comp.revalidate();
                comp.repaint();
              }
            }
          }
        }
      } catch (eTree) {
        log("Tree scan error: " + eTree);
      }
    }
    if (comp instanceof Container) {
      var children = comp.getComponents();
      for (var i = 0; i < children.length; i++) {
        scanContainer(children[i]);
      }
    }
  }

  // Launch options (OpenLauncher > Launch options): -Docm.feature.<name>=0 switches a feature off. Default: on.
  function feature(name) { return String(System.getProperty("ocm.feature." + name, "1")) !== "0"; }
  var LABELS = feature("labels");
  var off = ["labels", "tools", "backup"].filter(function (n) { return !feature(n); });
  if (off.length) log("Launch options: off for this session: " + off.join(", "));

  // OpenLauncher section in E-Sys's navigation bar (esys/tools.js). Optional: any problem only disables the tools.
  var tools = null;
  try {
    var toolsFile = new File(SCRIPT_FILE.getParentFile(), "tools.js");
    if (toolsFile.exists() && feature("tools")) {
      tools = load(toolsFile.getPath())({
        log: log, defDir: DEF_DIR, runtimeDir: RUNTIME_DIR, python: PYTHON, tools: TOOLS,
        logDir: LOG_FILE.getParentFile(), loadXmlDef: loadXmlDef,
        findDef: function (cid, ver) { var v = String(ver).split("_"); return findXmlDefFile(cid, v[0], v[1], v[2]); }
      });
      System.getProperties().put("ocm.toolsObj", tools);   // test hook (headless tests)
    }
  } catch (eTools) {
    log("OpenLauncher tools not loaded: " + eTools);
  }

  // FA / SVT read from the vehicle -> copy in E-Sys's data folder (esys/backup.js). Optional like the tools.
  var backup = null;
  try {
    var backupFile = new File(SCRIPT_FILE.getParentFile(), "backup.js");
    if (backupFile.exists() && feature("backup")) {
      backup = load(backupFile.getPath())({
        log: log, dataDir: System.getProperty("ocm.datadir") ? String(System.getProperty("ocm.datadir")) : null
      });
    }
  } catch (eBackup) {
    log("FA/SVT backup not loaded: " + eBackup);
  }

  var tickFn = new java.lang.Runnable({
    run: function() {
      try {
        if (tools) tools.tick();
      } catch (eT) {
        log("Tools error: " + eT);
        tools = null;
      }
      try {
        if (backup) backup.tick();
      } catch (eB) {
        log("FA/SVT backup error: " + eB);
        backup = null;
      }
      try {
        var wins = LABELS ? Window.getWindows() : [];   // labels off (Launch options): leave E-Sys's trees as they are
        for (var i = 0; i < wins.length; i++) {
          if (wins[i].isShowing()) {
            scanContainer(wins[i]);
          }
        }
      } catch (e) {
        log("Tick error: " + e);
      }
    }
  });

  System.getProperties().put("ocm.tick", tickFn);
  // Test hook (used by tools\test_agent.js to run the mapping headlessly on a JAXB Caf1).
  System.getProperties().put("ocm.applyCaf", function (caf) { return applyAndDumpCaf(caf); });
  System.getProperties().put("ocm.lastModified", java.lang.Long.valueOf(SCRIPT_FILE.lastModified()));
  log("OpenCAFDMap script loaded (timestamp=" + SCRIPT_FILE.lastModified() + ")");

  if (!System.getProperties().get("ocm.daemonStarted")) {
    System.getProperties().put("ocm.daemonStarted", "true");
    var daemon = new java.lang.Thread(new java.lang.Runnable({
      run: function() {
        log("OpenCAFDMap background daemon started");
        while (true) {
          try {
            java.lang.Thread.sleep(900);
            // Number(): Java 8's Nashorn hands longs over as java.lang.Long objects, and !== between two
            // Long objects compares identity, which made every tick look like an edit (endless hot reloads).
            var curMod = Number(SCRIPT_FILE.lastModified());
            var prevModObj = System.getProperties().get("ocm.lastModified");
            var prevMod = prevModObj ? Number(prevModObj.longValue()) : 0;
            if (curMod > 0 && curMod !== prevMod) {
              log("Detected change in agent.js, hot-reloading...");
              var sem = new javax.script.ScriptEngineManager();
              var eng = sem.getEngineByName("nashorn");
              var fr = new java.io.FileReader(SCRIPT_FILE);
              eng.eval(fr);
              fr.close();
            }
            var r = System.getProperties().get("ocm.tick");
            if (r) {
              SwingUtilities.invokeLater(r);
            }
          } catch (eLoop) {
            log("Daemon loop error: " + eLoop);
          }
        }
      }
    }), "OpenCAFDMap-Daemon");
    daemon.setDaemon(true);
    daemon.start();
  }
})();
