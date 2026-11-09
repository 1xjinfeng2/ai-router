#!/usr/bin/env python3
"""Tests for the server-side deploy script's pure logic (no systemd, no root)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import server_deploy as dep  # noqa: E402


class VersionTests(unittest.TestCase):
    def test_read_version_from_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "SKILL.md"
            p.write_text("---\nname: ai-router\nversion: 1.6.3\n---\n\nbody\n",
                         encoding="utf-8")
            self.assertEqual(dep.read_version(p), "1.6.3")

    def test_read_version_missing_returns_empty(self):
        self.assertEqual(dep.read_version(Path("/nonexistent/SKILL.md")), "")


class SmokeTests(unittest.TestCase):
    def test_matching_health_is_ok(self):
        health = {"ok": True, "version": "1.6.3", "providers_configured": 16,
                  "drift": []}
        with mock.patch.object(dep, "fetch_health", return_value=health):
            ok, problems, _ = dep.smoke("1.6.3", 16)
        self.assertTrue(ok, problems)

    def test_old_version_is_caught(self):
        health = {"ok": True, "version": "1.6.2", "providers_configured": 16,
                  "drift": []}
        with mock.patch.object(dep, "fetch_health", return_value=health):
            ok, problems, _ = dep.smoke("1.6.3", 16)
        self.assertFalse(ok)
        self.assertTrue(any("version" in p for p in problems))

    def test_missing_providers_is_caught(self):
        health = {"ok": True, "version": "1.6.3", "providers_configured": 12,
                  "drift": []}
        with mock.patch.object(dep, "fetch_health", return_value=health):
            ok, problems, _ = dep.smoke("1.6.3", 16)
        self.assertFalse(ok)
        self.assertTrue(any("providers" in p for p in problems))

    def test_server_side_drift_fails_smoke(self):
        health = {"ok": True, "version": "1.6.3", "providers_configured": 16,
                  "drift": ["providers 16 != expected 12"]}
        with mock.patch.object(dep, "fetch_health", return_value=health):
            ok, problems, _ = dep.smoke("1.6.3", 16)
        self.assertFalse(ok)
        self.assertTrue(any("drift" in p for p in problems))

    def test_unreachable_service_fails(self):
        with mock.patch.object(dep, "fetch_health",
                               return_value={"ok": False, "error": "URLError: boom"}):
            ok, problems, _ = dep.smoke("1.6.3", 16)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
