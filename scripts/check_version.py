#!/usr/bin/env python3
"""Fail when backend/app/version.py, backend/pyproject.toml and frontend/package.json disagree."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    app_version = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', (ROOT / "backend/app/version.py").read_text()).group(1)
    py_version = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "backend/pyproject.toml").read_text(), re.M).group(1)
    js_version = json.loads((ROOT / "frontend/package.json").read_text())["version"]
    versions = {"backend/app/version.py": app_version, "backend/pyproject.toml": py_version, "frontend/package.json": js_version}
    if len(set(versions.values())) != 1:
        for path, version in versions.items():
            print(f"{path}: {version}")
        print("version mismatch", file=sys.stderr)
        return 1
    print(app_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
