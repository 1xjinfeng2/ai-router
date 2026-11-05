# 部署到服务器

`scripts/server.py` 把 ai-router 变成一个 HTTP 服务，任何能发 HTTP 请求的东西都可以借用服务器上的
Key：手机、另一台电脑、公司系统、小程序。

## 1. 最小启动

```bash
# 在服务器上（Python 3.10+，零依赖）
export AI_ROUTER_SERVER_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
python scripts/server.py --host 127.0.0.1 --port 8787
```

**不开 token 时只允许监听回环地址**——这是刻意的：没有鉴权的公网端口等于把 API 余额送给全网。
想对外提供访问，先配 token，再把 TLS 反代放到前面。

## 2. 端点

| 方法 | 路径 | 作用 | 需要 token |
|---|---|---|---|
| GET | `/health` | 存活、版本、已配置供应商数量 | 否 |
| GET | `/providers` | 供应商清单及是否可用 | 是 |
| GET | `/usage?days=7` | 用量与费用汇总 | 是 |
| POST | `/route` | 只路由不调用（省钱） | 是 |
| POST | `/ask` | 路由 + 调用 + 返回答案 | 是 |

请求体字段（除 `prompt` 外都可选）：

```json
{
  "prompt": "帮我把这段话压到 100 字",
  "system": "你是资深会务编辑",
  "task": "chinese",
  "provider": "doubao",
  "model": "doubao-seed-evolving",
  "max_tokens": 1024,
  "temperature": 0.3,
  "timeout": 90,
  "images": ["/srv/data/poster.jpg"],
  "no_fallback": false,
  "budget": false,
  "use_llm": false
}
```

`/ask` 返回：

```json
{"text": "...", "provider": "doubao", "model": "doubao-seed-evolving",
 "requested_provider": "doubao", "task_type": "chinese", "reason": "...",
 "usage": {"prompt_tokens": 12, "completion_tokens": 88, "total_tokens": 100},
 "latency_ms": 1840, "fallback_from": null}
```

错误码：`400` 参数不对、`401` token 错、`413` 太长、`429` 超频、`502` 上游模型失败
（响应里带 `upstream_status`）、`503` 服务器上一个模型都没配。

## 3. 客户端怎么用

**A. 已有技能改成走远端**（本机不再需要 Key，只留 `AI_ROUTER_SERVER`）：

```bash
export AI_ROUTER_SERVER=http://your-server:8787
export AI_ROUTER_SERVER_TOKEN=...        # 与服务器一致
python scripts/ask.py --prompt "帮我写条通知"     # 实际由服务器出 key
```

写进 `.env` 就长期生效：

```
AI_ROUTER_SERVER=https://ai.example.com
AI_ROUTER_SERVER_TOKEN=xxxxxxxx
```

**B. 任何语言直接 HTTP**：

```bash
curl -s -X POST https://ai.example.com/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"写一句签到提醒"}' | jq -r .text
```

## 4. 常驻：systemd

```ini
# /etc/systemd/system/ai-router.service
[Unit]
Description=ai-router HTTP service
After=network-online.target

[Service]
Type=simple
User=airouter
WorkingDirectory=/opt/ai-router
EnvironmentFile=/opt/ai-router/.env
Environment=AI_ROUTER_SERVER_TOKEN=change-me
ExecStart=/usr/bin/python3 /opt/ai-router/scripts/server.py --host 127.0.0.1 --port 8787
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/opt/ai-router

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now ai-router
curl -s localhost:8787/health
```

## 5. TLS 反代：nginx

```nginx
server {
    listen 443 ssl http2;
    server_name ai.example.com;
    ssl_certificate     /etc/letsencrypt/live/ai.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ai.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_read_timeout 180s;      # 模型生成慢，别用默认 60s
        client_max_body_size 2m;
    }
}
```

不想碰证书就用 Caddy（自动 HTTPS），或者干脆只开本地端口 + SSH 隧道：

```bash
ssh -N -L 8787:127.0.0.1:8787 user@server    # 之后本机访问 localhost:8787
```

