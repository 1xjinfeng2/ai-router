# 配置 GitHub 并推送本仓库

本指南面向 **Windows + Git for Windows**，针对本仓库（已初始化 `main` 分支、已有 2 个提交、尚未设置 remote）的实际状态。

## -1. 还没有 GitHub 账号？

如果连账号都没有，先注册（免费，约 5 分钟），再回到第 0 节。

1. 打开官方注册页 **https://github.com/signup**（`https://github.com/join` 是同一入口），
   或点首页右上角 **Sign up**。官方也支持 **Continue with Google / Apple** 快捷注册。
2. 依次填写：
   - **邮箱**：一个没注册过 GitHub 的邮箱；**未验证邮箱无法创建仓库**。
     QQ / 163 等国内邮箱偶尔收不到验证邮件，先看垃圾箱，必要时换 Outlook / Gmail。
   - **密码**：至少 8 位且含小写字母和数字，或至少 15 位纯字符；不要用常见弱密码。
   - **用户名**：只能用英文字母、数字、连字符 `-`，**不能以连字符开头或结尾**，不能有中文或空格，且全站唯一。
3. 完成**人机验证**（图形题，失败可重试）→ 点 Create account →
   去邮箱找 GitHub 的 **8 位验证码**，在 24 小时内填入。
4. 选 **Free** 计划（ unlimited 公开/私有仓库、GitHub Actions、Pages 都包含），
   引导问卷可直接 Skip。建议随后在 https://github.com/settings/security 开启两步验证（2FA）。

> ⚠️ **用户名会永久出现在你的主页地址 `github.com/<用户名>`、每个仓库链接和提交记录里**。
> 虽然后续可以在设置里改名（旧链接会重定向），但本地 remote、提交归属等都要跟着调整。
> 建议一次取一个长期可用的英文名。想确认是否被占用，可访问
> `https://api.github.com/users/<想用的名字>`：返回 404 表示尚未被注册。

**网络提示**：2026-11-05 实测 `github.com` 主站一度返回 200，随后注册页、登录页、主站
全部出现 `CONNECT tunnel failed, response 502`（本机代理隧道波动）。这**不是链接失效**，
浏览器里也可能时好时坏——换个网络或稍后重试即可。国内直连的 Gitee / GitCode 当时可访问。

### 如果其实有账号，只是不记得用户名

- 登录后打开 https://github.com/settings/profile ，或用右上角头像下拉菜单里 `@` 开头的那行。
- 个人主页 URL 里 `github.com/` 后面那段就是用户名。
- 用户名**不是邮箱**，也不是显示名（Name）；填 remote 时要用用户名。

### 不想注册 GitHub 的替代方案

| 方案 | 说明 |
|---|---|
| **Gitee** | 国内直连、中文界面、手机号/邮箱即可注册；国内平台一般要求实名认证后才能建公开仓库。开源影响力与生态小于 GitHub |
| **GitCode**（CSDN） | 国内可直连，支持从 GitHub 导入仓库 |
| **直接分享分发包** | 已生成的 `ai-router.zip` 可以直接发给别人，放到 `~/.workbuddy/skills/` 即可安装，**完全不需要账号** |
| **先留在本地** | 本仓库已就绪（`main` 分支、已有本地提交、工作区干净），随时可以再接远程 |

> 若改用 Gitee，`git remote add origin https://gitee.com/<用户名>/ai-router.git` 后
> 同样执行 `git push -u origin main`；GCM 的浏览器登录流程不适用，需按 Gitee 提示用账号密码 / 私人令牌。

## 0. 先确认本机状态

```bash
git --version                                  # 本仓库实测 2.55.0.windows.3
git config --global user.name                  # 实测：未设置 → 需要配
git config --global user.email                 # 实测：未设置 → 需要配
git config --system --get credential.helper    # 实测：manager → Git Credential Manager 可用
git -C <仓库> remote -v                        # 实测：空 → 需要 add origin
curl -s -o /dev/null -w "%{http_code}\n" https://github.com     # 实测 200，网络通
ssh -T git@github.com                          # 实测 Permission denied (publickey) → 公钥未授权
```

GitHub **已移除账号密码认证**。`git push` 时的"密码"必须是 Personal Access Token，或改用 GCM 浏览器登录 / SSH 密钥。

## 1. 配置 Git 提交身份

没有身份时 `git commit` 会报 `Author identity unknown`。二选一：

```bash
# 全局（对这台机器所有仓库生效）
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"

# 或只对本仓库生效
git -C ~/.workbuddy/skills/ai-router config user.name "你的名字"
git -C ~/.workbuddy/skills/ai-router config user.email "你的邮箱"
```

**推荐用 GitHub 的隐私邮箱**，避免公开个人邮箱：
`https://github.com/settings/emails` → 找到 `<ID>+<用户名>@users.noreply.github.com`。

> 注意：`user.email` 必须与 GitHub 账号已验证的邮箱（或上面的 noreply 邮箱）一致，
> 否则提交不会计入 Contribution 绿格，但推送本身不受影响。

本仓库前两个提交是用临时身份 `Angela Yan <angela.yan@users.noreply.github.com>` 创建的。
配好真实身份后，若要重写这两个（尚未推送的）提交的作者：

```bash
cd ~/.workbuddy/skills/ai-router
git rebase --root --exec "git commit --amend --reset-author --no-edit"
```

