"""Whisper speech-to-text as a standalone service.

This only produces transcripts for audio that actually has PCM available —
today that's the local mic/line-in capture in app.identity.audio_capture
(the Yamaha meter feed has no raw audio). It's structured as a generic
`transcribe(audio_chunk)` service so it can be pointed at any future raw
per-channel audio source without changing callers.

Lazily imports faster-whisper (preferred) or openai-whisper; if neither is
installed, or ENABLE_WHISPER is false, `transcribe()` returns None so the
rest of the pipeline degrades gracefully to VAD-only observations.
"""

from functools import lru_cache
from typing import Optional

import asyncio

import numpy as np

from app.config import settings
from app.domain.observations import AudioObservation, TranscriptResult
from app.domain.service_context import service_context
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)


class WhisperService:
    """Transcribes PCM audio chunks. No-op if whisper isn't installed/enabled."""

    def __init__(self) -> None:
        self._model = None
        self._backend: Optional[str] = None
        if settings.enable_whisper:
            self._model, self._backend = self._load_model()

    def _load_model(self):
        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(
                settings.whisper_model_size,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
            logger.info("Whisper backend loaded", backend="faster-whisper", model=settings.whisper_model_size)
            return model, "faster-whisper"
        except ImportError:
            pass
        try:
            import whisper

            model = whisper.load_model(settings.whisper_model_size)
            logger.info("Whisper backend loaded", backend="openai-whisper", model=settings.whisper_model_size)
            return model, "openai-whisper"
        except ImportError:
            logger.warning(
                "ENABLE_WHISPER=true but neither faster-whisper nor openai-whisper is installed; "
                "transcription disabled"
            )
            return None, None

    @property
    def available(self) -> bool:
        return self._model is not None

    def transcribe(self, audio_chunk: np.ndarray, sample_rate: int = 16000) -> Optional[TranscriptResult]:
        """Transcribe one PCM float32 audio segment. Returns None if unavailable."""
        if not self.available:
            return None

        try:
            if self._backend == "faster-whisper":
                segments, _info = self._model.transcribe(audio_chunk, language="en")
                text = " ".join(seg.text.strip() for seg in segments)
                avg_logprob = 0.0
                return TranscriptResult(
                    text=text.strip(),
                    confidence=1.0 if text else 0.0,
                    start_time=0.0,
                    end_time=len(audio_chunk) / sample_rate,
                )
            else:  # openai-whisper
                result = self._model.transcribe(audio_chunk, language="en", fp16=False)
                return TranscriptResult(
                    text=str(result.get("text", "")).strip(),
                    confidence=1.0,
                    start_time=0.0,
                    end_time=len(audio_chunk) / sample_rate,
                )
        except Exception:
            logger.exception("Whisper transcription failed")
            return None


@lru_cache(maxsize=1)
def get_whisper_service() -> WhisperService:
    return WhisperService()


# --- Multichannel, per-role, VAD-gated transcription (WO-MGX-USB-1) ---------

MAX_UTTERANCE_SECONDS = 15.0
MIN_UTTERANCE_SECONDS = 0.4


def whisper_roles() -> set[str]:
    """Roles that receive per-channel ASR, from WHISPER_ROLES config."""
    return {r.strip().lower() for r in settings.whisper_roles.split(",") if r.strip()}


class MultiChannelTranscriber:
    """Accumulates VAD-gated speech per role and transcribes each independently.

    ``feed`` is called per USB frame with the channel's current VAD state; speech
    is buffered while a role is speaking and flushed (transcribed) when it goes
    silent or the utterance grows too long. A Whisper failure on one role marks
    only that role degraded — other roles keep transcribing (WO section 6).
    Silence is never streamed into Whisper.
    """

    def __init__(self, roles: Optional[set[str]] = None, sample_rate: int = 16000):
        self.roles = roles if roles is not None else whisper_roles()
        self.sample_rate = sample_rate
        self._buffers: dict[str, list[np.ndarray]] = {}
        self._start_ts: dict[str, float] = {}
        self._prev_speaking: dict[str, bool] = {}
        self._failed_roles: set[str] = set()

    def transcribes(self, role: str) -> bool:
        return role in self.roles

    def feed(self, role: str, channel: int, pcm: np.ndarray, speaking: bool, t: float) -> None:
        if role not in self.roles:
            return
        prev = self._prev_speaking.get(role, False)
        if speaking:
            if not prev or role not in self._buffers:
                self._start_ts[role] = t
                self._buffers[role] = []
            self._buffers[role].append(np.asarray(pcm, dtype=np.float32).reshape(-1))
            total = sum(b.size for b in self._buffers[role])
            if total >= MAX_UTTERANCE_SECONDS * self.sample_rate:
                self._flush(role, channel, t)
        elif prev and role in self._buffers:
            self._flush(role, channel, t)
        self._prev_speaking[role] = speaking

    def _flush(self, role: str, channel: int, t_end: float) -> None:
        chunks = self._buffers.pop(role, [])
        t_start = self._start_ts.pop(role, t_end)
        if not chunks:
            return
        audio = np.concatenate(chunks)
        if audio.size < MIN_UTTERANCE_SECONDS * self.sample_rate:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(self._transcribe_async(role, channel, audio, t_start, t_end))
        else:
            self._emit(role, channel, self._run_whisper(role, audio), t_start, t_end)

    async def _transcribe_async(self, role, channel, audio, t_start, t_end) -> None:
        result = await asyncio.to_thread(self._run_whisper, role, audio)
        self._emit(role, channel, result, t_start, t_end)

    def _run_whisper(self, role: str, audio: np.ndarray) -> Optional[TranscriptResult]:
        try:
            return get_whisper_service().transcribe(audio, self.sample_rate)
        except Exception:
            logger.exception("Whisper failed for role; degrading it to VAD-only", role=role)
            self._failed_roles.add(role)
            return None

    def _emit(self, role, channel, result: Optional[TranscriptResult], t_start, t_end) -> None:
        if result is None or not result.text:
            return
        service_context.record_audio(
            AudioObservation(
                channel=channel,
                speaker_role=role,
                speaking=True,
                transcript=result.text,
                confidence=result.confidence,
                duration_ms=int(max(0.0, t_end - t_start) * 1000),
            )
        )
        event_bus.publish(
            {
                "event": "TRANSCRIPT",
                "payload": {
                    "role": role,
                    "text": result.text,
                    "t_start": t_start,
                    "t_end": t_end,
                },
            }
        )

    def status(self) -> dict:
        return {
            "roles": sorted(self.roles),
            "failed_roles": sorted(self._failed_roles),
            "asr_available": get_whisper_service().available,
        }


# Module-level singleton
multichannel_transcriber = MultiChannelTranscriber()
