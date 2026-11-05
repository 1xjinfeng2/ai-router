#!/usr/bin/env python3
"""Unit tests for the judge (task classification + provider ranking)."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import judge  # noqa: E402


class JudgeTests(unittest.TestCase):
    def test_code_prompt(self):
        task, _ = judge.rule_classify("帮我用 Python 写一个爬虫脚本，有个 bug 报错")
        self.assertEqual(task, "code")

    def test_chinese_copywriting_prompt(self):
        task, _ = judge.rule_classify("帮我写一段小红书文案，中文润色")
        self.assertEqual(task, "chinese")

    def test_structured_prompt(self):
        task, _ = judge.rule_classify("把这段文本提取成 json 结构化字段")
        self.assertEqual(task, "structured")

    def test_empty_keyword_defaults_to_cheap(self):
        task, reason = judge.rule_classify("嗯")
        self.assertEqual(task, "cheap")
        self.assertIn("no keyword", reason)

    def test_every_task_type_has_a_route(self):
        for task in judge.KEYWORDS:
            self.assertIn(task, judge.ROUTING)

    def test_default_model_comes_from_providers_table(self):
        self.assertEqual(judge.default_model("doubao"), "doubao-seed-evolving")
        self.assertEqual(judge.default_model("th-flash"), "deepseek-v4.1-flash")
        self.assertIsNone(judge.default_model(None))

    def test_configured_providers_filters_by_env(self):
        with mock.patch.dict(os.environ, {"ARK_API_KEY": "x"}, clear=False):
            os.environ.pop("HUNYUAN_API_KEY", None)
            configured = judge.configured_providers()
            self.assertIn("doubao", configured)
            self.assertIn("ark-deepseek", configured)
            self.assertNotIn("th-flash", configured)

    def test_skip_list_is_excluded_from_configured(self):
        with mock.patch.dict(os.environ,
                             {"ARK_API_KEY": "x", "AI_ROUTER_SKIP_PROVIDERS": "doubao"},
                             clear=False):
            configured = judge.configured_providers()
            self.assertNotIn("doubao", configured)
            self.assertIn("ark-deepseek", configured)

    def test_llm_classify_falls_back_to_rules_on_error(self):
        with mock.patch("subprocess.run", side_effect=TimeoutError("boom")):
            task, reason = judge.llm_classify("帮我写 python 函数")
        self.assertEqual(task, "code")
        self.assertIn("matched keywords", reason)


class HealthRoutingTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        import usage
        self._usage = usage
        self._old_db = usage.DB_PATH
        usage.DB_PATH = Path(self._tmp.name) / "usage.db"

    def tearDown(self):
        self._usage.DB_PATH = self._old_db
        self._tmp.cleanup()

    def test_unhealthy_provider_is_demoted(self):
        for _ in range(4):
            self._usage.record("doubao", "m", error="HTTP 500")
        self._usage.record("doubao", "m", 10, 10, 20)
        ranked = judge.health_adjusted(["doubao", "th-flash"])
        self.assertEqual(ranked, ["th-flash", "doubao"])

    def test_single_failure_does_not_reorder(self):
        self._usage.record("doubao", "m", error="HTTP 500")
        ranked = judge.health_adjusted(["doubao", "th-flash"])
        self.assertEqual(ranked, ["doubao", "th-flash"])

    def test_healthy_order_is_preserved(self):
        for _ in range(5):
            self._usage.record("doubao", "m", 10, 10, 20)
        ranked = judge.health_adjusted(["doubao", "th-flash"])
        self.assertEqual(ranked, ["doubao", "th-flash"])


if __name__ == "__main__":
    unittest.main()
