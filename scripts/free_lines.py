#!/usr/bin/env python3
"""Governance for free-tier lines (stdlib only).

Three things the 2026-11-09 all-model review asked for, in one place:

1. Soft-failure cooldown. Vendor rate limits (Zhipu 1302/1305, OpenRouter 429)
   are account-level: hammering them with exponential backoff only wastes
   concurrency slots. Mark the line as cooling for a window and let the router
   demote it instead.
2. OpenRouter dynamic free-model discovery. `:free` SKUs are delisted without
   notice, so a hardcoded ID silently rots. Discover at runtime, keep a
   last-good snapshot, and fall back to a seed list.
3. Reasoning-model metadata. Some lines only emit `reasoning_content`; a
   too-small budget yields an empty body, which must never count as success.

No hard dependency between this module and call_model/judge: everything is
injectable and testable in isolation.
"""
import json
import os
import random
import re
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- soft failure cooldown -------------------------------------------------
SOFT_LIMIT_CODES = {"1302", "1305"}          # Zhipu: account rate / model busy
SOFT_LIMIT_STATUSES = {429, 529}             # OpenRouter and generic overload
COOLDOWN_BASE = 60.0                         # seconds
COOLDOWN_MAX = 300.0
JITTER = 0.25

_until = {}                                  # provider -> monotonic-ish epoch
_lock = threading.Lock()


def is_soft_limit(status, detail):
    """True for vendor rate limiting that a cooldown (not a retry) should absorb."""
    if status in SOFT_LIMIT_STATUSES:
        return True
    text = str(detail or "")
    explicit_code = re.search(r'"code"\s*:\s*"(?:1302|1305)"', text)
    rate_word = ("速率" in text or "rate" in text.lower()
                 or "访问量" in text or "limit" in text.lower())
    return bool(explicit_code or (re.search(r"\b(?:1302|1305)\b", text) and rate_word))


def penalize(provider, status=None, detail="", now=None):
    """Record a soft failure. Returns the cooldown seconds (0 if not a soft limit)."""
    if not is_soft_limit(status, detail):
        return 0
    now = time.time() if now is None else now
    with _lock:
        prior = _until.get(provider, 0)
        # Escalate only when it keeps failing inside the window; cap it.
        base = COOLDOWN_BASE * (2 if prior > now else 1)
        seconds = min(base, COOLDOWN_MAX) * (1 + random.uniform(-JITTER, JITTER))
        _until[provider] = max(prior, now) + seconds
    return round(seconds, 1)


def cooldown_remaining(provider, now=None):
    now = time.time() if now is None else now
    with _lock:
        return max(0.0, _until.get(provider, 0) - now)


def is_cooling(provider, now=None):
    return cooldown_remaining(provider, now) > 0


def clear(provider=None):
    """Reset cooldown state (used by tests and by `--reset-cooldown`)."""
    with _lock:
        if provider is None:
            _until.clear()
        else:
            _until.pop(provider, None)


def demote(ranked):
    """Move cooling providers to the end, preserving relative order.

    Deliberately does NOT remove them: if every line is cooling we would rather
    try one than fail with 'no provider available'.
    """
    good = [p for p in ranked if not is_cooling(p)]
    cooling = [p for p in ranked if is_cooling(p)]
    return good + cooling


# --- OpenRouter free-model discovery --------------------------------------
MODELS_URL = "https://openrouter.ai/api/v1/models"
SEED_FREE_MODELS = ["recursive/flint-v1.0:free", "cohere/north-mini-code:free"]
CACHE_TTL = 24 * 3600
DEFAULT_CACHE = ROOT / "free_tier_cache.json"


def cache_path():
    return Path(os.environ.get("AI_ROUTER_FREE_CACHE", str(DEFAULT_CACHE)))


def discover_free_models(api_key, timeout=10):
    """Return currently listed free model IDs, or [] on any failure.

    A model counts as free when its id ends with ':free' AND its prompt price
    is literally 0. Both signals together avoid paid models rebranded as free.
    """
    if not api_key:
        return []
    req = urllib.request.Request(
        MODELS_URL, headers={"Authorization": f"Bearer {api_key}",
                             "User-Agent": "ai-router-free-discovery"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - discovery must never break a call
        return []
    out = []
    for m in (data.get("data") or []):
        mid = m.get("id") or ""
        if not mid.endswith(":free"):
            continue
        pricing = m.get("pricing") or {}
        try:
            if float(pricing.get("prompt", 1)) != 0:
                continue
        except (TypeError, ValueError):
            continue
        out.append(mid)
    return out


def load_last_good(path=None):
    try:
        d = json.loads((path or cache_path()).read_text(encoding="utf-8"))
        models = d.get("free_models") or []
        return [m for m in models if isinstance(m, str) and m.endswith(":free")]
    except (OSError, ValueError, TypeError):
        return []


def save_last_good(models, path=None):
    if not models:
        return False
    p = path or cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"updated": int(time.time()),
                                 "free_models": models[:50]},
                                ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def resolve_openrouter_model(api_key, force=False, path=None, timeout=10):
    """Pick a usable free model: fresh cache -> discovery -> last-good -> seed.

    Returns (model_id, source). `source` is one of cache/discovery/last_good/seed.
    """
    p = path or cache_path()
    try:
        age = time.time() - p.stat().st_mtime
    except OSError:
        age = float("inf")
    if not force and age < CACHE_TTL:
        cached = load_last_good(p)
        if cached:
            return cached[0], "cache"

    found = discover_free_models(api_key, timeout)
    if found:
        # Prefer a seed we know returns real text, then any discovered free SKU.
        ordered = [m for m in SEED_FREE_MODELS if m in found] + \
                  [m for m in found if m not in SEED_FREE_MODELS]
        save_last_good(ordered, p)
        return ordered[0], "discovery"

    cached = load_last_good(p)
    if cached:
        return cached[0], "last_good"
    return SEED_FREE_MODELS[0], "seed"


# --- reasoning model metadata ---------------------------------------------
REASONING_ONLY_HINT = "reasoning_content"


def extract_reasoning(resp, mode):
    """Return hidden thinking text if present, else ''. Never used as an answer."""
    if mode == "anthropic":
        return "".join(p.get("thinking", "") for p in (resp.get("content") or [])
                       if p.get("type") == "thinking")
    try:
        msg = resp["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""
    for key in ("reasoning_content", "reasoning", "thinking"):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Inspect free-tier governance state.")
    ap.add_argument("--discover", action="store_true",
                    help="refresh OpenRouter free-model list")
    ap.add_argument("--reset-cooldown", action="store_true")
    args = ap.parse_args(argv)
    if args.reset_cooldown:
        clear()
        print("[free_lines] cooldown state cleared")
    if args.discover:
        # CLI runs outside server.py, so .env is not loaded automatically.
        try:
            from env_loader import load_env_file
            load_env_file()
        except ImportError:  # pragma: no cover
            pass
        key = os.environ.get("OPENROUTER_API_KEY", "")
        model, source = resolve_openrouter_model(key, force=True)
        print(f"[free_lines] OpenRouter free model: {model} (source={source})")
    if not (args.reset_cooldown or args.discover):
        print("[free_lines] cooling:",
              {p: round(cooldown_remaining(p), 1)
               for p in sorted(_until) if is_cooling(p)} or "none")
        print("[free_lines] last-good:", load_last_good()[:5] or "none")


if __name__ == "__main__":
    main()
