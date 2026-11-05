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
import base64
import json
import os
import socket
import sys
import time
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
    # Vision alias: same TokenHub key, multimodal model (verified in /v1/models).
    "th-vision":   ("https://tokenhub.tencentmaas.com/v1", "HUNYUAN_API_KEY", "hy-vision-2.0-instruct", "openai"),
}

# Load .env from the skill root using the bundled stdlib-only loader.
try:
    from env_loader import load_env_file
except ImportError:  # running from an unusual cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from env_loader import load_env_file
load_env_file()


IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}


def encode_image(path):
    """Return (mime, base64) for a local image, sniffing the header if needed."""
    with open(path, "rb") as f:
        raw = f.read()
    mime = IMAGE_MIME.get(os.path.splitext(path)[1].lower())
    if mime is None:
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        elif raw[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            mime = "image/webp"
        elif raw[:6] in (b"GIF87a", b"GIF89a"):
            mime = "image/gif"
        else:
            mime = "image/png"
    return mime, base64.b64encode(raw).decode("ascii")


def _build_content(prompt, images, mode):
    """Plain string for text-only calls; multimodal parts when images are given."""
    if not images:
        return prompt
    if mode == "anthropic":
        parts = [{"type": "text", "text": prompt}]
        for p in images:
            mime, b64 = encode_image(p)
            parts.append({"type": "image",
                          "source": {"type": "base64", "media_type": mime, "data": b64}})
        return parts
    parts = [{"type": "text", "text": prompt}]
    for p in images:
        mime, b64 = encode_image(p)
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:{mime};base64,{b64}"}})
    return parts


def call_openai(base_url, api_key, model, prompt, system, temperature, max_tokens, top_p, timeout,
                images=None):
    url = base_url.rstrip("/") + "/chat/completions"
    content = _build_content(prompt, images, "openai")
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": content}]
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


def call_anthropic(base_url, api_key, model, prompt, system, temperature, max_tokens, top_p, timeout,
                   images=None):
    url = base_url.rstrip("/") + "/messages"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": _build_content(prompt, images, "anthropic")}],
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


# ---------------------------------------------------------------------------
# Streaming (Server-Sent Events). No third-party dependencies.
# ---------------------------------------------------------------------------
def _iter_sse(resp):
    """Yield the JSON payload of each `data:` line from an SSE response."""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("", "[DONE]"):
            if payload == "[DONE]":
                return
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def stream_openai(base_url, api_key, model, prompt, system, temperature,
                  max_tokens, top_p, timeout):
    url = base_url.rstrip("/") + "/chat/completions"
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
               "stream": True}
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for obj in _iter_sse(resp):
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield piece


def stream_anthropic(base_url, api_key, model, prompt, system, temperature,
                     max_tokens, top_p, timeout):
    url = base_url.rstrip("/") + "/messages"
    payload = {"model": model, "max_tokens": max_tokens, "stream": True,
               "messages": [{"role": "user", "content": prompt}]}
    if temperature is not None:
        payload["temperature"] = temperature
    if system:
        payload["system"] = system
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 method="POST")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for obj in _iter_sse(resp):
            if obj.get("type") == "content_block_delta":
                delta = obj.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield delta["text"]


