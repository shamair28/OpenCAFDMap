// Headless test of agent.js: load a decrypted CAFD into E-Sys's JAXB Caf1 model, run the agent's
// labelling on it and report what got renamed. No E-Sys window is opened.
//   python ocm\esys.py run esys\test_agent.js <defs folder> <decrypted cafd .xml(.gz)> [log]  [runtime dir] [python]
var File = java.io.File;
var System = java.lang.System;
var err = System.err;
var here = new File(String(__DIR__));
System.setProperty("ocm.launcher", "1");
System.setProperty("ocm.defs", String(arguments[0]));
System.setProperty("ocm.agent", new File(here, "agent.js").getAbsolutePath());
System.setProperty("ocm.contrib", new File(here.getParentFile(), "definitions").getAbsolutePath());
System.setProperty("ocm.log", arguments.length > 2 ? String(arguments[2]) : File.createTempFile("ocm", ".log").getAbsolutePath());
System.getProperties().put("ocm.daemonStarted", "true"); // no GUI polling thread in a headless test
// optional on-demand definitions: [runtime dir] [python exe]
if (arguments.length > 4) {
  System.setProperty("ocm.runtime", String(arguments[3]));
  System.setProperty("ocm.python", String(arguments[4]));
  System.setProperty("ocm.tools", new File(here.getParentFile(), "ocm").getAbsolutePath());
}
load(new File(here, "agent.js").getAbsolutePath());
var apply = System.getProperties().get("ocm.applyCaf");

var src = String(arguments[1]);
var ins = new java.io.FileInputStream(src);
if (/\.gz$/i.test(src)) ins = new java.util.zip.GZIPInputStream(ins);
var Caf1 = Java.type("com.bmw.esys.domain.jaxb.caf1.Caf1");
var ctx = javax.xml.bind.JAXBContext.newInstance(Java.to([Caf1.class], "java.lang.Class[]"));
var obj = ctx.createUnmarshaller().unmarshal(new javax.xml.transform.stream.StreamSource(ins), Caf1.class).getValue();
ins.close();
apply(obj);
var exec = System.getProperties().get("ocm.runtimeExec");
if (exec) {
  report("BEFORE");
  exec.shutdown();
  exec.awaitTermination(300, java.util.concurrent.TimeUnit.SECONDS);
  apply(obj);  // the definition file now exists: relabel as the GUI tick would
}
report("RESULT");
System.exit(0);

function report(label) {
var dgs = obj.getData().getDatagroup();
var fnTotal = 0, fnNamed = 0, cpTotal = 0, cpLabelled = 0, grpNamed = 0;
for (var i = 0; i < dgs.size(); i++) {
  if (!/^Group_/.test(String(dgs.get(i).getName()))) grpNamed++;
  var fns = dgs.get(i).getFunction();
  for (var j = 0; j < fns.size(); j++) {
    var fn = fns.get(j);
    fnTotal++;
    if (!/_Byte_\d+/.test(String(fn.getName())) && String(fn.getName()) !== ".") fnNamed++;
    var cps = fn.getCondParam();
    for (var k = 0; k < cps.size(); k++) {
      cpTotal++;
      if (/\[|^wert_\d+ \S/.test(String(cps.get(k).getParamName()))) cpLabelled++;
    }
    if (j === 0 && i < 4) err.println("SAMPLE " + dgs.get(i).getAddress() + " " + dgs.get(i).getName() + " / " +
                                      fn.getName() + " / " + cps.get(0).getParamName());
  }
}
err.println(label + " ECU " + obj.getSweHeader().getEcuName() + ": groups " + grpNamed + "/" + dgs.size() +
            " named, functions " + fnNamed + "/" + fnTotal + " named, values " + cpLabelled + "/" + cpTotal + " labelled");
}
