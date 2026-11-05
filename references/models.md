# 模型与供应商参考 (Model & Provider Reference)

本文件是 AI Router 的权威数据源：列出每个供应商的接入方式、默认模型、代表模型
与能力标签，供 `scripts/call_model.py` 与 `SKILL.md` 的路由表引用。

## 统一接入方式

除 Claude（Anthropic 专用分支）外，所有供应商均兼容 **OpenAI Chat Completions**
格式：

```
POST {base_url}/chat/completions
Header: Authorization: Bearer <API_KEY>
Body:   { "model": "...", "messages": [{"role":"user","content":"..."}] }
```

脚本 `call_model.py` 对上述供应商走同一段逻辑；Claude 走 `messages` 专用分支。

## 供应商清单

| Provider | 中文 | base_url | Key 环境变量 | 默认模型 | 代表模型 |
|---|---|---|---|---|---|
| `doubao` | 豆包（火山方舟） | `https://ark.cn-beijing.volces.com/api/v3` | `ARK_API_KEY` | `doubao-seed-evolving` | doubao-seed-evolving, doubao-seed-2-1-pro-260915, doubao-seed-2-1-lite-260915 |
| `deepseek` | DeepSeek | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` | `deepseek-flash` | deepseek-flash, deepseek-v4-pro |
| `kimi` | Kimi（月之暗面） | `https://api.moonshot.cn/v1` | `MOONSHOT_API_KEY` | `kimi-k3.1-pro` | kimi-k3.1-pro (1M 上下文), kimi-k3.1-pro-highspeed |
| `qwen` | 通义千问（阿里百炼） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` | `qwen3.7-plus` | qwen3.8-max, qwen3.7-plus, qwen3.8-flash；平台也托管 glm-5.3 / kimi-k3 |
| `glm` | 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `ZHIPU_API_KEY` | `glm-5` | glm-5.3, glm-5, glm-4.6；glm-4.7-flash 免费 |
| `hunyuan` | 腾讯混元（**TokenHub**） | `https://tokenhub.tencentmaas.com/v1` | `HUNYUAN_API_KEY` | `hy4-preview` | 120 online 模型；同 Key 可传 `--model glm-5.3` / `kimi-k3.1-pro` / `deepseek-v4-pro-202606` |
| `gemini` | Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `GEMINI_API_KEY` | `gemini-2.5-flash` | gemini-3.8-flash, gemini-2.5-flash, gemini-2.5-pro（均 1M 上下文） |
| `openai` | OpenAI GPT | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `gpt-5.1` | gpt-5.1, gpt-5.2, gpt-5-pro, o3-pro；**gpt-4o 已弃用** |
| `claude` | Anthropic Claude | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` | claude-opus-5-5, claude-sonnet-5-5, claude-sonnet-4-6；**3.5 已退役** |
| `ernie` | 文心一言（百度千帆） | `https://qianfan.baidubce.com/v2` | `QIANFAN_API_KEY` | `ernie-4.0-8k` | 未核实，接入后先跑 `--scan` 或查千帆模型广场 |
| `ark-deepseek` | 方舟托管 DeepSeek-V4 | `https://ark.cn-beijing.volces.com/api/v3` | `ARK_API_KEY` | `deepseek-v4-1-flash-261017` | 与 doubao 共用同一个 Key |
| `ark-kimi` | 方舟托管 Kimi-K3 | `https://ark.cn-beijing.volces.com/api/v3` | `ARK_API_KEY` | `kimi-k3-1-pro-261024` | 1M 上下文 |
| `ark-glm` | 方舟托管 GLM-5.3 | `https://ark.cn-beijing.volces.com/api/v3` | `ARK_API_KEY` | `glm-5-3-flash-260828` | 低成本 |
| `th-code` | TokenHub 托管 Kimi K2.7 Code | `https://tokenhub.tencentmaas.com/v1` | `HUNYUAN_API_KEY` | `kimi-k2.7-code` | 代码专用 ✅ 实测 |
| `th-deepseek` | TokenHub 托管 DeepSeek-V4-Pro | 同上 | `HUNYUAN_API_KEY` | `deepseek-v4-pro-202606` | 推理 / 代码 ✅ 实测 |
| `th-flash` | TokenHub 托管 DeepSeek-V4.1-Flash | 同上 | `HUNYUAN_API_KEY` | `deepseek-v4.1-flash` | 快速 / 性价比 ✅ 实测 |
| `th-kimi` | TokenHub 托管 Kimi-K3.1-Pro | 同上 | `HUNYUAN_API_KEY` | `kimi-k3.1-pro` | 1M 长文；推理模型，需较大 max-tokens |
| `th-glm` | TokenHub 托管 GLM-5.3-Flash | 同上 | `HUNYUAN_API_KEY` | `glm-5.3-flash` | 中文 / 低成本 ✅ 实测 |
| `th-minimax` | TokenHub 托管 MiniMax-M3.1-Flash | 同上 | `HUNYUAN_API_KEY` | `minimax-m3.1-flash` | 长文本 ✅ 实测 |

