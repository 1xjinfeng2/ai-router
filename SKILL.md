---
name: ai-router
display_name: "AI 路由器 / AI Router"
description: 'Acts as a judge that classifies every incoming question by task type and dispatches it to the single most capable AI model for that job, drawing on Doubao (火山方舟), DeepSeek, Kimi (Moonshot), Qwen (通义千问), GLM (智谱), Hunyuan (腾讯混元), Gemini (Google), OpenAI (GPT) and Claude (Anthropic). It can also act as a privacy relay: redact PII into local-vault placeholders, fan the sanitized prompt out to multiple AIs, merge their answers, then restore the real values for the user. Use this skill when the user wants the "best" or "smartest" AI to answer, asks to compare or vote across models, or explicitly names a model/provider (e.g. 豆包, GPT, Claude, Kimi, 通义, DeepSeek, 智谱, 文心), or wants a privacy-preserving middleman that strips personal data before calling any AI. 触发词：AI路由, 最强AI, 路由AI, 多模型, 模型投票, 问豆包, 问GPT, 用最好的AI, ai-router, best AI, route this, which AI, 哪个AI最好, 集成回答, 让多个AI, 隐私中转, 隐私脱敏, PII脱敏, 中转站, 脱敏后调用AI, 多AI汇总, privacy relay.'
description_zh: "把每个问题按任务类型自动分类，派发给当前最强的大模型（豆包/DeepSeek/Kimi/通义/智谱/混元/Gemini/GPT/Claude），并支持多模型对比与投票。"
description_en: "Classify each question by task type and route it to the single best-fit model, with optional multi-model comparison and voting."
category: productivity
version: 1.1.0
author: "小许 / Angela Yan"
title: "AI 路由器 / AI Router"
runtime: generic
tags:
  - "AI路由"
  - "ai-router"
  - "多模型"
  - "豆包"
  - "GPT"
  - "Claude"
  - "DeepSeek"
  - "Kimi"
  - "通义千问"
  - "大模型路由"
dependencies: []
agent_created: true
---

# AI Router / AI 路由器

## Overview

This skill turns WorkBuddy into an intelligent router across multiple frontier
LLMs. For every incoming question it (1) **judges** the task type, (2) **routes**
to the single best-fit model from the connected providers, and (3) **calls** that
model through a unified OpenAI-compatible script, returning the answer tagged with
which model produced it. It also supports multi-model comparison and voting.

All providers except Claude share the OpenAI Chat Completions format, so a single
caller (`scripts/call_model.py`) covers Doubao, DeepSeek, Kimi, Qwen, GLM,
Hunyuan, Gemini and OpenAI. Claude uses a dedicated Anthropic branch.

Two aggregator keys do most of the work: `ARK_API_KEY` (Volcengine Ark, 55 models)
and `HUNYUAN_API_KEY` (Tencent TokenHub, 126 models / 120 online). Together they
cover Doubao, Hunyuan, DeepSeek, Kimi, GLM, MiniMax and Step without applying for
each vendor's own key.

## When to Use

| Request | Action |
|---|---|
| "用最好的AI帮我…" / "路由这个问题" / "把这个问题交给最厉害的模型" | Judge → route → call |
| "问豆包/GPT/Claude/Kimi/通义/DeepSeek…" | Use named provider directly |
| "哪个AI最擅长写文案/写代码？" | Explain routing, then route |
| "让多个AI都回答并对比" / "模型投票" / "集成回答" | Multi-model compare & vote |
| Any generic question where answer quality matters | Default to judge-then-route |

## How It Works — Judge → Route → Call → Return

1. **Judge** — classify the prompt into a task type: `code`, `chinese`,
   `reasoning`, `long-context`, `vision`, `structured`, `cheap`, `search`.
   Prefer `scripts/judge.py --prompt "..."` for an automatic recommendation, or
   classify inline using the table below.
2. **Route** — look up the task type in the Expert Routing Table to get the
   ranked provider list; pick the top provider whose API key is actually set in
   the environment. Skip providers with missing keys and fall through.
3. **Call** — run
   `scripts/call_model.py --provider <p> --model <m> --prompt "..."`.
4. **Return** — paste the model's answer, prefixed with a one-line attribution,
   e.g. `> 🤖 本次由 TokenHub (kimi-k2.7-code) 完成`.

If the user names a specific provider/model, skip judging and call it directly.

