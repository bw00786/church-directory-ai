"""Service Replay Mode.

Replays a recorded sequence of audio observations through the AI Service
Director (Claude) without touching any hardware, so decisions can be reviewed
against an actual Sunday service before trusting AI_DIRECTED mode live.

Usage:
    python scripts/replay_service.py path/to/recording.json

Recording format (list of AudioObservation-shaped dicts, in chronological
order):
    [
      {"channel": 2, "speaker_role": "liturgist", "speaking": true,
       "transcript": "Let us worship together...", "confidence": 0.95},
      ...
    ]

Each entry is fed into a fresh ServiceContext, then AIServiceDirector.decide()
is called and the resulting decision is printed — no ActionEngine, no policy
engine, no hardware calls.
"""

import asyncio
import json
import sys
from pathlib import Path

from app.ai.service_director import ai_service_director
from app.domain.observations import AudioObservation
from app.domain.service_context import ServiceContext


async def replay(path: Path) -> None:
    entries = json.loads(path.read_text(encoding="utf-8"))
    context = ServiceContext()

    for i, entry in enumerate(entries):
        observation = AudioObservation(**entry)
        context.record_audio(observation)

        decision = await ai_service_director.decide(context)
        print(f"--- step {i} ---")
        print(f"observation: {observation.speaker_role} speaking={observation.speaking} "
              f"transcript={observation.transcript!r}")
        print(f"decision: {decision.decision} (confidence={decision.confidence:.2f}) — {decision.reason}")
        for action in decision.actions:
            print(f"  proposed action: {action.type} {action.camera_role or action.easyworship_item or ''}")
        print()


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/replay_service.py path/to/recording.json")
        sys.exit(1)
    asyncio.run(replay(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
