"""Pose detection support for future vision features."""

from typing import Any


class PoseDetector:
    async def initialize(self) -> None:
        pass

    async def process(self, frame: Any, timestamp: float, camera_id: int) -> dict:
        return {
            "detector": "pose",
            "timestamp": timestamp,
            "camera_id": camera_id,
            "objects": [],
        }
