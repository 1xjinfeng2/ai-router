# 免费额度领取清单（2026-11 核实）

本文件的每一条都对应 `PROVIDERS` 里的一个可调用线路。**注册步骤必须你本人完成**
（短信/邮箱验证码），但代码侧已经全部接好：拿到 Key 填进 `.env` 即可用，无需改代码。

最后核实时间：2026-11-09（本日复查了 Groq / Mistral / NVIDIA 三条待接线路的免费档模型白名单）。免费政策变动频繁，以各家控制台当日页面为准。

## 一、优先领这三个（国内直连、不绑卡、长期有效）

| 平台 | 免费额度 | 环境变量 | 默认模型 | 领取入口 |
|---|---|---|---|---|
| 智谱 BigModel | GLM-4.7-Flash **永久免费、无总量限制**，200K 上下文；另赠约 2000 万 tokens | `ZHIPU_API_KEY` | `glm-4.7-flash` | https://open.bigmodel.cn → API Keys |
| 腾讯云 TokenHub | 每个模型 100 万 tokens，有效期 1 年，活动至 2026-12-31 | `HUNYUAN_API_KEY` | 已有 | https://console.cloud.tencent.com/tokenhub/models **逐个点领取，可全选** |
| 火山方舟 | 单模型约 50 万 tokens；每日刷新额度 | `ARK_API_KEY` | 已有 | 控制台需**手动开通**模型，不开通调不通 |

> TokenHub 是当前性价比最高的一条：一个 Key 覆盖 126 个模型，**每个模型独立 100 万**。
> 如果你只领了默认那几个，现在就回去把列表全选领一遍 —— 这是本次最大的“白拿”。

## 二、可选（国内直连，但要实名或额度较小）

| 平台 | 免费额度 | 环境变量 | 默认模型 | 备注 |
|---|---|---|---|---|
| 硅基流动 SiliconFlow | 新户约 2000 万 tokens 赠金；部分小模型/embedding/OCR 永久免费 | `SILICONFLOW_API_KEY` | `Qwen/Qwen3-8B` | 2026-05 起需实名 |
| 阿里云百炼 | 每模型 100 万 tokens，合计约 7000 万，90–180 天有效 | `DASHSCOPE_API_KEY` | `qwen3.7-plus` | 仅新户一次性，地域限制以控制台为准 |
| 百度千帆 | 新户每模型 100 万（3 个月） | `QIANFAN_API_KEY` | `ernie-4.0-8k` | 永久免费的 Speed/Lite 版本太旧，不推荐 |

## 三、海外（能连就能用，不绑卡）

| 平台 | 免费额度 | 环境变量 | 默认模型 | 注意 |
|---|---|---|---|---|
| Groq | 30 RPM / 1000 次/天 / 8K TPM（每模型），速度极快 | `GROQ_API_KEY` | `openai/gpt-oss-120b` | 上下文短，长文会撞 TPM 上限 |
| OpenRouter | 12+ 个 `:free` 模型，20 RPM / 50 次/天（充过 $10 提到 1000 次） | `OPENROUTER_API_KEY` | 运行时动态发现（见下） | `openrouter/free` 别名已失效要 credits；免费模型频繁上下架，提示词可能被记录 |
| Google Gemini | Flash 全系免费层，免绑卡，1M 上下文，原生多模态 | `GEMINI_API_KEY` | `gemini-2.5-flash` | **国内需代理**；免费层数据用于改进产品 |
| Mistral | 免绑卡免费层，约 1 req/s；仅开放 Nemo / Ministral / Codestral 等开放权重模型 | `MISTRAL_API_KEY` | `open-mistral-nemo` | `mistral-large/medium/small` 对免费 Key 返回 **403**；免费层数据用于改进产品 |
| NVIDIA NIM | 全目录模型免费试原型，多数 40 RPM | `NVIDIA_API_KEY` | `openai/gpt-oss-120b` | 仅限开发验证用途 |

## 四、明确不接的（省得你白折腾）

| 平台 | 原因 |
|---|---|
| GitHub Models | 2026-07-30 已整体下线，网上旧教程还在推荐 |
| SambaNova | 免费层已取消 |
| Hugging Face | 免费账号已无推理额度，只剩付费 credits |
| 讯飞星火 Spark Lite | 走 WebSocket 协议，不是 OpenAI 兼容，需要单独适配层 |
| Cloudflare Workers AI | 每天 1 万 neurons，但接口是 `/ai/run` 自有格式，同样要适配层 |
| 百度 ERNIE-Speed / Lite | 永久免费，但是 2024 代模型，按你的要求“太久版本不要” |
| 七牛云 Conatus | 每天 1 亿 token，但活动 11-30 截止，临时性质 |
| Kimi K2 系列 | 2026-10-31 对新用户关门，12 月底全退，正在被 K3 取代 |

