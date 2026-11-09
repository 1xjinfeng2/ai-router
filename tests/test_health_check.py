#!/usr/bin/env python3
"""Tests for deployment-drift detection and the post-deploy smoke gate."""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import health_check as hc  # noqa: E402
import update  # noqa: E402


class ConfigHashTests(unittest.TestCase):
    def test_hash_is_stable_and_order_independent(self):
        a = hc.config_hash("1.6.3", ["doubao", "glm-flash", "openrouter"])
        b = hc.config_hash("1.6.3", ["openrouter", "doubao", "glm-flash"])
        self.assertEqual(a, b)
        self.assertEqual(len(a), 12)

    def test_hash_changes_with_version_or_provider_set(self):
        base = hc.config_hash("1.6.3", ["a", "b"])
        self.assertNotEqual(base, hc.config_hash("1.6.2", ["a", "b"]))
        self.assertNotEqual(base, hc.config_hash("1.6.3", ["a", "b", "c"]))


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.health = {
            "ok": True, "version": "1.6.3", "providers_configured": 16,
            "providers": ["a"] * 16,
            "config_hash": hc.config_hash("1.6.3", ["a"] * 16),
            "drift": [],
        }

    def test_no_expectations_is_healthy(self):
        ok, problems, _ = hc.evaluate(self.health, env={})
        self.assertTrue(ok)
        self.assertEqual(problems, [])

    def test_version_drift_detected(self):
        ok, problems, _ = hc.evaluate(self.health, {"version": "1.6.4"}, env={})
        self.assertFalse(ok)
        self.assertTrue(any("version" in p for p in problems))

    def test_provider_count_drift_detected(self):
        ok, problems, _ = hc.evaluate(self.health, {"providers": 12}, env={})
        self.assertFalse(ok)
        self.assertTrue(any("providers" in p for p in problems))

    def test_hash_drift_detected(self):
        ok, problems, _ = hc.evaluate(self.health, {"config_hash": "deadbeef"}, env={})
        self.assertFalse(ok)
        self.assertTrue(any("config_hash" in p for p in problems))

    def test_all_match(self):
        ok, problems, _ = hc.evaluate(
            self.health,
            {"version": "1.6.3", "providers": 16,
             "config_hash": self.health["config_hash"]}, env={})
        self.assertTrue(ok, problems)

    def test_expectations_read_from_env(self):
        env = {hc.ENV_EXPECT_VERSION: "1.6.4",
               hc.ENV_EXPECT_PROVIDERS: "16",
               hc.ENV_EXPECT_HASH: ""}
        self.assertEqual(hc.expectations(env), {"version": "1.6.4", "providers": 16})


class CLITests(unittest.TestCase):
    def test_unreachable_returns_2(self):
        with mock.patch.object(hc, "fetch", return_value=(None, "boom")):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(hc.main(["--url", "http://x/health"]), hc.EXIT_UNREACHABLE)

    def test_drift_returns_1(self):
        health = {"ok": True, "version": "1.6.2", "providers_configured": 12,
                  "drift": []}
        with mock.patch.object(hc, "fetch", return_value=(health, "")):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(hc.main(["--expect-version", "1.6.3"]), hc.EXIT_DRIFT)

    def test_healthy_returns_0(self):
        health = {"ok": True, "version": "1.6.3", "providers_configured": 16,
                  "drift": []}
        with mock.patch.object(hc, "fetch", return_value=(health, "")):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(hc.main(["--expect-version", "1.6.3",
                                          "--expect-providers", "16"]), hc.EXIT_OK)

    def test_server_side_drift_fails_even_without_local_expectations(self):
        health = {"ok": True, "version": "1.6.3", "providers_configured": 16,
                  "drift": ["providers 16 != expected 12"]}
        with mock.patch.object(hc, "fetch", return_value=(health, "")):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(hc.main([]), hc.EXIT_DRIFT)


class SmokeGateTests(unittest.TestCase):
    """A failed smoke test must roll the code back, just like a failed unit test."""

    def test_smoke_success(self):
        health = {"ok": True, "version": "1.6.3", "providers_configured": 16,
                  "drift": []}
        with mock.patch.object(hc, "fetch", return_value=(health, "")), \
                mock.patch.object(update, "read_version", return_value="1.6.3"), \
                mock.patch("judge.configured_providers", return_value=set(range(16))):
            ok, problems = update.smoke_test(ROOT, "http://x/health")
        self.assertTrue(ok, problems)

    def test_smoke_failure_is_reported(self):
        health = {"ok": True, "version": "1.6.2", "providers_configured": 12,
                  "drift": []}
        with mock.patch.object(hc, "fetch", return_value=(health, "")), \
                mock.patch.object(update, "read_version", return_value="1.6.3"), \
                mock.patch("judge.configured_providers", return_value=set(range(16))):
            ok, problems = update.smoke_test(ROOT, "http://x/health")
        self.assertFalse(ok)
        self.assertTrue(any("version" in p for p in problems))

    def test_apply_rolls_back_when_smoke_fails(self):
        repo = Path("/nonexistent/repo")
        with mock.patch.object(update, "has_git", return_value=True), \
                mock.patch.object(update, "is_dirty", return_value=False), \
                mock.patch.object(update, "git", side_effect=[
                    (0, "aaa1111111111111111111111111111111111111111", ""),  # rev-parse
                    (0, "Updating abc..def", ""),                            # pull
                    (0, "", ""),                                             # reset --hard
                ]), \
                mock.patch.object(update, "smoke_test", return_value=(False, ["version drift"])):
            code, notes = update.apply_update(repo, run_tests=False,
                                              smoke_url="http://x/health")
        self.assertEqual(code, update.EXIT_SMOKE_FAILED)
        self.assertTrue(any("ROLLED BACK" in n for n in notes))
        self.assertTrue(any("smoke" in n for n in notes))

    def test_apply_succeeds_when_smoke_passes(self):
        repo = Path("/nonexistent/repo")
        with mock.patch.object(update, "has_git", return_value=True), \
                mock.patch.object(update, "is_dirty", return_value=False), \
                mock.patch.object(update, "git", side_effect=[
                    (0, "aaa1111111111111111111111111111111111111111", ""),
                    (0, "Updating abc..def", ""),
                    (0, "aaa1111", ""),
                ]), \
                mock.patch.object(update, "smoke_test", return_value=(True, [])), \
                mock.patch.object(update, "read_version", return_value="1.6.3"):
            code, notes = update.apply_update(repo, run_tests=False,
                                              smoke_url="http://x/health")
        self.assertEqual(code, update.EXIT_CURRENT)
        self.assertTrue(any("smoke" in n and "OK" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
