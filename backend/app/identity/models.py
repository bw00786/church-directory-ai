"""Identity recognition data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FaceMatch:
    person_id: str | None
    name: str
    role: str | None
    confidence: float
    is_known: bool


@dataclass
class VoiceMatch:
    person_id: str | None
    name: str
    role: str | None
    confidence: float
    is_known: bool
    activity: str  # "singing" | "speech" | "silence"