## Expert Routing Table (default ranking)

Full provider/model details live in `references/models.md`. Default order:

| Task type | 候选顺序（最佳在前；跳过未配置 Key 的供应商） |
|---|---|
| `code` (编程/调试/重构) | claude → th-code → ark-deepseek → deepseek → openai |
| `chinese` (中文文案/翻译/润色) | doubao → hunyuan → th-glm → qwen → glm |
| `reasoning` (推理/数学/逻辑) | openai (gpt-5.1) → th-deepseek → ark-deepseek → deepseek → gemini |
| `long-context` (长文档/论文/总结) | gemini → th-kimi → ark-kimi → claude → qwen |
| `vision` (图像/截图/视觉) | openai (gpt-5.1) → gemini → doubao |
| `structured` (JSON/提取/表格) | openai → qwen → th-flash → deepseek |
| `cheap` (日常/闲聊/快速) | doubao → hunyuan → th-flash → ark-deepseek → deepseek → glm |
| `search` (实时联网/最新) | (needs a web tool) — |

Always prefer a provider whose key is set; never attempt a call with a missing
key. For `search`, use WorkBuddy's built-in web tools and then optionally have a
model synthesize the findings.

## Using the Call Script

```bash
python scripts/call_model.py --provider th-code \
  --prompt "用Python写一个快速排序并加注释"
```

Parameters:
- `--provider` (required): `doubao | deepseek | kimi | qwen | glm | hunyuan | gemini | openai | claude | ernie | ark-deepseek | ark-kimi | ark-glm | th-code | th-deepseek | th-flash | th-kimi | th-glm | th-minimax`
- `--model` (optional): overrides the provider default; any Ark or TokenHub model ID works with `--provider doubao` / `--provider hunyuan`
- `--prompt` (required): the user message
- `--system` (optional): a system prompt
- `--temperature` / `--top-p` (both **omitted by default**): several newer models
  (e.g. `kimi-k3-1-pro`) reject explicit sampling params unless they equal a fixed
  value. Only pass them when the user asks; otherwise the model's own defaults apply.
- `--max-tokens` (default `2048`)
- `--json`: print the full raw API response (debugging)
- `--timeout` (default `60`s)

## Configuration

Copy `.env.example` to `.env` and fill in the keys for the providers to enable.
The script reads keys from environment variables and loads `.env` automatically
via the bundled stdlib-only loader (`scripts/env_loader.py`) — no
`python-dotenv` needed. Providers with no key are skipped during routing.

Before enabling a new provider, verify it with
`scripts/check_provider.py --provider <name> --scan`. This reports whether the
key is valid, whether the console project/site matches, and lists the model IDs
actually available to the account.

### Volcengine Ark (Doubao) notes

- **Official direct links** (console is a SPA; a "404 / 当前页面无法访问" screen is
  usually a front-end route issue, not a dead site — retest with these links after
  logging in and selecting the project space at the bottom-left):
  - Console home: `https://console.volcengine.com/ark`
  - API Key management: `https://ark.volcengine.com/region:cn-beijing/apiKey`
  - Model activation: `https://console.volcengine.com/ark/region:cn-beijing/openManagement`
  - Model list docs: `https://ark.volcengine.com/region:cn-beijing/docs/model-list.md`
  - Public model catalog JSON: `https://ark.volcengine.com/models.json`
- **Model IDs change weekly.** The recommended ID is `doubao-seed-evolving`
  (unified, no date suffix); official examples use date-suffixed IDs such as
  `doubao-seed-2-1-pro-260915`. There is NO model called
  `doubao-seed-evolving-latest-version`. On "model not found", run
  `check_provider.py --provider doubao --scan`, which fetches the public catalog.
- **API key plaintext format changed** on 2026-09-17. Do not infer the provider
  from the key prefix (`sk-` vs UUID vs `ark-`); trust the console where it was created.
- Data-plane base URL: `https://ark.cn-beijing.volces.com/api/v3`,
  header `Authorization: Bearer $ARK_API_KEY`.

### Tencent TokenHub notes

- **CN Base URL is `https://tokenhub.tencentmaas.com/v1` — there is NO "cloud"**
  in the hostname. Using `tokenhub.tencentcloudmaas.com` returns `401002`
  "API Key does not exist or signature verification failed ... confirm that the
  Key and Base URL belong to the same site". Keys are NOT interchangeable
  between the CN, Singapore (`tokenhub-intl.tencentcloudmaas.com/v1`) and
  US (`tokenhub-us.tencentcloudmaas.tech/v1`) sites.
