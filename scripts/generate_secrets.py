#!/usr/bin/env python3
"""Fill empty/placeholder secrets in a .env file with random values (idempotent).

Usage: python scripts/generate_secrets.py [.env]
Keys: API_KEY, API_ADMIN_KEY, SECRET_KEY, POSTGRES_PASSWORD, EASYWORSHIP_AGENT_TOKEN.
Existing non-placeholder values are kept.
"""

import re
import secrets
import sys
from pathlib import Path

KEYS = {
    "API_KEY": 32,
    "API_ADMIN_KEY": 32,
    "SECRET_KEY": 48,
    "POSTGRES_PASSWORD": 18,
    "EASYWORSHIP_AGENT_TOKEN": 32,
}
PLACEHOLDERS = ("", "changeme", "your-secret-key-here-change-in-production")


def main(path: str) -> int:
    env = Path(path)
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    changed = []
    for key, nbytes in KEYS.items():
        pattern = re.compile(rf"^(?P<k>{key})=(?P<v>[^#\n]*)(?P<rest>.*)$", re.M)
        match = pattern.search(text)
        value = secrets.token_urlsafe(nbytes)
        if match is None:
            text += f"\n{key}={value}\n"
            changed.append(key)
        elif match.group("v").strip().strip('"') in PLACEHOLDERS:
            text = pattern.sub(lambda m: f"{m.group('k')}={value}{m.group('rest')}", text, count=1)
            changed.append(key)
    env.write_text(text, encoding="utf-8")
    print("generated: " + (", ".join(changed) if changed else "nothing (all set)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ".env"))
