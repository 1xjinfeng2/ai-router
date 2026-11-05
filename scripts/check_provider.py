#!/usr/bin/env python3
"""
check_provider.py - Diagnose whether a provider is configured correctly.

Sends a minimal chat request (max_tokens=1) and translates the API response
into an actionable message. With --scan, first tries GET /models to list the
model IDs actually available to the account, then falls back to probing a
candidate list. This is especially useful for Volcengine Ark (Doubao), whose
model IDs change often and are bound to a console "project".

Usage:
    python scripts/check_provider.py --provider doubao
    python scripts/check_provider.py --provider doubao --scan
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

try:
    from env_loader import load_env_file
except ImportError:  # running from an unusual cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from env_loader import load_env_file

from call_model import PROVIDERS  # single source of truth for endpoints/keys

load_env_file()

KEY_HINT = {
    "doubao": "方舟 API Key 管理直链: https://ark.volcengine.com/region:cn-beijing/apiKey",
    "ark-deepseek": "同上（方舟托管 DeepSeek-V4，共用 ARK_API_KEY）",
    "ark-kimi": "同上（方舟托管 Kimi-K3，共用 ARK_API_KEY）",
    "ark-glm": "同上（方舟托管 GLM-5.3，共用 ARK_API_KEY）",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "kimi": "https://platform.moonshot.cn/console/api-keys",
    "qwen": "https://bailian.console.aliyun.com/",
    "glm": "https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys",
    "hunyuan": "腾讯云 TokenHub → API Key 管理: https://console.cloud.tencent.com/tokenhub/apikey",
    "th-code": "同上（TokenHub 托管 Kimi K2.7 Code，共用 HUNYUAN_API_KEY）",
    "th-deepseek": "同上（TokenHub 托管 DeepSeek-V4-Pro，共用 HUNYUAN_API_KEY）",
    "th-flash": "同上（TokenHub 托管 DeepSeek-V4.1-Flash，共用 HUNYUAN_API_KEY）",
    "th-kimi": "同上（TokenHub 托管 Kimi-K3.1，共用 HUNYUAN_API_KEY）",
    "th-glm": "同上（TokenHub 托管 GLM-5.3-Flash，共用 HUNYUAN_API_KEY）",
    "th-minimax": "同上（TokenHub 托管 MiniMax-M3.1-Flash，共用 HUNYUAN_API_KEY）",
    "ernie": "https://console.bce.baidu.com/qianfan/",
    "gemini": "https://aistudio.google.com/app/apikey",
    "openai": "https://platform.openai.com/api-keys",
    "claude": "https://console.anthropic.com/settings/keys",
}

# Verified against the official catalog on 2026-11-05.
# Source: https://ark.volcengine.com/models.json  (public, no API key needed)
ARK_OFFICIAL_CATALOG = "https://ark.volcengine.com/models.json"

CANDIDATES = {
    "doubao": [
        "doubao-seed-evolving",          # recommended fast-iterating ID, no date suffix
        "doubao-seed-2-1-pro-260915",    # official curl example in base-url docs
        "doubao-seed-2-1-lite-260915",
        "doubao-seed-2-1-turbo-260628",
        "doubao-seed-2-0-mini-260428",
    ],
    "deepseek": ["deepseek-flash", "deepseek-v4-pro"],
    "qwen": ["qwen3.7-plus", "qwen3.8-max", "qwen3.8-flash"],
    "openai": ["gpt-5.1", "gpt-5-mini", "gpt-5-pro"],
    "hunyuan": ["hy4-preview", "hy4", "hy3", "deepseek-v4-pro-202606",
                "deepseek-v4.1-flash", "kimi-k3.1-pro", "kimi-k2.7-code",
                "glm-5.3-flash", "minimax-m3.1-flash", "step-5-preview"],
}


def auth_headers(provider, key, mode):
    if mode == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": "Bearer " + key}


def probe(provider, model, timeout=20):
    base_url, key_env, default_model, mode = PROVIDERS[provider]
    key = os.environ.get(key_env)
    if not key:
        return "NO_KEY", key_env
    model = model or default_model
    if mode == "anthropic":
        url = base_url.rstrip("/") + "/messages"
        payload = {"model": model, "max_tokens": 1,
                   "messages": [{"role": "user", "content": "hi"}]}
    else:
        url = base_url.rstrip("/") + "/chat/completions"
        payload = {"model": model, "max_tokens": 1,
                   "messages": [{"role": "user", "content": "hi"}]}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST")
    for k, v in auth_headers(provider, key, mode).items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return "OK", model
    except urllib.error.HTTPError as e:
        return "HTTP_%d" % e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return "NETWORK", str(e)[:200]


def list_models(provider, timeout=15):
    """Try GET /models (OpenAI-compatible providers only)."""
    base_url, key_env, _, mode = PROVIDERS[provider]
    if mode == "anthropic":
        return None
    key = os.environ.get(key_env)
    if not key:
        return None
    req = urllib.request.Request(base_url.rstrip("/") + "/models", method="GET")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("id") for m in data.get("data", []) if m.get("id")]
    except Exception:  # noqa: BLE001
        return None


def official_ark_models(timeout=15):
    """Fetch Ark's PUBLIC model catalog (no API key required).

    Ark does not reliably expose GET /models, and its model IDs are refreshed
    weekly, so hardcoded IDs go stale. This catalog is the authoritative source.
    Returns (generated_at, [doubao LLM model IDs]).
    """
    try:
        with urllib.request.urlopen(ARK_OFFICIAL_CATALOG, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        names = [m["name"] for m in data.get("models", [])
                 if m.get("name", "").startswith("doubao-")
                 and "LLM" in (m.get("domains") or [])]
        return data.get("generatedAt"), names
    except Exception:  # noqa: BLE001
        return None, []


def diagnose(provider, model, code, detail):
    if code == "NO_KEY":
        return ("未设置 %s。请在技能根目录的 .env 中填写；申请地址：%s"
                % (detail, KEY_HINT.get(provider, "见 .env.example")))
    if code == "OK":
        return "可用：%s / %s" % (provider, model)
    if code in ("HTTP_401", "HTTP_403"):
        extra = ""
        if provider == "doubao":
            extra = (" 方舟的 API Key 与「项目」绑定：确认控制台左上角当前项目"
                     "与创建 Key 时所属项目一致。")
        return "Key 无效或权限不足。" + extra + " 详情：" + detail
    if code == "HTTP_402":
        return "余额不足 / 免费额度已用尽。到控制台充值，或在「开通管理」开通模型转为付费。详情：" + detail
    if code == "HTTP_429":
        return "触发限流，稍后重试或检查「开通管理」中的模型限流设置。详情：" + detail
    if code == "NETWORK":
        return "网络不可达：" + detail
    low = detail.lower()
    if code == "HTTP_404" or "model" in low and ("not found" in low or "not exist" in low
                                                  or "不存在" in detail or "未开通" in detail):
        tip = (" 到「开通管理」开通该模型，或运行 "
               "`python scripts/check_provider.py --provider %s --scan` 找出可用模型 ID。"
               % provider)
        return "模型 ID 不存在或未开通。" + tip + " 详情：" + detail
    return "HTTP %s：%s" % (code, detail)


def main():
    ap = argparse.ArgumentParser(description="Diagnose a provider's API configuration.")
    ap.add_argument("--provider", required=True, choices=list(PROVIDERS.keys()))
    ap.add_argument("--model", default=None, help="test a specific model ID")
    ap.add_argument("--scan", action="store_true",
                    help="list account models and probe candidate IDs")
    args = ap.parse_args()

    _, _, default_model, _ = PROVIDERS[args.provider]

    if args.scan:
        models = list_models(args.provider)
        if models:
            print("[账号可用模型 /models]")
            for m in models:
                print("  -", m)
            print()
        else:
            print("[账号可用模型] /models 接口不可用，改用候选 ID 探测。\n")

        # Ark's model IDs change weekly and /models is often unavailable.
        # Show the authoritative public catalog for reference.
        if args.provider == "doubao":
            generated, official = official_ark_models()
            if official:
                print("[方舟官方模型目录] %s" % ARK_OFFICIAL_CATALOG)
                print("  生成时间: %s ｜ 豆包 LLM 模型 %d 个"
                      % (generated or "未知", len(official)))
                for m in official:
                    print("  -", m)
                print("\n  注意：目录中的 ID 不带日期后缀；部分模型调用时需用带日期的")
                print("  完整 ID（如 doubao-seed-2-1-pro-260915），推荐优先用")
                print("  doubao-seed-evolving（统一 ID，周级迭代）。\n")
            else:
                print("[方舟官方模型目录] 拉取失败（网络问题），仅用本地候选 ID。\n")

        _, key_env, _, _ = PROVIDERS[args.provider]
        if not os.environ.get(key_env):
            print("未配置 %s：上方已给出官方模型目录供参考；填入 .env 后" % key_env)
            print("再运行 --scan 才会对该账号做真实探测。")
            return

        candidates = CANDIDATES.get(args.provider, [])
        if not candidates:
            candidates = [default_model]
        # de-duplicate, keep order
        seen, todo = set(), []
        for m in list(models or [])[:8] + candidates + [default_model]:
            if m and m not in seen:
                seen.add(m)
                todo.append(m)
        ok = []
        for m in todo:
            code, detail = probe(args.provider, m)
            flag = "OK " if code == "OK" else "FAIL"
            print("  [%s] %-45s %s" % (flag, m, code))
            if code == "OK":
                ok.append(m)
        print()
        if ok:
            print("可用模型 ID：", ", ".join(ok))
            print("若与默认不同，可在调用时加 --model <ID>，或更新 call_model.py 的默认模型。")
        else:
            print("没有可用模型。参考上方错误信息排查（Key / 项目 / 开通管理 / 余额）。")
        return

    model = args.model or default_model
    code, detail = probe(args.provider, model)
    print("provider :", args.provider)
    print("model    :", model)
    print("status   :", code)
    print("result   :", diagnose(args.provider, model, code, detail))
    if code != "OK":
        print("\n提示：加 --scan 可自动找出该账号真实可用的模型 ID。")


if __name__ == "__main__":
    main()
