# Porting OpenLauncher to E-Sys on Java 17 (E-Sys 26.04)

Working notes for the `java-17-compat` branch (based on `v3-testing`).

## Status (2026-11-23): option A works
With OpenJDK's standalone Nashorn 15.7 (`lib/nashorn/`, see its README) on E-Sys 26.04.02:
- **E-Sys starts through OpenLauncher**; the agent loads, and the OpenLauncher section shows Compare NCD, Cheat Sheet,
  FA Helper and DTC.
- **Labels**: headless agent test (CAFD 00002458) labels 20/22 functions from the untrimmed names, saves the CAFD
  through jakarta JAXB, builds its definition on demand (3.5 s) and relabels 22/22 functions, 34/34 values.
- **Decrypt** (`decrypt.js` with 26.04's PsdZ): byte-identical output to E-Sys 25.10 for the CAFDs tested.
- **Live FDL editor + Cheat Sheet Apply** (`python tests\live_apply.py <E-Sys folder> <NCD>`): E-Sys opens the NCD,
  the agent labels the live tree, and Apply picks a listed value and sets a custom value through E-Sys's own edit path
  (26.04: `FdlTreeController`, 25.10: `FDLController`). Passes on 26.04.02 and 25.10.03.
- **E-Sys 25.10** (Java 11) still uses the JRE's own Nashorn; same results as before.
- **Not checked yet**: the tools' views on screen (a screen overlay on the test PC hid them), FA/SVT backup and DTC
  against a car. Cheat Sheet Apply and FA Helper Apply stay behind "Try anyway" on 26.04 until it's added to
  `TESTED_ESYS` (`ocm/esys.py`).

Changes on the branch: `ocm/esys.py` (E-Sys.bat module options; `script_runner` picks the standalone Nashorn on Java
15+ and passes `--add-opens` to headless scripts), `esys/*.js` (moved GUI classes looked up under both names;
`jaxbFor()` picks javax or jakarta JAXB from the model class's annotations).

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
4. **JAXB**: 26.04's domain classes carry `jakarta.xml.bind` annotations; `javax.xml.bind` reads and writes them
   empty. **Fixed on this branch** (`jaxbFor()`).
5. **Cheat Sheet Apply**: 25.10's FDL module had `getFDLController()`; 26.04's `FdlEditorController` doesn't, but keeps
   an `FdlTreeController` (same `setValue(AttributeWrapper, Object)`) in its private field `fdlTreeController`.
   **Fixed on this branch** (`fdlController()` in `esys/cheatsheet.js` uses whichever exists).

## Ways to get a script engine back
| Option | What | Effort | Notes |
|---|---|---|---|
| **A. Standalone Nashorn** | OpenJDK Nashorn 15.x (`org.openjdk.nashorn:nashorn-core`, ~2.3 MB) + ASM 7.3.1 (asm, asm-commons, asm-tree, asm-util, ~0.4 MB) on E-Sys's classpath; main class `org.openjdk.nashorn.tools.Shell` | Small: scripts stay JavaScript, plus the class moves above | GPL-2.0 with Classpath Exception (like the JDK). Ship the jars or download them once with pinned SHA-256s. Same engine, so 25.10 behaviour is unchanged. |
| **B. Rewrite in Java** | Agent and tools as Java, compiled with `javac --release 17` against E-Sys's jars, shipped as one jar | Large: ~3,500 lines of JS (agent, tools, cheat sheet, FA Helper, DTC, backup, decrypt) | No third-party engine; a release step must build the jar (setup-java in release.yml). |
| C. GraalJS | Graal JavaScript on the classpath | Medium | Tens of MB, different Java interop rules than Nashorn. |

Tools available on the maintainer PC: JDK 27 (`C:\Program Files\Java\jdk-27.0.1.1`: javac, javap, jshell) and JDK 8.
