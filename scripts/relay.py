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
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from call_model import (PROVIDERS, call_openai, call_anthropic, extract_text,
                        extract_usage, call_text, effective_max_tokens, stream_text)
import privacy
import usage

try:
    import judge
except Exception:  # noqa: BLE001
    judge = None


def call_provider(provider, prompt, system, max_tokens, timeout=60,
                  use_cache=False, meter=True, session_id=None,
                  allow_fallback=True):
    """Call one provider with retry + cross-model fallback. Never raises.

    Returns a dict: text, error, usage, provider (the one that actually
    answered), model, fallback_from, retried, latency_ms, cache_hit.
    """
    out = {"text": None, "error": None, "usage": None, "provider": provider,
           "model": PROVIDERS.get(provider, ("", "", "", ""))[2],
           "fallback_from": None, "retried": False, "latency_ms": 0,
           "cache_hit": False}
    if provider not in PROVIDERS:
        out["error"] = f"unknown provider '{provider}'"
        return out
    base_url, key_env, default_model, mode = PROVIDERS[provider]
    if not os.environ.get(key_env):
        out["error"] = f"missing key {key_env}"
        return out

    if use_cache:
        _, cached = usage.cache_get(provider, default_model, system, prompt,
                                    effective_max_tokens(provider, max_tokens))
        if cached is not None:
            if meter:
                usage.record(provider, default_model, cache_hit=1, session_id=session_id)
            sys.stderr.write(f"[relay] {provider}: CACHE HIT (no API call)\n")
            out.update({"text": cached.strip(), "cache_hit": True})
            return out

    started = time.time()
    res = call_text(provider, prompt, system, None, max_tokens, None, timeout,
                    allow_fallback=allow_fallback,
                    log=lambda m: sys.stderr.write(f"[relay]   {m}\n"))
    latency = int((time.time() - started) * 1000)
    out.update({
        "text": res["text"], "error": res["error"], "usage": res["usage"],
        "provider": res["provider"], "model": res["model"],
        "fallback_from": provider if res["provider"] != provider else None,
        "retried": res["retried"], "latency_ms": latency,
    })

    if meter:
        if res["text"]:
            u = res["usage"] or {}
            usage.record(res["provider"], res["model"],
                         u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                         u.get("total_tokens", 0), session_id=session_id,
                         latency_ms=latency, fallback_from=out["fallback_from"])
            if use_cache:
                usage.cache_put(provider, default_model, system, prompt,
                                effective_max_tokens(provider, max_tokens), res["text"])
        elif res["error"]:
            usage.record(provider, default_model, session_id=session_id,
                         latency_ms=latency, error=str(res["error"])[:300])
    return out


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
        ranked = judge.health_adjusted(ranked)
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

VOTE_SYSTEM = (
    "你是严格的评审员。用户会给出一个请求和多位 AI 的候选回复（已脱敏，<PII_*> 是占位符）。"
    "请按准确性、完整性、可执行性、表达质量给每份回复打 1-10 分，"
    "并严格只输出如下 JSON（不要代码块、不要解释）："
    '{"scores":[{"provider":"名字","score":9,"reason":"一句话理由"}],'
    '"best":"得分最高的 provider 名字"}'
)


def _parse_json_block(text):
    """Extract the first JSON object from a model answer (tolerates fences)."""
    import json as _json
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return _json.loads(t[start:end + 1])
    except Exception:  # noqa: BLE001
        return None


def vote(results, judge_provider, session, max_tokens, timeout=90,
         meter=True, use_cache=False):
    """Ask a judge model to score each candidate and pick the best one.

    Returns (decision, meta) where decision is
    {"best": provider, "text": answer, "scores": [...], "mean": float} or None.
    """
    ok = [r for r in results if r["text"] is not None]
    parts = [f"[{i}] {r['provider']}\n{r['text']}" for i, r in enumerate(ok, 1)]
    prompt = (
        "用户请求（已脱敏）：\n" + results[0]["sanitized_prompt"] + "\n\n"
        "候选回复：\n" + "\n\n".join(parts) + "\n\n请打分并选出最佳回复。"
    )
    res = call_provider(judge_provider, prompt, VOTE_SYSTEM, max_tokens, timeout,
                        use_cache=use_cache, meter=meter, session_id=session,
                        allow_fallback=True)
    if res["error"] or not res["text"]:
        return None, res
    data = _parse_json_block(res["text"])
    if not data:
        sys.stderr.write("[relay] vote: could not parse judge JSON; falling back\n")
        return None, res

    by_name = {}
    by_idx = {}
    for i, r in enumerate(ok, 1):
        by_name.setdefault(r["provider"], r)   # fallback may repeat a name
        by_idx[str(i)] = r
        by_idx[r["provider"]] = r
    scores = data.get("scores") or []
    best_name = data.get("best")
    chosen = by_name.get(best_name) or by_idx.get(str(best_name))
    if chosen is None:
        # tolerate "best": 2, or a name written with different case/spacing
        ranked = sorted(
            [s for s in scores if s.get("provider") in by_idx],
            key=lambda s: float(s.get("score") or 0), reverse=True)
        chosen = by_idx[ranked[0]["provider"]] if ranked else ok[0]
    numeric = [float(s.get("score") or 0) for s in scores if s.get("score") is not None]
    return {
        "best": chosen["provider"],
        "text": chosen["text"],
        "scores": scores,
        "mean": (sum(numeric) / len(numeric)) if numeric else None,
    }, res


