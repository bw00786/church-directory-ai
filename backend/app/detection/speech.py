"""Speech/activity detection support for future multimodal vision features."""

from typing import Any


class SpeechDetector:
    async def initialize(self) -> None:
        pass

    async def process(self, frame: Any, timestamp: float, camera_id: int) -> dict:
        return {
            "detector": "speech",
            "timestamp": timestamp,
            "camera_id": camera_id,
            "objects": [],
        }
