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
"""
import argparse
import re
import sqlite3
import sys
import uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "privacy_vault.db"

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
    return c


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
    return {
        "text": working,
        "session_id": session_id,
        "count": total,
        "breakdown": breakdown,
    }


def _store(session_id, category, placeholder, original):
    c = _conn()
    try:
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
    c = _conn()
    try:
        rows = c.execute(
            "SELECT placeholder, original FROM vault WHERE session_id = ?", (session_id,)
        ).fetchall()
    finally:
        c.close()
    for placeholder, original in rows:
        if placeholder in text:
            text = text.replace(placeholder, original)
    return text


def clear_session(session_id):
    c = _conn()
    try:
        n = c.execute("DELETE FROM vault WHERE session_id = ?", (session_id,)).rowcount
        c.commit()
    finally:
        c.close()
    return n


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
    args = ap.parse_args()

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