- Token Plan (personal subscription) is a separate product with Base URL
  `https://tokenhub.tencentmaas.com/plan/v3` and `sk-tp-xxx` keys.
- **Activate each model service in the console first**, otherwise calls return 402.
- Error codes: `401002` key/site mismatch, `400004` unknown model,
  `402xxx` not activated / quota exhausted, `429xxx` rate limited.

### Aggregator aliases (verified 2026-11-05)

`ark-deepseek`, `ark-kimi`, `ark-glm` reuse `ARK_API_KEY` (55 models on Ark).
`th-code`, `th-deepseek`, `th-flash`, `th-kimi`, `th-glm`, `th-minimax` reuse
`HUNYUAN_API_KEY` (126 models on TokenHub). All of them are in the automatic
routing table (`judge.py` ROUTING) so code / reasoning / long-text tasks no
longer fall through to the unpaid official DeepSeek account; they can also be
selected explicitly. Several are reasoning models (they emit `reasoning_content`
first), so keep `--max-tokens` generous — a very small value yields empty output.
Run `check_provider.py --provider <name> --scan` to list all available IDs.

```
ARK_API_KEY=...        # 豆包 / 火山方舟（+ ark-* 别名）
HUNYUAN_API_KEY=...    # 腾讯 TokenHub（+ th-* 别名）
DEEPSEEK_API_KEY=...
MOONSHOT_API_KEY=...   # Kimi / 月之暗面
DASHSCOPE_API_KEY=...  # 通义千问 (阿里 DashScope)
ZHIPU_API_KEY=...      # 智谱 GLM (BigModel)
GEMINI_API_KEY=...     # Google Gemini
OPENAI_API_KEY=...     # OpenAI GPT
ANTHROPIC_API_KEY=...  # Claude (Anthropic)
QIANFAN_API_KEY=...    # 文心一言 (百度千帆, 可选)
```

## Multi-Model Compare & Vote

When the user wants several models' views:
1. Pick 2–4 configured providers (or let `judge.py` return the top candidates).
2. Call `call_model.py` once per provider/model (can be parallel Bash calls).
3. Present each answer under its own attribution header, then add a short synthesis.

Prefer the built-in paths instead of hand-rolling the loop:

```bash
# score every answer with a judge model and return the winner (+ score table)
python scripts/relay.py --prompt "..." --providers th-flash,doubao,th-glm --vote

# stream a single answer live (needs --no-organize; cache is bypassed)
python scripts/relay.py --prompt "..." --providers th-flash --no-organize --stream
python scripts/call_model.py --provider doubao --prompt "..." --stream
```

## Reliability: Retry & Cross-Model Fallback

`call_model.py` classifies every failure instead of blindly retrying:

| Failure | Examples | Behaviour |
|---|---|---|
| Transient | 429, 5xx, read timeout | exponential backoff, 3 tries (1s→2s→4s) |
| Fatal | 401, 402, 403, 404 | no retry, immediately switch to a backup model |
| Empty body | reasoning model burned tokens on thinking | one retry with doubled `max_tokens` (cap 8192) |

`FALLBACK_CHAINS` maps each provider to 1–2 backups from a different vendor
(`doubao → ark-deepseek → th-flash`, `claude → openai → th-deepseek`, …).
`REASONING_FLOOR` raises `max_tokens` to a safe minimum for reasoning models
(`th-deepseek`, `th-kimi`, `hunyuan` → 4096). Every retry/fallback is recorded in
`usage.db` (`fallback_from`, `latency_ms`, `error`).

```bash
python scripts/call_model.py --provider deepseek --prompt "..."   # 402 -> th-flash answers
python scripts/call_model.py --provider doubao --prompt "..." --no-fallback
python scripts/call_model.py --provider doubao --prompt "..." --retries 5
```

## Judge Script

```bash
python scripts/judge.py --prompt "帮我写一封英文商务邮件"
# add --use-llm to let a small model classify instead of keyword rules
```

Outputs JSON: `{task_type, reason, recommended:{provider,model}, ranked_providers, configured_providers}`.

## Adding a New Provider

