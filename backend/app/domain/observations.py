"""Structured observations fed into the Service State engine and AI Director."""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AudioObservation(BaseModel):
    """One observation from a mixer channel (see app.audio.audio_observer)."""

    channel: int
    speaker_role: str  # "pastor" | "liturgist" | "vocalist" | "congregation"
    speaking: bool
    transcript: str = ""
    confidence: float = 0.0
    timestamp: datetime = Field(default_factory=_utcnow)
    duration_ms: int = 0


class VisionObservation(BaseModel):
    """Verification signal from the vision subsystem (not the primary driver)."""

    camera_id: int
    person_detected: bool
    role: Optional[str] = None
    confidence: float = 0.0
    timestamp: datetime = Field(default_factory=_utcnow)


class TranscriptResult(BaseModel):
    """A Whisper (or heuristic) transcription result for one speech segment."""

    text: str
    confidence: float
    start_time: float
    end_time: float
