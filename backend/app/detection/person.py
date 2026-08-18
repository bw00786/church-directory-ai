"""Person detection support for the vision subsystem."""

from typing import Any


class PersonDetector:
    async def initialize(self) -> None:
        pass

    async def process(self, frame: Any, timestamp: float, camera_id: int) -> dict:
        return {
            "detector": "person",
            "timestamp": timestamp,
            "camera_id": camera_id,
            "objects": [],
        }
