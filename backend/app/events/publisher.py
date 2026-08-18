"""Event publisher helpers."""

from .bus import event_bus


def publish_event(event: dict) -> None:
    event_bus.publish(event)
