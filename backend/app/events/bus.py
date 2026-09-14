"""Internal event bus for publishing vision and production events."""

import asyncio
import threading
from typing import Any


class EventBus:
    def __init__(self):
        self.subscribers: list[asyncio.Queue] = []
        self._loops: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()

    async def subscribe(self, maxsize: int = 0) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self.subscribers.append(queue)
            self._loops[queue] = asyncio.get_running_loop()
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self.subscribers = [s for s in self.subscribers if s is not queue]
            self._loops.pop(queue, None)

    def publish(self, message: Any) -> None:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        with self._lock:
            subscribers = list(self._loops.items())
        for subscriber, loop in subscribers:
            if loop is running_loop:
                self._deliver(subscriber, message)
            else:
                try:
                    loop.call_soon_threadsafe(self._deliver, subscriber, message)
                except RuntimeError:
                    pass  # Subscriber loop has closed.

    def _deliver(self, subscriber: asyncio.Queue, message: Any) -> None:
        with self._lock:
            if subscriber not in self._loops:
                return
            try:
                subscriber.put_nowait(message)
            except asyncio.QueueFull:
                pass


event_bus = EventBus()
