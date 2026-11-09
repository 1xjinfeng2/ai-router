#!/usr/bin/env python3
"""Tests for free-tier governance: cooldown, discovery, reasoning detection."""
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import free_lines as fl  # noqa: E402
import call_model as cm  # noqa: E402
import ask  # noqa: E402


class SoftLimitTests(unittest.TestCase):
    def tearDown(self):
        fl.clear()

    def test_429_is_soft(self):
        self.assertTrue(fl.is_soft_limit(429, "rate limited"))

    def test_zhipu_1302_body_is_soft(self):
        self.assertTrue(fl.is_soft_limit(429, '{"error":{"code":"1302","message":"速率限制"}}'))

    def test_zhipu_1305_body_is_soft(self):
        self.assertTrue(fl.is_soft_limit(429, '{"error":{"code":"1305","message":"访问量过大"}}'))

    def test_fatal_is_not_soft(self):
        self.assertFalse(fl.is_soft_limit(401, "unauthorized"))
        self.assertFalse(fl.is_soft_limit(402, "insufficient credits"))

    def test_plain_text_mentioning_1302_without_rate_word_is_not_soft(self):
        self.assertFalse(fl.is_soft_limit(400, "order 1302 shipped"))

    def test_penalize_sets_cooldown(self):
        secs = fl.penalize("glm-flash", 429, "rate limit")
        self.assertGreater(secs, 0)
        self.assertTrue(fl.is_cooling("glm-flash"))

    def test_penalize_ignores_fatal(self):
        self.assertEqual(fl.penalize("doubao", 401, "bad key"), 0)
        self.assertFalse(fl.is_cooling("doubao"))

    def test_cooldown_expires(self):
        now = 1000.0
        fl.penalize("glm-flash", 429, "rate", now=now)
        self.assertFalse(fl.is_cooling("glm-flash", now=now + 10000))

    def test_demote_moves_cooling_to_end_preserving_order(self):
        now = time.time()
        fl.penalize("th-flash", 429, "rate", now=now)
        ranked = ["th-flash", "doubao", "th-code", "th-kimi"]
        out = fl.demote(ranked)
        self.assertEqual(out, ["doubao", "th-code", "th-kimi", "th-flash"])

    def test_demote_never_drops_a_provider(self):
        fl.penalize("a", 429, "rate")
        fl.penalize("b", 429, "rate")
        self.assertEqual(sorted(fl.demote(["a", "b"])), ["a", "b"])

    def test_clear(self):
        fl.penalize("a", 429, "rate")
        fl.clear()
        self.assertFalse(fl.is_cooling("a"))