> 注：文心一言（千帆）现已提供 OpenAI 兼容端点，按 `openai` 模式处理即可；
> `QIANFAN_API_KEY` 可在百度智能云控制台获取。

### 核实状态（2026-11-05）

上表默认模型 ID 已于该日对照各家官方文档更新。真实 Key 实测状态：

| 状态 | 供应商 |
|---|---|
| ✅ 真实调用通过 | doubao, hunyuan, ark-deepseek, ark-kimi, ark-glm, th-code, th-deepseek, th-flash, th-kimi, th-glm, th-minimax |
| ✅ Key 有效但余额 0 | deepseek（402） |
| 📄 仅官方文档核对 | kimi, qwen, glm, gemini, openai, claude |
| ⚠️ 未核实 | ernie |

因此**接入任一家后第一步固定是**：
`python scripts/check_provider.py --provider <name> --scan`
它会调 `/models` 列出账号真实可用的 ID；若默认 ID 报错，用 `--model <ID>` 覆盖
或更新 `call_model.py` 的默认值。模型 ID 换代很快，别把任何一张表当永久真理。

**已废弃 / 易错配置（本次修正）**
- 腾讯混元老端点 `api.hunyuan.cloud.tencent.com` 已于 2026-09 停服，现统一走 TokenHub。
- **TokenHub 国内站点域名是 `tokenhub.tencentmaas.com`（无 `cloud`）**。写成
  `tokenhub.tencentcloudmaas.com` 会返回 `401002`：「API Key 不存在或签名校验失败 …
  请确认 Key 与 Base URL 属于同一站点」。国际站 / 硅谷站的 Key 与国内站不通用。
- `gpt-4o` 在 OpenAI 官方模型页已标记 Deprecated；`claude-3-5-sonnet-*` 已退役；
  `gemini-2.0-flash`、`glm-4-plus`、`moonshot-v1-8k`、`qwen-plus` 均非当前主力。

## 两个聚合平台：一个 Key 覆盖多家模型（2026-11-05 实测）

路由器目前主要靠**两个聚合平台**的 Key 驱动，无需逐家申请官方 Key：

| 平台 | Key | `/models` 返回 | 覆盖 |
|---|---|---|---|
| 火山方舟 | `ARK_API_KEY` | 55 个模型 | 豆包 + DeepSeek + Kimi + GLM |
| 腾讯 TokenHub | `HUNYUAN_API_KEY` | 126 个（120 online） | 混元 Hy4 + DeepSeek + Kimi + GLM + MiniMax + Step + 图像/视频/语音/3D |

### 方舟别名（真实对话调用均通过）

| 别名 | 模型 ID | 说明 |
|---|---|---|
| `ark-deepseek` | `deepseek-v4-1-flash-261017` | ✅ 推理模型，`reasoning_content` 先耗 token |
| `ark-glm` | `glm-5-3-flash-260828` | ✅ |
| `ark-kimi` | `kimi-k3-1-pro-261024` | ✅ 1M 上下文；勿显式传采样参数 |
| — | `doubao-seed-evolving` | ✅ 默认 |
| — | `doubao-seed-2-0-code-preview-260215` | ❌ 404，目录里有但账号无权限 |

### TokenHub 别名（10 个文本模型实测 9 个直接出回答）

