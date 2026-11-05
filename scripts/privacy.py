#!/usr/bin/env python3
"""
privacy.py - Local PII vault + redaction/restore for the ai-router privacy relay.

Design
------
You (WorkBuddy) are the middleman. Before a user prompt leaves the machine to an
external AI, every sensitive value is replaced with an opaque placeholder such as
`<PII_EMAIL_1>`. The real value is stored ONLY in a local SQLite vault
(`privacy_vault.db`, git-ignored, never packaged). External AIs therefore never
see the raw PII. When results come back we can restore the placeholders to the
real values for the human who owns them.

Zero third-party dependencies (Python stdlib only). The vault file lives in the
skill root and is excluded from git and from the distributable zip.

Detection levels
----------------
* Default (high-confidence): email, CN mobile, 18-digit ID card, 16-19 digit bank
  card, IPv4, QQ number, plus any `--custom` terms you name.
* `--strict` adds heuristic name/address matching (may over-redact; use when the
  prompt is known to contain names/addresses that the high-confidence set misses).

CLI
---
  python privacy.py --redact "联系张三 13800138000 a@b.com"
  python privacy.py --restore --session <sid> --text "<PII_EMAIL_1> ..."
  python privacy.py --list
  python privacy.py --export --session <sid>     # owner audit of own vault
  python privacy.py --clear --session <sid>
  python privacy.py --clear-all
  python privacy.py --expire 7                   # drop rows older than 7 days
  python privacy.py --encrypt                    # encrypt existing rows (needs passphrase)
  python privacy.py --decrypt                    # revert to plaintext

Security notes
--------------
* Rows older than AI_ROUTER_VAULT_TTL_DAYS (default 7) are swept automatically
  on every redact, so a forgotten session cannot live in the vault forever.
* Encryption is OPTIONAL and keeps the core dependency-free: install
  `cryptography` and export AI_ROUTER_VAULT_PASSPHRASE to turn it on. Without
  it the vault stays plaintext SQLite and prints a warning.
"""
import argparse
import base64
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("AI_ROUTER_VAULT_DB")
               or (Path(__file__).resolve().parent.parent / "privacy_vault.db"))

ENC_PREFIX = "enc:v1:"
DEFAULT_TTL_DAYS = int(os.environ.get("AI_ROUTER_VAULT_TTL_DAYS", "7") or 0)
_PII_NEEDLE = "<PII_"

# Ordered high-confidence detectors. Run specific (ID) before generic (bank card)
# so an 18-digit number is treated as an ID, not a card.
DETECTORS = [
    ("EMAIL",    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), False),
    ("PHONE",    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), False),
    ("IDCARD",   re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), False),
    ("BANKCARD", re.compile(r"(?<!\d)\d{16,19}(?!\d)"), False),
    ("IP",       re.compile(r"(?:\d{1,3}\.){3}\d{1,3}"), False),
    ("QQ",       re.compile(r"(?<![\w])[Qq][Qq][:：]?\s?\d{5,12}"), False),
]

# Opt-in with --strict. These use a capture group: only the captured span is
# replaced, preserving the surrounding cue word (e.g. "我叫<PII_NAME_1>").
STRICT_DETECTORS = [
    ("NAME", re.compile(r"(?:我叫|我是|联系人|患者|客户|本人|收件人|寄件人|甲方|乙方|尊敬的|你好，?)[:：]?\s*([\u4e00-\u9fa5]{2,4})"), True),
    ("ADDR", re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]*(?:省|市|区|县|镇|路|街|道|号|栋|单元|室|小区|大厦|广场|巷)[^\n，,。；;]{0,40}"), False),
]


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute(
        """CREATE TABLE IF NOT EXISTS vault (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               session_id TEXT NOT NULL,
               category TEXT NOT NULL,
               placeholder TEXT NOT NULL,
               original TEXT NOT NULL,
               created_at TEXT DEFAULT (datetime('now'))
           )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS meta (
               key TEXT PRIMARY KEY,
               value TEXT
           )"""
    )
    return c


# ---------------------------------------------------------------------------
# Optional encryption (opt-in; core stays standard-library only)
# ---------------------------------------------------------------------------
def _have_crypto():
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _meta_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
    conn.commit()


def encryption_enabled():
    """True only when the vault is marked encrypted AND a passphrase is set."""
    if not DB_PATH.exists():
        return False
    c = _conn()
    try:
        flag = _meta_get(c, "encryption")
    finally:
        c.close()
    return flag == "1" and bool(os.environ.get("AI_ROUTER_VAULT_PASSPHRASE"))


