#!/usr/bin/env python3
"""
call_model.py - Unified caller for multiple OpenAI-compatible LLM providers.

Routes a single prompt to the best-fit model by provider name. Supports Doubao,
DeepSeek, Kimi, Qwen, GLM, Hunyuan, Gemini, OpenAI and ERNIE (all OpenAI
compatible) plus Claude (dedicated Anthropic branch).

Usage:
    python call_model.py --provider deepseek --prompt "Hello"
    python call_model.py --provider openai --model gpt-5.1 --prompt "..." --json

No third-party dependencies: uses only the Python standard library.
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

# provider -> (base_url, api_key_env, default_model, mode)
# mode: "openai" (OpenAI-compatible chat/completions) or "anthropic"
# Defaults cross-checked with official docs on 2026-11-05 (see references/models.md).
# Only deepseek, doubao and the ark-* aliases have been tested with real keys here;
# verify the others with check_provider.py --scan after adding a key.
PROVIDERS = {
    "doubao":   ("https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY", "doubao-seed-evolving", "openai"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-flash", "openai"),
    "kimi":     ("https://api.moonshot.cn/v1", "MOONSHOT_API_KEY", "kimi-k3.1-pro", "openai"),
    "qwen":     ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", "qwen3.7-plus", "openai"),
    "glm":      ("https://open.bigmodel.cn/api/paas/v4", "ZHIPU_API_KEY", "glm-5", "openai"),
    # The old api.hunyuan.cloud.tencent.com platform shut down in 2026-09.
    # Hunyuan now lives on TokenHub (CN site: tokenhub.tencentmaas.com, NO "cloud"
    # in the hostname). One key there also reaches Kimi/GLM/DeepSeek/MiniMax.
    "hunyuan":  ("https://tokenhub.tencentmaas.com/v1", "HUNYUAN_API_KEY", "hy4-preview", "openai"),
    "gemini":   ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-2.5-flash", "openai"),
    "openai":   ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-5.1", "openai"),
    "claude":   ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", "claude-sonnet-5-5", "anthropic"),
    "ernie":    ("https://qianfan.baidubce.com/v2", "QIANFAN_API_KEY", "ernie-4.0-8k", "openai"),
    # A single Ark key also reaches third-party models hosted on Volcengine Ark.
    # Verified 2026-11-05 via GET /models + real chat calls.
    "ark-deepseek": ("https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY", "deepseek-v4-1-flash-261017", "openai"),
    "ark-kimi":     ("https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY", "kimi-k3-1-pro-261024", "openai"),
    "ark-glm":      ("https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY", "glm-5-3-flash-260828", "openai"),
    # A single Tencent TokenHub key reaches 120+ online models (verified
    # 2026-11-05 via GET /v1/models + real chat calls on all of these).
    # CN site is tokenhub.tencentmaas.com -- there is NO "cloud" in the hostname.
    "th-code":     ("https://tokenhub.tencentmaas.com/v1", "HUNYUAN_API_KEY", "kimi-k2.7-code", "openai"),
    "th-deepseek": ("https://tokenhub.tencentmaas.com/v1", "HUNYUAN_API_KEY", "deepseek-v4-pro-202606", "openai"),
    "th-flash":    ("https://tokenhub.tencentmaas.com/v1", "HUNYUAN_API_KEY", "deepseek-v4.1-flash", "openai"),
    "th-kimi":     ("https://tokenhub.tencentmaas.com/v1", "HUNYUAN_API_KEY", "kimi-k3.1-pro", "openai"),
    "th-glm":      ("https://tokenhub.tencentmaas.com/v1", "HUNYUAN_API_KEY", "glm-5.3-flash", "openai"),
    "th-minimax":  ("https://tokenhub.tencentmaas.com/v1", "HUNYUAN_API_KEY", "minimax-m3.1-flash", "openai"),
}

# Load .env from the skill root using the bundled stdlib-only loader.
try:
    from env_loader import load_env_file
except ImportError:  # running from an unusual cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from env_loader import load_env_file
load_env_file()


def call_openai(base_url, api_key, model, prompt, system, temperature, max_tokens, top_p, timeout):
    url = base_url.rstrip("/") + "/chat/completions"
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    # Many newer models (e.g. kimi-k3-1-pro) reject explicit sampling params
    # unless they equal a fixed value. Only send them when the user asked for
    # them; otherwise let the server apply the model's own defaults.
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_anthropic(base_url, api_key, model, prompt, system, temperature, max_tokens, top_p, timeout):
    url = base_url.rstrip("/") + "/messages"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if system:
        payload["system"] = system
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_text(resp, mode):
    if mode == "anthropic":
        return "".join(p.get("text", "") for p in resp.get("content", []) if p.get("type") == "text")
    return resp["choices"][0]["message"]["content"]


def extract_usage(resp, mode):
    """Return {prompt_tokens, completion_tokens, total_tokens} from a raw resp."""
    try:
        if mode == "anthropic":
            u = resp.get("usage", {}) or {}
            inp = u.get("input_tokens", 0) or 0
            out = u.get("output_tokens", 0) or 0
            return {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}
        u = resp.get("usage", {}) or {}
        return {
            "prompt_tokens": u.get("prompt_tokens", 0) or 0,
            "completion_tokens": u.get("completion_tokens", 0) or 0,
            "total_tokens": u.get("total_tokens", 0) or 0,
        }
    except Exception:  # noqa: BLE001
        return None


def main():
    p = argparse.ArgumentParser(description="Unified LLM caller for multi-provider routing.")
    p.add_argument("--provider", required=True, choices=list(PROVIDERS.keys()))
    p.add_argument("--model", default=None, help="override provider default model")
    p.add_argument("--prompt", required=True)
    p.add_argument("--system", default=None)
    p.add_argument("--temperature", type=float, default=None,
                   help="sampling temperature; omit to use the model's default")
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--top-p", type=float, default=None,
                   help="nucleus sampling; omit to use the model's default")
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--json", action="store_true", help="print full raw JSON response")
    args = p.parse_args()

    base_url, key_env, default_model, mode = PROVIDERS[args.provider]
    api_key = os.environ.get(key_env)
    if not api_key:
        sys.stderr.write(
            f"[ai-router] ERROR: missing API key for provider '{args.provider}'. "
            f"Set {key_env} in the environment or .env\n"
        )
        sys.exit(2)

    model = args.model or default_model

    try:
        if mode == "anthropic":
            resp = call_anthropic(base_url, api_key, model, args.prompt, args.system,
                                  args.temperature, args.max_tokens, args.top_p, args.timeout)
        else:
            resp = call_openai(base_url, api_key, model, args.prompt, args.system,
                               args.temperature, args.max_tokens, args.top_p, args.timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        sys.stderr.write(f"[ai-router] HTTP {e.code} from {args.provider}: {body}\n")
        sys.exit(3)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[ai-router] ERROR calling {args.provider}: {e}\n")
        sys.exit(4)

    if args.json:
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    else:
        text = extract_text(resp, mode) or ""
        if not text.strip() and mode != "anthropic":
            message = (resp.get("choices") or [{}])[0].get("message") or {}
            if message.get("reasoning_content"):
                sys.stderr.write(
                    "[ai-router] 提示：模型把 token 用于思考（reasoning_content），正文为空。"
                    "请增大 --max-tokens（例如 --max-tokens 1024）后重试。\n"
                )
        print(text)

    # Metering: record this call's token usage locally (best-effort).
    try:
        from usage import record as _record_usage
        _u = extract_usage(resp, mode)
        if _u:
            _record_usage(args.provider, model, _u["prompt_tokens"],
                          _u["completion_tokens"], _u["total_tokens"])
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
