"""Audio observation abstraction and mock provider for multimodal vision events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class AudioObservation:
    channel: str
    level: float
    active: bool
    confidence: float
    timestamp: float


class AudioProvider:
    async def get_observation(self) -> AudioObservation | None:
        raise NotImplementedError


class MockAudioProvider(AudioProvider):
    def __init__(self, channel: str = "pastor_mic"):
        self.channel = channel

    async def get_observation(self) -> AudioObservation | None:
        now = datetime.utcnow().timestamp()
        return AudioObservation(
            channel=self.channel,
            level=0.83,
            active=True,
            confidence=0.9,
            timestamp=now,
        )
