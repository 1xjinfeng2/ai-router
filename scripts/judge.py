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

# Weighted keywords. "strong" terms are unambiguous signals (weight 3);
# "weak" terms are common words that only decide when nothing strong matches
# (weight 1). This matters: a flat keyword list used to send "写一句签到提醒"
# to the code model because of the single character "写".
KEYWORDS = {
    "code": {
        "strong": ["代码", "编程", "函数", "报错", "调试", "重构", "编译", "部署", "接口",
                   "数据库", "脚本", "爬虫", "正则", "python", "javascript", "typescript",
                   "java", "react", "vue", "sql", "api", "docker", "git", "css", "bug",
                   "debug", "code", "function"],
        "weak": ["实现", "前端", "后端", "算法", "性能", "变量", "组件", "服务端"],
    },
    "chinese": {
        "strong": ["文案", "润色", "翻译", "小红书", "公众号", "朋友圈", "标题", "邮件",
                   "公文", "发言稿", "主持词", "邀请函", "通知稿", "诗歌", "作文", "话术",
                   "通稿", "软文", "宣传语", "slogan", "copywrite", "translate"],
        "weak": ["写", "中文", "措辞", "语气", "风格", "繁体", "简体", "英文", "改写"],
    },
    "reasoning": {
        "strong": ["证明", "推理", "数学", "逻辑", "论证", "悖论", "概率", "博弈", "统计推断",
                   "prove", "math", "reason", "logic"],
        "weak": ["为什么", "分析", "策略", "决策", "权衡", "原因", "该不该", "方案",
                 "analysis", "trade-off"],
    },
    "long-context": {
        "strong": ["总结", "提炼", "摘要", "长文", "论文", "全文", "逐页", "pdf", "会议纪要",
                   "summarize", "paper", "document"],
        "weak": ["文档", "报告", "阅读", "概括", "要点", "章节", "report", "long"],
    },
    "vision": {
        "strong": ["图片", "图像", "截图", "照片", "视觉", "看图", "ocr",
                   "image", "photo", "picture", "screenshot", "vision"],
        "weak": ["画", "配色", "设计图", "识别"],
    },
    "structured": {
        "strong": ["json", "yaml", "xml", "csv", "结构化", "字段", "解析", "提取", "抽取",
                   "schema", "extract", "parse", "structured"],
        "weak": ["表格", "格式转换", "清洗", "table"],
    },
    "cheap": {
        "strong": ["闲聊", "你好", "在吗", "hello", "谢谢", "哈哈", "早上好", "晚安"],
        "weak": ["随便", "简单", "快速", "日常", "聊", "hi"],
    },
    "search": {
        "strong": ["最新", "今天", "实时", "新闻", "天气", "股价", "汇率", "查一下", "搜一下",
                   "最近发生", "latest", "news"],
        "weak": ["现在", "当前", "2026", "搜索", "current", "search"],
    },
}

# Deterministic tie-break when two task types score the same.
TASK_PRIORITY = ["code", "structured", "vision", "search", "long-context",
                 "reasoning", "chinese", "cheap"]

STRONG_WEIGHT = 3
WEAK_WEIGHT = 1

# A prompt with no keyword at all: very short ones are small talk, anything
# longer is almost certainly a general Chinese task, not "cheap".
SHORT_PROMPT_CHARS = 8

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
    """Score every task type by weighted keyword hits and take the best one.

    Strong signals count 3, weak ones 1. Ties are broken by TASK_PRIORITY so the
    result is deterministic. A prompt with no hit at all is treated as small talk
    when it is very short, otherwise as a general Chinese task.
    """
    low = prompt.lower()
    scores, hits = {}, {}
    for task, groups in KEYWORDS.items():
        score, matched = 0, []
        for kw in groups.get("strong", []):
            if kw.lower() in low:
                score += STRONG_WEIGHT
                matched.append(kw)
        for kw in groups.get("weak", []):
            if kw.lower() in low:
                score += WEAK_WEIGHT
                matched.append(kw)
        scores[task], hits[task] = score, matched

    best_score = max(scores.values())
    if best_score == 0:
        if len(prompt.strip()) <= SHORT_PROMPT_CHARS:
            return "cheap", "no keyword matched, short prompt -> cheap"
        return "chinese", "no keyword matched, default to chinese"

    tied = [t for t, s in scores.items() if s == best_score]
    tied.sort(key=lambda t: TASK_PRIORITY.index(t) if t in TASK_PRIORITY else 99)
    best = tied[0]
    return best, f"matched keywords for '{best}' ({', '.join(hits[best][:4])})"


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
    configured = {p for p, e in env_map.items() if os.environ.get(e)}
    # Providers with no usable balance are excluded from automatic routing so a
    # dead line never becomes the "best" pick or a wasted failover hop.
    skip = {p.strip() for p in os.environ.get("AI_ROUTER_SKIP_PROVIDERS", "").split(",")
            if p.strip()}
    return configured - skip


def health_adjusted(ranked, min_calls=3, min_rate=0.6):
    """Demote providers whose recorded success rate is poor.

    Reads usage.db (written by the reliability layer). A provider needs at least
    `min_calls` recorded calls before it can be demoted, so a single hiccup never
    reorders the routing table. Ordering inside each group is preserved.
    """
    if not ranked:
        return ranked
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import usage
        rows = usage.health()
    except Exception:  # noqa: BLE001
        return ranked
    stats = {r[0]: (r[1] or 0, r[2] or 0) for r in rows}
    good, bad = [], []
    for p in ranked:
        calls, ok = stats.get(p, (0, 0))
        rate = (ok / calls) if calls else 1.0
        (bad if calls >= min_calls and rate < min_rate else good).append(p)
    return good + bad


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
