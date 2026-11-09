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


class ExpectVersionSyncTests(unittest.TestCase):
    def test_updates_existing_expected_version(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("AI_ROUTER_SERVER_TOKEN=secret\n"
                           "AI_ROUTER_EXPECT_VERSION=1.6.5\n"
                           "AI_ROUTER_EXPECT_PROVIDERS=16\n", encoding="utf-8")
            with mock.patch.object(dep, "APP", Path(d)), \
                    mock.patch.object(dep, "run", return_value=(0, "", "")):
                dep.sync_expect_version("1.6.7")
            text = env.read_text(encoding="utf-8")
            self.assertIn("AI_ROUTER_EXPECT_VERSION=1.6.7", text)
            self.assertIn("AI_ROUTER_SERVER_TOKEN=secret", text)
            self.assertIn("AI_ROUTER_EXPECT_PROVIDERS=16", text)

    def test_appends_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("AI_ROUTER_SERVER_TOKEN=secret\n", encoding="utf-8")
            with mock.patch.object(dep, "APP", Path(d)), \
                    mock.patch.object(dep, "run", return_value=(0, "", "")):
                dep.sync_expect_version("1.6.7")
            self.assertIn("AI_ROUTER_EXPECT_VERSION=1.6.7",
                          env.read_text(encoding="utf-8"))

    def test_missing_env_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(dep, "APP", Path(d)):
                dep.sync_expect_version("1.6.7")


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
