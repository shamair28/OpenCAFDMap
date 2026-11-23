# Licences of the files in lib/nashorn

These jars are third-party software, shipped unchanged as published on Maven Central. They are separate programs
that OpenLauncher only puts on E-Sys's classpath; OpenLauncher's own code stays under its MIT licence.

## OpenJDK Nashorn 15.7 (`nashorn-core-15.7.jar`)
- Licence: **GNU General Public License, version 2, with the Classpath Exception** (the same licence as the JDK).
  Full text: [LICENSE-nashorn.txt](LICENSE-nashorn.txt), with [ADDITIONAL_LICENSE_INFO-nashorn.txt](ADDITIONAL_LICENSE_INFO-nashorn.txt)
  (both copied unchanged from the source jar).
- Source code: **[src/nashorn-core-15.7-sources.jar](src)**, the complete corresponding source of this jar, shipped next
  to it (unchanged from Maven Central, `org.openjdk.nashorn:nashorn-core:15.7:sources`). Upstream:
  https://github.com/openjdk/nashorn (release 15.7). The source jar is kept in `src\` so it's never on E-Sys's classpath.
- Copyright: Oracle and/or its affiliates.

## ASM 7.3.1 (`asm-7.3.1.jar`, `asm-analysis-7.3.1.jar`, `asm-commons-7.3.1.jar`, `asm-tree-7.3.1.jar`, `asm-util-7.3.1.jar`)
- Licence: **BSD-3-Clause**: https://asm.ow2.io/license.html
- Source code: https://gitlab.ow2.org/asm/asm (tag ASM_7_3_1)
- Copyright: INRIA, France Telecom.

File sizes and SHA-1 checksums: [README.md](README.md).
