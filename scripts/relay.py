#!/usr/bin/env python3
"""
relay.py - Privacy relay: you (WorkBuddy) as the middleman for multi-AI tasks.

Flow
----
   user prompt (may contain PII)
        |
        v
   [privacy.redact]  -> store real values in local SQLite vault,
                         replace with <PII_*> placeholders
        |
        v
   fan-out to N providers (judge-routed or explicitly listed),
   each receives ONLY the sanitized prompt
        |
        v
   [organize] one model merges/dedupes/polishes the sanitized answers,
              instructed to keep every <PII_*> token verbatim
        |
        v
   [privacy.restore] swap placeholders back to real values for the owner
        |
        v
   final answer -> stdout

External AIs never see raw PII. The vault is local, git-ignored, never packaged.

Usage
-----
  # auto-route to the top-2 models for the task, then polish + restore
  python relay.py --prompt "帮我给客户张伟写封跟进邮件，电话13800138000"

  # explicit fan-out to two providers
  python relay.py --prompt "..." --providers doubao,th-flash

  # run every configured provider, skip the polish pass
  python relay.py --prompt "..." --all --no-organize

  # reuse a vault session (e.g. continue the same confidential thread)
  python relay.py --prompt "..." --session <sid>

No third-party dependencies: only the Python standard library.
"""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from call_model import PROVIDERS, call_openai, call_anthropic, extract_text, extract_usage
import privacy
import usage

try:
    import judge
except Exception:  # noqa: BLE001
    judge = None


def call_provider(provider, prompt, system, max_tokens, timeout=60,
                  use_cache=False, meter=True, session_id=None):
    """Call one provider; return (text, error, usage). Never raises.

    usage is a dict {prompt_tokens, completion_tokens, total_tokens} or None.
    If use_cache is True, identical requests are served from the local cache
    without hitting the API (and recorded as a cache hit).
    """
    if provider not in PROVIDERS:
        return None, f"unknown provider '{provider}'", None
    base_url, key_env, default_model, mode = PROVIDERS[provider]
    api_key = os.environ.get(key_env)
    if not api_key:
        return None, f"missing key {key_env}", None

    if use_cache:
        _, cached = usage.cache_get(provider, default_model, system, prompt, max_tokens)
        if cached is not None:
            if meter:
                usage.record(provider, default_model, cache_hit=1, session_id=session_id)
            sys.stderr.write(f"[relay] {provider}: CACHE HIT (no API call)\n")
            return cached.strip(), None, None

    try:
        if mode == "anthropic":
            resp = call_anthropic(base_url, api_key, default_model, prompt,
                                  system, None, max_tokens, None, timeout)
        else:
            resp = call_openai(base_url, api_key, default_model, prompt,
                               system, None, max_tokens, None, timeout)
        text = (extract_text(resp, mode) or "").strip()
        u = extract_usage(resp, mode)
        if meter:
            if u:
                usage.record(provider, default_model, u["prompt_tokens"],
                             u["completion_tokens"], u["total_tokens"],
                             session_id=session_id)
            if use_cache and text:
                usage.cache_put(provider, default_model, system, prompt, max_tokens, text)
        return text, None, u
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}", None


def resolve_providers(args):
    """Return the list of providers to fan out to."""
    if args.all:
        if judge is not None:
            return sorted(judge.configured_providers())
        return [p for p, (_, env, _, _) in PROVIDERS.items() if os.environ.get(env)]
    if args.providers and args.providers != "auto":
        return [p.strip() for p in args.providers.split(",") if p.strip()]
    # auto: judge-route, take top N for the task
    if judge is not None:
        try:
            from env_loader import load_env_file
            load_env_file()
        except Exception:  # noqa: BLE001
            pass
        configured = judge.configured_providers()
        task, _ = judge.rule_classify(args.prompt)
        ranked = [p for p in judge.ROUTING.get(task, []) if p in configured]
        if not ranked:
            ranked = list(configured)
        return ranked[: args.top]
    # no judge available: use every configured provider
    return [p for p, (_, env, _, _) in PROVIDERS.items() if os.environ.get(env)]


ORGANIZE_SYSTEM = (
    "你是结果整理官。下面是一份用户请求，以及多位 AI 对同一请求的回复（已做隐私脱敏，"
    "其中 <PII_*> 是占位符，代表真实隐私信息）。请：1) 整合各路回复、去重、补全；"
    "2) 纠正明显矛盾与事实错误；3) 给出一份结构清晰、可直接使用的最终稿。"
    "严格要求：必须原样保留所有 <PII_*> 占位符，不得改写、删除或猜测其含义；"
    "不要输出任何解释，只输出整理后的最终内容。"
)


def organize(results, organizer, session, max_tokens, timeout=90,
             use_cache=False, meter=True):
    """Merge sanitized provider outputs into one polished answer.

    Returns (text, usage). On failure returns (None, None).
    """
    parts = []
    for i, r in enumerate(results, 1):
        if r["text"] is None:
            parts.append(f"[来源{i} {r['provider']}]（调用失败：{r['error']}）")
        else:
            parts.append(f"[来源{i} {r['provider']}]\n{r['text']}")
    body = "\n\n".join(parts)
    prompt = (
        "用户请求（已脱敏）：\n" + results[0]["sanitized_prompt"] + "\n\n"
        "各路 AI 回复如下：\n" + body + "\n\n请按规则整理输出最终稿。"
    )
    text, err, u = call_provider(organizer, prompt, ORGANIZE_SYSTEM, max_tokens,
                                timeout, use_cache=use_cache, meter=meter,
                                session_id=session)
    if err:
        sys.stderr.write(f"[relay] organizer '{organizer}' failed: {err}; falling back to concat\n")
        return None, None
    # A reasoning model can return an empty body (tokens went to thinking). Treat
    # a blank answer as a failure so the caller falls back to raw concatenation.
    if not (text or "").strip():
        sys.stderr.write(f"[relay] organizer '{organizer}' returned empty body; falling back to concat\n")
        return None, None
    return text, u


