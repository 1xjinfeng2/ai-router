# ROADMAP · ai-router 优化方向（9 家 AI 交叉评审汇总）

> 评审方式：将项目现状简报发给全部已配置模型，各自独立输出「优化点 + 下一步方向」表，再人工汇总。
> 参与模型：ark-deepseek / ark-glm / ark-kimi / doubao / th-code / th-deepseek / th-flash / th-kimi / th-minimax（9 份有效评审）。
> 未参评：deepseek（余额 0，402）、hunyuan 与 th-glm（多次尝试正文为空——这本身就是待修复问题的现场证据）。

## 一、各模型核心观点速览

| 模型 | 最优先建议 | 独有洞见 |
|---|---|---|
| ark-deepseek | 隐私库/usage.db 安全治理，先清理后加密 | 增加失败与质量报表，定位故障 provider |
| ark-glm | 失败重试+降级；隐私库加密（口令派生密钥，可选依赖优雅降级） | 失败数据反哺路由：usage.db 做滑动窗口健康度，judge 选型自动降权；测试首要守 PII 往返一致性 |
| ark-kimi | 敏感数据最小留存 + TTL 清理，密钥外置保持核心零依赖 | 重试/降级/裁判调用全部纳入 usage 计费，防止"修可靠性反而涨成本" |
| doubao | 字段级加密 + 过期清理；指数退避重试 | 流式场景需适配"占位符流式还原"逻辑；打分投票复用裁判模块替代单一汇总 |
| th-code | 加密+TTL；主备模型队列重试 | 推理模型按模型标识自动调整 max_tokens 与超时 |
| th-deepseek | 加密+合规；重试+错误分类降级 | Web UI 待可靠性稳定后再做，复用现有脚本 |
| th-flash | 重试+超时+备用链；空正文自动扩容重试 | 先覆盖 judge/privacy/usage 关键路径再接 CI |
| th-kimi | 数据保护、故障降级、测试先行 | 空正文只允许有限次提额重试，并统计追加费用 |
| th-minimax | 可靠性、隐私治理、回归保障优先于交互扩展 | 流式先在单模型层打通，隐私中转汇总链路后接 |

## 二、交叉共识（按票数与优先级排序）

| # | 方向 | 高优先票 | 中/低票 | 结论 |
|---|---|---|---|---|
| 1 | **失败重试 + 跨模型自动降级** | 9/9 | 0 | 全票高优先。本次评审自身就撞上了：11 路并发触发限流超时、ark-kimi 服务端 500、deepseek 402、hunyuan/th-glm 空正文——没有降级链导致 5/12 路直接报废 |
| 2 | **隐私库与 usage.db 安全治理（TTL 过期清理 + 加密）** | 9/9 | 0 | 全票高优先。共识路径：先做最小留存/TTL/定期清理（成本低），再评估加密（多模型提醒与零依赖原则冲突，建议口令派生密钥 + 可选依赖优雅降级） |
| 3 | **单元测试 + CI（GitHub Actions）** | 4 | 5 | 全部至少"中"。首要覆盖：脱敏↔还原往返一致性、路由、重试降级、打包密钥扫描 |
| 4 | **推理模型 max_tokens 自适应** | 3 | 4 | 共识方案：按模型建档维护 token 下限；空正文时**有限次**提额重试一次，仍失败再降级，重试费用记入 usage |
| 5 | **流式输出（SSE）** | 0 | 9 | 一致"中"，且多模型给出相同顺序：先在 call_model 单模型层打通，隐私中转的并发汇总/还原链路后接；doubao 提醒占位符流式还原的特殊处理 |
| 6 | **多模型打分投票选优** | 0 | 8 | 一致"中"，做成可选模式（默认仍汇总以控成本），复用 judge 做评分器，展示理由与新增费用 |
| 7 | **用量观测反哺路由** | 0 | 3 | 在 usage.db 补延迟/错误类型/降级路径/缓存命中字段，验证指标稳定后再让 judge 参考健康度降权 |
| 8 | **Web UI** | 0 | 0（均"低/暂缓"） | 9/9 一致后置；确有需求时先做只读费用报表页，勿动核心链路 |

## 三、执行路线图

### Phase 1 · 可靠性与安全（已完成 ✅）
1. ✅ `call_model.py` 统一重试层：错误分类（429/5xx 退避重试，401/402/403/404 立即降级）、`FALLBACK_CHAINS` 按供应商交叉备用链、`call_text()` 一次性返回降级路径
2. ✅ 推理模型 token 档案：`REASONING_FLOOR`（th-deepseek/th-kimi/hunyuan 等 4096 起），空正文自动翻倍重试一次（上限 8192），仍空则进入降级
3. ✅ 隐私库/usage.db 生命周期：`AI_ROUTER_VAULT_TTL_DAYS`（默认 7 天，脱敏时自动清扫）、`AI_ROUTER_USAGE_TTL_DAYS`、`--expire N`、`privacy.py --status`
4. ✅ 可选加密：`cryptography` + `AI_ROUTER_VAULT_PASSPHRASE` → PBKDF2(200k)+Fernet，`enc:v1:` 前缀，`--encrypt/--decrypt`；无依赖时优雅降级并明确提示

