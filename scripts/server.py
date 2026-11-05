#!/usr/bin/env python3
"""
server.py - expose ai-router as a small HTTP service.

Why: the skill normally runs on one machine with the API keys in ~/.env. Put it
on a box that is always on and every other device (phone, another laptop, a
mini-program, a colleague's script) can borrow those keys over HTTP instead of
copying them around.

    python scripts/server.py --port 8787 --token "$(cat ~/.ai-router-token)"
    curl -H "Authorization: Bearer $TOKEN" localhost:8787/health
    curl -H "Authorization: Bearer $TOKEN" -d '{"prompt":"帮我写条通知"}' localhost:8787/ask

Endpoints
    GET  /health              liveness + which providers are configured (no auth)
    GET  /providers           provider list and whether each is usable
    GET  /usage?days=7        local metering summary
    POST /route               {"prompt": ...} -> routing decision, calls nothing
    POST /ask                 {"prompt": ...} -> {"text", "provider", "model", ...}

Safety defaults
  * refuses to start on a non-loopback address without a token
  * bearer token compared in constant time; /health is the only open route
  * request body capped at 1 MiB, prompt at 20k chars, per-IP rate limit
  * prompts are never logged; only method, path, status, ms and client IP

No third-party dependencies: standard library only.
"""
import argparse
import hmac
import json
import os
import sys
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from env_loader import load_env_file  # noqa: E402

load_env_file()

import ask  # noqa: E402
import call_model  # noqa: E402
import judge  # noqa: E402

MAX_BODY = 1 << 20          # 1 MiB
MAX_PROMPT = 20000          # characters
START_TS = time.time()


