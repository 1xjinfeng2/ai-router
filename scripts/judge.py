#!/usr/bin/env python3
"""
judge.py - Classify a prompt into a task type and recommend the best model.

Default: keyword-based rules (no API key needed).
Optional: --use-llm uses a small model to classify semantically.

Usage:
    python judge.py --prompt "帮我用Python写个爬虫"
    python judge.py --prompt "写一封英文邮件" --use-llm
"""
import argparse
import json
import os
import subprocess
import sys

# Task type -> ordered candidate providers (best first).
ROUTING = {
    "code":         ["claude", "th-code", "ark-deepseek", "deepseek", "openai"],
    "chinese":      ["doubao", "hunyuan", "th-glm", "qwen", "glm"],
    "reasoning":    ["openai", "th-deepseek", "ark-deepseek", "deepseek", "gemini"],
    "long-context": ["gemini", "th-kimi", "ark-kimi", "claude", "qwen"],
    "vision":       ["openai", "gemini", "doubao"],
    "structured":   ["openai", "qwen", "th-flash", "deepseek"],
    "cheap":        ["doubao", "hunyuan", "th-flash", "ark-deepseek", "deepseek", "glm"],
    "search":       [],  # needs a web tool, no built-in model
}

KEYWORDS = {
    "code": ["代码", "编程", "写", "函数", "bug", "debug", "报错", "重构", "前端", "后端",
             "python", "javascript", "react", "sql", "脚本", "接口", "算法", "code", "function", "bug"],
    "chinese": ["文案", "中文", "翻译", "润色", "小红书", "公众号", "朋友圈", "邮件", "诗歌", "作文",
                "writing", "chinese", "translate", "copywrite"],
    "reasoning": ["推理", "数学", "证明", "逻辑", "复杂", "为什么", "分析", "策略", "决策",
                  "reason", "math", "prove", "logic", "analysis"],
    "long-context": ["总结", "长文", "论文", "文档", "PDF", "报告", "阅读", "提炼",
                     "summarize", "long", "paper", "document", "report"],
    "vision": ["图片", "图像", "截图", "照片", "识别", "画", "视觉",
               "image", "photo", "picture", "vision", "screenshot"],
    "structured": ["json", "提取", "表格", "结构化", "解析", "字段",
                   "extract", "table", "parse", "structured"],
    "cheap": ["闲聊", "日常", "快速", "简单", "随便", "hello", "hi", "聊天"],
    "search": ["最新", "今天", "实时", "新闻", "搜索", "查一下", "2026", "现在",
               "latest", "news", "search", "current"],
}

DEFAULT_MODEL = {
    "claude": "claude-sonnet-5-5",
    "deepseek": "deepseek-flash",
    "openai": "gpt-5.1",
    "doubao": "doubao-seed-evolving",
    "qwen": "qwen3.7-plus",
    "glm": "glm-5",
    "gemini": "gemini-2.5-flash",
}


def default_model(provider):
    """Read the default model from call_model.PROVIDERS (single source of truth).

    This keeps judge.py in sync when new providers or Ark aliases are added,
    instead of maintaining a second hand-written table.
    """
    if not provider:
        return None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from call_model import PROVIDERS
        if provider in PROVIDERS:
            return PROVIDERS[provider][2]
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_MODEL.get(provider)


def pick_judge_provider(configured):
    """Pick a configured provider for semantic classification (cheap + available)."""
    for p in ("doubao", "deepseek", "qwen", "glm", "openai", "claude"):
        if p in configured:
            return p
    return "deepseek"


def rule_classify(prompt):
    scores = {k: 0 for k in KEYWORDS}
    low = prompt.lower()
    for t, kws in KEYWORDS.items():
        for kw in kws:
            if kw.lower() in low:
                scores[t] += 1
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return "cheap", "no keyword matched, default to cheap"
    return best, f"matched keywords for '{best}'"


def llm_classify(prompt, judge_provider="deepseek", judge_model=None):
    """Use a small model to classify. Falls back to rules on any error."""
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, "call_model.py"),
           "--provider", judge_provider,
           "--prompt", (
               "Classify the user request into exactly one of these task types: "
               "code, chinese, reasoning, long-context, vision, structured, cheap, search. "
               "Respond with ONLY the lowercase type name and nothing else.\n\nRequest: " + prompt
           )]
    if judge_model:
        cmd += ["--model", judge_model]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        label = out.stdout.strip().lower()
        if label in ROUTING:
            return label, f"LLM judge ({judge_provider}) classified as '{label}'"
    except Exception:  # noqa: BLE001
        pass
    return rule_classify(prompt)


def configured_providers():
    env_map = {
        "doubao": "ARK_API_KEY", "deepseek": "DEEPSEEK_API_KEY", "kimi": "MOONSHOT_API_KEY",
        "qwen": "DASHSCOPE_API_KEY", "glm": "ZHIPU_API_KEY", "hunyuan": "HUNYUAN_API_KEY",
        "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY",
        "ernie": "QIANFAN_API_KEY",
        # Ark aliases all share the same key; they are verified-working models
        # and let code/long-context tasks avoid the unpaid DeepSeek account.
        "ark-deepseek": "ARK_API_KEY", "ark-kimi": "ARK_API_KEY", "ark-glm": "ARK_API_KEY",
        # TokenHub aliases share HUNYUAN_API_KEY (verified 2026-11-05).
        "th-code": "HUNYUAN_API_KEY", "th-deepseek": "HUNYUAN_API_KEY",
        "th-flash": "HUNYUAN_API_KEY", "th-kimi": "HUNYUAN_API_KEY",
        "th-glm": "HUNYUAN_API_KEY", "th-minimax": "HUNYUAN_API_KEY",
    }
    return {p for p, e in env_map.items() if os.environ.get(e)}


def main():
    ap = argparse.ArgumentParser(description="Classify a prompt and recommend a model.")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--use-llm", action="store_true", help="use a small model to classify")
    ap.add_argument("--judge-provider", default=None,
                    help="provider used for --use-llm; auto-picks a configured one by default")
    ap.add_argument("--judge-model", default=None)
    args = ap.parse_args()

    try:
        from env_loader import load_env_file
    except ImportError:  # running from an unusual cwd
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from env_loader import load_env_file
    load_env_file()

    configured = configured_providers()
    if args.use_llm:
        judge_provider = args.judge_provider or pick_judge_provider(configured)
        task, reason = llm_classify(args.prompt, judge_provider, args.judge_model)
    else:
        task, reason = rule_classify(args.prompt)

    ranked = [p for p in ROUTING.get(task, []) if p in configured]
    if not ranked:
        ranked = list(configured)  # fall back to any configured provider
    recommended = ranked[0] if ranked else None
    result = {
        "task_type": task,
        "reason": reason,
        "recommended": (
            {"provider": recommended, "model": default_model(recommended)}
            if recommended else None
        ),
        "ranked_providers": ranked,
        "configured_providers": sorted(configured),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
