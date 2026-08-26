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
    """A verification signal from the vision subsystem (WO-VISION-1).

    Never an actor: this only *observes* (occupancy, framing, output health) so
    Claude can reason and PTZ actions can be verified. Anonymous — no identity.
    """

    input: str = "program"                 # capture input tag
    camera_id: Optional[int] = None
    role: Optional[str] = None
    person_present: bool = False
    person_in_roi: bool = False
    subject_dx: float = 0.0                 # normalized offset from ROI center
    subject_dy: float = 0.0
    frame_health: str = "ok"               # ok | black | frozen | no_frame
    confidence: float = 0.0
    timestamp: datetime = Field(default_factory=_utcnow)

    @property
    def offset_magnitude(self) -> float:
        return (self.subject_dx ** 2 + self.subject_dy ** 2) ** 0.5


class TranscriptResult(BaseModel):
    """A Whisper (or heuristic) transcription result for one speech segment."""

    text: str
    confidence: float
    start_time: float
    end_time: float
