"""Manual, hardware-free Claude (Anthropic) connectivity and inference check.

From backend: python scripts/test_claude.py
Uses the application's ANTHROPIC_* settings and the bounded
check_anthropic_connection. Requires ANTHROPIC_API_KEY in backend/.env; this
script does not execute production tools.
"""

import asyncio
import json
import sys
from pathlib import Path

# Support direct execution without an editable install or a PYTHONPATH override.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.llm import check_anthropic_connection


async def main() -> None:
    result = await check_anthropic_connection()
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
