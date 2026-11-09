#!/usr/bin/env python3
"""Standalone CLIs must bootstrap .env before resolving AI_ROUTER_* variables.

Production hardening moved usage.db / privacy_vault.db into a
service-account-owned data/ directory, with the paths supplied by
/opt/ai-router/.env.  server.py loads that file, but a bare
`python usage.py --health` does NOT go through server.py: it used the
skill-root fallback path and died with "unable to open database file".

These tests rebuild that layout in a temp tree and run real subprocesses,
so the regression cannot quietly come back.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class StandaloneEnvBootstrapTests(unittest.TestCase):
    def _sandbox(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        shutil.copytree(SCRIPTS, root / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (root / "data").mkdir()
        # Simulate a fresh process whose environment has no AI_ROUTER_* vars:
        # every value must come from the .env file itself.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("AI_ROUTER_")}
        return root, env

    @staticmethod
    def _write_env(root, text):
        (root / ".env").write_text(text, encoding="utf-8")

    def _cli(self, root, env, script, *args):
        return subprocess.run(
            [sys.executable, str(root / "scripts" / script), *args],
            capture_output=True, text=True, env=env, cwd=str(root), timeout=30,
        )

    def test_usage_cli_uses_db_path_from_env(self):
        root, env = self._sandbox()
        db = root / "data" / "usage.db"
        self._write_env(root, "AI_ROUTER_USAGE_DB=%s\n" % db)
        proc = self._cli(root, env, "usage.py", "--health")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("unable to open database file", proc.stderr)
        self.assertTrue(db.exists(),
                        "usage.py ignored AI_ROUTER_USAGE_DB from .env")

    def test_privacy_cli_uses_db_path_from_env(self):
        root, env = self._sandbox()
        db = root / "data" / "vault.db"
        self._write_env(root, "AI_ROUTER_VAULT_DB=%s\n" % db)
        proc = self._cli(root, env, "privacy.py", "--list")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(db.exists(),
                        "privacy.py ignored AI_ROUTER_VAULT_DB from .env")

    def test_judge_import_reads_custom_routes_from_env(self):
        # CUSTOM_ROUTES is computed at import time, so loading .env only inside
        # main() used to leave standalone CLI runs with the default "cheap".
        root, env = self._sandbox()
        self._write_env(root, "AI_ROUTER_CUSTOM_ROUTES=code,structured\n")
        code = ("import sys; sys.path.insert(0, %r); import judge; "
                "print(','.join(judge.CUSTOM_ROUTES))" % str(root / "scripts"))
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True, env=env,
                              cwd=str(root), timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "code,structured", proc.stdout)


if __name__ == "__main__":
    unittest.main()
