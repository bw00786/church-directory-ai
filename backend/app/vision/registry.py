"""Registry for vision detectors and pipelines."""

from typing import Any

from .inference import Detector, PersonDetector
from .config import VisionSettings


class DetectorRegistry:
    def __init__(self, settings: VisionSettings):
        self.settings = settings
        self.person_detector = PersonDetector(
            confidence_threshold=self.settings.person_detector_confidence,
            model_path=self.settings.person_detector_model,
        )

    async def initialize(self) -> None:
        await self.person_detector.initialize()

    async def shutdown(self) -> None:
        await self.person_detector.shutdown()

    async def process_person(self, frame: Any, timestamp: float, camera_id: int):
        return await self.person_detector.process(frame, timestamp, camera_id)
