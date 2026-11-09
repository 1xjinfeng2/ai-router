#!/usr/bin/env python3
"""Shared deployment-drift checks for ai-router (stdlib only).

Both the HTTP service (/health) and deploy scripts import this module, so the
server and the verifier can never disagree about what "healthy" means.

The core idea: a config hash over (version + sorted provider names) makes a
deployment fingerprint. If the running service reports a different hash from
the one the deployer expects, the release did not actually land - which is
exactly what happened when production stayed on 1.6.2 / 12 providers while
the local tree was 1.6.3 / 16.

Usage (CLI):
    python scripts/health_check.py --url http://127.0.0.1/health \
        --expect-version 1.6.3 --expect-providers 16
    echo $?   # 0 = healthy, 1 = drift/failure, 2 = unreachable
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_UNREACHABLE = 2

ENV_EXPECT_VERSION = "AI_ROUTER_EXPECT_VERSION"
ENV_EXPECT_PROVIDERS = "AI_ROUTER_EXPECT_PROVIDERS"
ENV_EXPECT_HASH = "AI_ROUTER_EXPECT_CONFIG_HASH"


def config_hash(version, providers):
    """Stable 12-char fingerprint of a deployment's version + provider set."""
    blob = json.dumps([str(version), sorted(set(providers))],
                      ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def expectations(env=None):
    """Read deployment expectations from the environment.

    Returns a dict with only the keys that are actually set, so a service with
    no expectations configured simply reports config_ok=True.
    """
    e = os.environ if env is None else env
    exp = {}
    v = (e.get(ENV_EXPECT_VERSION) or "").strip()
    if v:
        exp["version"] = v
    p = (e.get(ENV_EXPECT_PROVIDERS) or "").strip()
    if p.isdigit():
        exp["providers"] = int(p)
    h = (e.get(ENV_EXPECT_HASH) or "").strip()
    if h:
        exp["config_hash"] = h
    return exp


def evaluate(health, expect=None, env=None):
    """Compare a /health payload against expectations.

    Returns (ok, problems, computed_hash). `problems` is a list of short
    human-readable strings; empty means healthy.
    """
    exp = dict(expect or {})
    exp.update(expectations(env))
    problems = []

    if health.get("ok") is not True:
        problems.append("service reports ok=false")

    computed = config_hash(health.get("version", ""),
                           health.get("providers") or [])
    # Fall back to the count when the service does not expose the full list.
    if "providers" not in health and "providers_configured" in health:
        computed = config_hash(
            health.get("version", ""),
            ["#%d" % i for i in range(int(health["providers_configured"]))])

    if "version" in exp and health.get("version") != exp["version"]:
        problems.append("version %r != expected %r" %
                        (health.get("version"), exp["version"]))
    if "providers" in exp:
        actual = health.get("providers_configured")
        if actual is None:
            actual = len(health.get("providers") or [])
        if actual != exp["providers"]:
            problems.append("providers %r != expected %r" %
                            (actual, exp["providers"]))
    if "config_hash" in exp:
        reported = health.get("config_hash") or computed
        if reported != exp["config_hash"]:
            problems.append("config_hash %r != expected %r" %
                            (reported, exp["config_hash"]))
    return (not problems), problems, computed


def fetch(url, timeout=10):
    """Return (health_dict_or_None, error_string)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ai-router-health-check"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), ""
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"


def main(argv=None):
    p = argparse.ArgumentParser(description="Verify a deployed ai-router /health endpoint.")
    p.add_argument("--url", default=os.environ.get("AI_ROUTER_HEALTH_URL",
                                                   "http://127.0.0.1/health"))
    p.add_argument("--expect-version", default=None)
    p.add_argument("--expect-providers", type=int, default=None)
    p.add_argument("--expect-hash", default=None)
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    exp = {}
    if args.expect_version:
        exp["version"] = args.expect_version
    if args.expect_providers is not None:
        exp["providers"] = args.expect_providers
    if args.expect_hash:
        exp["config_hash"] = args.expect_hash

    health, err = fetch(args.url, args.timeout)
    if health is None:
        msg = f"unreachable: {err}"
        print(json.dumps({"ok": False, "error": msg}) if args.json else "[health] " + msg)
        return EXIT_UNREACHABLE

    ok, problems, computed = evaluate(health, exp)
    result = {
        "ok": ok, "url": args.url,
        "version": health.get("version"),
        "providers_configured": health.get("providers_configured"),
        "config_hash": health.get("config_hash") or computed,
        "problems": problems,
        "drift": health.get("drift", []),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[health] {args.url}  version={result['version']}  "
              f"providers={result['providers_configured']}  hash={result['config_hash']}")
        for prob in problems:
            print(f"[health] DRIFT: {prob}")
        for prob in result["drift"]:
            print(f"[health] server-side drift: {prob}")
        print("[health] OK" if ok and not result["drift"] else "[health] FAILED")
    return EXIT_OK if (ok and not result["drift"]) else EXIT_DRIFT


if __name__ == "__main__":
    sys.exit(main())