def _fernet(conn):
    """Build a Fernet from AI_ROUTER_VAULT_PASSPHRASE + per-vault salt."""
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    passphrase = os.environ.get("AI_ROUTER_VAULT_PASSPHRASE", "")
    if not passphrase:
        raise RuntimeError("AI_ROUTER_VAULT_PASSPHRASE is not set")
    salt_b64 = _meta_get(conn, "salt")
    if not salt_b64:
        salt_b64 = base64.urlsafe_b64encode(os.urandom(16)).decode()
        _meta_set(conn, "salt", salt_b64)
    salt = base64.urlsafe_b64decode(salt_b64.encode())
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=200_000)
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
    return Fernet(key)


def _encrypt(conn, value):
    if not value.startswith(ENC_PREFIX):
        value = ENC_PREFIX + _fernet(conn).encrypt(value.encode()).decode()
    return value


def _decrypt(conn, value):
    if not value.startswith(ENC_PREFIX):
        return value
    return _fernet(conn).decrypt(value[len(ENC_PREFIX):].encode()).decode()


def enable_encryption():
    """Encrypt every plaintext row in place. Requires the cryptography package
    and AI_ROUTER_VAULT_PASSPHRASE. Returns (rows_converted, message)."""
    if not _have_crypto():
        return 0, ("cryptography not installed -> encryption unavailable. "
                   "Install it with: pip install cryptography")
    c = _conn()
    try:
        rows = c.execute("SELECT id, original FROM vault").fetchall()
        n = 0
        for rid, original in rows:
            if not original.startswith(ENC_PREFIX):
                c.execute("UPDATE vault SET original=? WHERE id=?",
                          (_encrypt(c, original), rid))
                n += 1
        _meta_set(c, "encryption", "1")
        c.commit()
    finally:
        c.close()
    return n, f"encrypted {n} row(s); set AI_ROUTER_VAULT_PASSPHRASE to read them"


def disable_encryption():
    """Convert every encrypted row back to plaintext and clear the flag."""
    if not _have_crypto():
        return 0, "cryptography not installed -> cannot decrypt"
    c = _conn()
    try:
        rows = c.execute("SELECT id, original FROM vault").fetchall()
        n = 0
        for rid, original in rows:
            if original.startswith(ENC_PREFIX):
                c.execute("UPDATE vault SET original=? WHERE id=?",
                          (_decrypt(c, original), rid))
                n += 1
        _meta_set(c, "encryption", "0")
        c.commit()
    finally:
        c.close()
    return n, f"decrypted {n} row(s); vault is plaintext again"


def expire(days=DEFAULT_TTL_DAYS):
    """Delete vault rows older than `days` (0/None disables). Returns count."""
    if not days:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))
              ).strftime("%Y-%m-%d %H:%M:%S")
    c = _conn()
    try:
        n = c.execute("DELETE FROM vault WHERE created_at < ?", (cutoff,)).rowcount
        c.commit()
    finally:
        c.close()
    return n


def new_session():
    return uuid.uuid4().hex


def redact(text, session_id=None, custom=None, strict=False):
    """Replace PII in `text` with placeholders, storing the real values in the
    local vault under `session_id`. Returns a dict with the sanitized text,
    session id, a count and a per-category breakdown (values omitted)."""
    if session_id is None:
        session_id = new_session()
    seen = {}          # (category, original) -> placeholder
    counts = {}        # category -> count
    working = text

    def _replace(category, regex, use_group):
        nonlocal working
        for m in regex.finditer(working):
            original = m.group(1) if use_group else m.group(0)
            if not original:
                continue
            key = (category, original)
            if key in seen:
                placeholder = seen[key]
            else:
                idx = counts.get(category, 0) + 1
                counts[category] = idx
                placeholder = f"<PII_{category}_{idx}>"
                seen[key] = placeholder
                _store(session_id, category, placeholder, original)
            start, end = m.span(1) if use_group else m.span(0)
            working = working[:start] + placeholder + working[end:]

    if custom:
        for term in [t.strip() for t in custom.split(",") if t.strip()]:
            _replace("CUSTOM", re.compile(re.escape(term)), False)

    for category, regex, use_group in DETECTORS:
        _replace(category, regex, use_group)
    if strict:
        for category, regex, use_group in STRICT_DETECTORS:
            _replace(category, regex, use_group)

    total = sum(counts.values())
    breakdown = {k: v for k, v in counts.items()}
    # Sweep old sessions so a forgotten vault cannot grow forever.
    if DEFAULT_TTL_DAYS:
        try:
            dropped = expire(DEFAULT_TTL_DAYS)
            if dropped:
                sys.stderr.write(
                    f"[privacy] swept {dropped} row(s) older than {DEFAULT_TTL_DAYS}d\n")
        except Exception:  # noqa: BLE001
            pass
    return {
        "text": working,
        "session_id": session_id,
        "count": total,
        "breakdown": breakdown,
    }


