#!/usr/bin/env python3
"""Build the release assets: dist/OpenCAFDMap.zip (every tracked file + VERSION) and its SHA-256.

    python installer/build_release.py v1.0.0

Run by .github/workflows/release.yml when a v* tag is pushed; the release then carries
OpenCAFDMap.zip, OpenCAFDMap.zip.sha256 and installer/OpenLauncher-Setup.bat.
"""
import hashlib
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
SKIP = (".github/", "installer/", ".gitattributes", ".gitignore")  # not needed in an install


def main():
    version = (sys.argv[1] if len(sys.argv) > 1 else "dev").lstrip("v")
    files = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], capture_output=True, check=True).stdout
    files = [f for f in files.decode("utf-8").split("\0") if f and not f.startswith(SKIP)]
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "OpenCAFDMap.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(files):
            z.write(os.path.join(ROOT, f), f)   # bytes as checked out (definitions stay byte-exact)
        z.writestr("VERSION", version + "\n")
    digest = hashlib.sha256(open(out, "rb").read()).hexdigest()
    with open(out + ".sha256", "w", newline="\n") as f:
        f.write(f"{digest}  OpenCAFDMap.zip\n")
    print(f"OpenCAFDMap {version}: {len(files)} files -> {out} ({os.path.getsize(out) // 1024} KB) sha256 {digest}")


if __name__ == "__main__":
    main()
