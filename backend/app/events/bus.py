"""Internal event bus for publishing vision and production events."""

import asyncio
from typing import Any


class EventBus:
    def __init__(self):
        self.subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self.subscribers.append(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self.subscribers = [s for s in self.subscribers if s is not queue]

    def publish(self, message: Any) -> None:
        for subscriber in list(self.subscribers):
            try:
                subscriber.put_nowait(message)
            except asyncio.QueueFull:
                pass


event_bus = EventBus()
