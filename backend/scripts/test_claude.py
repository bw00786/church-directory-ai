"""Quick manual check that the Anthropic API key/connection works.

Usage: python scripts/test_claude.py
Requires ANTHROPIC_API_KEY to be set (e.g. in backend/.env).
"""

import asyncio

from app.agents.llm import check_anthropic_connection


async def main():
    result = await check_anthropic_connection()
    print(result)
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
