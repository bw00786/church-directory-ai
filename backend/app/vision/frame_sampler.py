"""Frame sampling and bounded queue management."""

import asyncio
from typing import Any

from .models import FrameMetadata
from .frame_source import VideoSource


class FrameSampler:
    def __init__(self, source: VideoSource, vision_fps: int):
        self.source = source
        self.vision_interval = 1.0 / max(1, vision_fps)
        self._queue: asyncio.Queue[tuple[Any, FrameMetadata]] = asyncio.Queue(maxsize=1)
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while self._running:
            frame, metadata = await self.source.get_frame()
            if frame is not None and metadata is not None:
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                await self._queue.put((frame, metadata))
            await asyncio.sleep(self.vision_interval)

    async def get_latest(self) -> tuple[Any | None, FrameMetadata | None]:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None, None
