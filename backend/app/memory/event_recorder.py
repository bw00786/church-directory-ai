"""Auto-records director cue activity into production memory.

Subscribes to the internal event bus and turns each cue action the director
performs into a `ServiceObservation` (see database.models), so "what happened
in past services" accumulates automatically from real usage instead of
requiring a separate manual data-entry step.

Also captures the AI Service Director's own decisions and actions (published
as `{"event": ..., "payload": ...}` messages, a different shape from the cue
engine's `{"type": "director_action", "data": ...}`) so those are searchable
alongside cue history too.
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
            return

        event = message.get("event")
        if event == "AI_DECISION":
            payload = message.get("payload") or {}
            decision = payload.get("decision", "continue")
            if decision == "continue":
                return  # not useful as service history
            reason = payload.get("reason", "")
            confidence = payload.get("confidence", 0.0)
            text = f"AI decision: {decision} (confidence={confidence:.2f}) — {reason}" if reason else f"AI decision: {decision}"
            memory_manager.record_observation(category="ai_decision", text=text, source="ai_director")
        elif event in ("AI_ACTION_APPROVED", "AI_ACTION_REJECTED"):
            payload = message.get("payload") or {}
            action_type = payload.get("type", "action")
            target = payload.get("target") or ""
            confidence = payload.get("confidence", 0.0)
            status = "approved" if event == "AI_ACTION_APPROVED" else "rejected"
            reason = payload.get("reason", "")
            text = f"AI action {status}: {action_type}"
            if target:
                text += f" -> {target}"
            text += f" (confidence={confidence:.2f})"
            if reason:
                text += f" — {reason}"
            memory_manager.record_observation(category="ai_action", text=text, source="ai_director")


memory_event_recorder = MemoryEventRecorder()