def skill_version():
    """Version from SKILL.md frontmatter, so /health reports what is deployed."""
    try:
        text = (HERE.parent / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines()[:20]:
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return "unknown"


class RateLimiter:
    """Tiny per-client fixed-window limiter; enough to stop a runaway script."""

    def __init__(self, per_minute):
        self.per_minute = max(0, per_minute)
        self.hits = defaultdict(list)
        self.lock = threading.Lock()

    def allow(self, client):
        if self.per_minute <= 0:
            return True
        now = time.time()
        with self.lock:
            recent = [t for t in self.hits[client] if now - t < 60]
            if len(recent) >= self.per_minute:
                self.hits[client] = recent
                return False
            recent.append(now)
            self.hits[client] = recent
            return True


class Handler(BaseHTTPRequestHandler):
    server_version = "ai-router/" + skill_version()
    protocol_version = "HTTP/1.1"   # keep-alive; we always send Content-Length

    # ---- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):  # noqa: N802 - stdlib hook name
        sys.stderr.write("[server] %s %s\n" % (self.address_string(), fmt % args))

    def _cors(self):
        origin = self.server.cors
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _client(self):
        return self.client_address[0] if self.client_address else "?"

    def _auth_ok(self):
        token = self.server.token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        given = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not given:
            given = (self.headers.get("X-API-Key") or "").strip()
        return bool(given) and hmac.compare_digest(given, token)

    def _guard(self):
        """Auth + rate limit. Returns True when the request may proceed."""
        if not self._auth_ok():
            self._send(401, {"error": "missing or invalid bearer token"})
            return False
        if not self.server.limiter.allow(self._client()):
            self._send(429, {"error": f"rate limit: {self.server.limiter.per_minute}/min"})
            return False
        return True

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "bad Content-Length"})
            return None
        if length > MAX_BODY:
            self._send(413, {"error": f"body too large (max {MAX_BODY} bytes)"})
            return None
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            self._send(400, {"error": f"invalid JSON: {e}"})
            return None
        if not isinstance(data, dict):
            self._send(400, {"error": "JSON body must be an object"})
            return None
        return data

    # ---- routes ---------------------------------------------------------
    def do_OPTIONS(self):  # noqa: N802
        if not self.server.cors:
            self._send(405, {"error": "CORS is disabled; start with --cors ORIGIN"})
            return
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        query = {}
        if "?" in self.path:
            for pair in self.path.split("?", 1)[1].split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    query[k] = v

        if path == "/health":
            # Deliberately open: monitoring needs it, and it leaks nothing but
            # provider names the caller could guess anyway.
            configured = sorted(judge.configured_providers())
            self._send(200, {
                "ok": True,
                "version": skill_version(),
                "uptime_s": int(time.time() - START_TS),
                "providers_configured": len(configured),
                "custom_endpoint": bool(call_model.PROVIDERS["custom"][0]),
                "auth": bool(self.server.token),
            })
            return

        if not self._guard():
            return
        if path == "/providers":
            self._send(200, {"providers": [
                {"name": p, "configured": call_model.is_configured(p),
                 "default_model": call_model.PROVIDERS[p][2]}
                for p in sorted(call_model.PROVIDERS)]})
            return
        if path == "/usage":
            import usage
            days = 7
            try:
                days = max(1, min(365, int(query.get("days", "7"))))
            except ValueError:
                pass
            since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400))
            rows = usage.report(since=since)
            calls, ptok, ctok, ttok, cost, hits = usage.total_line(since=since)
            self._send(200, {
                "since": since,
                "totals": {"calls": calls, "prompt_tokens": ptok,
                           "completion_tokens": ctok, "total_tokens": ttok,
                           "cost_cny": round(cost, 4), "cache_hits": hits},
                "by_model": [
                    {"provider": r[0], "model": r[1], "calls": r[2],
                     "total_tokens": r[5], "cost_cny": round(r[6] or 0, 4),
                     "cache_hits": r[7]} for r in rows],
            })
            return
        self._send(404, {"error": f"unknown path {path}"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path not in ("/ask", "/route"):
            self._send(404, {"error": f"unknown path {path}"})
            return
        if not self._guard():
            return
        data = self._read_json()
        if data is None:
            return

        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            self._send(400, {"error": "field 'prompt' is required"})
            return
        if len(prompt) > MAX_PROMPT:
            self._send(413, {"error": f"prompt too long (max {MAX_PROMPT} chars)"})
            return

        images = data.get("images") or data.get("image") or []
        if isinstance(images, str):
            images = [images]
        missing = [p for p in images if not os.path.isfile(str(p))]
        if missing:
            # Remote callers cannot send a path that only exists on their laptop.
            self._send(400, {"error": f"image not found on server: {missing[0]}",
                             "hint": "copy the file to the server, or pass base64 "
                                     "via a client that supports it"})
            return

        try:
            if path == "/route":
                task, reason, ranked = ask.route(
                    prompt, bool(data.get("use_llm")), data.get("judge_provider"),
                    data.get("task") or ("vision" if images else None),
                    bool(data.get("budget")))
                self._send(200, {
                    "task_type": task, "reason": reason,
                    "provider": ranked[0] if ranked else None,
                    "model": (judge.default_model(ranked[0]) if ranked else None),
                    "ranked_providers": ranked,
                    "configured_providers": sorted(judge.configured_providers()),
                })
                return

            res = ask.answer(
                prompt,
                system=data.get("system"),
                task=data.get("task"),
                provider=data.get("provider"),
                model=data.get("model"),
                max_tokens=int(data.get("max_tokens") or 2048),
                temperature=data.get("temperature"),
                timeout=int(data.get("timeout") or 90),
                images=images or None,
                no_fallback=bool(data.get("no_fallback")),
                budget=bool(data.get("budget")),
                use_llm=bool(data.get("use_llm")),
            )
        except ask.AskError as e:
            code = 503 if e.code == 2 else (502 if e.status else 502)
            self._send(code, {"error": str(e), "upstream_status": e.status,
                              "task_type": None})
            return
        except Exception as e:  # noqa: BLE001 - never leak a stack trace to clients
            sys.stderr.write(f"[server] unexpected error: {e!r}\n")
            self._send(500, {"error": f"internal error: {type(e).__name__}"})
            return

        self._send(200, res)


def build_parser():
    p = argparse.ArgumentParser(
        description="Serve ai-router over HTTP so other devices can use it.")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default loopback; use 0.0.0.0 behind a proxy)")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--token", default=os.environ.get("AI_ROUTER_SERVER_TOKEN"),
                   help="bearer token (env: AI_ROUTER_SERVER_TOKEN); required off-loopback")
    p.add_argument("--rate", type=int, default=30,
                   help="max requests per minute per client IP (0 disables)")
    p.add_argument("--cors", default=None, metavar="ORIGIN",
                   help="allow browser calls from ORIGIN (e.g. 'https://app.example.com')")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.token:
        sys.stderr.write(
            "[server] refusing to listen on a public address without a token.\n"
            "[server] set AI_ROUTER_SERVER_TOKEN or pass --token (and put TLS in "
            "front of it: nginx/caddy, or an SSH tunnel).\n")
        return 2

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.token = (args.token or "").strip()
    httpd.cors = args.cors
    httpd.limiter = RateLimiter(args.rate)

    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    sys.stderr.write(f"[server] ai-router {skill_version()} on http://{shown}:{args.port}\n")
    sys.stderr.write(f"[server] auth: {'bearer token required' if httpd.token else 'OFF (loopback only)'}\n")
    sys.stderr.write("[server] providers: " + ", ".join(
        sorted(judge.configured_providers())) + "\n")
    sys.stderr.write(f"[server] rate limit: {args.rate}/min per IP\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[server] stopping\n")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
