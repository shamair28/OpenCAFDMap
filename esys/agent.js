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
  var jaxbCtx = null;

  function saveCafXml(caf, file) {
    var cls = caf.getClass();
    if (!jaxbCtx) jaxbCtx = javax.xml.bind.JAXBContext.newInstance(Java.to([cls], "java.lang.Class[]"));
    var el = new javax.xml.bind.JAXBElement(new javax.xml.namespace.QName(CAF_NS, "caf"), cls, caf);
    file.getParentFile().mkdirs();
    var tmp = new File(file.getPath() + ".tmp");
    var os = new java.util.zip.GZIPOutputStream(new java.io.FileOutputStream(tmp));
    try { jaxbCtx.createMarshaller().marshal(el, os); } finally { os.close(); }
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

  // BMW untrimmed names packed by datagroup layout (definitions/untrimmed/<CAFD id>.tsv.gz, ocm/untrimmed.py).
  // Applied at runtime to whatever is still a placeholder, so versions that were never exported get names too.
  var untrimmedCache = {};
  function loadUntrimmed(cafId) {
    if (untrimmedCache.hasOwnProperty(cafId)) return untrimmedCache[cafId];
    var res = null;
    try {
      var f = CONTRIB_DIR ? new File(new File(CONTRIB_DIR, "untrimmed"), cafId + ".tsv.gz") : null;
      if (f && f.exists()) {
        res = { groups: {}, fallback: {} };
        var br = new java.io.BufferedReader(new java.io.InputStreamReader(
          new java.util.zip.GZIPInputStream(new java.io.FileInputStream(f)), "UTF-8"));
        var line;
        while ((line = br.readLine()) !== null) {
          line = String(line);
          if (!line || line.charAt(0) === "#") continue;
          var p = line.split("\t");
          if (p[0] === "G" && p[2] === "*") { res.fallback[p[1]] = p[7]; continue; }
          var key = p[1] + "|" + p[2];
          var g = res.groups[key] || (res.groups[key] = { G: null, F: {}, V: {} });
          if (p[0] === "G") g.G = p[7];
          else if (p[0] === "F") g.F[p[3] + "|" + p[4] + "|" + p[5]] = p[7];
          else if (p[0] === "V") g.V[p[3] + "|" + p[4] + "|" + p[5] + "|" + p[6]] = p[7];
        }
        br.close();
      }
    } catch (eU) {
      log("Error reading untrimmed names for CAFD_" + cafId + ": " + eU);
      res = null;
    }
    untrimmedCache[cafId] = res;
    return res;
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
      || /^\*?\s*wert_\d+(\s*\[[^\]]*\])?$/.test(n);
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

      var uG = null;
      if (uPack) {
        var uFns = dg.getFunction();
        if (uFns && uFns.size() > 0) uG = uPack.groups[addr + "|" + layoutHash(uFns)] || null;
        if (isPlaceholder(dg.getName())) {
          var uGName = (uG && uG.G) || uPack.fallback[addr];
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
          if (uG && uG.F[uKey] && isPlaceholder(fn.getName())) {
            fn.setName(uG.F[uKey]);
            untrimmedHits++;
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
              var uVal = (uG && cpHex) ? uG.V[uKey + "|" + cpHex] : null;
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
      + ", untrimmed=" + untrimmedHits + ")";
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

  function loadFpXmlDef(mainSeries) {
    var f = new File(DEF_DIR, "FP_" + mainSeries + ".xml");
    if (!f.exists()) return null;

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
    var fpXmlFile = new File(DEF_DIR, "FP_" + mainSeries + ".xml");
    var stateKey = fpXmlFile.exists() ? String(fpXmlFile.lastModified()) : "none";

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

  var tickFn = new java.lang.Runnable({
    run: function() {
      try {
        var wins = Window.getWindows();
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
            var curMod = SCRIPT_FILE.lastModified();
            var prevModObj = System.getProperties().get("ocm.lastModified");
            var prevMod = prevModObj ? prevModObj.longValue() : 0;
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
