#!/usr/bin/env python3
"""
usage.py - Local usage metering + response cache for ai-router.

Zero third-party dependencies (sqlite3 + stdlib only). Two responsibilities:

  1) METering: record every LLM call's token usage and an *estimated* CNY cost
     per provider/model, so you can see how much your multi-provider setup
     actually spends.
  2) CACHING: identical requests (same provider/model/system/prompt/max_tokens)
     are served from a local cache, saving quota and latency.

All data lives in usage.db at the skill root. It is git-ignored and never
packaged, so it never leaves your machine.

The PRICES table below is APPROXIMATE (CNY per 1M tokens, in/out) as of
2026-11-05 and is meant to be edited by the user. Unknown models -> cost shows
as "n/a". Token counts come straight from the provider's response, so those are
exact; only the money figure is an estimate.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Skill root by default. Override when the skill directory must stay read-only
# (a packaged install, a systemd unit with ProtectSystem=strict) or when tests
# want to keep the real accounting clean.
DB_PATH = Path(os.environ.get("AI_ROUTER_USAGE_DB") or (HERE.parent / "usage.db"))

# (input_price, output_price) in CNY per 1,000,000 tokens. Approximate; edit me.
# Keyed by exact model id first, then by provider alias as a fallback.
PRICES = {
    # Doubao (Volcengine Ark)
    "doubao-seed-evolving": (0.8, 2.0),
    # DeepSeek (official)
    "deepseek-flash": (0.0, 0.0),          # flash tier is free in the trial plan
    "deepseek-v4-pro": (4.0, 12.0),
    "deepseek-v4-pro-202606": (4.0, 12.0),
    "deepseek-v4-1-flash-261017": (0.5, 1.2),
    # Kimi (Moonshot)
    "kimi-k3.1-pro": (8.0, 24.0),
    "kimi-k3-1-pro-261024": (8.0, 24.0),
    "kimi-k2.7-code": (3.0, 9.0),
    # GLM (Zhipu)
    "glm-5": (4.0, 12.0),
    "glm-5.3-flash": (0.5, 1.5),
    "glm-5-3-flash-260828": (0.5, 1.5),
    # Hunyuan / TokenHub (Tencent)
    "hy4-preview": (3.0, 9.0),
    "deepseek-v4.1-flash": (0.5, 1.2),     # TokenHub alias th-flash
    "minimax-m3.1-flash": (1.0, 3.0),
    # Gemini / GPT / Claude / Qwen / ERNIE (reference list prices)
    "gemini-2.5-flash": (0.3, 1.0),
    "gpt-5.1": (10.0, 30.0),
    "claude-sonnet-5-5": (6.0, 18.0),
    "qwen3.7-plus": (2.0, 6.0),
    "ernie-4.0-8k": (4.0, 12.0),
}


def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS calls(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cost REAL,
            cache_hit INTEGER NOT NULL DEFAULT 0,
            session_id TEXT,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            fallback_from TEXT
        )"""
    )
    # Migration for databases created before the reliability columns existed.
    have = {r[1] for r in conn.execute("PRAGMA table_info(calls)").fetchall()}
    for col, ddl in (("latency_ms", "INTEGER NOT NULL DEFAULT 0"),
                     ("error", "TEXT"),
                     ("fallback_from", "TEXT")):
        if col not in have:
            conn.execute(f"ALTER TABLE calls ADD COLUMN {col} {ddl}")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cache(
            key TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            text TEXT NOT NULL,
            ts TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def estimate_cost(provider, model, prompt_tokens, completion_tokens):
    """Return estimated CNY cost, or None if the model is unknown."""
    price = PRICES.get(model) or PRICES.get(provider)
    if not price:
        return None
    inn, out = price
    return (prompt_tokens / 1_000_000) * inn + (completion_tokens / 1_000_000) * out


def record(provider, model, prompt_tokens=0, completion_tokens=0,
           total_tokens=0, cache_hit=0, session_id=None,
           latency_ms=0, error=None, fallback_from=None):
    """Insert one call record (or a cache-hit marker when cache_hit=1).

    latency_ms / error / fallback_from are filled by the reliability layer so the
    report can show how often a provider failed or a backup had to answer.
    """
    cost = estimate_cost(provider, model, prompt_tokens, completion_tokens)
    ts = datetime.now(timezone.utc).isoformat()
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO calls(ts,provider,model,prompt_tokens,completion_tokens,"
            "total_tokens,cost,cache_hit,session_id,latency_ms,error,fallback_from) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, provider, model, prompt_tokens, completion_tokens,
             total_tokens, cost, cache_hit, session_id, latency_ms or 0,
             error, fallback_from),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001
        # Metering must never break the actual task.
        sys.stderr.write(f"[usage] record failed (ignored): {e}\n")


def cache_key(provider, model, system, prompt, max_tokens):
    blob = json.dumps([provider, model, system, prompt, max_tokens],
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_get(provider, model, system, prompt, max_tokens):
    """Return (key, cached_text_or_None)."""
    k = cache_key(provider, model, system, prompt, max_tokens)
    try:
        conn = _conn()
        row = conn.execute("SELECT text FROM cache WHERE key=?", (k,)).fetchone()
        conn.close()
        return k, (row[0] if row else None)
    except Exception:  # noqa: BLE001
        return k, None


def cache_put(provider, model, system, prompt, max_tokens, text):
    k = cache_key(provider, model, system, prompt, max_tokens)
    ts = datetime.now(timezone.utc).isoformat()
    try:
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO cache(key,provider,model,text,ts) VALUES(?,?,?,?,?)",
            (k, provider, model, text, ts),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[usage] cache_put failed (ignored): {e}\n")


def report(since=None, provider=None):
    """Aggregate rows: (provider, model, calls, ptok, ctok, ttok, cost, hits)."""
    conn = _conn()
    q = ("SELECT provider, model, COUNT(*), SUM(prompt_tokens), "
         "SUM(completion_tokens), SUM(total_tokens), SUM(cost), SUM(cache_hit) "
         "FROM calls")
    clauses, params = [], []
    if since:
        clauses.append("ts >= ?")
        params.append(since)
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " GROUP BY provider, model ORDER BY SUM(total_tokens) DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows


def total_line(since=None, provider=None):
    conn = _conn()
    q = ("SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
         "COALESCE(SUM(total_tokens),0), COALESCE(SUM(cost),0), COALESCE(SUM(cache_hit),0) FROM calls")
    clauses, params = [], []
    if since:
        clauses.append("ts >= ?")
        params.append(since)
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    row = conn.execute(q, params).fetchone()
    conn.close()
    return row  # (calls, ptok, ctok, ttok, cost, hits)


def clear():
    conn = _conn()
    conn.execute("DELETE FROM calls")
    conn.execute("DELETE FROM cache")
    conn.commit()
    conn.close()


def expire(days):
    """Delete call records and cache entries older than `days`. 0 disables.

    Keeps usage.db from growing without bound (and shortens the window in which
    local telemetry can be read if the machine is compromised).
    """
    if not days:
        return 0, 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
    conn = _conn()
    try:
        n_calls = conn.execute("DELETE FROM calls WHERE ts < ?", (cutoff,)).rowcount
        n_cache = conn.execute("DELETE FROM cache WHERE ts < ?", (cutoff,)).rowcount
        conn.commit()
    finally:
        conn.close()
    return n_calls, n_cache


def health(since=None, limit=5):
    """Per-provider success rate / latency / fallback counts for routing health."""
    conn = _conn()
    q = ("SELECT provider, COUNT(*) AS calls, "
         "SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END) AS ok, "
         "COALESCE(AVG(CASE WHEN latency_ms > 0 THEN latency_ms END), 0) AS avg_ms, "
         "SUM(CASE WHEN fallback_from IS NOT NULL THEN 1 ELSE 0 END) AS fallbacks "
         "FROM calls")
    params = []
    if since:
        q += " WHERE ts >= ?"
        params.append(since)
    q += " GROUP BY provider ORDER BY calls DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows[:limit]


def _fmt_cost(c):
    return "n/a" if c is None else f"¥{c:.4f}"


def main():
    p = argparse.ArgumentParser(description="ai-router usage metering & cache.")
    p.add_argument("--report", action="store_true", help="print usage report")
    p.add_argument("--export", metavar="PATH", help="export report as CSV")
    p.add_argument("--clear", action="store_true", help="delete all usage + cache data")
    p.add_argument("--expire", type=int, metavar="DAYS",
                   help="delete calls/cache older than DAYS (0 disables)")
    p.add_argument("--health", action="store_true",
                   help="per-provider success rate, latency and fallback counts")
    p.add_argument("--since", help="filter rows since ISO timestamp (e.g. 2026-11-05)")
    p.add_argument("--provider", help="filter by provider")
    args = p.parse_args()

    if args.clear:
        clear()
        print("usage.db cleared (calls + cache).")
        return

    if args.expire is not None:
        nc, ncache = expire(args.expire)
        print(f"expired {nc} call record(s) and {ncache} cache entr(ies) "
              f"older than {args.expire} day(s)")
        return

    if args.health:
        print("=== provider health ===")
        print(f"{'provider':<14}{'calls':>7}{'ok':>6}{'fail':>6}{'rate':>8}"
              f"{'avg_ms':>9}{'fallbacks':>11}")
        print("-" * 62)
        for prov, calls, ok, avg_ms, fb in health(args.since):
            ok = ok or 0
            rate = (ok / calls * 100) if calls else 0
            print(f"{prov:<14}{calls:>7}{ok:>6}{calls - ok:>6}{rate:>7.0f}%"
                  f"{avg_ms:>9.0f}{fb:>11}")
        print("-" * 62)
        return

    if not (args.report or args.export):
        args.report = True  # default action

    rows = report(args.since, args.provider)
    tot = total_line(args.since, args.provider)

    print("=== ai-router usage report ===")
    print(f"{'provider':<14}{'model':<26}{'calls':>6}{'in':>9}{'out':>9}"
          f"{'total':>9}{'cost':>12}{'hit':>5}")
    print("-" * 90)
    for prov, model, calls, ptok, ctok, ttok, cost, hits in rows:
        print(f"{prov:<14}{model:<26}{calls:>6}{ptok:>9}{ctok:>9}{ttok:>9}"
              f"{_fmt_cost(cost):>12}{hits:>5}")
    if not rows:
        print("(no records yet — run a relay/call first)")
    print("-" * 90)
    if tot:
        calls, ptok, ctok, ttok, cost, hits = tot
        print(f"{'TOTAL':<40}{calls:>6}{ptok:>9}{ctok:>9}{ttok:>9}"
              f"{_fmt_cost(cost):>12}{hits:>5}")
        if hits:
            print(f"(cache hits saved an estimated {hits} API call(s))")

    if args.export:
        import csv
        with open(args.export, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["provider", "model", "calls", "prompt_tokens",
                        "completion_tokens", "total_tokens", "cost_cny", "cache_hits"])
            for r in rows:
                w.writerow(list(r))
        print(f"\nexported -> {args.export}")


if __name__ == "__main__":
    main()