def _store(session_id, category, placeholder, original):
    c = _conn()
    try:
        if _meta_get(c, "encryption") == "1":
            if os.environ.get("AI_ROUTER_VAULT_PASSPHRASE"):
                original = _encrypt(c, original)
            else:
                sys.stderr.write(
                    "[privacy] WARNING: vault is encrypted but "
                    "AI_ROUTER_VAULT_PASSPHRASE is not set -> storing plaintext\n")
        c.execute(
            "INSERT INTO vault (session_id, category, placeholder, original) VALUES (?,?,?,?)",
            (session_id, category, placeholder, original),
        )
        c.commit()
    finally:
        c.close()


def restore(text, session_id):
    """Replace every `<PII_*>` placeholder in `text` with its real value from the
    given vault session. Unknown placeholders are left untouched."""
    if _vault_flag_is_encrypted() and not os.environ.get("AI_ROUTER_VAULT_PASSPHRASE"):
        sys.stderr.write(
            "[privacy] ERROR: vault is encrypted; set "
            "AI_ROUTER_VAULT_PASSPHRASE to restore values\n")
        return text
    for placeholder, original in _mapping(session_id).items():
        if placeholder in text:
            text = text.replace(placeholder, original)
    return text


def _vault_flag_is_encrypted():
    if not DB_PATH.exists():
        return False
    c = _conn()
    try:
        return _meta_get(c, "encryption") == "1"
    finally:
        c.close()


def clear_session(session_id):
    c = _conn()
    try:
        n = c.execute("DELETE FROM vault WHERE session_id = ?", (session_id,)).rowcount
        c.commit()
    finally:
        c.close()
    return n


class StreamRestorer:
    """Restore placeholders incrementally while text is being streamed.

    A placeholder can be split across two chunks (`<PII_PHO` + `NE_1>`), so we
    hold back any trailing partial placeholder until it completes.
    """

    def __init__(self, session_id):
        self.session_id = session_id
        self.buf = ""
        self.mapping = _mapping(session_id)

    def feed(self, chunk):
        self.buf += chunk
        return self._drain(final=False)

    def flush(self):
        return self._drain(final=True)

    def _drain(self, final):
        out, pos = [], 0
        while True:
            i = self.buf.find(_PII_NEEDLE, pos)
            if i == -1:
                end = len(self.buf)
                if not final:
                    # Hold back a tail that could still grow into "<PII_"
                    # (e.g. a chunk boundary right after "电话<").
                    for k in range(len(_PII_NEEDLE) - 1, 0, -1):
                        if self.buf.endswith(_PII_NEEDLE[:k]):
                            end = len(self.buf) - k
                            break
                out.append(self.buf[pos:end])
                pos = end
                break
            j = self.buf.find(">", i)
            if j == -1:
                out.append(self.buf[pos:i])
                if final:
                    out.append(self.buf[i:])
                    pos = len(self.buf)
                else:
                    pos = i  # hold the partial placeholder
                break
            token = self.buf[i:j + 1]
            out.append(self.buf[pos:i])
            out.append(self.mapping.get(token, token))
            pos = j + 1
        self.buf = "" if final else self.buf[pos:]
        return "".join(out)


def _mapping(session_id):
    """Return {placeholder: original} for one session, decrypting if needed."""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT placeholder, original FROM vault WHERE session_id = ?",
            (session_id,)).fetchall()
        encrypted = _meta_get(c, "encryption") == "1"
        mapping = {}
        for placeholder, original in rows:
            if encrypted and original.startswith(ENC_PREFIX):
                if not os.environ.get("AI_ROUTER_VAULT_PASSPHRASE"):
                    continue
                original = _decrypt(c, original)
            mapping[placeholder] = original
    finally:
        c.close()
    return mapping


def clear_all():
    c = _conn()
    try:
        n = c.execute("DELETE FROM vault").rowcount
        c.commit()
    finally:
        c.close()
    return n


