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

from call_model import PROVIDERS, call_openai, call_anthropic, extract_text
import privacy

try:
    import judge
except Exception:  # noqa: BLE001
    judge = None


def call_provider(provider, prompt, system, max_tokens, timeout=60):
    """Call one provider and return (text, error). Never raises."""
    if provider not in PROVIDERS:
        return None, f"unknown provider '{provider}'"
    base_url, key_env, default_model, mode = PROVIDERS[provider]
    api_key = os.environ.get(key_env)
    if not api_key:
        return None, f"missing key {key_env}"
    try:
        if mode == "anthropic":
            resp = call_anthropic(base_url, api_key, default_model, prompt,
                                  system, None, max_tokens, None, timeout)
        else:
            resp = call_openai(base_url, api_key, default_model, prompt,
                               system, None, max_tokens, None, timeout)
        return (extract_text(resp, mode) or "").strip(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


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


def organize(results, organizer, session, max_tokens, timeout=90):
    """Merge sanitized provider outputs into one polished answer."""
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
    text, err = call_provider(organizer, prompt, ORGANIZE_SYSTEM, max_tokens, timeout)
    if err:
        sys.stderr.write(f"[relay] organizer '{organizer}' failed: {err}; falling back to concat\n")
        return None
    return text


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
    for p in providers:
        if p not in PROVIDERS:
            sys.stderr.write(f"[relay] skip unknown provider '{p}'\n")
            continue
        text, err = call_provider(p, sanitized, args.system, args.max_tokens, args.timeout)
        if err:
            sys.stderr.write(f"[relay] {p}: ERROR {err}\n")
            results.append({"provider": p, "text": None, "error": err, "sanitized_prompt": sanitized})
        else:
            sys.stderr.write(f"[relay] {p}: ok ({len(text)} chars)\n")
            results.append({"provider": p, "text": text, "error": None, "sanitized_prompt": sanitized})

    ok = [r for r in results if r["text"] is not None]
    if not ok:
        sys.stderr.write("[relay] all providers failed; nothing to organize.\n")
        sys.exit(3)

    # 3) organize
    if args.no_organize:
        merged = "\n\n".join(f"[{r['provider']}]\n{r['text']}" for r in ok)
    else:
        merged = organize(results, args.organizer, session, args.max_tokens, args.timeout)
        if merged is None:
            merged = "\n\n".join(f"[{r['provider']}]\n{r['text']}" for r in ok)

    # 4) restore for the owner
    if args.no_restore:
        final = merged
    else:
        final = privacy.restore(merged, session)

    sys.stderr.write(f"[relay] done. final length={len(final)} chars.\n")
    print(final)


if __name__ == "__main__":
    main()
