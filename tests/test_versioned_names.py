"""Regression tests for version-scoped names (discussion #9): names\\versioned\\*.tsv rows with a versions column
and versions="..." on contributed definitions apply only to those CAFD versions; every-version names still apply
everywhere else; check_names validates them.

    python -m unittest discover -s tests -v

Synthetic files and DBs only: no psdzdata."""
import contextlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ocm"))
import build_defdb as B  # noqa: E402
import check_names as C  # noqa: E402
import versions as V  # noqa: E402

CID = "000017BE"


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


class Ranges(unittest.TestCase):
    def test_syntax(self):
        self.assertEqual(V.ranges(""), [(None, None)])
        self.assertEqual(V.ranges("062-"), [(62_000_000, None)])
        self.assertEqual(V.ranges("-052"), [(None, 52_999_999)])
        self.assertEqual(V.ranges("052-061"), [(52_000_000, 61_999_999)])
        self.assertEqual(V.ranges("062_010_106"), [(62_010_106, 62_010_106)])
        self.assertEqual(V.ranges("62.10.106-"), [(62_010_106, None)])
        self.assertEqual(V.ranges("062"), [(62_000_000, 62_999_999)])
        self.assertEqual(V.ranges("-040, 062-"), [(None, 40_999_999), (62_000_000, None)])

    def test_errors(self):
        for bad in ("-", "062--070", "abc", "070-062", "062,", "1234", "062_010_106_001"):
            with self.assertRaises(ValueError, msg=bad):
                V.ranges(bad)

    def test_overlap(self):
        self.assertTrue(V.overlap((None, 52_999_999), (52_000_000, None)))
        self.assertFalse(V.overlap((None, 52_999_999), (62_000_000, None)))
        self.assertEqual(V.text(62_000_000, None), "062_000_000-")


