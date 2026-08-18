"""Frame source abstractions for vision input."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore

from .models import FrameMetadata, VideoSourceType


class VideoSource(ABC):
    def __init__(self, camera_id: int, source_name: str):
        self.camera_id = camera_id
        self.source_name = source_name
        self.metadata = None

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_frame(self) -> tuple[Any | None, FrameMetadata | None]:
        raise NotImplementedError

    def get_metadata(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "source": self.source_name,
        }


class RTSPSource(VideoSource):
    def __init__(self, camera_id: int, rtsp_url: str):
        super().__init__(camera_id, VideoSourceType.RTSP.value)
        self.rtsp_url = rtsp_url
        self.capture = None

    async def connect(self) -> None:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for RTSP sources")
        self.capture = cv2.VideoCapture(self.rtsp_url)
        if not self.capture.isOpened():
            raise ConnectionError(f"Unable to open RTSP stream: {self.rtsp_url}")

    async def disconnect(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    async def get_frame(self) -> tuple[Any | None, FrameMetadata | None]:
        if self.capture is None:
            return None, None
        ret, frame = self.capture.read()
        if not ret:
            return None, None
        height, width = frame.shape[:2]
        metadata = FrameMetadata(
            camera_id=self.camera_id,
            source=self.source_name,
            timestamp=datetime.utcnow().timestamp(),
            width=width,
            height=height,
        )
        return frame, metadata


class USBSource(VideoSource):
    def __init__(self, camera_id: int, device_index: int = 0):
        super().__init__(camera_id, VideoSourceType.USB.value)
        self.device_index = device_index
        self.capture = None

    async def connect(self) -> None:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for USB sources")
        self.capture = cv2.VideoCapture(self.device_index)
        if not self.capture.isOpened():
            raise ConnectionError(f"Unable to open USB video source {self.device_index}")

    async def disconnect(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    async def get_frame(self) -> tuple[Any | None, FrameMetadata | None]:
        if self.capture is None:
            return None, None
        ret, frame = self.capture.read()
        if not ret:
            return None, None
        height, width = frame.shape[:2]
        metadata = FrameMetadata(
            camera_id=self.camera_id,
            source=self.source_name,
            timestamp=datetime.utcnow().timestamp(),
            width=width,
            height=height,
        )
        return frame, metadata


class FileSource(VideoSource):
    def __init__(self, camera_id: int, file_path: str):
        super().__init__(camera_id, VideoSourceType.FILE.value)
        self.file_path = file_path
        self.capture = None

    async def connect(self) -> None:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for file sources")
        self.capture = cv2.VideoCapture(self.file_path)
        if not self.capture.isOpened():
            raise FileNotFoundError(f"Unable to open video file: {self.file_path}")

    async def disconnect(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    async def get_frame(self) -> tuple[Any | None, FrameMetadata | None]:
        if self.capture is None:
            return None, None
        ret, frame = self.capture.read()
        if not ret:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            return None, None
        height, width = frame.shape[:2]
        metadata = FrameMetadata(
            camera_id=self.camera_id,
            source=self.source_name,
            timestamp=datetime.utcnow().timestamp(),
            width=width,
            height=height,
        )
        return frame, metadata


class TestPatternSource(VideoSource):
    def __init__(self, camera_id: int, width: int = 640, height: int = 360):
        super().__init__(camera_id, VideoSourceType.TEST_PATTERN.value)
        self.width = width
        self.height = height
        self.frame_count = 0

    async def connect(self) -> None:
        self.frame_count = 0

    async def disconnect(self) -> None:
        pass

    async def get_frame(self) -> tuple[Any | None, FrameMetadata | None]:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for test pattern source")
        import numpy as np

        frame = 255 * np.ones((self.height, self.width, 3), dtype='uint8')
        color = (100, 150, 200)
        cv2.putText(
            frame,
            f"Camera {self.camera_id}",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            color,
            2,
        )
        metadata = FrameMetadata(
            camera_id=self.camera_id,
            source=self.source_name,
            timestamp=datetime.utcnow().timestamp(),
            width=self.width,
            height=self.height,
        )
        self.frame_count += 1
        return frame, metadata
