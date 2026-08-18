"""Vision subsystem data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


class VisionEventType(str, Enum):
    PERSON_ENTERED = "PERSON_ENTERED"
    PERSON_LEFT = "PERSON_LEFT"
    PERSON_COUNT_CHANGED = "PERSON_COUNT_CHANGED"
    PERSON_CENTERED = "PERSON_CENTERED"
    PERSON_OFF_CENTER = "PERSON_OFF_CENTER"
    FACE_DETECTED = "FACE_DETECTED"
    FACE_LOST = "FACE_LOST"
    LIKELY_SPEAKER = "LIKELY_SPEAKER"
    SUBJECT_TOO_WIDE = "SUBJECT_TOO_WIDE"
    SUBJECT_TOO_CLOSE = "SUBJECT_TOO_CLOSE"
    GOOD_COMPOSITION = "GOOD_COMPOSITION"
    POOR_COMPOSITION = "POOR_COMPOSITION"
    CONGREGATION_ACTIVE = "CONGREGATION_ACTIVE"
    MUSIC_ACTIVE = "MUSIC_ACTIVE"
    MUSIC_INACTIVE = "MUSIC_INACTIVE"
    CAMERA_AVAILABLE = "CAMERA_AVAILABLE"
    CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"
    CAMERA_QUALITY_CHANGED = "CAMERA_QUALITY_CHANGED"


class ShotClassification(str, Enum):
    EXTREME_CLOSE = "EXTREME_CLOSE"
    CLOSE = "CLOSE"
    MEDIUM_CLOSE = "MEDIUM_CLOSE"
    MEDIUM = "MEDIUM"
    MEDIUM_WIDE = "MEDIUM_WIDE"
    WIDE = "WIDE"
    EXTREME_WIDE = "EXTREME_WIDE"


class VideoSourceType(str, Enum):
    RTSP = "RTSP"
    USB = "USB"
    FILE = "FILE"
    TEST_PATTERN = "TEST_PATTERN"


@dataclass
class FrameMetadata:
    camera_id: int
    source: str
    timestamp: float
    width: int
    height: int


@dataclass
class DetectorObject:
    class_name: str
    confidence: float
    bbox: List[int]
    metadata: Dict[str, Any] = None


@dataclass
class DetectorResult:
    detector: str
    timestamp: float
    camera_id: int
    objects: List[DetectorObject]
    metadata: Dict[str, Any] = None


@dataclass
class PersonTrack:
    person_id: int
    bbox: List[int]
    confidence: float
    first_seen: float
    last_seen: float
    velocity: float
    position: str
    camera_id: int
    active: bool = True


@dataclass
class CameraQuality:
    camera_id: int
    framing_score: float
    subject_visibility: float
    composition_score: float
    stability_score: float
    overall_score: float
    shot: ShotClassification
    subject_count: int


@dataclass
class VisionObservation:
    observation_id: str
    timestamp: float
    camera_id: int
    event_type: Optional[VisionEventType]
    confidence: float
    duration: float
    payload: Dict[str, Any]
    correlation_id: str


@dataclass
class VisionEvent:
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

    def __init__(self, event_type: VisionEventType, camera_id: int, source: str, confidence: float, duration: float, payload: Dict[str, Any], observation_id: str, correlation_id: str):
        self.id = str(uuid4())
        self.timestamp = datetime.utcnow().timestamp()
        self.event_type = event_type
        self.camera_id = camera_id
        self.source = source
        self.confidence = confidence
        self.duration = duration
        self.payload = payload
        self.observation_id = observation_id
        self.correlation_id = correlation_id