### Phase 2 · 质量保障（已完成 ✅）
5. ✅ 单测：`tests/` 138 项标准库 unittest，覆盖脱敏↔还原（含跨 chunk 流式还原）、TTL、加密往返、错误分类、退避重试、跨模型降级、token 扩容、缓存键敏感性、计量聚合、健康度、打包排除与密钥扫描
6. ✅ GitHub Actions CI：Python 3.10 / 3.13 双版本（runner 固定 `ubuntu-24.04`），编译 + 单测 + `package.py --check-only` + 脱敏冒烟

### Phase 3 · 体验增强（已完成 ✅）
7. ✅ 流式输出：`call_model.py --stream`（OpenAI/Anthropic 双 SSE 分支）、`relay.py --stream --no-organize`；`privacy.StreamRestorer` 处理跨 chunk 占位符
8. ✅ 打分投票：`relay.py --vote`（仅 ≥2 路时启用，裁判模型按 1-10 打分并返回最优，评分表打到 stderr，裁判调用计费入 usage）
9. ✅ 观测量化与反哺：usage 新增 `latency_ms/error/fallback_from` 字段、`--health` 成功率报表、`judge.health_adjusted()` 把成功率 <60% 且调用 ≥3 次的供应商降权

### Phase 4 · 自动化（已完成 ✅）
10. ✅ 自我更新：`scripts/update.py --check/--apply`，版本比对（git / HTTPS 降级）、脏工作区拦截（退出码 3）、快进拉取、拉后跑全量测试、失败 `git reset --hard` 回滚（退出码 4）、`--zip` 顺便重打包
11. ✅ 每周自动迭代：定时任务在无人值守下按「挑一个 ROADMAP 开放项 → 改代码 + 补测试 → 全绿才提交推送 → 写记录」循环，红则不留痕

12. ✅ HTTP 服务：`scripts/server.py` 提供 `/ask` `/route` `/health` `/providers` `/usage`，bearer 鉴权（非回环无 token 拒绝启动）、按 IP 限流、体积上限、提示词不入日志；客户端设 `AI_ROUTER_SERVER` 即改为远端调用
13. ✅ 自建模型服务：`--provider custom` 接 vLLM / Ollama / one-api 网关，端点与模型由环境变量给定，`AI_ROUTER_CUSTOM_ROUTES` 控制它接哪些任务（默认仅 `cheap`），失败降级 `th-flash → doubao`；部署手册见 `references/deploy.md`
14. ✅ 加密引导：首次真正落库 PII 时在 stderr 打一行可照抄的开启命令（未装 `cryptography` 时先提示安装），随后在库旁写 `<vault>.enc-hint` 标记不再重复；`--guide` 随时重看，`AI_ROUTER_NO_TIPS=1` 静音。刻意不做 TTY 判断——主要调用方是 agent，捕获到的 stderr 正是把这行送到人眼前的方式

### 仍开放（按价值排序）
- [ ] HTTP 服务的 SSE 流式与异步任务（当前 /ask 同步返回）
- [ ] 投票支持加权融合（现在只选最优，不做加权拼接）
- [ ] 流式接入汇总链路（当前 organizer 需完整文本）
- [ ] Ollama 本地模型接入（评审未提及，待以上稳定后评估）
- [ ] Web UI（9/9 一致后置）

## 四、自动迭代记录

格式：`YYYY-MM-DD | 提交 | 改了什么 | 测试数 | 未做`

- 2026-11-05 | 92f50bb | 新增自我更新（版本比对/脏工作区拦截/测试门+回滚），CI 矩阵 3.9/3.12 → 3.10/3.13 | 94 | 加密引导、投票加权、汇总流式
- 2026-11-05 | 8390076 | 新增 HTTP 服务（server.py，鉴权/限流/体积上限）与自建模型 provider `custom`；ask.py 支持远端模式 | 114 | 服务端 SSE 流式、异步任务
- 2026-11-09 | e1ad00e | 首次落库 PII 时一次性打印加密引导（`encryption_hint`/`maybe_hint_encryption`＋`--guide`，`AI_ROUTER_NO_TIPS=1` 静音，vault 旁 `.enc-hint` 标记；刻意不做 TTY 判断，让 agent 捕获的 stderr 能把提示带到人眼前），`.enc-hint` 同步排除出 git 与打包，版本 1.6.2→1.6.3；顺手把 `.workbuddy/`（agent 本地记忆）也排除出 git 与 zip，避免下次 `git add -A` 把内部笔记推上公开仓库 | 138 | 投票加权融合、汇总链路流式、服务端 SSE/异步、Ollama 接入
