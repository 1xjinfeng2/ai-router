#!/usr/bin/env python3
"""Unit tests for the packaging guard: exclusions, frontmatter validation and
the pre-package secret scan (the last line of defence before publishing)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import package  # noqa: E402


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_root = package.ROOT
        self._old_name = package.NAME
        package.ROOT = Path(self._tmp.name)
        package.NAME = package.ROOT.name

    def tearDown(self):
        package.ROOT = self._old_root
        package.NAME = self._old_name
        self._tmp.cleanup()

    def _write(self, rel, content, mode="w"):
        p = package.ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if "b" in mode:
            data = content.encode("utf-8") if isinstance(content, str) else content
            p.write_bytes(data)
        else:
            p.write_text(content, encoding="utf-8")
        return p

    def test_secrets_and_databases_are_excluded(self):
        for rel in (".env", "privacy_vault.db", "usage.db", "scripts/x.pyc"):
            p = self._write(rel, "x")
            self.assertTrue(package.should_skip(p), rel)

    def test_env_example_is_kept(self):
        p = self._write(".env.example", "ARK_API_KEY=")
        self.assertFalse(package.should_skip(p))

    def test_clean_tree_has_no_secret_hits(self):
        p = self._write("README.md", "use ARK_API_KEY from .env, never commit it")
        self.assertEqual(package.scan([p]), [])

    def test_real_looking_key_is_caught(self):
        fake = "ark-" + "0123456789abcdef" * 2
        p = self._write("scripts/leak.py", f'KEY = "{fake}"\n', mode="wb")
        hits = package.scan([p])
        self.assertEqual(len(hits), 1)
        self.assertIn("leak.py", hits[0][0])

    def test_sk_key_is_caught(self):
        fake = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
        p = self._write("notes.txt", fake, mode="wb")
        self.assertTrue(package.scan([p]))

    def test_doc_placeholder_is_not_a_false_positive(self):
        p = self._write("README.md", "export DEEPSEEK_API_KEY=sk-tp-xxx")
        self.assertEqual(package.scan([p]), [])

    def test_validate_flags_missing_required_files(self):
        errors = package.validate([])
        self.assertTrue(any("missing required file" in e for e in errors))

    def test_validate_flags_angle_brackets_in_frontmatter(self):
        self._write("SKILL.md", "---\nname: x\ndescription: 'a <b> c'\n---\nbody")
        errors = package.validate([package.ROOT / "SKILL.md"])
        self.assertTrue(any("angle brackets" in e for e in errors))

    def test_validate_accepts_good_frontmatter(self):
        self._write("SKILL.md", "---\nname: x\ndescription: 'plain text'\n---\nbody")
        errors = package.validate([package.ROOT / "SKILL.md"])
        self.assertFalse(any("angle brackets" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
