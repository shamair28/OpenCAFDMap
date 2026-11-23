# lib/nashorn: standalone OpenJDK Nashorn for E-Sys on Java 15+

Java removed its built-in JavaScript engine (Nashorn) in Java 15. E-Sys 26.04 runs on Java 17, so OpenLauncher starts
it with OpenJDK's standalone Nashorn instead (`org.openjdk.nashorn.tools.Shell`, see `ocm/esys.py script_runner`).
E-Sys on Java 8-14 keeps using the JRE's own Nashorn; this folder isn't used there. Java 15+ is experimental: OpenLauncher only
starts it with Settings > Options > "Experimental: E-Sys 26.x (Java 17)" on.

The jars below are shipped here unchanged from Maven Central (`https://repo1.maven.org/maven2/`); their SHA-1s match
Maven Central's `.sha1` files. To update them, replace the files and this table together.

| File | Maven coordinates | Bytes | SHA-1 |
|---|---|---|---|
| nashorn-core-15.7.jar | org.openjdk.nashorn:nashorn-core:15.7 | 2139093 | f959627b97cb21316906d3634fecf4ec04016bcd |
| asm-7.3.1.jar | org.ow2.asm:asm:7.3.1 | 121836 | 7ec32f922315924e82bf58b36ee1b673b2a9b820 |
| asm-commons-7.3.1.jar | org.ow2.asm:asm-commons:7.3.1 | 71548 | daaa79ef260eb67404b9a52bc319a024c7f49cfe |
| asm-tree-7.3.1.jar | org.ow2.asm:asm-tree:7.3.1 | 52826 | 587ce54d243145b2e89598bfcea7823ded73be5d |
| asm-util-7.3.1.jar | org.ow2.asm:asm-util:7.3.1 | 84817 | cac1bf54c2fb86671c357d281d1060fe5d50a0de |
| asm-analysis-7.3.1.jar | org.ow2.asm:asm-analysis:7.3.1 | 33446 | 045dfd299ea0c17d534499c4f06417ceccfa2d02 |
| src/nashorn-core-15.7-sources.jar | org.openjdk.nashorn:nashorn-core:15.7:sources | 1694683 | 62a74767e92e03875e3905f64a2f4c3617bf5997 |

The source jar is Nashorn's corresponding source (not on the classpath). Licences and source code: [LICENSES.md](LICENSES.md) (Nashorn: GPL-2.0 with the Classpath Exception; ASM: BSD-3-Clause).
