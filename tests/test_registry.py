#!/usr/bin/env python3
"""Registry invariants: every table that names a provider must agree with PROVIDERS.

These tests catch the most common maintenance mistake in this project — adding a
provider (or renaming one) in one table while forgetting the others, which only
shows up at runtime as a silent "unknown provider" skip.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ask  # noqa: E402
import call_model as cm  # noqa: E402
import judge  # noqa: E402

MODES = {"openai", "anthropic"}


class ProviderTableTests(unittest.TestCase):
    def test_every_provider_is_a_well_formed_tuple(self):
        for name, spec in cm.PROVIDERS.items():
            with self.subTest(provider=name):
                self.assertEqual(len(spec), 4)
                base_url, key_env, model, mode = spec
                self.assertTrue(base_url.startswith("https://"), base_url)
                self.assertTrue(key_env.endswith("_API_KEY"), key_env)
                self.assertTrue(model, "default model must not be empty")
                self.assertIn(mode, MODES)

    def test_routing_candidates_all_exist(self):
        for task, ranked in judge.ROUTING.items():
            for p in ranked:
                with self.subTest(task=task, provider=p):
                    self.assertIn(p, cm.PROVIDERS)

    def test_fallback_targets_all_exist(self):
        for src, targets in cm.FALLBACK_CHAINS.items():
            self.assertIn(src, cm.PROVIDERS)
            for p in targets:
                with self.subTest(source=src, target=p):
                    self.assertIn(p, cm.PROVIDERS)

    def test_fallback_chain_never_contains_itself(self):
        for src, targets in cm.FALLBACK_CHAINS.items():
            self.assertNotIn(src, targets)

    def test_every_provider_is_reachable_by_key_detection(self):
        """configured_providers() must recognise a key for each provider name."""
        import os
        from unittest import mock

        for name, (_url, key_env, _model, _mode) in cm.PROVIDERS.items():
            with self.subTest(provider=name):
                with mock.patch.dict(os.environ,
                                     {key_env: "test-key", "AI_ROUTER_SKIP_PROVIDERS": ""},
                                     clear=False):
                    self.assertIn(name, judge.configured_providers())

    def test_free_providers_are_real_and_keyed(self):
        self.assertTrue(judge.FREE_PROVIDERS)
        for p in judge.FREE_PROVIDERS:
            with self.subTest(provider=p):
                self.assertIn(p, cm.PROVIDERS)

    def test_generic_order_has_no_unknown_names(self):
        for p in ask.GENERIC_ORDER:
            self.assertIn(p, cm.PROVIDERS)


if __name__ == "__main__":
    unittest.main()
