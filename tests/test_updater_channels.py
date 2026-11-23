"""Regression tests for launcher update channels in OpenLauncher v2 (ocm/updater.py): a v2 install follows the
newest v2 release, never GitHub's "latest" release (OpenLauncher v3), and setup always gets the exact tag.

    python -m unittest discover -s tests -v

GitHub is never contacted: the release list and the setup download are synthetic."""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ocm"))
import updater as U  # noqa: E402

SETUP = b"@echo off\r\nrem OCM_SETUP_TAG\r\n#PS-START\r\n$Repo = '" + U.UPDATE_REPO.encode() + b"'\r\n"


def gh(*rels):
    """A GitHub /releases answer: (tag, prerelease[, draft])."""
    return json.dumps([{"tag_name": t, "prerelease": p, "draft": bool(d[0]) if d else False} for t, p, *d in rels]).encode()


BEFORE_V3 = gh(("v3.0.0-rc.6", True), ("v2.3.11", False), ("v3.0.0-rc.5", True), ("v2.3.10", False))
AFTER_V3 = gh(("v3.0.1", False), ("v3.0.0", False), ("v2.3.11", False), ("v3.1.0-rc.1", True),
              ("v2.3.12", False, True))


class Channels(unittest.TestCase):
    def check(self, installed, answer):
        with mock.patch.object(U, "_get", return_value=answer), \
             mock.patch.object(U, "launcher_installed", return_value=installed), \
             mock.patch.object(U, "is_git_clone", return_value=False):
            return U.launcher_check()

    def test_v2_follows_v2_only(self):
        self.assertEqual(self.check("2.3.10", BEFORE_V3), ("2.3.10", "2.3.11", True))
        # v3.0.1 is GitHub's latest and newer, the draft v2.3.12 doesn't count: no update for v2.3.11
        self.assertEqual(self.check("2.3.11", AFTER_V3), ("2.3.11", "2.3.11", False))
        self.assertEqual(self.check("2.3.11", gh(("v3.0.0", False), ("v2.4.0", False))), ("2.3.11", "2.4.0", True))

    def test_try_v3_is_the_newest_v3_release_or_preview(self):
        with mock.patch.object(U, "_get", return_value=BEFORE_V3):
            self.assertEqual(U.v3_target(), "3.0.0-rc.6")
        with mock.patch.object(U, "_get", return_value=AFTER_V3):
            self.assertEqual(U.v3_target(), "3.0.1")
            self.assertEqual(U.stable_target(), "2.3.11")

    def test_github_error_answer_is_an_update_error(self):
        with mock.patch.object(U, "_get", return_value=b'{"message": "API rate limit exceeded"}'):
            with self.assertRaises(U.UpdateError):
                U.launcher_check()


class Install(unittest.TestCase):
    def run_update(self, version, answer=AFTER_V3):
        with mock.patch.object(U, "_get", side_effect=lambda url, *a, **k: SETUP if url == U.SETUP_URL else answer), \
             mock.patch.object(U, "launcher_blocker", return_value=None), \
             mock.patch.object(U.subprocess, "Popen") as popen:
            U.launcher_update(out=lambda *_: None, version=version)
        return popen.call_args.kwargs["env"]

    def test_setup_always_gets_the_exact_tag(self):
        self.assertEqual(self.run_update(None)["OCM_SETUP_TAG"], "v2.3.11")   # default: newest v2, not "latest"
        self.assertEqual(self.run_update("3.0.1")["OCM_SETUP_TAG"], "v3.0.1")  # Try OpenLauncher v3
        self.assertEqual(self.run_update("3.1.0-rc.1")["OCM_SETUP_TAG"], "v3.1.0-rc.1")


class GitClones(unittest.TestCase):
    def test_branches(self):
        for on, follows in (("v2", "v2"), ("main", "main"), ("v3-testing", "v3-testing"), ("testing", "v3-testing"),
                            ("my-feature", "v2")):
            with mock.patch.object(U, "_git", return_value=on):
                self.assertEqual(U.git_target(), follows, on)


if __name__ == "__main__":
    unittest.main()