## 五、拿到 Key 之后的三步

```bash
# 1) 写进 .env（一行一个）
#    ZHIPU_API_KEY=xxxx
#    SILICONFLOW_API_KEY=xxxx

# 2) 验证并列出该账号真实可用的模型 ID
python scripts/check_provider.py --provider glm-flash --scan
python scripts/check_provider.py --provider siliconflow --scan

# 3) 只看免费线路会怎么选（不花钱）
python scripts/ask.py --prompt "帮我润色一段文案" --budget --dry-run
```

`--budget` 会把候选限制在永久免费线路内：任务能用免费模型就用（中文 → `glm-flash`），
用不上就退到任一已配置的免费线路。适合批量、试跑、跑不要紧的活。

## 五之一、三条待接线路的免费档白名单（2026-11-09 核验）

`GROQ_API_KEY` / `MISTRAL_API_KEY` / `NVIDIA_API_KEY` 还没填，代码侧已就绪。
没有 Key 就没法用真实调用验证默认模型，因此本日按厂商文档与免费档实测报告
复核了三条线路的默认 ID，写进 `call_model.FREE_PLAN_MODELS` 并用测试锁住：

| 线路 | 免费 Key 可调用的模型 | 默认 | 踩过的坑 |
|---|---|---|---|
| Groq | `openai/gpt-oss-120b`、`openai/gpt-oss-20b`、`qwen/qwen3.8-27b` | `openai/gpt-oss-120b` | `llama-3.3-70b-versatile` 已于 2026-08-16 退出免费档；免费 SKU 轮换频繁 |
| Mistral | `open-mistral-nemo`、`open-mistral-7b`、`ministral-{3,8,14}b-latest`、`codestral-latest` | `open-mistral-nemo` | **`mistral-large-latest` 对免费 Key 返回 403「not available in your subscription tier」**；`mistral-small/medium` 的每分钟限额是 0 |
| NVIDIA NIM | `openai/gpt-oss-120b`、`nvidia/nemotron-3-super-120b-a12b` 等目录内模型 | `openai/gpt-oss-120b` | 目录频繁变动，网上流传的不少 ID 并不存在；以 `/v1/models` 为准 |

拿到 Key 后先扫描该账号**实际可见**的模型，再决定要不要调整默认值：

```bash
python scripts/check_provider.py --provider groq --scan
python scripts/check_provider.py --provider mistral --scan
python scripts/check_provider.py --provider nvidia --scan
```

> 三条线路目前仅缺 Key。填进 `.env` 后 `judge.configured_providers()` 会自动认到，
> 自动路由随即把它们纳入候选，无需改任何代码。

## 五之二、免费线路的自愈（2026-11-09 起自动）

代码不再把某个 `:free` 模型 ID 写死当默认：`call_model.py` 调用 OpenRouter 时会
`GET /api/v1/models`，筛出 `id` 以 `:free` 结尾且 `pricing.prompt == 0` 的模型，
把结果缓存到 `free_tier_cache.json`（24h）。拉取失败时用 **last-good 快照**，
再失败才用静态种子列表。硬编码 ID 只作兜底种子——避免免费 SKU 悄悄下架后整条线路静默失效。

```bash
python scripts/free_lines.py --discover          # 手动刷新免费模型快照
python scripts/free_lines.py --reset-cooldown    # 清空限流冷却状态
AI_ROUTER_FREE_CACHE=/path/cache.json           # 自定义缓存位置
```

限流（`429` / 智谱 `1302` `1305`）不再盲目指数退避：命中即给该线路打一个 60–300s
的冷却窗口（带抖动），`call_with_retry` 立即让出、`call_text` 换厂商降级，
`ask.route()` 把冷却中的线路排到候选末尾。普通瞬时错误仍走重试，但加了抖动避免齐步重试。

另外**空正文不再算成功**：推理模型只吐 `reasoning_content` 时，先有限提额重试一次，
仍空就记 `error` 并降级。`ask._record()` 现在会把失败写入 `usage.db`，
`usage.health()` / `judge.health_adjusted()` 因此能真正按成功率给坏线路降权
（此前失败被记成成功，健康度一直是失真的）。

## 六、免费额度使用建议

- **重要任务别用免费层**：免费档通常限速严格，且不保证“这次到底是哪个模型在回答”。
- **免费层的数据条款不同**：Gemini 免费层、Mistral 默认都会用你的输入改进/训练模型，
  涉及患者信息、专家名单、报价这类内容，走 `relay.py` 脱敏，或直接用付费线路。
- **额度会过期**：TokenHub 领取后 1 年、百炼 90–180 天、硅基流动的赠金也有期限，
  先把快到期的用掉。
