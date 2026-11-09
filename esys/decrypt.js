// Headless decryption of your own psdzdata with E-Sys's own PsdZ logic. Runs on your machine only;
// the decrypted files stay in your workspace and are never committed.
//   python ocm\esys.py run esys\decrypt.js <outDir> <mainSeries|ALL> <fafpOut> [limit] [idFilterCsv]
// Writes <outDir>\cafd_<id>_<main>_<sub>_<patch>.xml.gz for every CAFD whose fpInfo lists <mainSeries>,
// and <fafpOut> = the newest FA->FP mapping (fafp) of that main series.
// Keeps a manifest (manifest.tsv) so reruns skip files already handled.
var File = java.io.File;
var outDir = new File(arguments[0]);
var series = String(arguments[1] || "G020");
var fafpOut = arguments.length > 2 && arguments[2] ? new File(arguments[2]) : null;
var limit = arguments.length > 3 ? parseInt(arguments[3], 10) : 0;
var idFilter = arguments.length > 4 && arguments[4] ? String(arguments[4]).toLowerCase().split(",") : null;
outDir.mkdirs();

var cfg = Java.type("com.bmw.esys.domain.psdz.PsdzConfiguration").getInstance();
cfg.setPsdzRoot();
var pl = Java.type("com.bmw.esys.domain.psdz.PsdzLogic").getInstance();
var cafDir = new File(cfg.getRoot(), "psdzdata\\swe\\cafd");
var tf = javax.xml.transform.TransformerFactory.newInstance().newTransformer();

// manifest: filename \t status(keep|skip|err) \t note
var manifestFile = new File(outDir, "manifest.tsv");
var done = {};
if (manifestFile.exists()) {
  var lines = java.nio.file.Files.readAllLines(manifestFile.toPath());
  for (var i = 0; i < lines.size(); i++) done[String(lines.get(i)).split("\t")[0]] = true;
}
var mw = new java.io.BufferedWriter(new java.io.FileWriter(manifestFile, true));

function seriesOf(doc) {
  var out = [];
  // fpInfo/mainSeries[@name] lists every series the CAFD serves; DOM may or may not be NS-aware.
  var nl = doc.getElementsByTagNameNS("*", "mainSeries");
  if (nl.getLength() === 0) nl = doc.getElementsByTagName("mainSeries");
  for (var i = 0; i < nl.getLength(); i++) {
    var n = nl.item(i).getAttribute("name");
    if (n && out.indexOf(String(n)) < 0) out.push(String(n));
  }
  return out;
}

var files = cafDir.listFiles();
java.util.Arrays.sort(files);
var t0 = java.lang.System.currentTimeMillis();
var n = 0, kept = 0, errs = 0;
for (var i = 0; i < files.length; i++) {
  var f = files[i];
  var name = String(f.getName());
  if (name.indexOf("cafd_") !== 0 || done[name]) continue;
  if (idFilter) {
    var id = name.substring(5, 13);
    if (idFilter.indexOf(id) < 0) continue;
  }
  if (limit && n >= limit) break;
  n++;
  try {
    var doc = pl.decryptVerifyCAF(f);
    var ser = seriesOf(doc);
    if (series === "ALL" || ser.indexOf(series) >= 0) {
      var m = name.match(/^cafd_([0-9a-f]{8})\.caf\.(\d{3})_(\d{3})_(\d{3})$/);
      var outName = m ? ("cafd_" + m[1].toUpperCase() + "_" + m[2] + "_" + m[3] + "_" + m[4] + ".xml.gz") : (name + ".xml.gz");
      var os = new java.util.zip.GZIPOutputStream(new java.io.FileOutputStream(new File(outDir, outName)));
      tf.transform(new javax.xml.transform.dom.DOMSource(doc), new javax.xml.transform.stream.StreamResult(os));
      os.close();
      kept++;
      mw.write(name + "\tkeep\t" + ser.join(",") + "\r\n");
    } else {
      mw.write(name + "\tskip\t" + ser.join(",") + "\r\n");
    }
  } catch (e) {
    errs++;
    mw.write(name + "\terr\t" + String(e).replace(/[\r\n\t]/g, " ") + "\r\n");
  }
  if (n % 100 === 0) {
    mw.flush();
    var dt = (java.lang.System.currentTimeMillis() - t0) / 1000;
    java.lang.System.err.println("PROGRESS " + n + " processed, " + kept + " kept, " + errs + " errors, " + dt.toFixed(0) + "s");
  }
}
mw.close();

// FA->FP mapping for the series (labels FP criteria as option codes / type keys / dates)
if (fafpOut) {
  var best = null, bestVer = "";
  var fapFiles = new File(cfg.getRoot(), "psdzdata\\swe\\fafp").listFiles() || [];
  for (var j = 0; j < fapFiles.length; j++) {
    var fn = String(fapFiles[j].getName());
    var mv = fn.match(/^fafp_[0-9a-f]{8}\.fap\.(\d{3}_\d{3}_\d{3})$/i);
    if (!mv) continue;
    try {
      var fdoc = pl.decryptVerifyCAF(fapFiles[j]);
      var hdrs = fdoc.getElementsByTagNameNS("*", "sweHeader");
      if (hdrs.getLength() === 0) hdrs = fdoc.getElementsByTagName("sweHeader");
      var ms = hdrs.getLength() ? String(hdrs.item(0).getAttribute("mainSeries")) : "";
      if (ms === series && mv[1] > bestVer) { best = fdoc; bestVer = mv[1]; }
    } catch (eF) {
      java.lang.System.err.println("NOTE fafp " + fn + ": " + eF);
    }
  }
  if (best) {
    fafpOut.getParentFile().mkdirs();
    tf.transform(new javax.xml.transform.dom.DOMSource(best), new javax.xml.transform.stream.StreamResult(fafpOut));
    java.lang.System.err.println("PROGRESS fafp for " + series + " (" + bestVer + ") -> " + fafpOut);
  } else {
    java.lang.System.err.println("NOTE no fafp found for " + series + " (option-code labels will be missing)");
  }
}
java.lang.System.err.println("DONE " + n + " processed, " + kept + " kept, " + errs + " errors, " +
  ((java.lang.System.currentTimeMillis() - t0) / 1000).toFixed(0) + "s");
java.lang.System.exit(0);
