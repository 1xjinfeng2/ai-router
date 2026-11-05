#!/usr/bin/env python3
"""Unit tests for usage metering, cache and provider health."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import usage  # noqa: E402


class UsageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = usage.DB_PATH
        usage.DB_PATH = Path(self._tmp.name) / "usage.db"

    def tearDown(self):
        usage.DB_PATH = self._old_db
        self._tmp.cleanup()

    def test_estimate_cost_known_and_unknown(self):
        cost = usage.estimate_cost("doubao", "doubao-seed-evolving", 1_000_000, 0)
        self.assertAlmostEqual(cost, 0.8, places=6)
        self.assertIsNone(usage.estimate_cost("nope", "no-such-model", 10, 10))

    def test_record_and_report_aggregate(self):
        usage.record("doubao", "doubao-seed-evolving", 100, 50, 150, latency_ms=1200)
        usage.record("doubao", "doubao-seed-evolving", 200, 100, 300, latency_ms=800)
        rows = usage.report()
        self.assertEqual(len(rows), 1)
        prov, model, calls, pt, ct, tt, cost, hits = rows[0]
        self.assertEqual((prov, model, calls), ("doubao", "doubao-seed-evolving", 2))
        self.assertEqual((pt, ct, tt), (300, 150, 450))
        self.assertGreater(cost, 0)
        self.assertEqual(hits, 0)

    def test_empty_report_does_not_crash(self):
        self.assertEqual(usage.report(), [])
        tot = usage.total_line()
        self.assertEqual(tot[0], 0)
        self.assertEqual(tot[4], 0)

    def test_cache_roundtrip_and_key_sensitivity(self):
        usage.cache_put("doubao", "m", "sys", "hello", 100, "CACHED")
        self.assertEqual(usage.cache_get("doubao", "m", "sys", "hello", 100)[1], "CACHED")
        # a different token budget is a different key
        self.assertIsNone(usage.cache_get("doubao", "m", "sys", "hello", 200)[1])
        self.assertIsNone(usage.cache_get("doubao", "m", "sys", "other", 100)[1])

    def test_cache_hit_recorded(self):
        usage.record("doubao", "m", cache_hit=1)
        self.assertEqual(usage.total_line()[5], 1)

    def test_health_reports_success_rate(self):
        usage.record("doubao", "m", 10, 10, 20, latency_ms=500)
        usage.record("doubao", "m", error="HTTP 500")
        usage.record("th-flash", "m", 5, 5, 10, fallback_from="doubao")
        rows = {r[0]: r for r in usage.health()}
        self.assertEqual(rows["doubao"][1], 2)   # calls
        self.assertEqual(rows["doubao"][2], 1)   # ok
        self.assertEqual(rows["th-flash"][4], 1)  # fallbacks

    def test_expire_removes_only_old_rows(self):
        usage.record("doubao", "m", 1, 1, 2)
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn = usage._conn()
        conn.execute("INSERT INTO calls(ts,provider,model) VALUES(?,?,?)",
                     (old, "oldprov", "oldmodel"))
        conn.execute("INSERT INTO cache(key,provider,model,text,ts) VALUES(?,?,?,?,?)",
                     ("k", "p", "m", "t", old))
        conn.commit()
        conn.close()
        nc, ncache = usage.expire(7)
        self.assertEqual((nc, ncache), (1, 1))
        remaining = {r[0] for r in usage.report()}
        self.assertNotIn("oldprov", remaining)

    def test_expire_zero_is_noop(self):
        usage.record("doubao", "m", 1, 1, 2)
        self.assertEqual(usage.expire(0), (0, 0))
        self.assertEqual(usage.total_line()[0], 1)


if __name__ == "__main__":
    unittest.main()
