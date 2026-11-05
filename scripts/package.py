#!/usr/bin/env python3
"""
package.py - Build a clean, secret-free distributable zip for ai-router.

Zero dependencies (Python stdlib only). Unlike generic folder packagers, this
script explicitly excludes secrets, VCS metadata, caches and generated
archives, then scans every bundled file for leaked API keys before writing
anything. Use it instead of zipping the folder by hand.

Usage:
    python scripts/package.py                 # writes ./ai-router.zip
    python scripts/package.py --output dist  # writes dist/ai-router.zip
    python scripts/package.py --check-only   # validate + scan, no zip written
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = ROOT.name

EXCLUDE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", ".idea", ".vscode",
    "dist", "build", ".pytest_cache", ".mypy_cache",
}
EXCLUDE_NAMES = {".env", ".DS_Store", "Thumbs.db", "privacy_vault.db"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip", ".log", ".bak"}

# Coarse but effective: real keys are long random strings. Short placeholders
# such as `sk-tp-xxx` in the docs deliberately do not match.
KEY_PATTERNS = [
    re.compile(rb"ark-[0-9a-fA-F-]{24,}"),
    re.compile(rb"sk-(?!tp-xxx)[A-Za-z0-9_-]{24,}"),
    re.compile(rb"sk-tp-[A-Za-z0-9_-]{16,}"),
]

REQUIRED = ["SKILL.md", "README.md", "LICENSE", "scripts/call_model.py"]


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
        return True
    return False


def collect():
    files = []
    for p in sorted(ROOT.rglob("*")):
        if p.is_file() and not should_skip(p):
            files.append(p)
    return files


def validate(files):
    errors = []
    relnames = {str(p.relative_to(ROOT)).replace("\\", "/") for p in files}
    for req in REQUIRED:
        if req not in relnames:
            errors.append("missing required file: " + req)
    skill = ROOT / "SKILL.md"
    if skill.exists():
        text = skill.read_text(encoding="utf-8")
        parts = text.split("---")
        if not text.startswith("---") or len(parts) < 3:
            errors.append("SKILL.md missing YAML frontmatter")
        else:
            head = parts[1]
            for key in ("name:", "description:"):
                if key not in head:
                    errors.append("SKILL.md frontmatter missing " + key)
            if "<" in head or ">" in head:
                errors.append("SKILL.md frontmatter contains angle brackets")
    return errors


def scan(files):
    hits = []
    for p in files:
        data = p.read_bytes()
        for pat in KEY_PATTERNS:
            m = pat.search(data)
            if m:
                hits.append((str(p.relative_to(ROOT)), m.group(0)[:12].decode("ascii", "replace") + "..."))
                break
    return hits


def main():
    ap = argparse.ArgumentParser(description="Package ai-router into a clean zip")
    ap.add_argument("--output", default=".", help="output directory (default: cwd)")
    ap.add_argument("--check-only", action="store_true", help="validate and scan only")
    args = ap.parse_args()

    files = collect()
    errors = validate(files)
    hits = scan(files)

    print(f"skill root : {ROOT}")
    print(f"files      : {len(files)}")
    for p in files:
        print("  +", p.relative_to(ROOT).as_posix())

    if errors:
        print("\n[VALIDATION FAILED]")
        for e in errors:
            print("  -", e)
        return 1
    print("\nvalidation : OK (required files + SKILL.md frontmatter)")

    if hits:
        print("\n[SECRET SCAN FAILED] refusing to package")
        for name, sample in hits:
            print("  -", name, "->", sample)
        return 1
    print("secret scan: CLEAN (no API key patterns, .env excluded)")

    if args.check_only:
        print("\n--check-only: no zip written")
        return 0

    out_dir = Path(args.output).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{NAME}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path(NAME) / p.relative_to(ROOT))
    print(f"\npackaged   : {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
