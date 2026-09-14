"""Manual, hardware-free Ollama connectivity and inference check.

From backend: python scripts/test_ollama.py
Uses the application's OLLAMA_* settings and bounded check_ollama_connection.
Start the configured Ollama server and provision the exact model tag separately;
this script does not download models, enable voice, or execute production tools.
"""

import asyncio
import json
import sys
from pathlib import Path

# Support direct execution without an editable install or a PYTHONPATH override.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.llm import check_ollama_connection


async def main() -> None:
    result = await check_ollama_connection()
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())