"""Scene classification support for future vision features."""

from typing import Any


class SceneDetector:
    async def initialize(self) -> None:
        pass

    async def process(self, frame: Any, timestamp: float, camera_id: int) -> dict:
        return {
            "detector": "scene",
            "timestamp": timestamp,
            "camera_id": camera_id,
            "objects": [],
        }
