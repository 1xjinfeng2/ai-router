# 接下来接入哪些 AI（优先级清单）

> 核实日期：2026-11-05。免费额度与申请政策变动频繁，落地前以各平台官网为准。
> 本文回答「在已有多个 Key 的前提下，下一步装哪些最划算」。

## 先算一笔账：现在已接入什么

| 平台 | Key | 实测状态 |
|---|---|---|
| 火山方舟 | `ARK_API_KEY` | ✅ 豆包 + DeepSeek-V4 + Kimi-K3 + GLM-5.3（`/models` 共 55 个） |
| 腾讯云 TokenHub | `HUNYUAN_API_KEY` | ✅ `/models` 共 126 个 / 120 online；10 个文本模型实测 9 个可用 |
| DeepSeek 官方 | `DEEPSEEK_API_KEY` | ⚠️ Key 有效但余额 0，对话调用返回 402 |

**两个聚合平台已经覆盖国内五大模型家族**（豆包 / 混元 Hy4 / DeepSeek-V4 / Kimi-K3 /
GLM-5.3）外加 MiniMax-M3、Step-5、以及大量图像 / 视频 / 语音 / 3D 模型。
再一家家去申请官方独立 Key，基本是重复建设。真正缺的只剩：

1. **海外旗舰推理与代码**（GPT-5.1 / Claude Opus 5.5）——但门槛高，见下文。
2. **实时联网**——这不是模型问题，需要 Web 工具，`judge.py` 的 `search` 类型已标注。

## 优先级

| 顺序 | 装什么 | 成本 / 门槛 | 补上什么 | 状态 |
|---|---|---|---|---|
| **0** | 把 `ark-*` 纳入自动路由 | 0，改配置 | 代码/长文任务不再落到余额 0 的 DeepSeek | ✅ 已完成 |
| **1** | **腾讯云 TokenHub** | 国内直连，新用户免费额度 | 120+ 模型，顺带修好已停服的旧混元端点 | ✅ **已完成（2026-11-05）** |
| **2** | 智谱 GLM 官方 或 硅基流动（二选一） | 新用户赠 2000 万 token，有永久免费模型 | 独立备用线路，避免全押在两家聚合平台 | 有余力再做 |
| **3** | Google Gemini | 免费、免信用卡、Google 账号即可 | 1M 上下文 + 多模态 | 有网络条件再说 |
| — | **OpenAI / Claude** | 境外支付 + 网络 + Anthropic 地区限制 | GPT-5.1 / Claude Opus 5.5 顶级推理与代码 | **暂缓** |

## TokenHub 接入要点（已实测）

- **Key 管理**：https://console.cloud.tencent.com/tokenhub/apikey
- **国内 Base URL**：`https://tokenhub.tencentmaas.com/v1`（**注意没有 `cloud`**）。
  写成 `tokenhub.tencentcloudmaas.com` 会返回
  `401002`「API Key 不存在或签名校验失败 … 请确认 Key 与 Base URL 属于同一站点」。
  这个域名坑是本次接入实测踩到的，技能里已修正。
- 其他站点：`tokenhub-intl.tencentcloudmaas.com/v1`（新加坡）、
  `tokenhub-us.tencentcloudmaas.tech/v1`（硅谷）。**Key 不跨站通用**。
- **Token Plan 个人版是另一套**：`https://tokenhub.tencentmaas.com/plan/v3`，
  Key 格式 `sk-tp-xxx`；本技能用按量版 TokenHub，两者别混。
- 鉴权 `Authorization: Bearer <KEY>`，OpenAI 兼容；
  模型需在控制台**先开通服务**，未开通返回 402。
- 一个 Key 可用 `--model <ID>` 调平台上任意模型；技能内已固化 6 个实测别名：

| 别名 | 模型 ID | 定位 |
|---|---|---|
| `th-code` | `kimi-k2.7-code` | 代码专用 |
| `th-deepseek` | `deepseek-v4-pro-202606` | 推理 / 代码 |
| `th-flash` | `deepseek-v4.1-flash` | 快速 / 性价比 |
| `th-kimi` | `kimi-k3.1-pro` | 1M 长文（推理模型，max-tokens 需 ≥ 1024） |
| `th-glm` | `glm-5.3-flash` | 中文 / 低成本 |
| `th-minimax` | `minimax-m3.1-flash` | 长文本 |

## 为什么暂缓 OpenAI / Claude

1. 国内直连不通，注册还需境外信用卡 + 境外账单地址（Stripe 会校验卡段 / IP / 账单地址）。
2. Anthropic 官方文档站在部分地区直接返回 `app-unavailable-in-region`。
3. 第三方中转站能用微信/支付宝，但引入额外资金与数据合规风险，对一个打算**开源**的项目不划算。
4. 当前能力缺口已被方舟 + TokenHub 的 DeepSeek-V4 / GLM-5.3 / Kimi-K3 覆盖大半。

等出现「必须用 GPT-5.1 / Claude Opus 5.5 才能做好」的具体场景再接，届时只加一行 `PROVIDERS` 配置。

## 不建议单独申请的

Kimi、通义千问、文心一言、智谱的**官方独立 Key**：这些模型方舟 / 百炼 / TokenHub
都已托管，重复申请只会多几个要维护的 Key。除非某家给了明显更优的价格或独占模型，
或你需要一条不依赖聚合平台的备用线路（对应优先级 2）。

## 每接一家的固定三步

```bash
# 1) 在技能根目录 .env 填入对应的 KEY

# 2) 先自检（列出账号真实可用模型 ID，避免撞上过期型号 / 站点错误）
python scripts/check_provider.py --provider <name> --scan

# 3) 真实调用
python scripts/call_model.py --provider <name> --prompt "用一句话介绍你自己"
```

若第 2 步报「模型不存在」，用 `--model <ID>` 覆盖或更新 `call_model.py` 的默认模型。
**2026-11-05 已把 10 家供应商里 8 家过时的默认模型 ID 全量更新**，但除豆包 / DeepSeek /
`ark-*` / `hunyuan` / `th-*` 外均未经真实 Key 实测，仍建议走这三步。

## 免费额度参考（需以官网复核）

| 平台 | 额度 | 备注 |
|---|---|---|
| 火山方舟 | 每模型 50 万 token，另有每日额度 | 未开通也能先试；**已接入** |
| 腾讯 TokenHub | 新用户每模型约 100 万 token，混元 Lite 长期免费 | 需实名 + 开通模型；**已接入** |
| 智谱 BigModel | 新用户 2000 万 token；`glm-4.7-flash` 等免费 | 免费模型有限速 |
| 硅基流动 | 新用户约 2000 万 token，部分小模型长期免费 | 聚合平台，与 TokenHub 二选一 |
| Google AI Studio | 免费层，无需信用卡，约 10 RPM / 1500 RPD | **免费层输入可能被用于改进产品，勿传敏感数据**；国内需解决网络 |
| DeepSeek 官方 | 无免费额度，需充值 | 当前 Key 余额 0（402） |
