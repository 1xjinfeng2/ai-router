# 免费额度领取清单（2026-11 核实）

本文件的每一条都对应 `PROVIDERS` 里的一个可调用线路。**注册步骤必须你本人完成**
（短信/邮箱验证码），但代码侧已经全部接好：拿到 Key 填进 `.env` 即可用，无需改代码。

最后核实时间：2026-11-05。免费政策变动频繁，以各家控制台当日页面为准。

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
| OpenRouter | 25+ 个 `:free` 模型，20 RPM / 50 次/天（充过 $10 提到 1000 次） | `OPENROUTER_API_KEY` | `openrouter/free`（自动选免费模型） | 免费模型的提示词可能被记录 |
| Google Gemini | Flash 全系免费层，免绑卡，1M 上下文，原生多模态 | `GEMINI_API_KEY` | `gemini-2.5-flash` | **国内需代理**；免费层数据用于改进产品 |
| Mistral | Experiment 计划约 $10/月额度 | `MISTRAL_API_KEY` | `mistral-large-latest` | 默认会用你的数据训练，需手动关闭 |
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

## 六、免费额度使用建议

- **重要任务别用免费层**：免费档通常限速严格，且不保证“这次到底是哪个模型在回答”。
- **免费层的数据条款不同**：Gemini 免费层、Mistral 默认都会用你的输入改进/训练模型，
  涉及患者信息、专家名单、报价这类内容，走 `relay.py` 脱敏，或直接用付费线路。
- **额度会过期**：TokenHub 领取后 1 年、百炼 90–180 天、硅基流动的赠金也有期限，
  先把快到期的用掉。