class DiscoveryTests(unittest.TestCase):
    def test_discover_filters_paid_and_non_free(self):
        payload = {"data": [
            {"id": "good/free:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "paid:free", "pricing": {"prompt": "0.001", "completion": "0"}},
            {"id": "plain-model", "pricing": {"prompt": "0"}},
        ]}
        with mock.patch.object(fl.urllib.request, "urlopen") as u:
            u.return_value.__enter__ = lambda s: s
            u.return_value.__exit__ = mock.Mock()
            u.return_value.read.return_value = json.dumps(payload).encode()
            got = fl.discover_free_models("key")
        self.assertEqual(got, ["good/free:free"])

    def test_discover_failure_returns_empty(self):
        with mock.patch.object(fl.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("offline")):
            self.assertEqual(fl.discover_free_models("key"), [])

    def test_no_key_returns_empty_without_network(self):
        with mock.patch.object(fl.urllib.request, "urlopen") as u:
            self.assertEqual(fl.discover_free_models(""), [])
            u.assert_not_called()

    def test_resolve_uses_discovery_and_saves_last_good(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cache.json"
            with mock.patch.object(fl, "discover_free_models",
                                   return_value=["recursive/flint-v1.0:free", "other:free"]):
                model, source = fl.resolve_openrouter_model("key", force=True, path=p)
            self.assertEqual(source, "discovery")
            self.assertEqual(model, "recursive/flint-v1.0:free")
            self.assertEqual(fl.load_last_good(p)[0], "recursive/flint-v1.0:free")

    def test_resolve_falls_back_to_last_good_when_discovery_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cache.json"
            p.write_text(json.dumps({"free_models": ["cached:free"]}), encoding="utf-8")
            os.utime(p, (0, 0))
            with mock.patch.object(fl, "discover_free_models", return_value=[]):
                model, source = fl.resolve_openrouter_model("key", path=p)
            self.assertEqual(source, "last_good")
            self.assertEqual(model, "cached:free")

    def test_resolve_falls_back_to_seed_when_all_else_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "missing.json"
            with mock.patch.object(fl, "discover_free_models", return_value=[]):
                model, source = fl.resolve_openrouter_model("key", force=True, path=p)
            self.assertEqual(source, "seed")
            self.assertIn(model, fl.SEED_FREE_MODELS)

    def test_fresh_cache_skips_network(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cache.json"
            p.write_text(json.dumps({"free_models": ["cached:free"]}), encoding="utf-8")
            with mock.patch.object(fl, "discover_free_models") as disc:
                model, source = fl.resolve_openrouter_model("key", path=p)
            self.assertEqual(source, "cache")
            self.assertEqual(model, "cached:free")
            disc.assert_not_called()


class CLITests(unittest.TestCase):
    def test_discover_cli_loads_env_before_reading_key(self):
        # `free_lines.py --discover` runs without server.py, so it must load
        # .env itself; otherwise the key is empty and discovery silently falls
        # back to the static seed (observed on the production host).
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cache.json"
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False), \
                    mock.patch("env_loader.load_env_file",
                               side_effect=lambda: os.environ.update(
                                   {"OPENROUTER_API_KEY": "from-env"})), \
                    mock.patch.object(fl, "resolve_openrouter_model",
                                      return_value=("discovered:free", "discovery")) as res:
                fl.main(["--discover"])
            self.assertEqual(res.call_args.args[0], "from-env")


class ReasoningTests(unittest.TestCase):
    def test_extract_reasoning_from_openai_response(self):
        resp = {"choices": [{"message": {"content": "", "reasoning_content": "thinking..."}}]}
        self.assertEqual(fl.extract_reasoning(resp, "openai"), "thinking...")

    def test_extract_reasoning_empty_for_normal_response(self):
        resp = {"choices": [{"message": {"content": "hello"}}]}
        self.assertEqual(fl.extract_reasoning(resp, "openai"), "")

    def test_visible_text_never_includes_thinking(self):
        resp = {"choices": [{"message": {"content": "", "reasoning_content": "thinking..."}}]}
        self.assertEqual(cm.extract_text(resp, "openai"), "")
        self.assertTrue(cm.has_reasoning_only(resp, "openai"))


class RecordFailureTests(unittest.TestCase):
    def test_failed_call_is_recorded_with_error(self):
        used = {"provider": "glm-flash", "model": "glm-4.7-flash",
                "error": "empty body (reasoning_content only, budget exhausted)",
                "usage": {}, "fallback_from": None}
        with mock.patch("usage.record") as rec:
            ask._record("glm-flash", "glm-4.7-flash", used, 123)
        self.assertEqual(rec.call_count, 1)
        self.assertIn("empty body", rec.call_args.kwargs["error"])

    def test_fallback_records_primary_failure_too(self):
        used = {"provider": "th-flash", "model": "x", "error": None,
                "usage": {}, "fallback_from": "glm-flash"}
        with mock.patch("usage.record") as rec:
            ask._record("glm-flash", "glm-4.7-flash", used, 100)
        self.assertEqual(rec.call_count, 2)
        self.assertIn("primary failed", rec.call_args_list[1].kwargs["error"])

    def test_success_has_no_error(self):
        used = {"provider": "doubao", "model": "x", "error": None,
                "usage": {"total_tokens": 10}, "fallback_from": None}
        with mock.patch("usage.record") as rec:
            ask._record("doubao", "x", used, 100)
        self.assertIsNone(rec.call_args.kwargs["error"])


class SoftLimitIntegrationTests(unittest.TestCase):
    def tearDown(self):
        fl.clear()

    def test_soft_limit_stops_retrying_immediately(self):
        err = urllib.error.HTTPError("http://x", 429, "err", {},
                                     __import__("io").BytesIO(b'{"error":"rate limit"}'))
        with mock.patch.object(cm, "call_once", side_effect=err) as once, \
                mock.patch.object(cm.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                cm.call_with_retry("glm-flash", "hi", attempts=3)
        self.assertEqual(once.call_count, 1)
        sleep.assert_not_called()
        self.assertTrue(fl.is_cooling("glm-flash"))

    def test_normal_retry_still_happens(self):
        err = urllib.error.HTTPError("http://x", 500, "err", {},
                                     __import__("io").BytesIO(b'{"error":"oops"}'))
        with mock.patch.object(cm, "call_once", side_effect=err), \
                mock.patch.object(cm.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                cm.call_with_retry("doubao", "hi", attempts=2)
        # Only reached here if the loop completed; no cooldown expected.
        self.assertFalse(fl.is_cooling("doubao"))

    def test_route_demotes_cooling_line(self):
        now = time.time()
        fl.penalize("th-code", 429, "rate", now=now)
        with mock.patch.object(ask.judge, "configured_providers",
                               return_value={"th-code", "ark-deepseek"}), \
                mock.patch.object(ask.judge, "health_adjusted", side_effect=lambda r: r):
            _task, reason, ranked = ask.route("写一个 Python 快排")
        self.assertIn("demoted cooling lines", reason)
        self.assertEqual(ranked, ["ark-deepseek", "th-code"])


if __name__ == "__main__":
    unittest.main()
