"""Event aggregation and debouncing for vision observations."""

import asyncio
import time
from typing import Any, Dict, List
from uuid import uuid4

from .models import CameraQuality, VisionEventType
from .events import VisionEventRecord


class EventAggregator:
    def __init__(self, debounce_seconds: float = 1.0, threshold: float = 0.75):
        self.debounce_seconds = debounce_seconds
        self.threshold = threshold
        self._pending: Dict[str, dict[str, Any]] = {}
        self._active_events: Dict[str, VisionEventRecord] = {}
        self._lock = asyncio.Lock()

    async def aggregate_camera_quality(self, quality: CameraQuality, correlation_id: str) -> list[VisionEventRecord]:
        async with self._lock:
            events: list[VisionEventRecord] = []
            key = f"quality_{quality.camera_id}"
            score = quality.overall_score
            last = self._pending.get(key)
            now = time.time()

            event_type = VisionEventType.CAMERA_QUALITY_CHANGED
            payload = {
                "camera_id": quality.camera_id,
                "score": score,
                "shot": quality.shot,
                "subject_count": quality.subject_count,
            }

            if last is None or abs(score - last["score"]) >= 0.05:
                self._pending[key] = {"score": score, "timestamp": now, "payload": payload}
            elif now - last["timestamp"] >= self.debounce_seconds:
                event = VisionEventRecord.create(
                    event_type=event_type,
                    camera_id=quality.camera_id,
                    source="quality",
                    confidence=score,
                    duration=0.0,
                    observation_id=str(uuid4()),
                    payload=payload,
                    correlation_id=correlation_id,
                )
                events.append(event)
                self._active_events[key] = event
                self._pending.pop(key, None)

            return events

    async def aggregate_person_count(self, previous_count: int, current_count: int, camera_id: int, correlation_id: str) -> list[VisionEventRecord]:
        async with self._lock:
            events: list[VisionEventRecord] = []
            if previous_count != current_count:
                if current_count > previous_count:
                    event_type = VisionEventType.PERSON_ENTERED
                else:
                    event_type = VisionEventType.PERSON_LEFT
                payload = {
                    "previous_count": previous_count,
                    "current_count": current_count,
                }
                event = VisionEventRecord.create(
                    event_type=event_type,
                    camera_id=camera_id,
                    source="person_count",
                    confidence=0.9,
                    duration=0.0,
                    observation_id=str(uuid4()),
                    payload=payload,
                    correlation_id=correlation_id,
                )
                events.append(event)

                count_changed = VisionEventRecord.create(
                    event_type=VisionEventType.PERSON_COUNT_CHANGED,
                    camera_id=camera_id,
                    source="person_count",
                    confidence=0.92,
                    duration=0.0,
                    observation_id=str(uuid4()),
                    payload=payload,
                    correlation_id=correlation_id,
                )
                events.append(count_changed)
            return events
