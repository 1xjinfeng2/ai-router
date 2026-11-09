#!/usr/bin/env python3
"""
update.py - Self-update the ai-router skill from its git remote, safely.

Zero dependencies (Python stdlib only). The point is to make "keep this skill
current" a one-liner instead of a manual git pull on every machine:

  1. compare the local SKILL.md version with the remote one,
  2. refuse to touch a dirty working tree (your local edits are never lost),
  3. fast-forward pull, then run the test suite,
  4. if the tests fail, roll back to the exact commit you were on,
  5. optionally rebuild the distributable zip.

Usage:
    python scripts/update.py --check              # exit 0 = current, 1 = update available
    python scripts/update.py --apply              # pull + test + rollback on failure
    python scripts/update.py --apply --zip dist   # ... and repackage the zip
    python scripts/update.py --check --json       # machine-readable
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REMOTE = "https://github.com/1xjinfeng2/ai-router"

EXIT_CURRENT = 0
EXIT_BEHIND = 1
EXIT_ERROR = 2
EXIT_DIRTY = 3
EXIT_TEST_FAILED = 4
EXIT_SMOKE_FAILED = 5


def _run(args, cwd, timeout=120, env_extra=None):
    """Run a command without ever blocking on an interactive credential prompt."""
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
                "GIT_ASKPASS": "", "SSH_ASKPASS": ""})
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, env=env)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(args)} timed out after {timeout}s"


def parse_version(text):
    """'1.4.0' -> (1, 4, 0). Non-numeric parts are ignored."""
    nums = re.findall(r"\d+", str(text))
    return tuple(int(n) for n in nums[:4]) if nums else ()


def read_version(path):
    """Version from a SKILL.md-style YAML frontmatter ('' when absent)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    head = text[:end] if end != -1 else text
    m = re.search(r"^version:\s*([^\s#]+)", head, re.M)
    return m.group(1).strip("'\"") if m else ""


def is_newer(remote, local):
    """True when `remote` is a strictly higher version than `local`."""
    r, l = parse_version(remote), parse_version(local)
    if not r:
        return False
    return r > l


def git(args, repo, **kw):
    return _run(["git"] + args, repo, **kw)


def has_git(repo):
    code, _, _ = git(["rev-parse", "--is-inside-work-tree"], repo, timeout=20)
    return code == 0


def is_dirty(repo):
    code, out, _ = git(["status", "--porcelain"], repo, timeout=20)
    return code != 0 or bool(out)


def remote_slug(repo):
    """owner/repo from the origin URL, or '' when it cannot be parsed."""
    code, out, _ = git(["remote", "get-url", "origin"], repo, timeout=20)
    if code != 0 or not out:
        return ""
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", out.strip())
    return m.group(1) if m else ""