| 别名 | 模型 ID | 实测 |
|---|---|---|
| `th-code` | `kimi-k2.7-code` | ✅ |
| `th-deepseek` | `deepseek-v4-pro-202606` | ✅ |
| `th-flash` | `deepseek-v4.1-flash` | ✅ |
| `th-kimi` | `kimi-k3.1-pro` | ⚠️ 推理模型，`--max-tokens` 需 ≥ 1024，否则正文为空 |
| `th-glm` | `glm-5.3-flash` | ✅ |
| `th-minimax` | `minimax-m3.1-flash` | ✅ |
| `hunyuan` | `hy4-preview` / `hy4` / `hy3` | ✅ |
| — | `step-5-preview` | ✅ |

其余 110+ 模型（含 `hy-vision-2.0-instruct` 视觉、`kling-video-*` 视频、
`minimax-speech-*` 语音、`hy-3d-*` 3D）可用
`--provider hunyuan --model <ID>` 直接调用，但未逐一实测。

```bash
python scripts/call_model.py --provider th-code      --prompt "写个快速排序"
python scripts/call_model.py --provider th-kimi      --max-tokens 2048 --prompt "总结这段长文"
python scripts/call_model.py --provider ark-deepseek --prompt "写个快速排序"
python scripts/call_model.py --provider hunyuan --model hy-vision-2.0-instruct --prompt "..."
```

**采样参数注意**：部分新模型（如 `kimi-k3-1-pro`）对 `temperature` / `top_p` 有固定取值
要求，显式传 0.7 / 1.0 会返回 HTTP 400。因此 `call_model.py` **默认不发送**这两个字段，
由服务端采用模型默认值；只有显式传 `--temperature` / `--top-p` 时才带上。

**可用性会变**：模型 ID 带日期后缀且周级迭代，`/models` 列出的也不保证都已开通。
变更前跑 `check_provider.py --provider <name> --scan` 复核。

## 豆包（火山方舟）接入要点

**已验证的官方入口（2026-11-05 实测 HTTP 200）**

| 用途 | 直链 |
|---|---|
| 方舟控制台首页 | https://console.volcengine.com/ark |
| **API Key 管理（创建 Key）** | https://ark.volcengine.com/region:cn-beijing/apiKey |
| 开通管理 | https://console.volcengine.com/ark/region:cn-beijing/openManagement |
| 快速入门文档 | https://ark.volcengine.com/region:cn-beijing/docs/quick-start.md |
| 模型列表文档 | https://ark.volcengine.com/region:cn-beijing/docs/model-list.md |
| 官方模型目录 JSON | https://ark.volcengine.com/models.json |
| 调用 API（数据面 Base URL） | https://ark.cn-beijing.volces.com/api/v3 |

- **控制台 404 怎么办**：上述控制台都是 SPA（单页应用），服务器一律返回 HTTP 200，404 是**前端路由**渲染出来的。遇到「当前页面无法访问」时：① 换用上表直链而不是旧书签/深层链接；② 先登录再访问；③ 在控制台左下角选好项目空间；④ 清缓存或无痕窗口重试。不要因为 404 就以为服务不可用。
- **项目绑定**：API Key 与控制台**左下角的项目空间**绑定，不同项目互不相通；创建 Key、开通模型、调用须在同一项目下。这是 401 的首要原因。
- **API Key 格式**：官方公告 **2026-09-17 12:00 之后创建的 Key 改用新明文格式**，此前创建的仍有效。**不要靠前缀（UUID / `sk-`）判断 Key 属于哪家**，一律以创建它的控制台为准。方舟实测新格式为 `ark-` 前缀。
- **免费额度**：新用户每个模型有 50 万 token 免费额度，**未开通也能先调用**；额度用完后在「开通管理」开通服务即转为按量付费。建议勾选「全选 + 自动开通新增模型」。
- **模型 ID 经常换代（重要）**：2026-11-05 核对官方 `models.json`，当前推荐 `doubao-seed-evolving`（统一 ID，周级迭代，无日期后缀）。官方 curl 示例使用带日期的完整 ID，如 `doubao-seed-2-1-pro-260915`。**不存在 `doubao-seed-evolving-latest-version` 这个 ID**（`latest-version` 只是目录里的 `primaryVersion` 字段值，不是模型 ID）。报「模型不存在」时运行 `python scripts/check_provider.py --provider doubao --scan`。
- **两种 model 写法**：可直接用模型 ID，也可用「推理接入点」ID（`ep-` 开头）；日常用模型 ID 更简单。
- **鉴权**：`ARK_API_KEY` 作为 `Authorization: Bearer`，与 OpenAI SDK 完全兼容。

