# AI Router · 多模型智能路由

> 把每个问题按任务类型自动分类，派发给当前最强的大模型（豆包 / DeepSeek / Kimi / 通义千问 / 智谱 GLM / 腾讯混元 / Gemini / GPT / Claude），并支持多模型对比与投票。

这是一个 **WorkBuddy 技能（Skill）**，定位为「AI 聚合路由层」：你只管提问，它充当裁判（Judge）判断任务类型，再把任务交给最合适的大模型完成。

## 特性

- 🧭 **裁判模型动态路由**：按任务类型（编程 / 中文 / 推理 / 长文 / 视觉 / 结构化 / 性价比 / 联网）自动选最优模型
- 🔌 **统一接入**：豆包、DeepSeek、Kimi、通义、智谱、混元、Gemini、OpenAI、Claude、文心一言，一套脚本全部打通
- 🔑 **两个聚合平台驱动**：火山方舟 + 腾讯云 TokenHub，两个 Key 覆盖 180+ 模型，无需逐家申请官方 Key
- 🪶 **零依赖**：调用脚本仅用 Python 标准库，无需 `pip install`
- 🔍 **接入自检**：一条命令判断 Key 是否有效、站点/项目是否匹配、模型 ID 是否过期
- 🗳️ **多模型对比 / 投票**：一键让多个模型同时回答并汇总
- 🔧 **易扩展**：加一个新供应商只需改几行配置

## 支持的供应商

| 供应商 | 默认模型 | 擅长 |
|---|---|---|
| 豆包（火山方舟） | doubao-seed-evolving | 中文、性价比 ✅ 已实测 |
| DeepSeek | deepseek-flash | 代码、推理（余额 0，待充值） |
| Kimi（月之暗面） | kimi-k3.1-pro | 长文本 / 1M 上下文 |
| 通义千问 | qwen3.7-plus | 中文、结构化 |
| 智谱 GLM | glm-5 | 中文、代码 |
| 腾讯混元（TokenHub） | hy4-preview | 一个 Key 覆盖 120+ 在线模型 ✅ 已实测 |
| Gemini | gemini-2.5-flash | 长上下文、视觉 |
| OpenAI | gpt-5.1 | 综合、推理 |
| Claude | claude-sonnet-5-5 | 编程、长文 |
| 文心一言（可选） | ernie-4.0-8k | 中文（未核实） |

> 2026-11-05 已按各家官方文档全量更新默认模型 ID（旧值 `gpt-4o` /
> `claude-3-5-sonnet` / `gemini-2.0-flash` / `glm-4-plus` / `moonshot-v1-8k` /
> `qwen-plus` / `hunyuan-pro` 均已过时）。腾讯混元老端点 2026-09 停服，现走 TokenHub。
> 详见 [`references/models.md`](references/models.md) 的「核实状态」。

详细端点与模型清单见 [`references/models.md`](references/models.md)。

**两个聚合平台的别名**（共用各自的 Key，无需单独申请）：

| 平台 | 别名 | 数量 |
|---|---|---|
| 火山方舟 `ARK_API_KEY` | `ark-deepseek` / `ark-kimi` / `ark-glm` | 55 模型 |
| 腾讯云 TokenHub `HUNYUAN_API_KEY` | `th-code` / `th-deepseek` / `th-flash` / `th-kimi` / `th-glm` / `th-minimax` | 126 模型 |

