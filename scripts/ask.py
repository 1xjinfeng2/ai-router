#!/usr/bin/env python3
"""
ask.py - One-shot auto-router: classify the question, pick the best model, answer.

This is the "just answer it with the strongest AI for this kind of question"
entry point. No provider flag needed:

    python ask.py --prompt "为什么这段 SQL 会锁表"
    python ask.py --prompt "把这段中文润色成小红书文案"
    python ask.py --prompt "..." --dry-run      # only show the routing decision
    python ask.py --prompt "..." --use-llm      # semantic judge instead of keywords

Design notes
  * Classification is keyword-based by default, so it is free and instant.
  * The chosen provider comes from judge.ROUTING filtered by configured keys,
    then re-ordered by recorded health (usage.db) so a flaky provider is demoted.
  * If the top provider fails, call_model's FALLBACK_CHAINS takes over, so the
    answer still arrives — with a note on stderr about who actually answered.
  * stdout carries only the answer (pipe-friendly). Routing, cost and warnings
    go to stderr. Use --json for a structured record on stdout.

No third-party dependencies: standard library only.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from env_loader import load_env_file  # noqa: E402

load_env_file()

import call_model  # noqa: E402
import judge  # noqa: E402

# Last-resort order when a task type has no configured candidate.
GENERIC_ORDER = [
    "doubao", "th-deepseek", "th-flash", "th-code", "th-kimi", "th-glm",
    "ark-deepseek", "ark-kimi", "ark-glm", "deepseek", "hunyuan", "kimi",
    "qwen", "glm", "glm-flash", "siliconflow", "groq", "openrouter", "mistral",
    "nvidia", "gemini", "openai", "claude", "ernie", "th-minimax", "th-vision",
]


def route(prompt, use_llm=False, judge_provider=None, task_override=None, budget=False):
    """Return (task, reason, ranked_providers) for a prompt."""
    configured = judge.configured_providers()
    if task_override:
        task, reason = task_override, f"forced by {task_override} input"
    elif use_llm:
        jp = judge_provider or judge.pick_judge_provider(configured)
        task, reason = judge.llm_classify(prompt, jp)
    else:
        task, reason = judge.rule_classify(prompt)

    ranked = [p for p in judge.ROUTING.get(task, []) if p in configured]
    if not ranked:
        # Either the task has no built-in model (search) or its preferred
        # providers have no key. Fall back to any configured provider.
        ranked = [p for p in GENERIC_ORDER if p in configured]
        reason += "; fell back to generic order"
    if budget:
        # Keep the task-appropriate free line when one exists, otherwise take
        # whatever free line is configured.
        free = [p for p in ranked if p in judge.FREE_PROVIDERS] \
            or [p for p in judge.FREE_PROVIDERS if p in configured]
        ranked = free
        reason += "; free tier only"
    ranked = judge.health_adjusted(ranked)
    return task, reason, ranked


def _record(provider, model, used, latency_ms):
    """Best-effort metering so `usage.py --report` stays complete."""
    try:
        import usage
        u = used.get("usage") or {}
        usage.record(
            provider, model,
            u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
            u.get("total_tokens", 0),
            latency_ms=latency_ms,
            fallback_from=used.get("fallback_from"),
        )
    except Exception:  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser(
        description="Classify a question, pick the best configured model, answer it.")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--system", default=None)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--model", default=None, help="override the provider's default model")
    ap.add_argument("--use-llm", action="store_true", help="semantic classification")
    ap.add_argument("--judge-provider", default=None)
    ap.add_argument("--no-fallback", action="store_true")
    ap.add_argument("--budget", action="store_true",
                    help="only consider providers covered by a permanent free tier")
    ap.add_argument("--image", action="append", default=None, metavar="PATH",
                    help="attach a local image (repeatable); forces the vision route")
    ap.add_argument("--dry-run", action="store_true", help="print routing only, call nothing")
    ap.add_argument("--json", action="store_true", help="structured output on stdout")
    args = ap.parse_args()

    override = "vision" if args.image else None
    task, reason, ranked = route(args.prompt, args.use_llm, args.judge_provider,
                                 override, args.budget)

    if not ranked:
        if args.budget:
            sys.stderr.write(
                "[ask] no free-tier provider configured. Add one of ZHIPU_API_KEY, "
                "SILICONFLOW_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, "
                "MISTRAL_API_KEY, NVIDIA_API_KEY (see references/free-tier.md).\n")
            sys.exit(2)
        sys.stderr.write(
            "[ask] no provider has an API key configured. "
            "Fill .env (see .env.example) or run check_provider.py --scan.\n")
        sys.exit(2)

    provider = ranked[0]
    model = args.model or judge.default_model(provider)

    if args.dry_run:
        payload = {
            "task_type": task, "reason": reason, "provider": provider,
            "model": model, "ranked_providers": ranked,
            "configured_providers": sorted(judge.configured_providers()),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if task == "search":
        sys.stderr.write(
            "[ask] this looks like a time-sensitive question; the model has no live "
            "web access — search first, then pass the findings in --prompt.\n")

    sys.stderr.write(f"[ask] task={task} ({reason}) -> {provider} / {model}\n")

    started = time.time()
    res = call_model.call_text(
        provider, args.prompt, system=args.system, temperature=args.temperature,
        max_tokens=args.max_tokens, timeout=args.timeout, model=args.model,
        allow_fallback=not args.no_fallback, images=args.image,
    )
    latency_ms = int((time.time() - started) * 1000)

    if res["error"]:
        status = res["status"]
        sys.stderr.write(f"[ask] {'HTTP ' + str(status) if status else 'ERROR'} "
                         f"from {provider}: {res['error']}\n")
        if res.get("attempted"):
            sys.stderr.write(f"[ask] tried: {res['attempted']}\n")
        _record(provider, model, res, latency_ms)
        sys.exit(3 if status else 4)

    used_p = res["provider"]
    used_m = res["model"]
    if res.get("fallback_from"):
        sys.stderr.write(f"[ask] {provider} failed; answered by fallback {used_p}\n")
        sys.stderr.write(f"[ask] answered by {used_p} / {used_m} "
                         f"(fallback from {provider})\n")
    else:
        sys.stderr.write(f"[ask] answered by {used_p} / {used_m}\n")

    _record(used_p, used_m, res, latency_ms)

    if args.json:
        u = res.get("usage") or {}
        print(json.dumps({
            "task_type": task, "reason": reason,
            "provider": used_p, "model": used_m,
            "requested_provider": provider,
            "fallback_from": res.get("fallback_from"),
            "text": res["text"],
            "usage": u,
            "latency_ms": latency_ms,
        }, ensure_ascii=False, indent=2))
    else:
        print(res["text"])


if __name__ == "__main__":
    main()