## 腾讯云 TokenHub 接入要点

- **Key 管理**：https://console.cloud.tencent.com/tokenhub/apikey
- **国内 Base URL**：`https://tokenhub.tencentmaas.com/v1`（广州，**无 `cloud`**）
- 其他站点：`tokenhub-intl.tencentcloudmaas.com/v1`（新加坡）、
  `tokenhub-us.tencentcloudmaas.tech/v1`（硅谷）。**Key 不跨站通用**，站错会返回 `401002`。
- **Token Plan 个人版是另一套**：Base URL `https://tokenhub.tencentmaas.com/plan/v3`，
  Key 格式 `sk-tp-xxx`（每主账号一个）。本技能用的是按量版 TokenHub，两者别混。
- **模型需在控制台先开通服务**，未开通返回 `402`。实测 10 个文本模型 9 个已开通可用。
- 鉴权 `Authorization: Bearer $HUNYUAN_API_KEY`；同时提供 `/v1/responses` 与
  `/v1/messages`（Anthropic 协议，`x-api-key` 头），本技能只用 Chat Completions。
- 错误码：`401002` Key/站点不匹配；`400004` 模型 ID 不存在；`402xxx` 未开通或额度不足；
  `429xxx` 限流。

## 能力标签（用于路由）

| 标签 | 代表供应商 / 模型 |
|---|---|
| `code` 编程 | th-code (kimi-k2.7-code), claude (sonnet-5-5), ark-deepseek, openai (gpt-5.1) |
| `chinese` 中文 | doubao, hunyuan (hy4), th-glm, qwen (qwen3.7-plus), glm (glm-5) |
| `reasoning` 推理 | th-deepseek, openai (gpt-5.1 / o3-pro), ark-deepseek, gemini |
| `long-context` 长上下文 | th-kimi / ark-kimi (1M), gemini (1M), claude (200k) |
| `vision` 视觉 | openai (gpt-5.1), gemini, doubao(视觉), hunyuan (hy-vision-2.0) |
| `cheap` 性价比 | doubao, hunyuan, th-flash, glm-4.7-flash(免费) |
| `structured` 结构化 | openai, qwen, deepseek, th-flash |

## 任务类型 → 推荐模型（权威版）

与 `SKILL.md` 路由表保持一致，供 `judge.py` 使用：

| task_type | 候选顺序（最佳在前；跳过未配置 Key 的供应商） |
|---|---|
| `code` | claude → th-code → ark-deepseek → deepseek → openai |
| `chinese` | doubao → hunyuan → th-glm → qwen → glm |
| `reasoning` | openai → th-deepseek → ark-deepseek → deepseek → gemini |
| `long-context` | gemini → th-kimi → ark-kimi → claude → qwen |
| `vision` | openai → gemini → doubao |
| `structured` | openai → qwen → th-flash → deepseek |
| `cheap` | doubao → hunyuan → th-flash → ark-deepseek → deepseek → glm |
| `search` | （需联网工具，无内置模型） |

> `ark-*` 共用 `ARK_API_KEY`，`th-*` 共用 `HUNYUAN_API_KEY`，两者均已纳入自动路由。
> 这样代码 / 推理 / 长文任务不会落到余额为 0 的 DeepSeek 官方账号。
> 若想恢复原策略，改 `judge.py` 的 `ROUTING` 即可。

## 扩展新供应商

1. 在 `call_model.py` 的 `PROVIDERS` 增加一行：`"name": (base_url, KEY_ENV, default_model, "openai"|"anthropic")`。
2. OpenAI 兼容的无需改逻辑；Anthropic 风格需新增一个 `call_*` 函数与分支。
3. 在 `.env.example` 与本文档补充密钥与模型信息。
4. 更新 `SKILL.md` 的路由表与 `judge.py` 的 `ROUTING` / `KEYWORDS`。
