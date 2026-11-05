"""Minimal .env loader using only the Python standard library.

Loads the .env file located at the skill root so that call_model.py and
judge.py work out of the box without python-dotenv installed.
Existing environment variables always take precedence.
"""
import os


def load_env_file():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if val and key not in os.environ:
                os.environ[key] = val
