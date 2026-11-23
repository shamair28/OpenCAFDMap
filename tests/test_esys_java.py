"""Regression tests for starting E-Sys on different Java versions (ocm/esys.py): Java 8-14 use the JRE's own Nashorn;
Java 15+ (E-Sys 26.x: Java 17) is experimental - only with the setting on and the standalone Nashorn in lib\\nashorn -
and E-Sys.bat's --add-opens is kept as one option.

    python -m unittest discover -s tests -v

No E-Sys needed: Java versions, jars and E-Sys.bat are faked."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ocm"))
import esys  # noqa: E402

JARS = [r"x\nashorn-core-15.7.jar", r"x\asm-7.3.1.jar"]


class Gate(unittest.TestCase):
    def check(self, java, on, jars=JARS):
        with mock.patch.object(esys, "java_major", return_value=java), \
             mock.patch.object(esys, "nashorn_jars", return_value=jars), \
             mock.patch.object(esys, "esys_version", return_value="26.04.02"):
            return esys.check_java("E", {"experimental_java17": on})

    def test_java_8_to_14_always_start(self):
        for j in (8, 11, 14):
            self.assertEqual(self.check(j, False), j)

    def test_java_17_needs_the_setting(self):
        with self.assertRaises(SystemExit) as e:
            self.check(17, False)
        self.assertIn(esys.EXPERIMENTAL_LABEL, str(e.exception))
        self.assertEqual(self.check(17, True), 17)

    def test_java_17_needs_the_jars(self):
        with self.assertRaises(SystemExit) as e:
            self.check(17, True, jars=[])
        self.assertIn("standalone Nashorn", str(e.exception))

    def test_runner(self):
        with mock.patch.object(esys, "java_major", return_value=11):
            self.assertEqual(esys.script_runner("E"), ("jdk.nashorn.tools.Shell", []))
        with mock.patch.object(esys, "java_major", return_value=17), \
             mock.patch.object(esys, "nashorn_jars", return_value=JARS):
            self.assertEqual(esys.script_runner("E"), (esys.STANDALONE_SHELL, JARS))


class Bat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocm-bat-")
        os.makedirs(os.path.join(self.tmp, "lib", "environment"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_opens_is_one_option(self):
        with open(os.path.join(self.tmp, "E-Sys.bat"), "w", encoding="latin-1") as f:
            f.write("@echo off\r\nset OPTION_ADD_OPENS=--add-opens java.base/java.lang=ALL-UNNAMED\r\n"
                    "set OPTION_MEMORY=-Xmx1024m\r\nset SP=%~dp0lib\r\nset CLASSPATH=%SP%\\*\r\n"
                    "cd /D %~dp0lib\\environment\r\n"
                    "start javaw %OPTION_ADD_OPENS% %OPTION_MEMORY% -splash:../splash.gif com.bmw.esys.gui.control.Framework\r\n")
        with mock.patch.object(esys.config, "esys_properties", return_value={}):
            spec = esys.parse_bat(self.tmp)
        self.assertEqual(spec["main"], "com.bmw.esys.gui.control.Framework")
        self.assertEqual(spec["vm_options"], ["--add-opens", "java.base/java.lang=ALL-UNNAMED", "-Xmx1024m"])
        self.assertEqual(esys.module_options(spec), ["--add-opens", "java.base/java.lang=ALL-UNNAMED"])


if __name__ == "__main__":
    unittest.main()
