#!/usr/bin/env python3
"""Unit tests for ask.py (auto-route: classify -> pick best model)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ask  # noqa: E402
import judge  # noqa: E402
import call_model  # noqa: E402

CONFIGURED = {"doubao", "th-code", "th-deepseek", "th-flash", "th-kimi",
              "th-glm", "th-vision", "ark-deepseek", "ark-kimi", "deepseek"}


class RouteTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(judge, "configured_providers", return_value=set(CONFIGURED))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_code_question_picks_a_code_model(self):
        task, _, ranked = ask.route("为什么这段 SQL 会锁表")
        self.assertEqual(task, "code")
        self.assertEqual(ranked[0], "th-code")

    def test_chinese_copywriting_picks_doubao(self):
        task, _, ranked = ask.route("帮我写一段小红书文案，中文润色")
        self.assertEqual(task, "chinese")
        self.assertEqual(ranked[0], "doubao")

    def test_reasoning_picks_deepseek(self):
        task, _, ranked = ask.route("帮我证明这个数学猜想")
        self.assertEqual(task, "reasoning")
        self.assertEqual(ranked[0], "th-deepseek")

    def test_long_context_picks_kimi(self):
        task, _, ranked = ask.route("总结这份 40 页的论文并提炼结论")
        self.assertEqual(task, "long-context")
        self.assertEqual(ranked[0], "th-kimi")

    def test_search_task_falls_back_but_still_answers(self):
        task, reason, ranked = ask.route("今天深圳的天气怎么样")
        self.assertEqual(task, "search")
        self.assertTrue(ranked, "search must still resolve to a provider")
        self.assertIn("generic order", reason)

    def test_unconfigured_preferred_provider_is_skipped(self):
        with mock.patch.object(judge, "configured_providers", return_value={"doubao"}):
            task, _, ranked = ask.route("用 Python 写个爬虫脚本")
            self.assertEqual(task, "code")
            self.assertEqual(ranked, ["doubao"])

    def test_vision_override_prefers_tokenhub_vision(self):
        task, reason, ranked = ask.route("这张图里是什么", task_override="vision")
        self.assertEqual(task, "vision")
        self.assertIn("forced", reason)
        self.assertEqual(ranked[0], "th-vision")

    def test_budget_mode_keeps_only_free_lines(self):
        free_set = {"glm-flash", "siliconflow", "groq"}
        with mock.patch.object(judge, "configured_providers",
                               return_value=set(CONFIGURED) | free_set):
            task, reason, ranked = ask.route("帮我润色一段中文文案", budget=True)
        self.assertEqual(task, "chinese")
        self.assertIn("free tier only", reason)
        self.assertTrue(set(ranked) <= free_set, ranked)
        self.assertEqual(ranked[0], "glm-flash")

    def test_budget_mode_without_free_lines_yields_nothing(self):
        task, reason, ranked = ask.route("帮我润色一段中文文案", budget=True)
        self.assertEqual(ranked, [])
        self.assertIn("free tier only", reason)

    def test_ranking_is_deterministic(self):
        first = ask.route("帮我写一段小红书文案")[2]
        second = ask.route("帮我写一段小红书文案")[2]
        self.assertEqual(first, second)


class RegressionTests(unittest.TestCase):
    """Cases where a flat keyword list used to misroute."""

    def test_single_verb_does_not_route_to_code(self):
        task, _ = judge.rule_classify("写一句30字以内的学术会议签到提醒，语气专业友好")
        self.assertEqual(task, "chinese")

    def test_plain_chinese_request_is_not_classified_as_small_talk(self):
        task, _ = judge.rule_classify("帮我想想这个活动流程怎么安排比较顺")
        self.assertEqual(task, "chinese")

    def test_short_filler_is_small_talk(self):
        task, _ = judge.rule_classify("嗯")
        self.assertEqual(task, "cheap")


class CliTests(unittest.TestCase):
    def test_dry_run_prints_routing_without_calling(self):
        import contextlib
        import io

        buf = io.StringIO()
        argv = ["ask.py", "--prompt", "帮我写一段小红书文案", "--dry-run"]
        with mock.patch.object(judge, "configured_providers", return_value={"doubao"}), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch.object(ask.call_model, "call_text",
                                  side_effect=AssertionError("dry-run must not call")), \
                contextlib.redirect_stdout(buf):
            ask.main()
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["provider"], "doubao")
        self.assertEqual(payload["task_type"], "chinese")
        self.assertNotIn("text", payload)

    def test_no_configured_provider_exits_with_code_2(self):
        import contextlib
        import io

        with mock.patch.object(judge, "configured_providers", return_value=set()), \
                mock.patch.object(sys, "argv", ["ask.py", "--prompt", "hi"]), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                ask.main()
        self.assertEqual(ctx.exception.code, 2)


class RemoteModeTests(unittest.TestCase):
    """In client mode the server holds the keys, so files must travel inline."""

    def test_images_are_base64_encoded_before_sending(self):
        sent = {}

        def fake_post(url, payload, token=None, timeout=120):
            sent.update(url=url, payload=payload, token=token)
            return {"text": "ok", "provider": "th-vision", "model": "m"}

        with mock.patch.object(ask, "_post_json", fake_post):
            with tempfile.TemporaryDirectory() as d:
                pic = Path(d) / "pic.png"
                pic.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
                ask.answer("看图", images=[str(pic)], remote="http://x:1")

        self.assertTrue(sent["url"].endswith("/ask"))
        self.assertTrue(sent["payload"]["images"][0].startswith("data:image/png;base64,"))

    def test_text_only_request_carries_no_images_field(self):
        sent = {}

        def fake_post(url, payload, token=None, timeout=120):
            sent.update(payload=payload)
            return {"text": "ok"}

        with mock.patch.object(ask, "_post_json", fake_post):
            ask.answer("你好", remote="http://x:1")
        self.assertNotIn("images", sent["payload"])


class PaidExclusionTests(unittest.TestCase):
    """Auto-routing must never select a provider that has no free tier."""
    PAID = {"openai", "claude", "deepseek"}
    FULL = {"openai", "claude", "deepseek", "doubao", "th-code", "th-kimi",
            "th-deepseek", "th-flash", "th-glm", "th-vision", "ark-deepseek",
            "ark-kimi", "ark-glm", "kimi", "qwen", "glm", "hunyuan", "gemini",
            "ernie", "glm-flash", "siliconflow", "groq", "openrouter",
            "mistral", "nvidia"}

    def setUp(self):
        p = mock.patch.object(judge, "configured_providers",
                              return_value=set(self.FULL))
        p.start()
        self.addCleanup(p.stop)

    def test_no_paid_provider_in_any_route(self):
        prompts = [
            "帮我写一段 Python 代码",
            "把这段会议通知润色成中文",
            "帮我证明哥德巴赫猜想",
            "总结这篇 50 页文档",
            "描述这张图片的内容",
            "查一下最新的政策变化",
        ]
        for prompt in prompts:
            _, _, ranked = ask.route(prompt)
            self.assertTrue(ranked, f"route for {prompt!r} returned nothing")
            overlap = set(ranked) & self.PAID
            self.assertFalse(overlap, f"{prompt!r} routed to paid: {overlap}")

    def test_paid_aliases_with_free_quota_still_routed(self):
        # th-deepseek / ark-deepseek keep a free quota and must remain usable.
        _, _, ranked = ask.route("帮我证明这个数学猜想")
        self.assertIn("th-deepseek", ranked)
        self.assertIn("ark-deepseek", ranked)
        self.assertNotIn("deepseek", ranked)


class ExplicitPaidProviderTests(unittest.TestCase):
    """An explicit --provider must still reach a paid model on deliberate use."""
    def test_explicit_provider_overrides_paid_exclusion(self):
        fake = {"text": "x", "provider": "openai", "model": "gpt-5.1",
                "error": None, "usage": {}, "attempted": [],
                "fallback_from": None}
        with mock.patch.object(call_model, "call_text", return_value=fake) as m, \
                mock.patch.object(judge, "configured_providers",
                                  return_value={"openai"}):
            res = ask.answer("hi", provider="openai")
        m.assert_called_once()
        self.assertEqual(m.call_args.args[0], "openai")
        self.assertEqual(res["provider"], "openai")


if __name__ == "__main__":
    unittest.main()
