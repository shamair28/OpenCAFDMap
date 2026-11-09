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

  var Framework = Java.type("com.bmw.esys.gui.control.Framework");
  Framework.main(Java.to([], "java.lang.String[]"));
})();