def organize(results, organizer, session, max_tokens, timeout=90,
             use_cache=False, meter=True):
    """Merge sanitized provider outputs into one polished answer.

    Returns (text, meta) where meta is the call_provider dict. On failure
    returns (None, meta) so the caller can fall back to raw concatenation.
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
    res = call_provider(organizer, prompt, ORGANIZE_SYSTEM, max_tokens, timeout,
                        use_cache=use_cache, meter=meter, session_id=session,
                        allow_fallback=True)
    if res["error"]:
        sys.stderr.write(f"[relay] organizer '{organizer}' failed: {res['error']}; falling back to concat\n")
        return None, res
    # A reasoning model can return an empty body (tokens went to thinking). Treat
    # a blank answer as a failure so the caller falls back to raw concatenation.
    if not (res["text"] or "").strip():
        sys.stderr.write(f"[relay] organizer '{organizer}' returned empty body; falling back to concat\n")
        return None, res
    return res["text"], res


def _rec(provider, model, usage_dict, cache_hit=False, fallback_from=None,
         latency_ms=0):
    """Build one summary record from a provider call."""
    pt = ct = tt = 0
    if usage_dict:
        pt, ct, tt = (usage_dict["prompt_tokens"], usage_dict["completion_tokens"],
                      usage_dict["total_tokens"])
    cost = usage.estimate_cost(provider, model, pt, ct) if not cache_hit else 0.0
    return {"provider": provider, "model": model, "pt": pt, "ct": ct, "tt": tt,
            "cost": cost, "hit": cache_hit, "fallback_from": fallback_from,
            "latency_ms": latency_ms}


def _usage_summary(records):
    """Render a per-run usage + cost summary block (for stderr)."""
    lines = ["[relay] === usage (this run) ==="]
    tot_pt = tot_ct = tot_tt = tot_cost = tot_hits = 0
    for r in records:
        tag = " [cache]" if r["hit"] else ""
        if r.get("fallback_from"):
            tag += f" [fallback from {r['fallback_from']}]"
        cost_s = "n/a" if r["cost"] is None else f"¥{r['cost']:.4f}"
        ms = f" {r['latency_ms']}ms" if r.get("latency_ms") else ""
        lines.append(
            f"[relay]   {r['provider']:<12}({r['model']}) "
            f"{r['pt']+r['ct']} tok, est {cost_s}{ms}{tag}"
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
    ap.add_argument("--no-fallback", action="store_true",
                    help="do not switch to a backup provider when one fails")
    ap.add_argument("--stream", action="store_true",
                    help="stream answers live (needs --no-organize; ignores cache)")
    ap.add_argument("--vote", action="store_true",
                    help="score every answer with the judge model and return the best one")
    args = ap.parse_args()

    # Optional retention sweep so usage.db cannot grow forever.
    usage_ttl = int(os.environ.get("AI_ROUTER_USAGE_TTL_DAYS", "0") or 0)
    if usage_ttl:
        dropped_calls, dropped_cache = usage.expire(usage_ttl)
        if dropped_calls or dropped_cache:
            sys.stderr.write(
                f"[relay] swept {dropped_calls} usage row(s) / {dropped_cache} "
                f"cache entr(ies) older than {usage_ttl}d\n")

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

    # 2b) live streaming mode: only meaningful without an organizer pass, since
    # the organizer needs the complete worker text before it can merge anything.
    if args.stream:
        if not args.no_organize:
            sys.stderr.write(
                "[relay] --stream requires --no-organize (the organizer needs the "
                "full text); falling back to buffered output\n")
        else:
            if args.cache:
                sys.stderr.write("[relay] --cache is ignored while streaming\n")
            streamed = 0
            for p in providers:
                if p not in PROVIDERS:
                    sys.stderr.write(f"[relay] skip unknown provider '{p}'\n")
                    continue
                sys.stderr.write(f"[relay] streaming from {p} ...\n")
                sys.stdout.write(f"===== {p} =====\n")
                restorer = None if args.no_restore else privacy.StreamRestorer(session)
                started = time.time()
                ok_stream = True
                try:
                    for chunk in stream_text(p, sanitized, args.system, None,
                                             effective_max_tokens(p, args.max_tokens),
                                             None, args.timeout):
                        out = chunk if restorer is None else restorer.feed(chunk)
                        if out:
                            sys.stdout.write(out)
                            sys.stdout.flush()
                except Exception as e:  # noqa: BLE001
                    ok_stream = False
                    sys.stderr.write(
                        f"[relay] {p}: stream error {type(e).__name__}: {e}\n")
                if ok_stream:
                    if restorer is not None:
                        tail = restorer.flush()
                        if tail:
                            sys.stdout.write(tail)
                    streamed += 1
                sys.stdout.write("\n")
                sys.stdout.flush()
                if not args.no_meter:
                    usage.record(p, PROVIDERS[p][2], session_id=session,
                                 latency_ms=int((time.time() - started) * 1000),
                                 error=None if ok_stream else "stream error")
            sys.stderr.write(f"[relay] done. streamed {streamed}/{len(providers)} provider(s).\n")
            return

    results = []
    run_records = []  # per-call usage collected in-memory for the summary
    for p in providers:
        if p not in PROVIDERS:
            sys.stderr.write(f"[relay] skip unknown provider '{p}'\n")
            continue
        res = call_provider(
            p, sanitized, args.system, args.max_tokens, args.timeout,
            use_cache=args.cache, meter=not args.no_meter, session_id=session,
            allow_fallback=not args.no_fallback)
        if res["error"]:
            sys.stderr.write(f"[relay] {p}: ERROR {res['error']}\n")
            results.append({"provider": p, "text": None, "error": res["error"],
                            "sanitized_prompt": sanitized})
        else:
            used = res["provider"]
            note = "" if used == p else f" (fell back to {used})"
            sys.stderr.write(f"[relay] {p}: ok ({len(res['text'])} chars){note}\n")
            results.append({"provider": used, "text": res["text"], "error": None,
                            "sanitized_prompt": sanitized})
        if not args.no_meter:
            run_records.append(_rec(res["provider"], res["model"], res["usage"],
                                    cache_hit=res["cache_hit"],
                                    fallback_from=res["fallback_from"],
                                    latency_ms=res["latency_ms"]))

    ok = [r for r in results if r["text"] is not None]
    if not ok:
        sys.stderr.write("[relay] all providers failed; nothing to organize.\n")
        sys.exit(3)

    # 3) decide: optional vote -> organizer -> concat
    merged = None
    if args.vote and len(ok) >= 2:
        sys.stderr.write("[relay] vote: scoring each answer ...\n")
        decided, vote_meta = vote(results, args.organizer, session,
                                  max(args.max_tokens, 2048), args.timeout,
                                  meter=not args.no_meter, use_cache=args.cache)
        if not args.no_meter:
            run_records.append(_rec(vote_meta["provider"], vote_meta["model"],
                                    vote_meta["usage"],
                                    fallback_from=vote_meta["fallback_from"],
                                    latency_ms=vote_meta["latency_ms"]))
        if decided:
            merged = decided["text"]
            for s in decided["scores"]:
                sys.stderr.write(f"[relay]   score {s.get('provider', '?'):<13} "
                                 f"{s.get('score', '?')}/10  {s.get('reason', '')}\n")
            mean = decided["mean"]
            sys.stderr.write(
                f"[relay]   winner: {decided['best']}"
                + (f" (mean {mean:.1f})" if mean is not None else "") + "\n")

    if merged is None and args.no_organize:
        merged = "\n\n".join(f"[{r['provider']}]\n{r['text']}" for r in ok)
    elif merged is None:
        merged, org = organize(results, args.organizer, session,
                               max(args.max_tokens, 2048), args.timeout,
                               use_cache=args.cache, meter=not args.no_meter)
        if merged is None:
            merged = "\n\n".join(f"[{r['provider']}]\n{r['text']}" for r in ok)
        elif not args.no_meter:
            run_records.append(_rec(org["provider"], org["model"], org["usage"],
                                    fallback_from=org["fallback_from"],
                                    latency_ms=org["latency_ms"]))

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
