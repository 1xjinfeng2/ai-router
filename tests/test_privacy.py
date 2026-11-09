#!/usr/bin/env python3
"""Unit tests for the privacy vault (redact / restore / TTL / encryption)."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import privacy  # noqa: E402


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = privacy.DB_PATH
        privacy.DB_PATH = Path(self._tmp.name) / "vault.db"
        os.environ.pop("AI_ROUTER_VAULT_PASSPHRASE", None)
        # Keep the one-time encryption hint out of these assertions' output; the
        # hint has its own test class that re-enables it.
        self._old_no_tips = os.environ.get("AI_ROUTER_NO_TIPS")
        os.environ["AI_ROUTER_NO_TIPS"] = "1"

    def tearDown(self):
        privacy.DB_PATH = self._old_db
        if self._old_no_tips is None:
            os.environ.pop("AI_ROUTER_NO_TIPS", None)
        else:
            os.environ["AI_ROUTER_NO_TIPS"] = self._old_no_tips
        self._tmp.cleanup()

    def test_roundtrip_all_high_confidence_types(self):
        text = ("客户张伟，电话13800138000，邮箱zhangwei@corp.com，"
                "身份证44030019900101123X，卡号6222021234567890123，"
                "IP 10.0.0.5，QQ:1234567")
        r = privacy.redact(text)
        self.assertIn("<PII_PHONE_1>", r["text"])
        self.assertIn("<PII_EMAIL_1>", r["text"])
        self.assertNotIn("13800138000", r["text"])
        self.assertNotIn("zhangwei@corp.com", r["text"])
        self.assertGreaterEqual(r["count"], 6)
        self.assertIn("PHONE", r["breakdown"])
        self.assertEqual(privacy.restore(r["text"], r["session_id"]), text)

    def test_plain_text_untouched(self):
        text = "请帮我写一份项目周报，语气专业。"
        r = privacy.redact(text)
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["text"], text)

    def test_strict_mode_redacts_name(self):
        r = privacy.redact("我是李娜，请联系王经理跟进。", strict=True)
        self.assertIn("<PII_NAME_", r["text"])
        self.assertNotIn("李娜", r["text"])
        self.assertIn("李娜", privacy.restore(r["text"], r["session_id"]))

    def test_custom_terms(self):
        r = privacy.redact("关于星河计划ProjectX的进展", custom="星河计划,ProjectX")
        self.assertNotIn("星河计划", r["text"])
        self.assertNotIn("ProjectX", r["text"])
        self.assertEqual(r["count"], 2)
        self.assertIn("星河计划", privacy.restore(r["text"], r["session_id"]))

    def test_repeated_value_reuses_placeholder(self):
        r = privacy.redact("电话13800138000，再次确认13800138000")
        self.assertEqual(r["text"].count("<PII_PHONE_1>"), 2)
        self.assertNotIn("<PII_PHONE_2>", r["text"])

    def test_sessions_are_isolated(self):
        a = privacy.redact("邮箱a@b.com")
        b = privacy.redact("邮箱c@d.com")
        self.assertNotEqual(a["session_id"], b["session_id"])
        # Each session keeps its own row set in the vault...
        self.assertEqual(len(privacy.list_sessions()), 2)
        # ...and restoring with the matching session always returns the original.
        self.assertEqual(privacy.restore(a["text"], a["session_id"]), "邮箱a@b.com")
        self.assertEqual(privacy.restore(b["text"], b["session_id"]), "邮箱c@d.com")
        # Clearing one session must not affect the other.
        privacy.clear_session(a["session_id"])
        self.assertEqual(privacy.restore(b["text"], b["session_id"]), "邮箱c@d.com")

    def test_expire_drops_only_old_rows(self):
        r = privacy.redact("电话13800138000")
        conn = privacy._conn()
        conn.execute(
            "INSERT INTO vault(session_id,category,placeholder,original,created_at) "
            "VALUES('old','X','<PII_X_1>','v','2020-01-01 00:00:00')")
        conn.commit()
        conn.close()
        removed = privacy.expire(7)
        self.assertEqual(removed, 1)
        # the fresh row survived
        self.assertIn("13800138000", privacy.restore(r["text"], r["session_id"]))

    def test_expire_zero_is_noop(self):
        privacy.redact("电话13800138000")
        self.assertEqual(privacy.expire(0), 0)
        self.assertEqual(len(privacy.list_sessions()), 1)

    def test_encryption_degrades_without_library(self):
        if privacy._have_crypto():
            self.skipTest("cryptography installed; degradation path not applicable")
        n, msg = privacy.enable_encryption()
        self.assertEqual(n, 0)
        self.assertIn("cryptography", msg)

    def test_encryption_roundtrip_when_available(self):
        if not privacy._have_crypto():
            self.skipTest("cryptography not installed")
        os.environ["AI_ROUTER_VAULT_PASSPHRASE"] = "unit-test-passphrase"
        try:
            r = privacy.redact("电话13900001111")
            n, _msg = privacy.enable_encryption()
            self.assertGreaterEqual(n, 1)
            conn = privacy._conn()
            stored = conn.execute(
                "SELECT original FROM vault WHERE session_id=?",
                (r["session_id"],)).fetchone()[0]
            conn.close()
            self.assertTrue(stored.startswith(privacy.ENC_PREFIX))
            self.assertNotIn("13900001111", stored)
            self.assertIn("13900001111", privacy.restore(r["text"], r["session_id"]))
        finally:
            os.environ.pop("AI_ROUTER_VAULT_PASSPHRASE", None)


class StreamRestorerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = privacy.DB_PATH
        privacy.DB_PATH = Path(self._tmp.name) / "vault.db"
        self._old_no_tips = os.environ.get("AI_ROUTER_NO_TIPS")
        os.environ["AI_ROUTER_NO_TIPS"] = "1"
        self.r = privacy.redact("电话13800138000 邮箱a@b.com")

    def tearDown(self):
        privacy.DB_PATH = self._old_db
        if self._old_no_tips is None:
            os.environ.pop("AI_ROUTER_NO_TIPS", None)
        else:
            os.environ["AI_ROUTER_NO_TIPS"] = self._old_no_tips
        self._tmp.cleanup()

    def test_placeholder_split_across_chunks(self):
        full = self.r["text"]
        restorer = privacy.StreamRestorer(self.r["session_id"])
        out = []
        # feed in 3-char slices so placeholders are guaranteed to be split
        for i in range(0, len(full), 3):
            out.append(restorer.feed(full[i:i + 3]))
        out.append(restorer.flush())
        self.assertEqual("".join(out), "电话13800138000 邮箱a@b.com")

    def test_unknown_placeholder_passes_through(self):
        restorer = privacy.StreamRestorer("no-such-session")
        self.assertEqual(restorer.feed("hello <PII_X_9> world"),
                         "hello <PII_X_9> world")
        self.assertEqual(restorer.flush(), "")

    def test_partial_placeholder_is_held_until_complete(self):
        restorer = privacy.StreamRestorer(self.r["session_id"])
        first = restorer.feed("prefix <PII_PHO")
        self.assertEqual(first, "prefix ")
        second = restorer.feed("NE_1> suffix")
        self.assertEqual(second, "13800138000 suffix")
        self.assertEqual(restorer.flush(), "")


class EncryptionHintTests(unittest.TestCase):
    """One-time encryption onboarding hint (fires once, then stays quiet)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = privacy.DB_PATH
        privacy.DB_PATH = Path(self._tmp.name) / "vault.db"
        self._env = {k: os.environ.pop(k, None) for k in
                     ("AI_ROUTER_VAULT_PASSPHRASE", "AI_ROUTER_NO_TIPS")}

    def tearDown(self):
        privacy.DB_PATH = self._old_db
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_hint_fires_once_then_stays_silent(self):
        first = privacy.encryption_hint()
        self.assertIsNotNone(first)
        self.assertIn("AI_ROUTER_VAULT_PASSPHRASE", first)
        self.assertTrue(privacy._hint_marker().exists())
        self.assertIsNone(privacy.encryption_hint())

    def test_marker_lives_next_to_vault(self):
        privacy.encryption_hint()
        self.assertEqual(privacy._hint_marker().parent, privacy.DB_PATH.parent)

    def test_no_hint_when_passphrase_already_set(self):
        os.environ["AI_ROUTER_VAULT_PASSPHRASE"] = "x" * 20
        self.assertIsNone(privacy.encryption_hint())
        self.assertFalse(privacy._hint_marker().exists())

    def test_opt_out_env_suppresses_hint(self):
        os.environ["AI_ROUTER_NO_TIPS"] = "1"
        self.assertIsNone(privacy.encryption_hint())
        self.assertFalse(privacy._hint_marker().exists())

    def test_maybe_hint_writes_to_given_stream(self):
        buf = io.StringIO()
        text = privacy.maybe_hint_encryption(stream=buf)
        self.assertIn("plaintext", text)
        self.assertEqual(buf.getvalue(), text + "\n")

    def test_default_stream_uses_sys_stderr_and_consumes_marker(self):
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            text = privacy.maybe_hint_encryption()
            printed = sys.stderr.getvalue()
        finally:
            sys.stderr = old
        self.assertIn(text, printed)
        # Second call is silent: the marker written by the first call holds.
        self.assertIsNone(privacy.maybe_hint_encryption())

    def test_redact_asks_for_hint_only_when_pii_stored(self):
        # The call site guards on count>0, so a prompt with no PII must not fire
        # it. The printing itself is covered by the maybe_hint tests above.
        calls = []
        old = privacy.maybe_hint_encryption
        privacy.maybe_hint_encryption = lambda: calls.append(1)
        try:
            privacy.redact("这段没有个人信息")
            self.assertEqual(calls, [])
            privacy.redact("电话13800138000")
            self.assertEqual(calls, [1])
        finally:
            privacy.maybe_hint_encryption = old

    def test_guide_names_dependency_when_crypto_missing(self):
        old = privacy._have_crypto
        try:
            privacy._have_crypto = lambda: False
            self.assertIn("pip install cryptography", privacy.encryption_guide())
            privacy._have_crypto = lambda: True
            self.assertNotIn("pip install", privacy.encryption_guide())
        finally:
            privacy._have_crypto = old


if __name__ == "__main__":
    unittest.main()
