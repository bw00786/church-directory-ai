"""Local audio-device capture feeding real PCM into voice diarization.

The Yamaha MGX16 has no raw-audio remote protocol -- only per-channel RMS via
its meter feed (see app.mixer.service) -- so there is no way to pull the
console's actual waveform over the network. This module instead captures
real audio from a local input device (e.g. the mixer's monitor/aux output,
or a room mic, physically wired into the line-in/USB audio interface of the
machine running the backend) and feeds genuine PCM windows into
IdentityService.identify_voice() for diarization. This is what makes voice
recognition operate on real audio rather than requiring a manual push to
POST /api/identity/voice/frame.
"""

from __future__ import annotations

import asyncio
import queue
import time
from typing import Any

import numpy as np

from app.config import settings as app_settings
from app.domain.observations import AudioObservation
from app.domain.service_context import service_context
from app.events.bus import event_bus
from app.identity.config import IdentitySettings
from app.identity.service import identity_service
from app.logging_config import get_logger
from app.vision.executors import run_in

logger = get_logger(__name__)

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - optional dependency; PortAudio may also
    # fail to initialise at import time on hosts with no audio server (CI).
    sd = None  # type: ignore


class AudioCaptureService:
    """Captures a local audio input device and runs it through voice diarization."""

    def __init__(self) -> None:
        settings = IdentitySettings()
        self.enabled = settings.enable_audio_capture
        self.device = settings.audio_capture_device
        self.sample_rate = settings.audio_capture_sample_rate
        self.window_seconds = settings.audio_capture_window_seconds
        self.channel_name = settings.audio_capture_channel_name
        self.role = settings.audio_capture_role or settings.audio_capture_channel_name

        self._stream: Any = None
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue()
        self._task: asyncio.Task | None = None
        self._running = False
        self.recent: list[dict[str, Any]] = []

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Audio capture disabled (ENABLE_AUDIO_CAPTURE=false)")
            return
        if sd is None:
            logger.warning("`sounddevice` not installed; local audio capture unavailable")
            return
        if self._running:
            return

        try:
            self._stream = sd.InputStream(
                device=self.device,
                channels=1,
                samplerate=self.sample_rate,
                dtype="float32",
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception:
            logger.exception("Failed to open audio capture device")
            self._stream = None
            return

        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("Audio capture started", device=self.device, sample_rate=self.sample_rate)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _on_audio(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if status:
            logger.warning("Audio capture status", status=str(status))
        self._queue.put(indata[:, 0].copy())

    async def _process_loop(self) -> None:
        window_samples = int(self.sample_rate * self.window_seconds)
        buffer = np.empty(0, dtype=np.float32)

        while self._running:
            try:
                chunk = await asyncio.to_thread(self._queue.get, True, 1.0)
            except queue.Empty:
                continue

            buffer = np.concatenate([buffer, chunk])
            if buffer.size < window_samples:
                continue

            window, buffer = buffer[:window_samples], buffer[window_samples:]
            # Diarization (embedding + match) and Whisper each take hundreds of
            # ms to seconds on CPU; run them on their own pools so neither
            # blocks the event loop nor starves the other / OCR / detection.
            try:
                result = await run_in(
                    "diarization", identity_service.identify_voice, self.channel_name, window, self.sample_rate
                )
            except Exception:
                logger.exception("Voice identification failed on captured audio")
                continue

            transcript = ""
            if app_settings.enable_whisper:
                try:
                    transcript = await run_in("whisper", self._transcribe, window)
                except Exception:
                    logger.exception("Whisper transcription failed on captured audio")

            result["timestamp"] = time.time()
            result["transcript"] = transcript
            self.recent.append(result)
            self.recent = self.recent[-50:]
            event_bus.publish({"event": "VOICE_OBSERVATION", "payload": result})

            speaking = result.get("activity") != "silence"
            service_context.record_audio(
                AudioObservation(
                    channel=-1,
                    speaker_role=self.role,
                    speaking=speaking,
                    transcript=transcript,
                    confidence=float(result.get("confidence", 0.0) or 0.0),
                    duration_ms=int(self.window_seconds * 1000),
                )
            )

    def _transcribe(self, window: np.ndarray) -> str:
        """Blocking Whisper call; runs on the ``whisper`` pool."""
        from app.audio.whisper_service import get_whisper_service

        transcribed = get_whisper_service().transcribe(window, self.sample_rate)
        return transcribed.text if transcribed is not None else ""

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.recent[-limit:]

    def get_status(self) -> dict[str, Any]:
        return {
            "available": sd is not None,
            "enabled": self.enabled,
            "running": self._running,
            "device": self.device,
            "sample_rate": self.sample_rate,
            "window_seconds": self.window_seconds,
            "channel_name": self.channel_name,
        }


audio_capture_service = AudioCaptureService()
