"""Version-scoped names (discussion #9) as contributions: ocm/versions.py ranges and Check contributions
(ocm/check_names.py) for names/versioned/*.tsv. The launcher on main doesn't apply these files yet (v3 previews do);
this only makes sure pull requests with them are checked.

    python -m unittest discover -s tests -v"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ocm"))
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
