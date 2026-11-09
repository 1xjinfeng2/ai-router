#!/usr/bin/env python3
"""Deploy a packaged ai-router zip on the server, with smoke test + rollback.

Run ON THE SERVER as root after uploading ai-router.zip to /tmp:

    python3 /opt/ai-router/scripts/server_deploy.py --zip /tmp/ai-router.zip

Flow:
  1. snapshot scripts/ + tests/ + SKILL.md + ROADMAP.md + README.md into a
     timestamped backup under /opt/ai-router/backups/
  2. unpack the new release over /opt/ai-router (never touching .env, *.db)
  3. fix ownership, restart the systemd unit
  4. smoke-test /health against the version/provider count from the NEW release
  5. on ANY failure: restore the backup, restart, re-smoke, exit non-zero

Zero third-party dependencies; uses only the standard library.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

APP = Path("/opt/ai-router")
BACKUPS = APP / "backups"
UNIT = "ai-router"
HEALTH = "http://127.0.0.1/health"
FILES_TO_BACKUP = ["scripts", "tests", "SKILL.md", "ROADMAP.md", "README.md"]


def run(args, timeout=120):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def log(msg):
    print("[deploy] " + msg, flush=True)


def read_version(path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            return ""
        head = text.split("\n---", 1)[0]
        for line in head.splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def fetch_health(timeout=10):
    try:
        req = urllib.request.Request(HEALTH, headers={"User-Agent": "ai-router-deploy"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def smoke(version, providers, timeout=10):
    health = fetch_health(timeout)
    problems = []
    if health.get("ok") is not True:
        problems.append("service not ok: %s" % health.get("error", health))
    if version and health.get("version") != version:
        problems.append("version %r != %r" % (health.get("version"), version))
    if providers is not None and health.get("providers_configured") != providers:
        problems.append("providers %r != %r" %
                        (health.get("providers_configured"), providers))
    for d in (health.get("drift") or []):
        problems.append("server drift: " + d)
    return (not problems), problems, health


def snapshot():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUPS / stamp
    dest.mkdir(parents=True, exist_ok=False)
    for name in FILES_TO_BACKUP:
        src = APP / name
        if not src.exists():
            continue
        target = dest / name
        if src.is_dir():
            shutil.copytree(src, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    log(f"backup -> {dest}")
    return dest


def restore(backup):
    log(f"ROLLING BACK from {backup}")
    for name in FILES_TO_BACKUP:
        src = backup / name
        dst = APP / name
        if not src.exists():
            continue
        if dst.is_dir():
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    run(["chown", "-R", "airouter:airouter", str(APP / "scripts"), str(APP / "tests")])
    # The EnvironmentFile is deliberately not in the backup, so realign the
    # expected version with the restored release to avoid a false drift alarm.
    old_version = read_version(backup / "SKILL.md")
    if old_version:
        sync_expect_version(old_version)
    run(["systemctl", "restart", UNIT])
    time.sleep(2)


def sync_expect_version(version):
    """Keep AI_ROUTER_EXPECT_VERSION in the EnvironmentFile in step.

    Without this, a static expected version would make /health report drift
    after every legitimate upgrade, and the smoke test would roll back a good
    release. Only the version line is touched; keys and token stay untouched.
    """
    env = APP / ".env"
    if not env.exists() or not version:
        return
    try:
        lines = env.read_text(encoding="utf-8").splitlines()
        out, found = [], False
        for line in lines:
            if line.strip().startswith("AI_ROUTER_EXPECT_VERSION="):
                out.append(f"AI_ROUTER_EXPECT_VERSION={version}")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"AI_ROUTER_EXPECT_VERSION={version}")
        env.write_text("\n".join(out) + "\n", encoding="utf-8")
        run(["chown", "airouter:airouter", str(env)])
        os.chmod(env, 0o600)
        log(f"synced AI_ROUTER_EXPECT_VERSION={version}")
    except OSError as e:
        log(f"could not sync expected version (non-fatal): {e}")


def unpack(zip_path):
    with zipfile.ZipFile(str(zip_path)) as z:
        names = z.namelist()
        tops = {n.split("/")[0] for n in names}
        root = "ai-router" if "ai-router" in tops else ""
        staging = Path("/tmp/ai-router-deploy-stage")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        z.extractall(str(staging))
        base = staging / root if root else staging
        # Copy everything except .env / databases / backups (never overwrite secrets)
        skip_dirs = {"backups", "__pycache__"}
        for item in base.iterdir():
            if item.name in (".env",) or item.suffix == ".db":
                continue
            if item.name in skip_dirs:
                continue
            dst = APP / item.name
            if item.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
    shutil.rmtree(staging, ignore_errors=True)


def main(argv=None):
    p = argparse.ArgumentParser(description="Deploy ai-router with smoke test + rollback.")
    p.add_argument("--zip", default="/tmp/ai-router.zip")
    p.add_argument("--expect-providers", type=int, default=None,
                   help="expected configured provider count (default: keep current count)")
    p.add_argument("--no-rollback", action="store_true")
    args = p.parse_args(argv)

    zip_path = Path(args.zip)
    if not zip_path.exists():
        log(f"ERROR: {zip_path} not found")
        return 2

    before = fetch_health()
    current_providers = before.get("providers_configured")
    expect_providers = args.expect_providers or current_providers
    log(f"before: version={before.get('version')} providers={current_providers}")

    backup = snapshot()
    try:
        unpack(zip_path)
        version = read_version(APP / "SKILL.md")
        log(f"unpacked new release, version={version}")
        sync_expect_version(version)
        run(["chown", "-R", "airouter:airouter", str(APP / "scripts"), str(APP / "tests")])
        code, _, err = run(["systemctl", "restart", UNIT])
        if code != 0:
            raise RuntimeError("systemctl restart failed: " + err)
        time.sleep(2)

        ok, problems, health = smoke(version, expect_providers)
        if not ok:
            raise RuntimeError("smoke failed: " + "; ".join(problems))
        log(f"OK: version={health.get('version')} "
            f"providers={health.get('providers_configured')} "
            f"hash={health.get('config_hash')}")
        return 0
    except Exception as e:  # noqa: BLE001
        log("FAILED: " + str(e))
        if not args.no_rollback:
            restore(backup)
            ok, problems, health = smoke(None, None)
            log("rollback verify: " + ("OK" if ok else "; ".join(problems)))
        return 1


if __name__ == "__main__":
    sys.exit(main())