def stream_text(provider, prompt, system=None, temperature=None, max_tokens=2048,
                top_p=None, timeout=60, model=None):
    """Yield text chunks from one provider. Retries once on a transient error
    that happens before the first chunk arrives; fallbacks are NOT attempted
    because the caller has already started printing output."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider '{provider}'")
    base_url, key_env, default_model, mode = PROVIDERS[provider]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(f"missing key {key_env}")
    model = model or default_model
    fn = stream_anthropic if mode == "anthropic" else stream_openai
    try:
        yield from fn(base_url, api_key, model, prompt, system, temperature,
                      max_tokens, top_p, timeout)
    except urllib.error.HTTPError as e:
        status, retryable, detail = classify_error(e)
        if not retryable:
            raise
        time.sleep(RETRY_BASE_DELAY)
        yield from fn(base_url, api_key, model, prompt, system, temperature,
                      max_tokens, top_p, timeout)


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


# ---------------------------------------------------------------------------
# Reliability layer: error classification, exponential-backoff retry, fallback
# ---------------------------------------------------------------------------
# Retryable = transient (rate limit, server hiccup, timeout). Fatal = retrying
# is pointless (bad key, no balance, unknown model) -> jump straight to a backup
# provider. Codes not listed fall back to "5xx retryable, 4xx fatal".
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
FATAL_STATUS = {400, 401, 402, 403, 404, 405, 413, 415, 422}

RETRY_ATTEMPTS = 3          # total tries per provider (1 initial + 2 retries)
RETRY_BASE_DELAY = 1.0      # seconds; doubled each retry (1s, 2s, 4s ...)
RETRY_MAX_DELAY = 8.0

# Reasoning models burn tokens on hidden thinking. If max_tokens is too small the
# visible body comes back empty. These are per-provider minimums (tokens).
REASONING_FLOOR = {
    "th-deepseek": 4096,
    "th-kimi": 4096,
    "kimi": 4096,
    "ark-kimi": 4096,
    "hunyuan": 4096,
    "deepseek": 2048,
    "ark-deepseek": 2048,
}
EMPTY_BODY_RETRY_CAP = 8192  # never auto-expand beyond this


def effective_max_tokens(provider, requested):
    """Apply the reasoning-model floor so we do not get empty bodies."""
    return max(int(requested or 0), REASONING_FLOOR.get(provider, 0))


def classify_error(exc):
    """Map an exception to (status|None, retryable: bool, detail: str)."""
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        retryable = exc.code in RETRY_STATUS or exc.code >= 500
        if exc.code in FATAL_STATUS:
            retryable = False
        return exc.code, retryable, detail or f"HTTP {exc.code}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return None, True, f"timeout: {exc}"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return None, True, f"timeout: {reason}"
        return None, True, f"network: {reason}"
    if isinstance(exc, (ConnectionError, OSError)):
        return None, True, f"network: {exc}"
    return None, False, f"{type(exc).__name__}: {exc}"


def call_once(provider, prompt, system=None, temperature=None, max_tokens=2048,
              top_p=None, timeout=60, model=None, images=None):
    """One raw API call. Raises the original exception on failure."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider '{provider}'")
    base_url, key_env, default_model, mode = PROVIDERS[provider]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(f"missing key {key_env}")
    model = model or default_model
    if mode == "anthropic":
        return call_anthropic(base_url, api_key, model, prompt, system,
                              temperature, max_tokens, top_p, timeout, images)
    return call_openai(base_url, api_key, model, prompt, system,
                       temperature, max_tokens, top_p, timeout, images)


def call_with_retry(provider, prompt, system=None, temperature=None,
                    max_tokens=2048, top_p=None, timeout=60, model=None,
                    attempts=RETRY_ATTEMPTS, log=None, images=None):
    """Call one provider with exponential backoff on transient errors.

    Returns (resp, model, attempts_used, errors) where errors is a list of
    (status, detail) tuples collected along the way. Raises the last exception
    if every attempt failed or the error was fatal.
    """
    errs = []
    last = None
    extra = {"images": images} if images else {}
    for i in range(attempts):
        try:
            resp = call_once(provider, prompt, system, temperature, max_tokens,
                             top_p, timeout, model, **extra)
            return resp, (model or PROVIDERS[provider][2]), i + 1, errs
        except Exception as e:  # noqa: BLE001
            status, retryable, detail = classify_error(e)
            errs.append((status, detail))
            last = e
            if not retryable or i == attempts - 1:
                raise
            delay = min(RETRY_BASE_DELAY * (2 ** i), RETRY_MAX_DELAY)
            if log:
                log(f"{provider}: {detail} -> retry {i + 1}/{attempts - 1} in {delay:.1f}s")
            time.sleep(delay)
    raise last  # pragma: no cover