def _rec(provider, model, usage_dict, cache_hit=False):
    """Build one summary record from a provider call."""
    pt = ct = tt = 0
    if usage_dict:
        pt, ct, tt = (usage_dict["prompt_tokens"], usage_dict["completion_tokens"],
                      usage_dict["total_tokens"])
    cost = usage.estimate_cost(provider, model, pt, ct) if not cache_hit else 0.0
    return {"provider": provider, "model": model, "pt": pt, "ct": ct, "tt": tt,
            "cost": cost, "hit": cache_hit}


def _usage_summary(records):
    """Render a per-run usage + cost summary block (for stderr)."""
    lines = ["[relay] === usage (this run) ==="]
    tot_pt = tot_ct = tot_tt = tot_cost = tot_hits = 0
    for r in records:
        tag = " [cache]" if r["hit"] else ""
        cost_s = "n/a" if r["cost"] is None else f"¥{r['cost']:.4f}"
        lines.append(
            f"[relay]   {r['provider']:<12}({r['model']}) "
            f"{r['pt']+r['ct']} tok, est {cost_s}{tag}"
        )
        tot_pt += r["pt"]; tot_ct += r["ct"]; tot_tt += r["tt"]
        if r["cost"] is not None:
            tot_cost += r["cost"]
        tot_hits += 1 if r["hit"] else 0
    lines.append(
        f"[relay]   TOTAL: {len(records)} calls, {tot_tt} tokens "
        f"(in {tot_pt}/out {tot_ct}), est ¥{tot_cost:.4f}"
        + (f", {tot_hits} cache hit(s)" if tot_hits else "")
    )
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Privacy relay: redact -> multi-AI -> organize -> restore.")
    ap.add_argument("--prompt", required=True, help="user prompt (may contain PII)")
    ap.add_argument("--providers", default="auto",
                    help="comma-separated providers, 'auto' (judge top-N), or use --all")
    ap.add_argument("--all", action="store_true", help="fan out to every configured provider")
    ap.add_argument("--top", type=int, default=2, help="auto mode: how many top providers to use")
    ap.add_argument("--organizer", default="th-deepseek",
                    help="provider used to merge/polish the answers (default th-deepseek, a stronger reasoning model)")
    ap.add_argument("--no-organize", action="store_true", help="skip the polish pass, just concat")
    ap.add_argument("--no-restore", action="store_true", help="keep <PII_*> placeholders in output")
    ap.add_argument("--strict", action="store_true", help="heuristic name/address redaction")
    ap.add_argument("--custom", help="extra terms to force-redact (comma-separated)")
    ap.add_argument("--session", help="reuse an existing vault session id")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--system", default=None, help="system prompt for the worker providers")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--cache", action="store_true",
                    help="cache identical requests locally to save quota")
    ap.add_argument("--no-meter", action="store_true",
                    help="do not record token usage into usage.db")
    args = ap.parse_args()

    # 1) redact into the local vault
    red = privacy.redact(args.prompt, args.session, args.custom, args.strict)
    session = red["session_id"]
    sys.stderr.write(
        f"[relay] privacy: session={session} redacted={red['count']} "
        f"breakdown={red['breakdown']}\n"
    )
    sanitized = red["text"]

    # 2) fan out
    providers = resolve_providers(args)
    sys.stderr.write(f"[relay] fan-out to: {providers}\n")
    results = []
    run_records = []  # per-call usage collected in-memory for the summary
    for p in providers:
        if p not in PROVIDERS:
            sys.stderr.write(f"[relay] skip unknown provider '{p}'\n")
            continue
        text, err, u = call_provider(
            p, sanitized, args.system, args.max_tokens, args.timeout,
            use_cache=args.cache, meter=not args.no_meter, session_id=session)
        if err:
            sys.stderr.write(f"[relay] {p}: ERROR {err}\n")
            results.append({"provider": p, "text": None, "error": err, "sanitized_prompt": sanitized})
        else:
            sys.stderr.write(f"[relay] {p}: ok ({len(text)} chars)\n")
            results.append({"provider": p, "text": text, "error": None, "sanitized_prompt": sanitized})
        if not args.no_meter:
            _, _, model, _ = PROVIDERS[p]
            run_records.append(_rec(p, model, u, cache_hit=(u is None and not err)))

    ok = [r for r in results if r["text"] is not None]
    if not ok:
        sys.stderr.write("[relay] all providers failed; nothing to organize.\n")
        sys.exit(3)

    # 3) organize
    if args.no_organize:
        merged = "\n\n".join(f"[{r['provider']}]\n{r['text']}" for r in ok)
    else:
        merged, org_u = organize(results, args.organizer, session,
                                max(args.max_tokens, 2048), args.timeout,
                                use_cache=args.cache, meter=not args.no_meter)
        if merged is None:
            merged = "\n\n".join(f"[{r['provider']}]\n{r['text']}" for r in ok)
        elif not args.no_meter:
            run_records.append(_rec(args.organizer, PROVIDERS[args.organizer][2], org_u, cache_hit=False))

    # 4) restore for the owner
    if args.no_restore:
        final = merged
    else:
        final = privacy.restore(merged, session)

    sys.stderr.write(f"[relay] done. final length={len(final)} chars.\n")

    # 5) usage summary for this run
    if not args.no_meter and run_records:
        sys.stderr.write(_usage_summary(run_records))

    print(final)


if __name__ == "__main__":
    main()
