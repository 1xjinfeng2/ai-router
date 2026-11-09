#!/usr/bin/env python3
"""Tests for scripts/server.py - auth, limits, routing, and the ask round trip.

The server is started on port 0 (an ephemeral port) in a background thread, so
these tests never touch the network beyond loopback and never spend money: the
model call itself is stubbed out.
"""
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ask  # noqa: E402
import server as srv  # noqa: E402


class Quiet(srv.Handler):
    def log_message(self, fmt, *args):  # noqa: N802
        pass


def start(token="t0ken", rate=30, cors=None):
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    httpd.token = token
    httpd.cors = cors
    httpd.trusted_proxy = None
    httpd.limiter = srv.RateLimiter(rate)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def call(base, path, payload=None, token=None, method=None):
    url = base + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_health_is_open_and_leaks_no_keys(self):
        code, body = call(self.base, "/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["auth"])
        self.assertNotIn("ARK_API_KEY", json.dumps(body))

    def test_health_reports_config_fingerprint(self):
        _, body = call(self.base, "/health")
        self.assertIn("config_hash", body)
        self.assertEqual(len(body["config_hash"]), 12)
        self.assertIn("providers", body)
        self.assertEqual(len(body["providers"]), body["providers_configured"])
        self.assertTrue(body["config_ok"])
        self.assertEqual(body["drift"], [])

    def test_health_reports_drift_when_expectations_are_set(self):
        with mock.patch.dict("os.environ", {"AI_ROUTER_EXPECT_VERSION": "0.0.0"}):
            _, body = call(self.base, "/health")
        self.assertFalse(body["config_ok"])
        self.assertTrue(body["drift"])
        self.assertTrue(any("version" in d for d in body["drift"]))

    def test_ask_requires_token(self):
        code, body = call(self.base, "/ask", {"prompt": "hi"})
        self.assertEqual(code, 401)
        self.assertIn("token", body["error"])

    def test_wrong_token_rejected(self):
        code, _ = call(self.base, "/ask", {"prompt": "hi"}, token="nope")
        self.assertEqual(code, 401)

    def test_route_decides_without_calling_a_model(self):
        code, body = call(self.base, "/route", {"prompt": "帮我写一段小红书文案"}, token="t0ken")
        self.assertEqual(code, 200)
        self.assertEqual(body["task_type"], "chinese")
        self.assertIsInstance(body["ranked_providers"], list)

    def test_ask_returns_model_answer(self):
        fake = {"text": "好的", "provider": "doubao", "model": "x",
                "requested_provider": "doubao", "task_type": "chinese",
                "reason": "test", "usage": {"total_tokens": 3},
                "latency_ms": 12, "fallback_from": None}
        with mock.patch.object(ask, "answer", return_value=fake) as m:
            code, body = call(self.base, "/ask", {"prompt": "写句话", "max_tokens": 64},
                              token="t0ken")
        self.assertEqual(code, 200)
        self.assertEqual(body["text"], "好的")
        self.assertEqual(m.call_args.kwargs["max_tokens"], 64)

    def test_ask_accepts_inline_base64_image(self):
        """A remote client cannot send a local path, so it sends a data: URL."""
        fake = {"text": "图里是海报", "provider": "th-vision", "model": "hy-vision-2.0-instruct",
                "requested_provider": "th-vision", "task_type": "vision",
                "reason": "forced", "usage": {}, "latency_ms": 5, "fallback_from": None}
        data_url = "data:image/png;base64,iVBORw0KGgo="
        with mock.patch.object(ask, "answer", return_value=fake) as m:
            code, body = call(self.base, "/ask",
                              {"prompt": "看图", "images": [data_url]}, token="t0ken")
        self.assertEqual(code, 200)
        self.assertEqual(body["text"], "图里是海报")
        # Forwarded untouched: re-encoding a data URL would corrupt it.
        self.assertEqual(m.call_args.kwargs["images"], [data_url])

    def test_image_path_that_does_not_exist_is_400(self):
        code, body = call(self.base, "/ask",
                          {"prompt": "看图", "images": ["/no/such/photo.jpg"]},
                          token="t0ken")
        self.assertEqual(code, 400)
        self.assertIn("not found", body["error"])
        self.assertIn("data: URL", body["hint"])

    def test_oversized_inline_image_is_413(self):
        with mock.patch.object(srv, "MAX_IMAGE_B64", 32):
            code, body = call(self.base, "/ask",
                              {"prompt": "看图",
                               "images": ["data:image/png;base64," + "A" * 200]},
                              token="t0ken")
        self.assertEqual(code, 413)
        self.assertIn("too large", body["error"])

    def test_missing_prompt_is_400(self):
        code, body = call(self.base, "/ask", {"system": "x"}, token="t0ken")
        self.assertEqual(code, 400)
        self.assertIn("prompt", body["error"])

    def test_bad_json_is_400(self):
        req = urllib.request.Request(self.base + "/ask", data=b"{not json",
                                     method="POST",
                                     headers={"Authorization": "Bearer t0ken",
                                              "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_provider_failure_maps_to_502(self):
        err = ask.AskError("upstream said 429", code=3, status=429)
        with mock.patch.object(ask, "answer", side_effect=err):
            code, body = call(self.base, "/ask", {"prompt": "hi"}, token="t0ken")
        self.assertEqual(code, 502)
        self.assertEqual(body["upstream_status"], 429)

    def test_unknown_path_is_404(self):
        code, _ = call(self.base, "/nope", token="t0ken")
        self.assertEqual(code, 404)

    def test_providers_lists_configuration(self):
        code, body = call(self.base, "/providers", token="t0ken")
        self.assertEqual(code, 200)
        names = {p["name"] for p in body["providers"]}
        self.assertIn("doubao", names)
        self.assertIn("custom", names)


class LimitTests(unittest.TestCase):
    def test_rate_limit_returns_429(self):
        httpd, base = start(rate=1)
        try:
            first, _ = call(base, "/providers", token="t0ken")
            second, body = call(base, "/providers", token="t0ken")
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(first, 200)
        self.assertEqual(second, 429)
        self.assertIn("rate limit", body["error"])

    def test_public_bind_without_token_is_refused(self):
        self.assertEqual(srv.main(["--host", "0.0.0.0", "--port", "1", "--token", ""]), 2)

    def test_cors_headers_when_enabled(self):
        httpd, base = start(cors="https://example.com")
        try:
            req = urllib.request.Request(base + "/health")
            with urllib.request.urlopen(req, timeout=10) as r:
                self.assertEqual(r.headers.get("Access-Control-Allow-Origin"),
                                 "https://example.com")
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
