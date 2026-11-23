// Live test of Cheat Sheet Apply in a real E-Sys window: E-Sys is started exactly like OpenLauncher starts it, but
// with this script in place of bootstrap.js (ocm/test_apply.py builds the command). It opens an NCD in E-Sys's FDL
// editor, then runs the Cheat Sheet's own Apply steps (pick a listed value, set a custom value) on it and checks that
// E-Sys took them. Writes the results to -Docm.test.out and closes E-Sys. Nothing is saved: the NCD file is untouched.
var System = java.lang.System, File = java.io.File, SwingUtilities = javax.swing.SwingUtilities;
var NCD = String(System.getProperty("ocm.test.ncd")), OUT = String(System.getProperty("ocm.test.out"));
var lines = [];
function say(s) {
  lines.push(String(s));
  try {
    var w = new java.io.OutputStreamWriter(new java.io.FileOutputStream(OUT), "UTF-8");
    w.write(lines.join("\r\n") + "\r\n");
    w.close();
  } catch (e) {}
}
function onEdt(fn) {
  var box = { v: null, e: null };
  SwingUtilities.invokeAndWait(function () { try { box.v = fn(); } catch (e) { box.e = e; } });
  if (box.e) throw box.e;
  return box.v;
}
function waitFor(what, seconds, fn) {
  for (var i = 0; i < seconds * 2; i++) {
    var v = null;
    try { v = fn(); } catch (e) {}
    if (v) return v;
    java.lang.Thread.sleep(500);
  }
  throw "timed out waiting for " + what;
}
function controllerClass() {
  var names = ["com.bmw.esys.gui.control.guicontroller.EsysGUIController", "com.bmw.esys.gui.control.EsysController"];
  for (var i = 0; i < names.length; i++) { try { return java.lang.Class.forName(names[i]); } catch (e) {} }
  return null;
}

var worker = new java.lang.Thread(function () {
  var code = 1;
  try {
    say("E-Sys " + System.getProperty("ocm.esysVersion") + ", Java " + System.getProperty("java.version"));
    var tools = waitFor("OpenLauncher's tools", 240, function () { return System.getProperties().get("ocm.toolsObj"); });
    var cs = waitFor("the Cheat Sheet", 60, function () { return tools.cheat(); });
    var T = cs.test;
    var CC = controllerClass(), C = Java.type(CC.getName());
    var inst = waitFor("E-Sys's controller", 120, function () {
      var f = CC.getDeclaredField("instance"); f.setAccessible(true); return f.get(null);
    });
    var frame = waitFor("E-Sys's window", 120, function () { var fr = inst.getEsysFrame(); return fr && fr.isShowing() ? fr : null; });
    say("controller " + CC.getName());

    var params = new java.util.HashMap();
    params.put("file", new File(NCD));
    onEdt(function () { C.openModule("FDLEditor", "module.section.editors", params); });
    var tree = waitFor("the NCD in the FDL editor", 120, function () { return onEdt(function () { return T.findTree(frame); }); });
    say("FDL editor shows " + NCD);
    var ctl = onEdt(function () { return T.fdlController(); });
    say("fdlController(): " + (ctl ? ctl.getClass().getName() : "null"));
    if (!ctl) throw "no FDL controller";

    var res = onEdt(function () {
      var idx = T.indexCaf(tree), out = { step: null, custom: null };
      var keys = Object.keys(idx.fns);
      for (var i = 0; i < keys.length && !(out.step && out.custom); i++) {
        var hit = idx.fns[keys[i]], fn = hit.fn, cps = fn.getCondParam();
        if (!cps || cps.size() < 2 || !fn.getInterpretation()) continue;
        var cur = T.tvalueHex(fn.getInterpretation().getValues());
        for (var k = 0; k < cps.size(); k++) {
          var cp = cps.get(k), hx = T.tvalueHex(cp.getValues());
          if (!hx || hx === cur) continue;
          var r = { hit: hit, cp: cp, cpHex: hx, fname: keys[i] + " " + fn.getName(), customHex: hx };
          if (!out.step) {
            try {
              T.applyStep(tree, r);
              var after = T.tvalueHex(fn.getInterpretation().getValues());
              out.step = (after === hx ? "PASS" : "FAIL") + " applyStep " + r.fname + ": " + cur + " -> " + after +
                         " (wanted " + hx + ", '" + cp.getParamName() + "')";
            } catch (eS) { out.step = "FAIL applyStep " + r.fname + ": " + eS; }
          } else if (!out.custom) {
            try {
              T.applyCustom(tree, r);
              var after2 = T.tvalueHex(fn.getInterpretation().getValues());
              out.custom = (after2 === hx ? "PASS" : "FAIL") + " applyCustom " + r.fname + ": " + cur + " -> " + after2;
            } catch (eC) { out.custom = "FAIL applyCustom " + r.fname + ": " + eC; }
          }
          break;
        }
      }
      return out;
    });
    say(res.step || "FAIL: no function to pick a value for");
    say(res.custom || "FAIL: no function for a custom value");
    code = (String(res.step).indexOf("PASS") === 0 && String(res.custom).indexOf("PASS") === 0) ? 0 : 1;
  } catch (e) {
    say("ERROR " + e);
  }
  say("DONE " + (code === 0 ? "PASS" : "FAIL"));
  java.lang.Runtime.getRuntime().halt(code);   // no save prompt: nothing was written
}, "ocm-test-apply");
worker.start();

load(new File(new File(__DIR__), "bootstrap.js").getPath());
