"""Face detection support for future vision features."""

from typing import Any


class FaceDetector:
    async def initialize(self) -> None:
        pass

    async def process(self, frame: Any, timestamp: float, camera_id: int) -> dict:
        return {
            "detector": "face",
            "timestamp": timestamp,
            "camera_id": camera_id,
            "objects": [],
        }
