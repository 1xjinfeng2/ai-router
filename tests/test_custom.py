#!/usr/bin/env python3
"""Tests for the self-hosted `custom` provider (vLLM / Ollama / any gateway).

The endpoint is env-driven rather than hardcoded, so most of the risk lives in
"did we pick up the environment at the right moment" - that is what these tests
pin down.
"""
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ask  # noqa: E402
import call_model as cm  # noqa: E402
import judge  # noqa: E402

ENV = {"AI_ROUTER_CUSTOM_BASE_URL": "http://gpu-box:8000/v1/",
       "AI_ROUTER_CUSTOM_MODEL": "Qwen3-32B",
       "AI_ROUTER_CUSTOM_API_KEY": "secret"}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CustomProviderTests(unittest.TestCase):
    def tearDown(self):
        for key in ENV:
            os.environ.pop(key, None)
        cm.refresh_custom()

    def test_refresh_reads_env_and_trims_slash(self):
        with mock.patch.dict(os.environ, ENV, clear=False):
            base, key_env, model, mode = cm.refresh_custom()
        self.assertEqual(base, "http://gpu-box:8000/v1")
        self.assertEqual(key_env, "AI_ROUTER_CUSTOM_API_KEY")
        self.assertEqual(model, "Qwen3-32B")
        self.assertEqual(mode, "openai")

    def test_not_configured_without_endpoint(self):
        with mock.patch.dict(os.environ, {"AI_ROUTER_CUSTOM_API_KEY": "x"}, clear=False):
            os.environ.pop("AI_ROUTER_CUSTOM_BASE_URL", None)
            self.assertFalse(cm.is_configured("custom"))
            self.assertNotIn("custom", judge.configured_providers())

    def test_configured_without_key_because_local_servers_need_none(self):
        env = {"AI_ROUTER_CUSTOM_BASE_URL": "http://127.0.0.1:11434/v1"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("AI_ROUTER_CUSTOM_API_KEY", None)
            self.assertTrue(cm.is_configured("custom"))
            self.assertEqual(cm.api_key_for("custom"), "EMPTY")

    def test_detected_by_judge_and_routed_for_cheap(self):
        with mock.patch.dict(os.environ, ENV, clear=False):
            os.environ["AI_ROUTER_SKIP_PROVIDERS"] = ""
            self.assertIn("custom", judge.configured_providers())
            task, _reason, ranked = ask.route("嗯")
        self.assertEqual(task, "cheap")
        self.assertEqual(ranked[0], "custom")

    def test_call_once_targets_the_configured_endpoint(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.get_header("Authorization")
            captured["body"] = json.loads(req.data.decode())
            return FakeResponse(json.dumps({
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2},
            }).encode())

        with mock.patch.dict(os.environ, ENV, clear=False), \
                mock.patch.object(cm.urllib.request, "urlopen", fake_urlopen):
            resp = cm.call_once("custom", "hello")

        self.assertEqual(captured["url"], "http://gpu-box:8000/v1/chat/completions")
        self.assertEqual(captured["auth"], "Bearer secret")
        self.assertEqual(captured["body"]["model"], "Qwen3-32B")
        self.assertEqual(cm.extract_text(resp, "openai"), "hi")

    def test_missing_endpoint_gives_a_clear_error(self):
        os.environ.pop("AI_ROUTER_CUSTOM_BASE_URL", None)
        cm.refresh_custom()
        with self.assertRaises(RuntimeError) as ctx:
            cm.call_once("custom", "hello")
        self.assertIn("AI_ROUTER_CUSTOM_BASE_URL", str(ctx.exception))

    def test_custom_is_a_fallback_source_not_a_dead_end(self):
        self.assertIn("custom", cm.FALLBACK_CHAINS)
        self.assertTrue(cm.FALLBACK_CHAINS["custom"])


if __name__ == "__main__":
    unittest.main()