## 2. 在 GitHub 网页上创建空仓库

打开 https://github.com/new ：

- Repository name: `ai-router`
- 选 **Public**（开源）
- **不要勾选** Add a README / .gitignore / license —— 本地已有这些文件，勾选会产生冲突
- 点 Create repository，复制页面给出的仓库地址

## 3. 选择一种认证方式

### 方式 A：HTTPS + Git Credential Manager（推荐，零额外安装）

本机已带 GCM（Git 2.55 ≫ 官方要求的 2.29），首次 push 会**弹浏览器**让你登录并授权 GitHub OAuth，支持两步验证，凭证自动存入 Windows 凭据管理器，之后免密。

```bash
cd ~/.workbuddy/skills/ai-router
git remote add origin https://github.com/<你的用户名>/ai-router.git
git push -u origin main
```

按弹窗完成登录即可。若之前缓存过错误凭证导致反复失败：
Windows 控制面板 → 用户账户 → **凭据管理器** → Windows 凭据 → 删除 `git:https://github.com` 条目，重新 push。

> ❌ **不要**把 Token 写进 remote 地址
> （`https://<token>@github.com/...` 会明文存进 `.git/config`，极易泄露）。

### 方式 B：SSH 密钥（免浏览器、长期稳定）

本机已有 `~/.ssh/id_ed25519.pub`。取公钥内容：

```bash
cat ~/.ssh/id_ed25519.pub
# 或 Windows 剪贴板：clip < ~/.ssh/id_ed25519.pub
```

1. 打开 https://github.com/settings/keys → **New SSH key**
2. Title 随意（如 `Windows-PC`），Key type 选 `Authentication Key`，粘贴公钥
3. 验证：

   ```bash
   ssh -T git@github.com
   # 成功：Hi <用户名>! You've successfully authenticated...
   ```

4. 用 SSH 地址设置 remote 并推送：

   ```bash
   git remote add origin git@github.com:<你的用户名>/ai-router.git
   git push -u origin main
   ```

**如果 22 端口被网络阻断**（报错 `Connection timed out`），改用 443 端口。新建 `~/.ssh/config`：

```
Host github.com
    HostName ssh.github.com
    Port 443
    User git
    IdentityFile ~/.ssh/id_ed25519
```

再跑 `ssh -T git@github.com` 验证。本仓库实测 22 端口可连通，通常不需要这段。

> 若现有密钥已用于其他服务，或这台机器是共享环境，建议生成一把专用密钥：
> `ssh-keygen -t ed25519 -C "你的邮箱" -f ~/.ssh/id_ed25519_github`
> 然后把 `-github.pub` 的公钥加到 GitHub，并在上面的 `IdentityFile` 指向新私钥。
> **私钥（无 `.pub` 后缀）绝不要外发。**

### 方式 C：GitHub CLI（一条命令建仓 + 推送）

```bash
winget install --id GitHub.cli
gh auth login          # 选 GitHub.com → HTTPS → 浏览器登录 → 询问 "Authenticate Git with your GitHub credentials?" 选 Yes
cd ~/.workbuddy/skills/ai-router
gh repo create ai-router --public --source=. --remote=origin --push
```

`gh auth login` 会顺带配置好 Git 凭证，之后 HTTPS push 免密。

## 4. 推送前的最后检查

```bash
cd ~/.workbuddy/skills/ai-router
python scripts/package.py --check-only     # 扫描待发布内容里有无 API Key 明文
git status --short                        # 应为干净
git check-ignore .env                     # 应输出 .env（确认密钥文件被排除）
git ls-files | grep -x .env               # 应无输出
git grep -nE 'sk-[A-Za-z0-9]{24,}|ark-[0-9a-fA-F-]{24,}' HEAD   # 提交历史中不应有命中
```

> ⚠️ 本项目的三把 API Key 曾在对话中明文出现过。仓库历史已确认干净，
> 但**推送到公开仓库前建议到方舟 / TokenHub / DeepSeek 控制台各轮换一次并删除旧 Key**。
> 公开仓库的内容会被全网爬虫索引，任何曾经写进文件的密钥都应视为已泄露。

## 5. 常见报错速查

| 报错 | 原因 | 解决 |
|---|---|---|
| `Author identity unknown` | 未配 user.name / user.email | 见第 1 节 |
| `Support for password authentication was removed` / 403 | 用了账号密码 | 用 GCM 浏览器登录或 PAT（`ghp_` / `github_pat_` 开头）当密码 |
| `Permission denied (publickey)` | SSH 公钥没加到 GitHub | 见方式 B 第 1–3 步 |
| `Connection timed out` / `Connection refused` | 网络或 22 端口被拦 | 走 HTTPS；或 SSH over 443（方式 B） |
| `remote origin already exists` | 已设过 remote | `git remote set-url origin <新地址>` |
| `! [rejected] main -> main (fetch first)` | 建仓时勾了 README，远端有本地没有的提交 | `git pull --rebase origin main` 后再 push；或删掉重建空仓库 |
| `fatal: not a git repository` | 没在仓库目录 | `cd ~/.workbuddy/skills/ai-router` |
| GCM 反复要求登录 | 缓存了失效凭证 | 凭据管理器删除 `git:https://github.com` |

## 6. 推送成功后

```bash
git remote -v
git log --oneline -3
git status
```

之后正常迭代：

```bash
git add .
git commit -m "描述改动"
git push
```