def list_sessions():
    c = _conn()
    try:
        rows = c.execute(
            "SELECT session_id, COUNT(*) AS n, GROUP_CONCAT(DISTINCT category) AS cats "
            "FROM vault GROUP BY session_id ORDER BY MAX(created_at) DESC"
        ).fetchall()
    finally:
        c.close()
    return [{"session_id": r[0], "count": r[1], "categories": r[2]} for r in rows]


def export_session(session_id):
    c = _conn()
    try:
        rows = c.execute(
            "SELECT category, placeholder, original, created_at FROM vault "
            "WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        encrypted = _meta_get(c, "encryption") == "1"
        if encrypted:
            out = []
            for category, placeholder, original, created_at in rows:
                if original.startswith(ENC_PREFIX) and os.environ.get("AI_ROUTER_VAULT_PASSPHRASE"):
                    original = _decrypt(c, original)
                out.append((category, placeholder, original, created_at))
            rows = out
    finally:
        c.close()
    return [
        {"category": r[0], "placeholder": r[1], "original": r[2], "created_at": r[3]}
        for r in rows
    ]


def main():
    ap = argparse.ArgumentParser(description="Local PII vault: redact / restore / manage.")
    ap.add_argument("--redact", metavar="TEXT", help="redact PII in TEXT, print sanitized output")
    ap.add_argument("--restore", action="store_true", help="restore placeholders in --text")
    ap.add_argument("--text", help="text to restore (used with --restore)")
    ap.add_argument("--session", help="vault session id")
    ap.add_argument("--custom", help="comma-separated extra terms to force-redact")
    ap.add_argument("--strict", action="store_true", help="also heuristic name/address matching")
    ap.add_argument("--list", action="store_true", help="list vault sessions")
    ap.add_argument("--export", action="store_true", help="dump a session's mappings (owner audit)")
    ap.add_argument("--clear", action="store_true", help="delete one session")
    ap.add_argument("--clear-all", action="store_true", help="delete the entire vault")
    ap.add_argument("--expire", type=int, metavar="DAYS",
                    help="delete rows older than DAYS (0 disables)")
    ap.add_argument("--encrypt", action="store_true",
                    help="encrypt existing rows (needs cryptography + AI_ROUTER_VAULT_PASSPHRASE)")
    ap.add_argument("--decrypt", action="store_true", help="revert rows to plaintext")
    ap.add_argument("--status", action="store_true", help="show vault TTL/encryption status")
    args = ap.parse_args()

    if args.status:
        enc = "on" if encryption_enabled() else "off"
        c = _conn()
        try:
            flag = _meta_get(c, "encryption") or "0"
            rows = c.execute("SELECT COUNT(*) FROM vault").fetchone()[0]
        finally:
            c.close()
        print(f"vault: {DB_PATH}")
        print(f"rows: {rows}")
        print(f"encryption flag: {flag} ({enc} effective)")
        print(f"ttl days: {DEFAULT_TTL_DAYS or 'disabled'}")
        print(f"cryptography available: {_have_crypto()}")
        return
    if args.expire is not None:
        n = expire(args.expire)
        print(f"expired {n} row(s) older than {args.expire} day(s)")
        return
    if args.encrypt:
        n, msg = enable_encryption()
        print(msg)
        return
    if args.decrypt:
        n, msg = disable_encryption()
        print(msg)
        return

    if args.clear_all:
        n = clear_all()
        print(f"cleared all sessions ({n} rows)")
        return
    if args.list:
        for s in list_sessions():
            print(f"{s['session_id']}  items={s['count']}  categories={s['categories']}")
        return
    if args.export:
        sid = args.session or _require_session()
        for r in export_session(sid):
            print(f"{r['category']:10} {r['placeholder']:18} -> {r['original']}  ({r['created_at']})")
        return
    if args.clear:
        sid = args.session or _require_session()
        n = clear_session(sid)
        print(f"cleared session {sid} ({n} rows)")
        return
    if args.restore:
        sid = args.session or _require_session()
        if not args.text:
            ap.error("--restore requires --text")
        print(restore(args.text, sid))
        return
    if args.redact is not None:
        res = redact(args.redact, args.session, args.custom, args.strict)
        print(res["text"])
        sys.stderr.write(
            f"[privacy] session={res['session_id']} redacted={res['count']} "
            f"breakdown={res['breakdown']}\n"
        )
        return
    ap.print_help()


def _require_session():
    sys.stderr.write("[privacy] ERROR: --session is required for this action\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
