"""Vision event model definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict
from uuid import uuid4

from .models import VisionEventType


@dataclass
class VisionEventPayload:
    details: Dict[str, Any]


@dataclass
class VisionEventRecord:
    id: str
    timestamp: float
    event_type: VisionEventType
    camera_id: int
    source: str
    confidence: float
    duration: float
    observation_id: str
    payload: Dict[str, Any]
    correlation_id: str

    @classmethod
    def create(
        cls,
        event_type: VisionEventType,
        camera_id: int,
        source: str,
        confidence: float,
        duration: float,
        observation_id: str,
        payload: Dict[str, Any],
        correlation_id: str,
    ) -> "VisionEventRecord":
        return cls(
            id=str(uuid4()),
            timestamp=datetime.utcnow().timestamp(),
            event_type=event_type,
            camera_id=camera_id,
            source=source,
            confidence=confidence,
            duration=duration,
            observation_id=observation_id,
            payload=payload,
            correlation_id=correlation_id,
        )
