#!/usr/bin/env python3
"""Unit tests for the reliability layer: error classification, retry, fallback,
and reasoning-model token expansion. No network access: call_once is mocked."""
import io
import os
import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import call_model as cm  # noqa: E402


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {},
                                  io.BytesIO(b'{"error":"stub"}'))


def _resp(text):
    return {"choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class ClassificationTests(unittest.TestCase):
    def test_payment_required_is_fatal(self):
        status, retryable, _ = cm.classify_error(_http_error(402))
        self.assertEqual(status, 402)
        self.assertFalse(retryable)

    def test_auth_error_is_fatal(self):
        self.assertFalse(cm.classify_error(_http_error(401))[1])

    def test_rate_limit_is_retryable(self):
        self.assertTrue(cm.classify_error(_http_error(429))[1])

    def test_server_error_is_retryable(self):
        self.assertTrue(cm.classify_error(_http_error(503))[1])

    def test_timeout_is_retryable(self):
        status, retryable, detail = cm.classify_error(TimeoutError("read timed out"))
        self.assertIsNone(status)
        self.assertTrue(retryable)
        self.assertIn("timeout", detail)

    def test_reasoning_floor(self):
        self.assertEqual(cm.effective_max_tokens("th-deepseek", 512), 4096)
        self.assertEqual(cm.effective_max_tokens("doubao", 512), 512)
        self.assertEqual(cm.effective_max_tokens("doubao", 9999), 9999)


class RetryFallbackTests(unittest.TestCase):
    def setUp(self):
        self._saved = cm.call_once
        self._delay = cm.RETRY_BASE_DELAY
        cm.RETRY_BASE_DELAY = 0
        os.environ["ARK_API_KEY"] = "test-key"
        os.environ["HUNYUAN_API_KEY"] = "test-key"
        os.environ.pop("DEEPSEEK_API_KEY", None)

    def tearDown(self):
        cm.call_once = self._saved
        cm.RETRY_BASE_DELAY = self._delay

    def test_retries_transient_then_succeeds(self):
        calls = []

        def flaky(provider, prompt, system=None, temperature=None,
                  max_tokens=2048, top_p=None, timeout=60, model=None):
            calls.append(provider)
            if len(calls) < 3:
                raise _http_error(500)
            return _resp("ok")

        cm.call_once = flaky
        res = cm.call_text("th-flash", "x", max_tokens=256)
        self.assertEqual(res["text"], "ok")
        self.assertTrue(res["retried"])
        self.assertEqual(len(calls), 3)

    def test_fatal_error_switches_provider(self):
        seen = []

        def primary_dead(provider, prompt, system=None, temperature=None,
                         max_tokens=2048, top_p=None, timeout=60, model=None):
            seen.append(provider)
            if provider == "doubao":
                raise _http_error(402)
            return _resp("from backup")

        cm.call_once = primary_dead
        res = cm.call_text("doubao", "x")
        self.assertEqual(res["text"], "from backup")
        self.assertEqual(res["provider"], "ark-deepseek")
        self.assertEqual(res["fallback_from"], "doubao")
        self.assertEqual(seen, ["doubao", "ark-deepseek"])

    def test_no_fallback_when_disabled(self):
        def dead(provider, prompt, system=None, temperature=None,
                 max_tokens=2048, top_p=None, timeout=60, model=None):
            raise _http_error(402)

        cm.call_once = dead
        res = cm.call_text("doubao", "x", allow_fallback=False)
        self.assertIsNone(res["text"])
        self.assertIn("402", str(res["error"]))

    def test_skip_list_removes_dead_fallback_hop(self):
        seen = []

        def dead(provider, prompt, system=None, temperature=None,
                 max_tokens=2048, top_p=None, timeout=60, model=None):
            seen.append(provider)
            raise _http_error(402)

        cm.call_once = dead
        os.environ["AI_ROUTER_SKIP_PROVIDERS"] = "deepseek"
        try:
            cm.call_text("th-flash", "x")  # chain: th-flash -> doubao -> deepseek
        finally:
            os.environ.pop("AI_ROUTER_SKIP_PROVIDERS", None)
        self.assertNotIn("deepseek", seen)
        self.assertEqual(seen, ["th-flash", "doubao"])

    def test_skip_list_does_not_block_an_explicit_request(self):
        seen = []

        def ok(provider, prompt, system=None, temperature=None,
               max_tokens=2048, top_p=None, timeout=60, model=None):
            seen.append(provider)
            return _resp("ok")

        cm.call_once = ok
        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        os.environ["AI_ROUTER_SKIP_PROVIDERS"] = "deepseek"
        try:
            res = cm.call_text("deepseek", "x")
        finally:
            os.environ.pop("AI_ROUTER_SKIP_PROVIDERS", None)
            os.environ.pop("DEEPSEEK_API_KEY", None)
        self.assertEqual(res["text"], "ok")
        self.assertEqual(seen, ["deepseek"])
        self.assertIsNone(res["fallback_from"])

    def test_empty_body_triggers_token_expansion(self):
        budgets = []

        def reasoning_model(provider, prompt, system=None, temperature=None,
                            max_tokens=2048, top_p=None, timeout=60, model=None):
            budgets.append(max_tokens)
            if max_tokens <= 4096:
                return {"choices": [{"message": {"content": "",
                                                 "reasoning_content": "thinking"}}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 400,
                                  "total_tokens": 405}}
            return _resp("visible")

        cm.call_once = reasoning_model
        res = cm.call_text("th-deepseek", "x", max_tokens=512)
        self.assertEqual(res["text"], "visible")
        self.assertTrue(res["empty_retry"])
        self.assertEqual(budgets, [4096, 8192])

    def test_all_providers_failing_returns_error(self):
        def dead(provider, prompt, system=None, temperature=None,
                 max_tokens=2048, top_p=None, timeout=60, model=None):
            raise _http_error(500)

        cm.call_once = dead
        res = cm.call_text("th-flash", "x", allow_fallback=False)
        self.assertIsNone(res["text"])
        self.assertIsNotNone(res["error"])


class ImageInputTests(unittest.TestCase):
    def _png(self):
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_encode_image_returns_mime_and_base64(self):
        mime, b64 = cm.encode_image(self._png())
        self.assertEqual(mime, "image/png")
        self.assertTrue(b64.startswith("iVBOR"), b64[:12])

    def test_mime_is_sniffed_when_extension_is_unknown(self):
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".dat", delete=False)
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
        f.close()
        self.addCleanup(os.unlink, f.name)
        mime, _ = cm.encode_image(f.name)
        self.assertEqual(mime, "image/jpeg")

    def test_text_only_content_stays_a_plain_string(self):
        self.assertEqual(cm._build_content("hi", None, "openai"), "hi")

    def test_openai_content_carries_data_url(self):
        parts = cm._build_content("看图", [self._png()], "openai")
        self.assertEqual(parts[0], {"type": "text", "text": "看图"})
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_anthropic_content_uses_source_blocks(self):
        parts = cm._build_content("看图", [self._png()], "anthropic")
        self.assertEqual(parts[1]["type"], "image")
        self.assertEqual(parts[1]["source"]["media_type"], "image/png")


if __name__ == "__main__":
    unittest.main()
