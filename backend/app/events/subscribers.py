"""Event subscriber helpers."""

from typing import Any

from .bus import EventBus, event_bus


async def create_subscription() -> Any:
    return await event_bus.subscribe()


async def remove_subscription(queue: Any) -> None:
    await event_bus.unsubscribe(queue)