def remote_version_http(slug, timeout=15):
    """Version of SKILL.md on the default branch, fetched over HTTPS.

    Used when git is missing or the remote is unreachable (SSH blocked, no
    credentials). Only the version is compared - no code is downloaded.
    """
    if not slug:
        return "", "no origin remote"
    url = f"https://raw.githubusercontent.com/{slug}/main/SKILL.md"
    req = urllib.request.Request(url, headers={"User-Agent": "ai-router-update"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return read_version_text(r.read().decode("utf-8", "replace")), ""
    except (urllib.error.URLError, OSError) as e:
        return "", f"HTTP check failed: {e}"


def read_version_text(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    head = text[:end] if end != -1 else text
    m = re.search(r"^version:\s*([^\s#]+)", head, re.M)
    return m.group(1).strip("'\"") if m else ""


def status(repo):
    """Collect everything needed to decide whether an update exists."""
    info = {"repo": str(repo), "local_version": read_version(repo / "SKILL.md"),
            "remote_version": "", "behind": 0, "commits": [], "dirty": False,
            "git": has_git(repo), "source": ""}

    if not info["git"]:
        version, why = remote_version_http(remote_slug(repo))
        info.update(remote_version=version, source="http")
        info["behind"] = 1 if is_newer(version, info["local_version"]) else 0
        if why:
            info["error"] = why
        info["update_available"] = info["behind"] > 0
        return info

    info["dirty"] = is_dirty(repo)
    code, out, err = git(["fetch", "--quiet", "origin"], repo, timeout=60)
    if code != 0:
        version, why = remote_version_http(remote_slug(repo))
        info.update(remote_version=version, source="http")
        info["behind"] = 1 if is_newer(version, info["local_version"]) else 0
        info["error"] = f"git fetch failed ({err or code}); fell back to HTTP"
        info["update_available"] = info["behind"] > 0
        return info

    upstream = "@{u}"
    code, out, _ = git(["rev-list", "--count", f"HEAD..{upstream}"], repo, timeout=20)
    if code != 0:
        code, out, _ = git(["rev-list", "--count", "HEAD..origin/main"], repo, timeout=20)
        upstream = "origin/main"
    if code == 0 and out.isdigit():
        info["behind"] = int(out)
    code, out, _ = git(["log", "--oneline", "--no-decorate", "-10",
                        f"HEAD..{upstream}"], repo, timeout=20)
    if code == 0 and out:
        info["commits"] = out.splitlines()
    code, out, _ = git(["show", f"{upstream}:SKILL.md"], repo, timeout=20)
    if code == 0 and out:
        info["remote_version"] = read_version_text(out)
    info["source"] = "git"
    info["update_available"] = info["behind"] > 0
    return info


def smoke_test(repo, url, timeout=15):
    """Verify the RUNNING service matches the code we just deployed.

    A green test suite only proves the files on disk are sound - it does not
    prove the service was restarted, or that its .env has the right keys. This
    check catches exactly the '1.6.3 on disk, 1.6.2 in production' drift.
    """
    try:
        sys.path.insert(0, str(Path(repo) / "scripts"))
        import health_check  # noqa: PLC0415
        import judge  # noqa: PLC0415
        from env_loader import load_env_file  # noqa: PLC0415
        load_env_file()
    except ImportError as e:
        return False, [f"cannot import health check helpers: {e}"]

    expect = {"version": read_version(Path(repo) / "SKILL.md")}
    try:
        expect["providers"] = len(judge.configured_providers())
    except Exception:  # noqa: BLE001
        pass

    health, err = health_check.fetch(url, timeout)
    if health is None:
        return False, [f"unreachable: {err}"]
    ok, problems, _ = health_check.evaluate(health, expect, env={})
    problems.extend("server-side: " + d for d in (health.get("drift") or []))
    return ok and not (health.get("drift")), problems


def apply_update(repo, zip_to=None, run_tests=True, timeout=600,
                 smoke_url=None, smoke_timeout=15):
    """Fast-forward pull, verify, smoke-test, roll back on any failure.

    Returns (code, notes). The rollback covers smoke failures too, so a bad
    release never stays on disk.
    """
    notes = []
    if not has_git(repo):
        return EXIT_ERROR, ["not a git checkout - update the zip manually"]
    if is_dirty(repo):
        return EXIT_DIRTY, ["working tree has uncommitted changes; commit or stash first "
                            "(nothing was modified)"]

    code, before, _ = git(["rev-parse", "HEAD"], repo, timeout=20)
    if code != 0:
        return EXIT_ERROR, ["cannot resolve HEAD"]
    notes.append(f"before: {before[:7]}")

    code, out, err = git(["pull", "--ff-only", "origin"], repo, timeout=180)
    if code != 0:
        return EXIT_ERROR, notes + [f"pull failed: {err or out}"]
    notes.append("pulled: " + (out.splitlines()[-1] if out else "ok"))

    def rollback(why):
        git(["reset", "--hard", before], repo, timeout=60)
        notes.append(f"ROLLED BACK to {before[:7]} - {why}, nothing kept")

    if run_tests:
        steps = [[sys.executable, "-m", "compileall", "-q", "scripts", "tests"],
                 [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]]
        for step in steps:
            code, out, err = _run(step, repo, timeout=timeout)
            tail = (out or err or "").strip().splitlines()[-1:] or [""]
            notes.append(f"{' '.join(step[2:4])}: exit {code} {tail[-1]}")
            if code != 0:
                rollback("tests failed")
                return EXIT_TEST_FAILED, notes

    if smoke_url:
        ok, problems = smoke_test(repo, smoke_url, smoke_timeout)
        notes.append(f"smoke {smoke_url}: {'OK' if ok else 'FAILED'}")
        for prob in problems:
            notes.append(f"  - {prob}")
        if not ok:
            rollback("deploy smoke test failed")
            return EXIT_SMOKE_FAILED, notes

    if zip_to:
        code, out, err = _run([sys.executable, "scripts/package.py", "--output", str(zip_to)],
                              repo, timeout=180)
        notes.append(("zip: " + (out.splitlines()[-1] if out else err)) if code == 0
                     else f"zip failed: {err or out}")
        if code != 0:
            return EXIT_ERROR, notes
    notes.append(f"now: {read_version(repo / 'SKILL.md')} at "
                 f"{git(['rev-parse', '--short', 'HEAD'], repo, timeout=20)[1]}")
    return EXIT_CURRENT, notes


def build_parser():
    p = argparse.ArgumentParser(description="Update the ai-router skill from its git remote.")
    p.add_argument("--check", action="store_true", help="only report (exit 1 = update available)")
    p.add_argument("--apply", action="store_true", help="pull, test, roll back on failure")
    p.add_argument("--repo", default=str(ROOT), help="repo root (default: the skill folder)")
    p.add_argument("--zip", metavar="DIR", help="rebuild the distributable zip into DIR after updating")
    p.add_argument("--no-test", action="store_true", help="skip the post-pull test gate")
    p.add_argument("--smoke-url", default=os.environ.get("AI_ROUTER_HEALTH_URL"),
                   metavar="URL", help="after updating, verify the running service's "
                   "/health (env: AI_ROUTER_HEALTH_URL); roll back on drift")
    p.add_argument("--smoke-timeout", type=float, default=15)
    p.add_argument("--json", action="store_true", help="structured output on stdout")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()

    if args.apply:
        code, notes = apply_update(repo, args.zip, run_tests=not args.no_test,
                                  smoke_url=args.smoke_url,
                                  smoke_timeout=args.smoke_timeout)
        if args.json:
            print(json.dumps({"ok": code == EXIT_CURRENT, "exit": code, "notes": notes},
                             ensure_ascii=False, indent=2))
        else:
            for n in notes:
                print(f"[update] {n}")
        return code

    info = status(repo)
    info["update_available"] = info["behind"] > 0
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(f"[update] local  : {info['local_version'] or '?'}")
        print(f"[update] remote : {info['remote_version'] or '?'} ({info['source'] or 'unknown'})")
        if info.get("error"):
            print(f"[update] note   : {info['error']}")
        if info["dirty"]:
            print("[update] warning: working tree has uncommitted changes")
        if info["update_available"]:
            print(f"[update] {info['behind']} commit(s) behind - run with --apply to update")
            for c in info["commits"]:
                print(f"           {c}")
        else:
            print("[update] already up to date")
    return EXIT_BEHIND if info["update_available"] else EXIT_CURRENT


if __name__ == "__main__":
    sys.exit(main())
