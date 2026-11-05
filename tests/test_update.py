#!/usr/bin/env python3
"""Tests for scripts/update.py - version compare, dirty guard, pull, rollback."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import update  # noqa: E402

GIT = shutil.which("git")
FM = "---\nname: ai-router\nversion: {v}\n---\n\nbody\n"


def git(cwd, *args):
    p = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
    return p.stdout.strip()


class VersionTests(unittest.TestCase):
    def test_parse_version_ignores_noise(self):
        self.assertEqual(update.parse_version("1.4.0"), (1, 4, 0))
        self.assertEqual(update.parse_version("v2.10.3-beta.1"), (2, 10, 3, 1))
        self.assertEqual(update.parse_version(""), ())

    def test_is_newer(self):
        self.assertTrue(update.is_newer("1.5.0", "1.4.9"))
        self.assertFalse(update.is_newer("1.4.0", "1.4.0"))
        self.assertFalse(update.is_newer("1.3.9", "1.4.0"))
        self.assertFalse(update.is_newer("", "1.4.0"))  # unknown never forces an update

    def test_read_version_text_needs_frontmatter(self):
        self.assertEqual(update.read_version_text(FM.format(v="1.4.0")), "1.4.0")
        self.assertEqual(update.read_version_text("no frontmatter\nversion: 9.9"), "")
        self.assertEqual(update.read_version_text("---\nname: x\n---\n"), "")

    def test_read_version_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "SKILL.md"
            p.write_text(FM.format(v="2.0.0"), encoding="utf-8")
            self.assertEqual(update.read_version(p), "2.0.0")
            self.assertEqual(update.read_version(Path(d) / "missing.md"), "")


@unittest.skipUnless(GIT, "git not installed")
class RepoTests(unittest.TestCase):
    """A bare remote plus two clones: one pushes, the other pulls."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="airouter-upd-"))
        cls.remote = cls.tmp / "remote.git"
        git(cls.tmp, "init", "--bare", "-b", "main", str(cls.remote))
        cls.work = cls.tmp / "work"
        git(cls.tmp, "clone", str(cls.remote), str(cls.work))
        git(cls.work, "config", "user.email", "t@example.com")
        git(cls.work, "config", "user.name", "t")
        (cls.work / "SKILL.md").write_text(FM.format(v="1.0.0"), encoding="utf-8")
        git(cls.work, "add", "-A")
        git(cls.work, "commit", "-m", "v1.0.0")
        git(cls.work, "push", "-u", "origin", "main")

        cls.clone = cls.tmp / "clone"
        git(cls.tmp, "clone", str(cls.remote), str(cls.clone))
        git(cls.clone, "config", "user.email", "t@example.com")
        git(cls.clone, "config", "user.name", "t")
        cls.base = git(cls.clone, "rev-parse", "HEAD")

        # publish one more commit from the "upstream" side
        (cls.work / "SKILL.md").write_text(FM.format(v="1.1.0"), encoding="utf-8")
        git(cls.work, "add", "-A")
        git(cls.work, "commit", "-m", "bump to 1.1.0")
        git(cls.work, "push", "origin", "main")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        git(self.clone, "reset", "--hard", self.base)
        git(self.clone, "clean", "-fdq")

    def test_status_detects_being_behind(self):
        info = update.status(self.clone)
        self.assertTrue(info["git"])
        self.assertEqual(info["local_version"], "1.0.0")
        self.assertEqual(info["remote_version"], "1.1.0")
        self.assertEqual(info["behind"], 1)
        self.assertTrue(info["commits"])
        self.assertFalse(info["dirty"])

    def test_check_exit_code_is_one_when_behind(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = update.main(["--check", "--repo", str(self.clone)])
        self.assertEqual(code, update.EXIT_BEHIND)
        self.assertIn("1 commit(s) behind", buf.getvalue())

    def test_dirty_tree_blocks_apply_without_changes(self):
        (self.clone / "SKILL.md").write_text(FM.format(v="1.0.0") + "local edit\n",
                                             encoding="utf-8")
        head_before = git(self.clone, "rev-parse", "HEAD")
        code, notes = update.apply_update(self.clone, run_tests=False)
        self.assertEqual(code, update.EXIT_DIRTY)
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), head_before)
        self.assertIn("local edit", (self.clone / "SKILL.md").read_text(encoding="utf-8"))

    def test_apply_fast_forwards(self):
        code, notes = update.apply_update(self.clone, run_tests=False)
        self.assertEqual(code, update.EXIT_CURRENT, notes)
        self.assertEqual(update.read_version(self.clone / "SKILL.md"), "1.1.0")

    def test_apply_rolls_back_when_tests_fail(self):
        head_before = git(self.clone, "rev-parse", "HEAD")
        real_run = update._run

        def fake(args, cwd, timeout=120, env_extra=None):
            if "unittest" in args:
                return 1, "", "1 test failed"
            return real_run(args, cwd, timeout, env_extra)

        with mock.patch.object(update, "_run", fake):
            code, notes = update.apply_update(self.clone, run_tests=True)
        self.assertEqual(code, update.EXIT_TEST_FAILED)
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), head_before)
        self.assertTrue(any("ROLLED BACK" in n for n in notes))

    def test_status_is_json_serialisable(self):
        import json
        info = update.status(self.clone)
        payload = json.dumps(info, ensure_ascii=False)  # raises if a Path leaked in
        self.assertIn("local_version", json.loads(payload))


class NoGitTests(unittest.TestCase):
    def test_plain_directory_is_not_a_repo(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "SKILL.md").write_text(FM.format(v="1.0.0"), encoding="utf-8")
            with mock.patch.object(update, "has_git", return_value=False), \
                    mock.patch.object(update, "remote_version_http", return_value=("1.2.0", "")):
                info = update.status(Path(d))
        self.assertFalse(info["git"])
        self.assertEqual(info["source"], "http")
        self.assertTrue(info["update_available"])

    def test_apply_refuses_outside_git(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(update, "has_git", return_value=False):
                code, notes = update.apply_update(Path(d), run_tests=False)
        self.assertEqual(code, update.EXIT_ERROR)
        self.assertIn("not a git checkout", notes[0])


if __name__ == "__main__":
    unittest.main()
