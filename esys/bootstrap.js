// Open CAFD Map: entry point for E-Sys started by OpenLauncher (ocm\esys.py launch).
//
// OpenLauncher runs E-Sys's own JRE + classpath with Java's JavaScript runner (jdk.nashorn.tools.Shell)
// as the main class. This script loads the labelling agent, then starts E-Sys exactly as E-Sys.bat
// does (com.bmw.esys.gui.control.Framework). Nothing is installed into E-Sys; an E-Sys started any
// other way never runs this code.
(function () {
  var System = java.lang.System;
  if (String(System.getProperty("ocm.launcher")) !== "1") {
    System.err.println("Open CAFD Map: bootstrap.js must be started by OpenLauncher.");
    System.exit(2);
  }

  // The agent is optional for E-Sys: any problem loading it is logged and E-Sys starts unlabelled.
  try {
    load(String(System.getProperty("ocm.agent")));
  } catch (e) {
    try {
      var w = new java.io.FileWriter(String(System.getProperty("ocm.log")), true);
      w.write("[bootstrap] agent failed to load, starting E-Sys without labels: " + e + "\r\n");
      w.close();
    } catch (e2) {}
  }

  // E-Sys's main class comes from its own E-Sys.bat (ocm\esys.py). If E-Sys fails while starting, log why and
  // exit: otherwise Java's GUI thread keeps the process alive and the splash screen stays up forever.
  var mainClass = String(System.getProperty("ocm.main") || "com.bmw.esys.gui.control.Framework");
  try {
    Java.type(mainClass).main(Java.to([], "java.lang.String[]"));
  } catch (e) {
    var msg = "[bootstrap] E-Sys " + (System.getProperty("ocm.esysVersion") || "") + " failed to start (" + mainClass + "): " + e;
    System.err.println(msg);
    if (e instanceof java.lang.Throwable) e.printStackTrace();
    try {
      var w2 = new java.io.FileWriter(String(System.getProperty("ocm.log")), true);
      w2.write(msg + "\r\n");
      w2.close();
    } catch (e3) {}
    System.exit(3);
  }
})();
