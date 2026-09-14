"""Synthesize and validate a local voice sample without opening an audio device."""

import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.voice.config import TTSSettings, VoiceSettings
from app.voice.models import Priority
from app.voice.playback import decode_audio
from app.voice.prosody import prosody_for
from app.voice.provider import build_provider


async def main():
    tts, voice = TTSSettings(), VoiceSettings()
    started = time.monotonic()
    audio = await build_provider(tts).synthesize(
        "This is a test of the AI Production Director headset.",
        voice.persona, prosody_for(Priority.ATTENTION, voice.persona),
    )
    samples, rate = decode_audio(audio)
    print(json.dumps({"provider": tts.provider, "sample_rate": rate,
                      "audio_seconds": round(len(samples) / rate, 2),
                      "synthesis_seconds": round(time.monotonic() - started, 2),
                      "audio_played": False}))


if __name__ == "__main__":
    asyncio.run(main())