def skipped_providers():
    """Providers the user marked as unusable (e.g. a zero-balance key).

    Set AI_ROUTER_SKIP_PROVIDERS="deepseek,kimi" in .env. Automatic routing and
    failover skip them entirely; an explicit --provider still tries (and reports)
    so a deliberate request never silently changes model.
    """
    raw = os.environ.get("AI_ROUTER_SKIP_PROVIDERS", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def call_text(provider, prompt, system=None, temperature=None, max_tokens=2048,
              top_p=None, timeout=60, model=None, allow_fallback=True,
              log=None, images=None):
    """Call a provider and return a dict describing the outcome.

    Keys: text, error, status, usage, provider (actually used), model,
          attempted (list of providers tried), retried (bool), empty_retry (bool)

    Behaviour:
      * transient errors are retried with backoff
      * an empty body from a reasoning model is retried once with a bigger
        token budget (capped at EMPTY_BODY_RETRY_CAP)
      * if the primary provider still fails and allow_fallback is on, walk
        FALLBACK_CHAINS[provider] and use the first one that answers
    """
    log = log or (lambda m: None)
    chain = [provider]
    if allow_fallback:
        chain += [p for p in FALLBACK_CHAINS.get(provider, []) if p not in chain]
    attempted = []
    budget = effective_max_tokens(provider, max_tokens)
    last_err, last_status = "no provider answered", None
    skip = skipped_providers()

    for idx, prov in enumerate(chain):
        if prov not in PROVIDERS:
            continue
        env = PROVIDERS[prov][1]
        if not os.environ.get(env):
            log(f"skip fallback '{prov}': missing {env}")
            continue
        if idx > 0 and prov in skip:
            log(f"skip fallback '{prov}': marked unavailable in AI_ROUTER_SKIP_PROVIDERS")
            continue
        # Fallbacks get the same reasoning-aware budget as their own floor.
        prov_budget = effective_max_tokens(prov, budget)
        attempted.append(prov)
        try:
            resp, used_model, tries, errs = call_with_retry(
                prov, prompt, system, temperature, prov_budget, top_p, timeout,
                model=None, log=log, images=images)
        except Exception as e:  # noqa: BLE001
            status, retryable, detail = classify_error(e)
            last_err, last_status = detail, status
            log(f"{prov}: failed ({'retryable' if retryable else 'fatal'}): {detail}")
            continue

        text = (extract_text(resp, PROVIDERS[prov][3]) or "").strip()
        u = extract_usage(resp, PROVIDERS[prov][3])
        empty_retry = False
        if not text and prov_budget < EMPTY_BODY_RETRY_CAP:
            # Reasoning model spent everything on hidden thinking: try once more
            # with a doubled budget (bounded), then give up on this provider.
            bigger = min(prov_budget * 2, EMPTY_BODY_RETRY_CAP)
            log(f"{prov}: empty body at {prov_budget} tokens -> retry once at {bigger}")
            empty_retry = True
            try:
                resp2, used_model, tries2, _ = call_with_retry(
                    prov, prompt, system, temperature, bigger, top_p, timeout,
                    model=None, log=log, images=images)
                text2 = (extract_text(resp2, PROVIDERS[prov][3]) or "").strip()
                if text2:
                    text, resp, u = text2, resp2, extract_usage(resp2, PROVIDERS[prov][3])
            except Exception as e:  # noqa: BLE001
                status, _r, detail = classify_error(e)
                log(f"{prov}: empty-body retry failed: {detail}")
        if not text:
            last_err, last_status = "empty body (model produced no visible text)", None
            log(f"{prov}: empty body even after retry; trying fallback")
            continue
        if idx > 0:
            log(f"answered by fallback '{prov}' (primary '{provider}' failed)")
        return {
            "text": text, "error": None, "status": None, "usage": u,
            "provider": prov, "model": used_model, "attempted": attempted,
            "retried": tries > 1 or empty_retry, "empty_retry": empty_retry,
            "fallback_from": provider if prov != provider else None,
        }

    return {
        "text": None, "error": last_err, "status": last_status, "usage": None,
        "provider": provider, "model": PROVIDERS.get(provider, ("", "", "", ""))[2],
        "attempted": attempted, "retried": len(attempted) > 1, "empty_retry": False,
        "fallback_from": None,
    }


# When a provider dies, these are the backups tried in order. Chooses a model of
# similar capability from a different vendor so one outage cannot kill a task.
FALLBACK_CHAINS = {
    "doubao":       ["ark-deepseek", "th-flash"],
    "ark-deepseek": ["doubao", "th-flash"],
    "ark-kimi":     ["th-kimi", "th-code"],
    "ark-glm":      ["th-glm", "th-flash"],
    "deepseek":     ["th-flash", "doubao"],
    "kimi":         ["th-kimi", "ark-kimi"],
    "glm":          ["th-glm", "ark-glm"],
    "qwen":         ["th-flash", "doubao"],
    "hunyuan":      ["th-deepseek", "doubao"],
    "gemini":       ["doubao", "th-flash"],
    "openai":       ["claude", "doubao"],
    "claude":       ["openai", "th-deepseek"],
    "ernie":        ["th-flash", "doubao"],
    "th-code":      ["ark-kimi", "th-kimi"],
    "th-deepseek":  ["doubao", "th-flash"],
    "th-flash":     ["doubao", "deepseek"],
    "th-kimi":      ["ark-kimi", "th-code"],
    "th-glm":       ["ark-glm", "th-flash"],
    "th-minimax":   ["th-flash", "doubao"],
    "th-vision":    ["gemini", "openai", "doubao"],
}


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
    p.add_argument("--retries", type=int, default=RETRY_ATTEMPTS,
                   help=f"tries per provider on transient errors (default {RETRY_ATTEMPTS})")
    p.add_argument("--no-fallback", action="store_true",
                   help="do not switch to a backup provider when this one fails")
    p.add_argument("--stream", action="store_true",
                   help="stream the answer with SSE (prints as it arrives)")
    p.add_argument("--image", action="append", default=None, metavar="PATH",
                   help="attach a local image (repeatable); needs a vision model")
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
    started = time.time()

    def _log(msg):
        sys.stderr.write(f"[ai-router] {msg}\n")

    if args.stream and args.image:
        sys.stderr.write("[ai-router] --stream does not support --image; "
                         "falling back to a buffered call\n")
        args.stream = False

    if args.stream:
        try:
            for chunk in stream_text(args.provider, args.prompt, args.system,
                                     args.temperature,
                                     effective_max_tokens(args.provider, args.max_tokens),
                                     args.top_p, args.timeout, args.model):
                sys.stdout.write(chunk)
                sys.stdout.flush()
            sys.stdout.write("\n")
        except Exception as e:  # noqa: BLE001
            status, _retryable, detail = classify_error(e)
            sys.stderr.write(
                f"[ai-router] {'HTTP ' + str(status) if status else 'ERROR'} while "
                f"streaming from {args.provider}: {detail}\n")
            sys.exit(3 if status else 4)
        try:
            from usage import record as _record_stream
            _record_stream(args.provider, model, 0, 0, 0,
                           latency_ms=int((time.time() - started) * 1000),
                           error="streamed (token usage not reported by SSE)")
        except Exception:  # noqa: BLE001
            pass
        return

    res = None
    resp = None
    if args.json:
        # Raw JSON output implies "show me exactly what this provider returned",
        # so we retry but never switch providers behind the caller's back.
        try:
            resp, used_model, tries, _ = call_with_retry(
                args.provider, args.prompt, args.system, args.temperature,
                effective_max_tokens(args.provider, args.max_tokens), args.top_p,
                args.timeout, args.model, attempts=max(1, args.retries), log=_log)
        except Exception as e:  # noqa: BLE001
            status, _retryable, detail = classify_error(e)
            sys.stderr.write(
                f"[ai-router] {'HTTP ' + str(status) if status else 'ERROR'} from "
                f"{args.provider}: {detail}\n")
            sys.exit(3 if status else 4)
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    else:
        res = call_text(args.provider, args.prompt, args.system, args.temperature,
                        args.max_tokens, args.top_p, args.timeout, args.model,
                        allow_fallback=not args.no_fallback, log=_log,
                        images=args.image)
        if res["error"]:
            status = res["status"]
            sys.stderr.write(
                f"[ai-router] {'HTTP ' + str(status) if status else 'ERROR'} from "
                f"{args.provider}: {res['error']}\n")
            if res["attempted"]:
                sys.stderr.write(f"[ai-router] tried: {res['attempted']}\n")
            try:
                from usage import record as _record_fail
                _record_fail(args.provider, model, 0, 0, 0,
                             latency_ms=int((time.time() - started) * 1000),
                             error=(res["error"] or "")[:300])
            except Exception:  # noqa: BLE001
                pass
            sys.exit(3 if status else 4)
        resp = None
        used_model = res["model"]
        text = res["text"]
        if res["fallback_from"]:
            sys.stderr.write(
                f"[ai-router] primary '{args.provider}' failed; answered by fallback '{res['provider']}'\n")
        print(text)

    # Metering: record this call's token usage locally (best-effort).
    try:
        from usage import record as _record_usage
        u = res["usage"] if not args.json else extract_usage(resp, mode)
        if u:
            _record_usage(res["provider"] if not args.json else args.provider,
                          used_model, u["prompt_tokens"], u["completion_tokens"],
                          u["total_tokens"],
                          latency_ms=int((time.time() - started) * 1000),
                          fallback_from=(res["fallback_from"] if not args.json else None))
        elif not args.json and res.get("empty_retry"):
            _record_usage(args.provider, used_model, 0, 0, 0,
                          latency_ms=int((time.time() - started) * 1000),
                          error="empty body after token expansion")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