1. Add an entry to `PROVIDERS` in `scripts/call_model.py` (base_url + key env + default model).
2. OpenAI-compatible providers need no extra code; for non-compatible ones (e.g. Anthropic) add a dedicated branch.
3. Add the key to `.env.example` and to `references/models.md`.
4. Update the Expert Routing Table above and `ROUTING` / key map in `judge.py`.

## Privacy Relay / 隐私中转站

You (WorkBuddy) act as the middleman. When a prompt contains personal data
(names, phones, emails, ID numbers, cards, addresses…), the relay stores the real
values in a **local SQLite vault** (`privacy_vault.db`, git-ignored, never packaged),
replaces them with opaque placeholders, sends only the sanitized text to the AIs,
then merges their answers and restores the real values for the human who owns them.
External AIs **never** see raw PII.

```bash
# Auto-route to the top-2 models, polish the merged answer, restore PII for you
python scripts/relay.py --prompt "帮客户张伟(电话13800138000)写封跟进邮件"

# Fan out to explicit providers; skip the polish pass
python scripts/relay.py --prompt "..." --providers doubao,th-flash --no-organize

# Run every configured provider, then merge
python scripts/relay.py --prompt "..." --all

# Heuristic name/address redaction (opt-in; default set is high-confidence only)
python scripts/relay.py --prompt "..." --strict

# Continue the same confidential thread (reuse the vault session)
python scripts/relay.py --prompt "..." --session <session_id>
```

Pipeline: `redact → fan-out(N providers) → organize(1 model) → restore → output`.
The organizer is instructed to keep every placeholder verbatim, so restore is exact.

Vault management (all local, owner-only):

```bash
python scripts/privacy.py --list                 # list sessions + redaction counts
python scripts/privacy.py --export --session <id> # audit a session's mappings
python scripts/privacy.py --clear  --session <id> # delete one session
python scripts/privacy.py --clear-all             # wipe the whole vault
python scripts/privacy.py --status                # rows / TTL / encryption state
python scripts/privacy.py --expire 3              # drop mappings older than 3 days
```

Retention and optional encryption (core stays dependency-free):

- `AI_ROUTER_VAULT_TTL_DAYS` (default **7**) — old mappings are swept on every redact.
- `AI_ROUTER_USAGE_TTL_DAYS` (default 0 = off) — usage/cache rows swept at relay start.
- Encryption is opt-in: `pip install cryptography`, export `AI_ROUTER_VAULT_PASSPHRASE`,
  then `privacy.py --encrypt` (rows become `enc:v1:` ciphertext, PBKDF2-derived Fernet key).
  Without the library or passphrase it degrades gracefully with a warning — never crashes.

Detection levels: **default** redacts email, CN mobile, 18-digit ID, 16–19 digit
bank card, IPv4, QQ, plus any `--custom` terms. **`--strict`** adds heuristic
name/address matching (may over-redact). Placeholders look like `PII_EMAIL_1`;
they are stored only in the local vault and excluded from git and from the zip.

## Resources

- `scripts/call_model.py` — unified OpenAI-compatible caller: retry/backoff, cross-model fallback, reasoning token floors, SSE streaming
- `scripts/judge.py` — task classifier, model recommender, health-based demotion
- `scripts/check_provider.py` — provider diagnostics and available-model discovery (`--scan`)
- `scripts/env_loader.py` — stdlib-only `.env` loader (zero dependencies)
- `scripts/privacy.py` — local PII vault: redact / restore / TTL / optional encryption / stream restore
- `scripts/relay.py` — privacy relay orchestrator: redact → fan-out → organize or vote → restore
- `scripts/usage.py` — local usage metering + response cache + provider health report (`--report`, `--health`, `--expire`)
- `tests/` — 52 stdlib `unittest` cases (privacy, retry/fallback, cache, routing, packaging)
- `.github/workflows/ci.yml` — CI: compile + unit tests + packaging guard on Python 3.9/3.12
- `references/models.md` — full provider list, endpoints, model catalogs, capability tags, Ark/TokenHub onboarding notes, verification status
- `references/onboarding.md` — which providers to add next, priority order, free-tier comparison
- `.env.example` — API key template with sign-up URLs per provider

## Verify Before Shipping

```bash
python -m unittest discover -s tests -v   # 52 tests, stdlib only
python -m compileall -q scripts tests
python scripts/package.py --check-only    # structure + secret scan
```
- `README.md` — install, configure, use, contribute (open-source)
