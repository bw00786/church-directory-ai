"""Auto-records director cue activity into production memory.

Subscribes to the internal event bus and turns each cue action the director
performs into a `ServiceObservation` (see database.models), so "what happened
in past services" accumulates automatically from real usage instead of
requiring a separate manual data-entry step.
"""

from __future__ import annotations

import asyncio

from app.events.bus import event_bus
from app.logging_config import get_logger

from .production_memory import memory_manager

logger = get_logger(__name__)


class MemoryEventRecorder:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._queue: asyncio.Queue | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._queue = await event_bus.subscribe()
        self._task = asyncio.create_task(self._run())
        logger.info("Memory event recorder started")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._queue is not None:
            await event_bus.unsubscribe(self._queue)
            self._queue = None

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            message = await self._queue.get()
            try:
                self._handle(message)
            except Exception:
                logger.exception("Failed to record memory observation", message=message)

    def _handle(self, message: dict) -> None:
        if message.get("type") == "director_action":
            data = message.get("data", {})
            action = data.get("action", "action")
            if action == "error":
                return  # not useful as service history
            detail = data.get("detail", "")
            description = data.get("description") or action
            memory_manager.record_observation(
                category="cue",
                text=f"{description}: {detail}" if detail else description,
                source=f"cue_index_{data.get('cue_index')}",
            )


memory_event_recorder = MemoryEventRecorder()
