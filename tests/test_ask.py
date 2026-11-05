#!/usr/bin/env python3
"""Unit tests for ask.py (auto-route: classify -> pick best model)."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ask  # noqa: E402
import judge  # noqa: E402

CONFIGURED = {"doubao", "th-code", "th-deepseek", "th-flash", "th-kimi",
              "th-glm", "ark-deepseek", "ark-kimi", "deepseek"}


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


if __name__ == "__main__":
    unittest.main()