class Apply(unittest.TestCase):
    """CAFD 17BE in two versions with the same function at 3070/0/0/00000001b."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocm-vnames-")
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(B.SCHEMA)
        self.pk = {}
        for ver in ("052_008_000", "062_010_106"):
            m, s, p = (int(x) for x in ver.split("_"))
            cpk = self.con.execute("INSERT INTO cafd(cafd_id, main, sub, patch, version) VALUES(?,?,?,?,?)",
                                   (CID, m, s, p, ver)).lastrowid
            for addr in ("3070", "3072"):
                dpk = self.con.execute("INSERT INTO datagroup(cafd_pk, address) VALUES(?, ?)", (cpk, addr)).lastrowid
                fpk = self.con.execute(
                    "INSERT INTO func(cafd_pk, dg_pk, address, idx, start, end, mask, name, name_source) "
                    "VALUES(?,?,?,0,0,0,'00000001b','x','derived')", (cpk, dpk, addr)).lastrowid
                self.con.execute("INSERT INTO val(func_pk, idx, hex, label, label_source) VALUES(?,0,'01','wert_01 [x]',"
                                 "'derived')", (fpk,))
            self.pk[ver] = cpk

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tsv(self, rel, rows):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("# test\n" + "".join("\t".join(r) + "\n" for r in rows))
        return p

    def names(self, ver, addr="3070"):
        cpk = self.pk[ver]
        g = self.con.execute("SELECT name FROM datagroup WHERE cafd_pk=? AND address=?", (cpk, addr)).fetchone()[0]
        f, v = self.con.execute("SELECT f.name, v.label FROM func f JOIN val v ON v.func_pk=f.pk "
                                "WHERE f.cafd_pk=? AND f.address=?", (cpk, addr)).fetchone()
        return g, f, v

    def apply(self, rows):
        self.con.executemany(B.OVERRIDE_INSERT, rows)
        quiet(B.apply_overrides, self.con)

    def test_scoped_rows_apply_to_their_versions_only(self):
        every = B.load_user_names(self.tsv("names/BDC.tsv", [
            [CID, "3070", "0", "0", "00000001b", "", "TM_OLD_NAME", "untrimmed v52"]]))
        scoped = quiet(B.load_user_names, self.tsv("names/versioned/BDC.tsv", [
            [CID, "3070", "", "", "", "", "NewGroup", "ESP ref 076", "062-"],
            [CID, "3070", "0", "0", "00000001b", "", "EM_NEW_NAME", "ESP ref 076", "062-"],
            [CID, "3070", "0", "0", "00000001b", "01", "aktiv", "", "062-"],
            [CID, "3072", "", "", "", "", "LaMaster0", "ESP ref 076", "062_010_106-"],
            [CID, "3072", "", "", "", "", "NoVersions", "skipped", ""]]), scoped=True)
        self.assertEqual(len(scoped), 4)                                   # the row without versions is skipped
        self.apply(every + scoped)
        self.assertEqual(self.names("052_008_000"), (None, "TM_OLD_NAME", "wert_01 [x]"))
        self.assertEqual(self.names("062_010_106"), ("NewGroup", "EM_NEW_NAME", "wert_01 aktiv [x]"))
        self.assertEqual(self.names("062_010_106", "3072")[0], "LaMaster0")
        self.assertIsNone(self.names("052_008_000", "3072")[0])

    def test_priority_between_sources_still_wins(self):
        local = B.load_user_names(self.tsv("my_names.tsv", [[CID, "3070", "0", "0", "00000001b", "", "MINE", ""]]),
                                  "local")
        scoped = B.load_user_names(self.tsv("names/versioned/BDC.tsv", [
            [CID, "3070", "0", "0", "00000001b", "", "SCOPED", "", "-099"]]), scoped=True)
        self.apply(local + scoped)
        self.assertEqual(self.names("062_010_106")[1], "MINE")

    def test_every_version_files_ignore_a_9th_column(self):
        rows = B.load_user_names(self.tsv("names/BDC.tsv", [[CID, "3070", "0", "0", "00000001b", "", "N", "", "062-"]]))
        self.assertEqual(rows[0][-2:], (None, None))

    def test_definition_xml_versions_attribute(self):
        p = os.path.join(self.tmp, "CAFD_000017BE.xml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f'<CafdDefinition cafdId="{CID}" source="test" versions="-052,070-"><Group id="3070" name="G">'
                    '<Function name="F" start="0" end="0" mask="00000001b"/></Group></CafdDefinition>')
        rows = B.load_definition_xmls([p])
        self.assertEqual({r[-2:] for r in rows}, {(None, 52_999_999), (70_000_000, None)})
        self.apply(rows)
        self.assertEqual(self.names("052_008_000")[:2], ("G", "F"))
        self.assertEqual(self.names("062_010_106")[1], "3070_0_0_1")   # not named: placeholder

    def test_old_db_gets_the_columns(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE name_override(cafd_id TEXT, address TEXT, start INT, end INT, mask TEXT, value_hex "
                    "TEXT, name TEXT, comment TEXT, source TEXT)")
        B.ensure_override_columns(con)
        con.execute(B.OVERRIDE_INSERT, (CID, "3070", None, None, None, None, "G", None, "community", None, None))
        self.assertEqual(con.execute("SELECT count(*) FROM name_override").fetchone()[0], 1)


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocm-vcheck-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, rel, rows):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("".join("\t".join(r) + "\n" for r in rows))
        errors, warnings, keys, scoped = [], [], {}, {}
        C.check_file(p, errors, warnings, keys, scoped)
        return errors

    def test_versioned_rows(self):
        ok = [CID, "3070", "0", "0", "00000001b", "", "A", "src", "-052"]
        self.assertEqual(self.check("names/versioned/a.tsv", [ok, ok[:6] + ["B", "src", "062-"]]), [])
        errs = self.check("names/versioned/b.tsv", [ok, ok[:6] + ["B", "src", "050-060"]])
        self.assertTrue(any("conflicts" in e for e in errs), errs)
        self.assertTrue(any("versions is empty" in e for e in self.check("names/versioned/c.tsv", [ok[:8]])))
        self.assertTrue(any("versions 'x1'" in e for e in self.check("names/versioned/d.tsv", [ok[:8] + ["x1"]])))

    def test_top_level_files_keep_8_columns(self):
        errs = self.check("names/e.tsv", [[CID, "3070", "0", "0", "00000001b", "", "A", "src", "062-"]])
        self.assertTrue(any("names\\versioned\\" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