## 6. 容器

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8787
CMD ["python", "scripts/server.py", "--host", "0.0.0.0", "--port", "8787"]
```

```bash
docker run -d --env-file .env -e AI_ROUTER_SERVER_TOKEN=... -p 127.0.0.1:8787:8787 ai-router
```

镜像里不要把 `.env` 打进层（`package.py` 已经排除它）；用 `--env-file` 或宿主机 secret。

## 7. 安全清单

- **必须**有 token；`server.py` 在非回环地址上无 token 会直接拒绝启动。
- 公网只暴露 443，`8787` 绑 `127.0.0.1`。
- `--rate 30`（默认）按 IP 限流，防止脚本跑飞；多用户场景按需调大。
- 提示词**不写日志**，只记录方法、路径、状态、耗时、IP。排查问题请用 `/route` 复现路由决策。
- 服务器上的 `.env` 是所有人的 Key：`chmod 600 .env`，不要 commit（已在 `.gitignore`）。
- 用量与隐私库留在服务器本地（`usage.db` / `privacy_vault.db`），备份按公司数据规范处理。

## 8. 已知限制

- `/ask` 是**同步**的：长文（几千字）可能跑 1-2 分钟，客户端和反代超时都要放宽。
- 不支持 SSE 流式（`call_model.py --stream` 仅本地 CLI 可用）。
- `images` 传的是**服务器上的路径**：客户端和服务器不共享文件系统时，先把图传上去，或改用 base64 扩展。
- 单进程多线程：够几十并发；更高并发请开多实例 + 反代轮询。

## 9. 自建模型服务（不走外部 API）

如果服务器上有 GPU，跑 vLLM / Ollama，然后把 `custom` 指向它：

```bash
# vLLM
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3-32B --port 8000
# 或 Ollama
ollama serve   # 默认 http://127.0.0.1:11434/v1

# .env
AI_ROUTER_CUSTOM_BASE_URL=http://127.0.0.1:8000/v1
AI_ROUTER_CUSTOM_MODEL=Qwen/Qwen3-32B
AI_ROUTER_CUSTOM_API_KEY=              # 本地服务可不填
AI_ROUTER_CUSTOM_ROUTES=cheap          # 默认只接低风险任务；想全接就写 cheap,chinese
```

验证：

```bash
python scripts/call_model.py --provider custom --prompt "你好" --max-tokens 32
python scripts/ask.py --prompt "嗯" --dry-run    # 应看到 provider=custom
```

`custom` 挂了会自动降级到 `th-flash → doubao`，不会因为 GPU 机器重启就断服务。

## 实战记录：腾讯云 OpenCloudOS 9.6（2026-11）

一次真实部署踩到的坑，重装时照这份走可以省时间：

| 现象 | 原因 | 处理 |
|---|---|---|
| 从外网连 `:8787` 超时，本机 `curl 127.0.0.1` 正常 | 腾讯云**安全组**没放行 8787（主机 iptables 是空的，firewalld 也没运行） | 先探测安全组放行了哪些端口：能连上但无人监听会返回 `ConnectionRefused`，说明放行；超时说明被拦。本例 80/443 已放行且无服务占用，于是改监听 80 |
| 想让非 root 用户监听 80 | 1024 以下端口需要特权 | unit 里只加 `AmbientCapabilities=CAP_NET_BIND_SERVICE` + `CapabilityBoundingSet=CAP_NET_BIND_SERVICE`，其余能力全部收掉 |
| 服务能答，但 `/usage` 永远是 0，日志报 `attempt to write a readonly database` | 以 root 跑过一次测试，`usage.db` 变成 root 属主；服务账号是 `airouter` | 用服务账号跑测试；升级后再 `chown -R airouter:airouter /opt/ai-router`。也可用 `AI_ROUTER_USAGE_DB` 把库放到别处 |
| 服务器 `git clone github.com` 超时（RPC failed / curl 28） | 国内机房到 GitHub 不稳定 | 在本地打包后 `scp ai-router.zip` 上去解包；`update.py` 的 HTTP 降级同样会失败，升级统一从本地推 |
| 远端调用带图片时 400 `image not found on server` | 客户端只发了本地路径 | 1.6.1 起客户端自动转 base64 `data:` URL；服务端单图上限 6M 字符、单请求 8 张、body 上限 8 MiB |

部署后的标准自检（在服务器上）：

```bash
systemctl is-active ai-router        # active
systemctl is-enabled ai-router       # enabled（开机自启）
curl -s localhost/health             # version / providers_configured / auth:true
ss -tlnp | grep ':80 '               # 只有我们的 python3
journalctl -u ai-router -n 20 -o cat # 不应出现任何提示词内容
```

从外部再验一遍鉴权，避免只测了回环：

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<IP>/ask -d '{"prompt":"hi"}'   # 401
curl -s http://<IP>/health                                                              # 200
```

**明文 HTTP 的取舍**：没有域名时无法签 Let's Encrypt，令牌会在链路上明文传输。要么给个域名走 443，要么只在可信网络/SSH 隧道内用，并定期换 `AI_ROUTER_SERVER_TOKEN`（改完 `.env` 后 `systemctl restart ai-router`）。

