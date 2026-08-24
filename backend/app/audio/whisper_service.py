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

import numpy as np

from app.config import settings
from app.domain.observations import TranscriptResult
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
