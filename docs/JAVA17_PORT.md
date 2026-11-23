# Porting OpenLauncher to E-Sys on Java 17 (E-Sys 26.04)

Working notes for the `java-17-compat` branch (based on `v3-testing`). Status: investigation.

## What changed in E-Sys 26.04.02
- **Java:** bundled JRE is Azul Zulu **17.0.16**. Nashorn (`jdk.scripting.nashorn`) was removed from Java in 15, and
  E-Sys ships no other script engine (no Rhino, GraalJS, Groovy, ...). `java.scripting` and `jdk.dynalink` are present
  (the standalone Nashorn needs dynalink). No `jdk.compiler` in the JRE.
- **Launch:** `E-Sys.bat` builds its VM options in `%OPTION_*%` variables and adds
  `--add-opens java.base/java.lang=ALL-UNNAMED`. Classpath: `lib\3rd-party\*`, `lib\PsdZ\*`, `lib\UUID\*`,
  `lib\environment\*`, `lib\estcm\*`, `lib\*`, `lib\fs\*`, `lib\odxchecker\*`, `lib\odxchecker\odx201\lib\*`, `lib\xml\*`.
  Main class unchanged: `com.bmw.esys.gui.control.Framework`.
- **JAXB:** both `javax` (jaxb-api 2.3.1) and `jakarta` (4.0.4) are on the classpath.
- **GUI classes moved** (25.10 -> 26.04):
  - `gui.control.EsysController` -> `gui.control.guicontroller.EsysGUIController` (same `instance`, `cacheModules`,
    `esysFrame`, `getCachedModules()`, `getEsysFrame()`)
  - `gui.fdl.Fdl1TreeNode` -> `gui.fdl.model.Fdl1TreeNode` (`setInterpretationSelectionList` unchanged);
    likewise `gui.caf.Caf*TreeNode` -> `gui.caf.model.*`, `gui.sweseq.SweseqTreeNode` -> `gui.sweseq.model.*`
  - module controllers -> `*.controller` packages (`fdl.FDLModuleController` -> `fdl.controller.FdlEditorController`,
    `svt.SvtEditor*` -> `svt.controller.*`, `caf.CafModuleController` -> `caf.controller.CafViewerController`)
- Every other E-Sys class our scripts reference (28 checked) still exists; apart from the moves, no member they use is
  gone (`EsysFrame.currentView`, PsdZ, JAXB `domain.jaxb.*` models unchanged).

## What breaks today
1. **No JavaScript engine**: `jdk.nashorn.tools.Shell` doesn't exist, so nothing of ours runs (launch, `decrypt.js`,
   `test_agent.js`). `ocm/esys.py check_java` already refuses Java > 14 with a clear message.
2. **E-Sys.bat parsing** read `--add-opens`' value as the main class and lost every VM option. **Fixed on this branch**
   (`KEEP_WITH_VALUE` in `ocm/esys.py`); 25.10 parses as before.
3. **Renamed GUI classes** (above): 6 scripts reach E-Sys through `EsysController`; `agent.js` calls
   `gui.fdl.Fdl1TreeNode` directly. A lookup that tries the new name, then the old, keeps 25.10 working.
4. **Cheat Sheet Apply**: 25.10's FDL module had `getFDLController()`; 26.04's `FdlEditorController` doesn't. Needs a
   new way to reach the FDL tree (likely `fdl.controller.FdlTreeController`).

## Ways to get a script engine back
| Option | What | Effort | Notes |
|---|---|---|---|
| **A. Standalone Nashorn** | OpenJDK Nashorn 15.x (`org.openjdk.nashorn:nashorn-core`, ~2.3 MB) + ASM 7.3.1 (asm, asm-commons, asm-tree, asm-util, ~0.4 MB) on E-Sys's classpath; main class `org.openjdk.nashorn.tools.Shell` | Small: scripts stay JavaScript, plus the class moves above | GPL-2.0 with Classpath Exception (like the JDK). Ship the jars or download them once with pinned SHA-256s. Same engine, so 25.10 behaviour is unchanged. |
| **B. Rewrite in Java** | Agent and tools as Java, compiled with `javac --release 17` against E-Sys's jars, shipped as one jar | Large: ~3,500 lines of JS (agent, tools, cheat sheet, FA Helper, DTC, backup, decrypt) | No third-party engine; a release step must build the jar (setup-java in release.yml). |
| C. GraalJS | Graal JavaScript on the classpath | Medium | Tens of MB, different Java interop rules than Nashorn. |

Tools available on the maintainer PC: JDK 27 (`C:\Program Files\Java\jdk-27.0.1.1`: javac, javap, jshell) and JDK 8.
