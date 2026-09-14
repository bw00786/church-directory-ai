"""Local file-only Piper synthesis, isolated so mute can terminate inference."""

import asyncio
import math
import re
import sys
import tempfile
from pathlib import Path

from ..config import TTSSettings
from ..models import AudioResult, ProsodyConfig, VoiceConfig
from ..playback import decode_audio

VOICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")


class PiperProvider:
    def __init__(self, config: TTSSettings):
        self.config = config

    def model_paths(self, voice_id: str):
        name = voice_id or self.config.piper_voice
        if not VOICE_ID.fullmatch(name):
            raise ValueError("Piper voice ID must be a local model name, not a path or URL")
        root = Path(self.config.piper_model_dir).expanduser().resolve()
        paths = [(root / (name + suffix)).resolve() for suffix in (".onnx", ".onnx.json")]
        if any(not p.is_relative_to(root) for p in paths):
            raise ValueError("Piper model must remain inside the configured voice directory")
        if not all(p.is_file() for p in paths):
            raise ValueError("Piper voice files are missing; download the model and configuration before testing")
        return paths

    async def synthesize(self, text: str, voice_config: VoiceConfig, prosody: ProsodyConfig) -> AudioResult:
        if not text.strip() or len(text) > 1000:
            raise ValueError("Piper requires 1..1000 characters of notification text")
        rate = prosody.speaking_rate
        if not math.isfinite(rate) or not .5 <= rate <= 1.5:
            raise ValueError("Invalid Piper speaking rate")
        model, config = self.model_paths(voice_config.voice_id)
        with tempfile.TemporaryDirectory(prefix="church-tts-") as directory:
            output = Path(directory) / "speech.wav"
            # Never omit --output-file: Piper must not play to the system default device.
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "piper", "--model", str(model), "--config", str(config),
                "--output-file", str(output), "--length-scale", str(1 / rate),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.communicate((text.strip() + "\n").encode("utf-8")), self.config.timeout_seconds)
                if process.returncode != 0:
                    raise RuntimeError(f"Piper synthesis failed (exit {process.returncode}); check installed runtime and voice files")
                if not output.is_file() or not 0 < output.stat().st_size <= 3_000_000:
                    raise ValueError("Piper returned missing, empty or oversized audio")
                audio = AudioResult(data=output.read_bytes())
                decode_audio(audio)
                return audio
            finally:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()