这些别名**均已纳入自动路由**。详见[「进阶：两个聚合平台」](#进阶两个聚合平台一个-key-覆盖多家模型)。

## 安装（作为 WorkBuddy 技能）

技能目录即本仓库根目录。将其放入：

- **用户级**（跨所有项目）：`~/.workbuddy/skills/ai-router/`
- **项目级**（团队共享）：`<项目>/.workbuddy/skills/ai-router/`

放入后，在 WorkBuddy 对话中直接说「用最好的 AI 帮我…」「路由这个问题」「问豆包…」即可触发。

## 配置

1. 复制配置模板并填入密钥：

   ```bash
   cp .env.example .env
   # 编辑 .env，填入你启用的供应商 API Key
   ```

2. 也可直接在 shell 中 `export` 对应环境变量（见 `.env.example`）。

`.env` 由 `scripts/env_loader.py` 自动加载（纯标准库，无需安装 `python-dotenv`）；已存在的环境变量优先级更高。

未配置密钥的供应商会在路由时被自动跳过，向下取下一个可用模型。

## 密钥在哪找

API Key 都在各家的「开放平台 / 控制台」，流程基本一致：注册 → 实名认证 → 创建 API Key。

| 供应商 | 申请地址 | 特别说明 |
|---|---|---|
| 豆包（火山方舟） | https://ark.volcengine.com/region:cn-beijing/apiKey | 每个模型 50 万 token 免费额度，未开通也能先试；用完再开通转付费 |
| DeepSeek | https://platform.deepseek.com/api_keys | 需先充值，无免费额度；余额不足报 402 |
| Kimi（月之暗面） | https://platform.moonshot.cn/console/api-keys | — |
| 通义千问（阿里云百炼） | https://bailian.console.aliyun.com/ | 在「API Key 管理」创建 |
| 智谱 GLM | https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys | 新用户常有免费额度 |
| 腾讯混元（TokenHub） | https://console.cloud.tencent.com/tokenhub/apikey | 老混元平台 2026-09 停服；国内端点 `tokenhub.tencentmaas.com/v1`（**无 cloud**），写错会报 401002 |
| 文心一言（百度千帆） | https://console.bce.baidu.com/qianfan/ | 应用接入 → API Key |
| Google Gemini | https://aistudio.google.com/app/apikey | 有免费额度；国内访问需自行解决网络 |
| OpenAI | https://platform.openai.com/api-keys | 需绑定境外支付方式 |
| Anthropic Claude | https://console.anthropic.com/settings/keys | 需预充值 |

**最容易踩的坑**

1. **豆包**：
   - API Key 与控制台**左下角的项目空间**绑定，换项目后 Key 不通用（401 的首要原因）。
   - **2026-09-17 之后创建的 Key 改用新明文格式**，旧 Key 继续有效；别靠 `sk-` / UUID 前缀猜归属，以创建它的控制台为准。
   - 模型 ID 周级换代。当前推荐 `doubao-seed-evolving`；官方示例用 `doubao-seed-2-1-pro-260915` 这类带日期的完整 ID。**不存在 `doubao-seed-evolving-latest-version`**。报「模型不存在」时用 `check_provider.py --scan` 查。
2. **TokenHub 域名**：国内站点是 `tokenhub.tencentmaas.com`（**没有 `cloud`**）。
  `tokenhub.tencentcloudmaas.com` 是另一站点，用国内 Key 访问会返回
  `401002`「Key 与 Base URL 不属于同一站点」。国际站 / 硅谷站 Key 也不通用。
3. **Key 只显示一次**：创建完立刻复制保存，关掉弹窗后无法再查看完整 Key，丢了只能重建。
4. **DeepSeek 要充值**：没有免费额度，余额不足会直接 402，先小额充值即可。
5. **国内平台需实名认证**：个人实名一般几分钟；企业认证更慢。

**建议的最小可用组合**：方舟 `ARK_API_KEY` + TokenHub `HUNYUAN_API_KEY` 两个 Key，
已能覆盖豆包 / 混元 / DeepSeek / Kimi / GLM / MiniMax / Step 等全部主流国内模型。
DeepSeek 官方 Key 只是可选项。

更完整的端点与模型清单见 [`references/models.md`](references/models.md)。

## 接入豆包（分步）

> **打开链接看到 404？** 见下方[「控制台 404 排障」](#控制台-404-排障)。这些控制台都是单页应用，服务器返回 200、只有前端路由渲染出 404，不代表服务不可用。

1. 打开 https://console.volcengine.com/ark ，用手机号注册并完成**实名认证**（未实名时搜不到模型）。
2. 记下控制台**左下角的项目空间名**（通常是 `Default`）——创建 Key、开通模型、调用 API 必须在同一项目下。
3. 开通模型：https://console.volcengine.com/ark/region:cn-beijing/openManagement → 建议勾选「全选 + 自动开通新增模型」。新用户每个模型有 **50 万 token 免费额度，未开通也能先调用**；额度用完后在此开通即转按量付费。
4. 创建 Key：https://ark.volcengine.com/region:cn-beijing/apiKey → 「创建 API Key」→ **立刻复制**（只完整显示一次）。
5. 填入技能根目录的 `.env`：

   ```bash
   ARK_API_KEY=你的方舟Key
   ```

6. 自检（推荐，一次区分 Key 无效 / 项目不匹配 / 模型 ID 过期）：

   ```bash
   python scripts/check_provider.py --provider doubao --scan
   ```

   脚本会拉取方舟**官方公开模型目录** `https://ark.volcengine.com/models.json`（无需 Key），列出当前有效的豆包 LLM 模型 ID，再逐个探测候选 ID。若与默认不同，调用时加 `--model 模型ID` 覆盖。

7. 试跑：

   ```bash
   python scripts/call_model.py --provider doubao --prompt "写一条小红书文案"
   ```

> 方舟也支持用「推理接入点」ID（`ep-` 开头）作为 model 值；日常直接用模型 ID 更简单。
> 调用端点是 `https://ark.cn-beijing.volces.com/api/v3`（数据面），鉴权 `Authorization: Bearer $ARK_API_KEY`。

### 控制台 404 排障

截图里那种「404 / 当前页面无法访问 / 可能由于网址已删除或尚未生效」页面，通常是**控制台 SPA 的前端路由 404**，不是域名失效（2026-11-05 实测上述入口 HTTP 均返回 200）。按顺序试：

1. **换直链**：不要用旧书签或搜索结果里的深层链接，直接用下表地址。
2. **先登录**：未登录时访问需鉴权的具体页面，前端常直接落到 404。先访问首页登录，再点左侧菜单。
3. **选项目空间**：控制台左下角切换到一个具体项目（如 `Default`）。
4. **清缓存**：`Ctrl+F5` 强刷，或用无痕窗口；排除旧 cookie / 浏览器插件干扰。
5. **换文档域名**：控制台打不开时，`ark.volcengine.com` 文档站是独立站点，可直接查步骤与模型列表。

| 用途 | 已验证直链 |
|---|---|
| 方舟首页 | https://console.volcengine.com/ark |
| API Key 管理 | https://ark.volcengine.com/region:cn-beijing/apiKey |
| 开通管理 | https://console.volcengine.com/ark/region:cn-beijing/openManagement |
| 快速入门文档 | https://ark.volcengine.com/region:cn-beijing/docs/quick-start.md |
| 模型列表文档 | https://ark.volcengine.com/region:cn-beijing/docs/model-list.md |
| 官方模型目录 JSON | https://ark.volcengine.com/models.json |

## 使用

**0. 接入自检（新增供应商后先跑）**

```bash
python scripts/check_provider.py --provider doubao          # 诊断当前配置
python scripts/check_provider.py --provider hunyuan --scan  # 列出 TokenHub 可用模型
```

**1. 自动路由（裁判模式）**

```bash
python scripts/judge.py --prompt "帮我用Python写个爬虫"
# 输出：task_type=code, recommended={provider: th-code, model: kimi-k2.7-code}
```

```bash
python scripts/call_model.py --provider th-code --prompt "帮我用Python写个爬虫"
```

**2. 直接指定模型**

```bash
python scripts/call_model.py --provider doubao --prompt "写一条小红书文案"
```

**3. 用 LLM 当裁判（语义分类）**

```bash
python scripts/judge.py --prompt "总结这篇论文的核心贡献" --use-llm
```

**4. 多模型对比**

```bash
python scripts/call_model.py --provider th-deepseek --prompt "解释一下量子纠缠"
python scripts/call_model.py --provider doubao      --prompt "解释一下量子纠缠"
```

## 进阶：两个聚合平台，一个 Key 覆盖多家模型

接入后发现，方舟与腾讯云 TokenHub 都是"一个 Key、一个 OpenAI 兼容端点、几十到上百个模型"的聚合平台，**共用各自的 Key**，不必再去 Kimi / 智谱 / DeepSeek / MiniMax 各家单独申请。

```bash
python scripts/check_provider.py --provider doubao  --scan   # 方舟：55 模型
python scripts/check_provider.py --provider hunyuan --scan   # TokenHub：126 模型 / 120 online

python scripts/call_model.py --provider th-code      --prompt "写个快速排序"
python scripts/call_model.py --provider th-kimi      --max-tokens 2048 --prompt "总结这段长文"
python scripts/call_model.py --provider ark-deepseek --prompt "写个快速排序"
python scripts/call_model.py --provider doubao       --prompt "写一条小红书文案"
python scripts/call_model.py --provider hunyuan --model hy-vision-2.0-instruct --prompt "..."
```

| 平台 | 别名 | 默认模型 ID | 实测 |
|---|---|---|---|
| 方舟 | `ark-deepseek` | `deepseek-v4-1-flash-261017` | ✅ |
| 方舟 | `ark-kimi` | `kimi-k3-1-pro-261024` | ✅ |
| 方舟 | `ark-glm` | `glm-5-3-flash-260828` | ✅ |
| TokenHub | `th-code` | `kimi-k2.7-code` | ✅ |
| TokenHub | `th-deepseek` | `deepseek-v4-pro-202606` | ✅ |
| TokenHub | `th-flash` | `deepseek-v4.1-flash` | ✅ |
| TokenHub | `th-kimi` | `kimi-k3.1-pro` | ⚠️ 推理模型，需较大 max-tokens |
| TokenHub | `th-glm` | `glm-5.3-flash` | ✅ |
| TokenHub | `th-minimax` | `minimax-m3.1-flash` | ✅ |

这些别名已纳入 `judge.py` 的自动路由：代码 → `th-code`，推理 → `th-deepseek`，
长文 → `th-kimi`，中文 → 豆包 / 混元。因此「DeepSeek 官方 Key 余额为 0」不再导致
代码任务 402。想恢复原策略，改 `judge.py` 的 `ROUTING` 即可。

> **TokenHub 端点注意**：国内站 `https://tokenhub.tencentmaas.com/v1`（无 `cloud`）。
> 写成 `tokenhub.tencentcloudmaas.com` 会报 `401002`「Key 与站点不匹配」。
> 模型需先在控制台开通服务，未开通返回 402。
>
> **推理模型**：`ark-deepseek` / `ark-kimi` / `th-kimi` 会先输出 `reasoning_content`；
> 把 `--max-tokens` 设得过小（如 20）会导致正文为空，默认值 2048 足够。
>
> 模型 ID 带日期后缀且周级迭代，`/models` 里列出的也不保证都有权限
> （例如 `doubao-seed-2-0-code-preview-260215` 实测 404）。变更前用 `--scan` 复核。

## 接下来接入哪些 AI

**结论：不用一家家装。** 方舟 + TokenHub 两个 Key 已实测覆盖豆包 / 混元 Hy4 /
DeepSeek-V4 / Kimi-K3 / GLM-5.3 / MiniMax-M3 / Step-5，国内主流模型家族全部齐了。
剩余优先顺序：

1. ✅ **已完成**：`ark-*` 与 `th-*` 纳入自动路由，TokenHub Key 接入。
2. **其次：智谱 GLM 官方或硅基流动**（二选一），作为不依赖聚合平台的独立备用线路。
3. **可选：Gemini**（免费、1M 上下文，但国内需解决网络，免费层勿传敏感数据）。
4. **暂缓：OpenAI / Claude**（境外支付 + 网络 + 地区限制，且能力已被聚合平台覆盖大半）。
5. **DeepSeek 官方 Key** 充值后即可恢复，但现在不充值也不影响使用。

完整理由、免费额度对照、每家的接入三步与「不建议申请」清单见
[`references/onboarding.md`](references/onboarding.md)。

## 目录结构

```
ai-router/
├── SKILL.md              # 技能入口与路由手册（WorkBuddy 读取）
├── README.md             # 本文件
├── LICENSE               # MIT 许可证
├── .env.example          # API Key 模板（含各家申请地址）
├── .gitignore            # 排除 .env 与缓存
├── scripts/
│   ├── call_model.py     # 统一调用脚本（OpenAI 兼容 + Claude 分支）
│   ├── judge.py          # 任务分类与模型推荐
│   ├── check_provider.py # 接入自检 / 列出可用模型 ID
│   ├── env_loader.py     # 纯标准库 .env 加载器
│   └── package.py        # 零依赖安全打包（排除密钥 / .git / 缓存）
└── references/
    ├── models.md         # 供应商端点、模型清单、能力标签、核实状态
    ├── onboarding.md     # 接下来接入哪些 AI（优先级与免费额度）
    └── github-setup.md   # 配置 GitHub 认证 / remote 并推送（含报错速查）
```

## 扩展新供应商

1. 在 `scripts/call_model.py` 的 `PROVIDERS` 增加一行（base_url + 密钥变量 + 默认模型 + 模式）。
2. OpenAI 兼容的无需改逻辑；Anthropic 风格需新增 `call_*` 函数与分支。
3. 在 `.env.example` 与 `references/models.md` 补充密钥与模型信息。
4. 更新 `SKILL.md` 路由表与 `scripts/judge.py` 的 `ROUTING` / `KEYWORDS`。

## 打包分发

**不要直接压缩文件夹**（会把 `.env` 和 `.git` 一起打进去）。用自带脚本：

```bash
python scripts/package.py                  # 生成 ./ai-router.zip
python scripts/package.py --check-only     # 只校验结构 + 扫描密钥
python scripts/package.py --output dist    # 输出到 dist/
```

它会排除 `.env`、`.git`、`__pycache__`、`*.pyc`、`*.zip`，
并扫描所有待打包文件里是否含有 API Key 特征串；命中即**拒绝打包**。
分享 `ai-router.zip` 即可让他人一键放到 `~/.workbuddy/skills/` 安装。

## 开源

本技能以 **MIT 许可证**开源（见 [`LICENSE`](LICENSE)），欢迎提 Issue / PR 接入更多模型或改进路由策略。

本地 Git 仓库已初始化在技能目录（`main` 分支，含全部源码与文档）。首次推送需要三件事：
**配提交身份 → 在 GitHub 建空仓库 → 设置 remote 并认证**。

```bash
# 1) 身份（邮箱建议用 GitHub 的 <ID>+<用户名>@users.noreply.github.com）
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"

# 2) 在 https://github.com/new 建一个**空**的 Public 仓库：
#    不要勾选 Add a README / .gitignore / license，否则远端会与本地冲突

# 3) 设置 remote 并推送（首次会弹浏览器登录 GitHub，由 Git Credential Manager 处理）
cd ~/.workbuddy/skills/ai-router
git remote add origin https://github.com/<你的用户名>/ai-router.git
git push -u origin main
```

GitHub **已禁用账号密码认证**，可选 HTTPS + GCM（推荐，本机已自带）、SSH 密钥、`gh` CLI 三种方式；
身份配置、SSH 授权、网络受限时的 443 端口方案、以及 `403` / `Permission denied (publickey)` /
`fetch first` 等报错的处理，见 [`references/github-setup.md`](references/github-setup.md)。

> ⚠️ **安全**：`.env` 已被 `.gitignore` 排除，**切勿** `git add -f .env`；
> 也**不要**把 Token 拼进 remote 地址（会明文写进 `.git/config`）。
> 若曾把真实 Key 写进任何文档或提交历史，推送前先到各家控制台轮换密钥。
> 提交前可跑 `python scripts/package.py --check-only` 做一次密钥扫描。
