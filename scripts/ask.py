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
import urllib.error
import urllib.request

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


class AskError(RuntimeError):
    """A routing or provider failure, carrying the exit code the CLI should use."""

    def __init__(self, message, code=4, status=None):
        super().__init__(message)
        self.code = code
        self.status = status


class RemoteError(AskError):
    """The deployed ai-router server could not answer."""


def _post_json(url, payload, token=None, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise RemoteError(f"server returned HTTP {e.code}: {body}",
                          code=3, status=e.code)
    except (urllib.error.URLError, OSError) as e:
        raise RemoteError(f"cannot reach ai-router server at {url}: {e}", code=4)


def remote_answer(remote, payload, token=None, timeout=120):
    """Ask a deployed ai-router instance instead of calling providers locally.

    This is what lets a laptop use the server's keys: set AI_ROUTER_SERVER and
    every ask/route call goes over HTTP instead of fanning out from here.
    """
    base = remote.rstrip("/")
    if not base.endswith("/ask"):
        base += "/ask"
    out = _post_json(base, payload, token, timeout)
    if out.get("error"):
        raise RemoteError(str(out["error"]), code=3)
    return out


def answer(prompt, system=None, task=None, provider=None, model=None,
           max_tokens=2048, temperature=None, timeout=90, images=None,
           no_fallback=False, budget=False, use_llm=False, judge_provider=None,
           remote=None, token=None, routing=None):
    """Route the prompt and call a model. Returns a result dict.

    Raises AskError with `code` set to the exit code the caller should use.
    `routing` lets a caller that already classified the prompt skip re-routing.
    """
    remote = (remote or os.environ.get("AI_ROUTER_SERVER") or "").strip()
    if remote:
        payload = {}
        for key, value in (("prompt", prompt), ("system", system), ("task", task),
                           ("provider", provider), ("model", model),
                           ("max_tokens", max_tokens), ("temperature", temperature),
                           ("timeout", timeout), ("images", images),
                           ("no_fallback", no_fallback), ("budget", budget),
                           ("use_llm", use_llm)):
            if value not in (None, [], False):
                payload[key] = value
        if payload.get("images"):
            # Encode here: the server cannot see this machine's filesystem.
            payload["images"] = [call_model.image_to_data_url(p)
                                 for p in payload["images"]]
        return remote_answer(remote, payload,
                             token or os.environ.get("AI_ROUTER_SERVER_TOKEN"),
                             timeout=max(int(timeout or 90), 30) + 30)

    override = task or ("vision" if images else None)
    if routing:
        task_type, reason, ranked = routing
    else:
        task_type, reason, ranked = route(prompt, use_llm, judge_provider, override, budget)
    if not ranked:
        raise AskError("no provider has an API key configured. Fill .env "
                       "(see .env.example) or run check_provider.py --scan.", code=2)

    chosen = provider or ranked[0]
    model_name = model or judge.default_model(chosen)

    started = time.time()
    res = call_model.call_text(
        chosen, prompt, system=system, temperature=temperature,
        max_tokens=max_tokens, timeout=timeout, model=model,
        allow_fallback=not no_fallback, images=images)
    latency_ms = int((time.time() - started) * 1000)
    _record(chosen, model_name, res, latency_ms)

    if res["error"]:
        detail = res["error"]
        if res.get("attempted"):
            detail += f" (tried: {', '.join(res['attempted'])})"
        raise AskError(detail, code=3 if res["status"] else 4, status=res["status"])

    return {
        "text": res["text"],
        "provider": res["provider"],
        "model": res["model"],
        "requested_provider": chosen,
        "task_type": task_type,
        "reason": reason,
        "usage": res.get("usage") or {},
        "latency_ms": latency_ms,
        "fallback_from": res.get("fallback_from"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Classify a question, pick the best configured model, answer it.")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--system", default=None)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--model", default=None, help="override the provider's default model")
    ap.add_argument("--provider", default=None, help="force a provider instead of routing")
    ap.add_argument("--use-llm", action="store_true", help="semantic classification")
    ap.add_argument("--judge-provider", default=None)
    ap.add_argument("--no-fallback", action="store_true")
    ap.add_argument("--budget", action="store_true",
                    help="only consider providers covered by a permanent free tier")
    ap.add_argument("--image", action="append", default=None, metavar="PATH",
                    help="attach a local image (repeatable); forces the vision route")
    ap.add_argument("--remote", default=None, metavar="URL",
                    help="call a deployed ai-router server instead of providers "
                         "(env: AI_ROUTER_SERVER)")
    ap.add_argument("--token", default=None, metavar="TOKEN",
                    help="bearer token for the server (env: AI_ROUTER_SERVER_TOKEN)")
    ap.add_argument("--dry-run", action="store_true", help="print routing only, call nothing")
    ap.add_argument("--json", action="store_true", help="structured output on stdout")
    args = ap.parse_args(argv)

    remote = (args.remote or os.environ.get("AI_ROUTER_SERVER") or "").strip()
    override = "vision" if args.image else None
    task, reason, ranked = route(args.prompt, args.use_llm, args.judge_provider,
                                 override, args.budget)

    if not ranked and not remote:
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

    provider = ranked[0] if ranked else None
    model = args.model or (judge.default_model(provider) if provider else None)

    if args.dry_run:
        payload = {
            "task_type": task, "reason": reason, "provider": provider,
            "model": model, "ranked_providers": ranked,
            "configured_providers": sorted(judge.configured_providers()),
            "remote": remote or None,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if task == "search":
        sys.stderr.write(
            "[ask] this looks like a time-sensitive question; the model has no live "
            "web access - search first, then pass the findings in --prompt.\n")

    target = remote or f"{provider} / {model}"
    sys.stderr.write(f"[ask] task={task} ({reason}) -> {target}\n")

    try:
        res = answer(args.prompt, system=args.system, task=override,
                     provider=args.provider, model=args.model,
                     max_tokens=args.max_tokens, temperature=args.temperature,
                     timeout=args.timeout, images=args.image,
                     no_fallback=args.no_fallback, budget=args.budget,
                     use_llm=args.use_llm, judge_provider=args.judge_provider,
                     remote=remote, token=args.token,
                     routing=(task, reason, ranked))
    except AskError as e:
        if e.status:
            sys.stderr.write(f"[ask] HTTP {e.status} from {provider}: {e}\n")
        else:
            sys.stderr.write(f"[ask] ERROR: {e}\n")
        sys.exit(e.code)

    used_p, used_m = res["provider"], res["model"]
    if res.get("fallback_from"):
        sys.stderr.write(f"[ask] {res['requested_provider']} failed; "
                         f"answered by fallback {used_p}\n")
    sys.stderr.write(f"[ask] answered by {used_p} / {used_m}\n")

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
