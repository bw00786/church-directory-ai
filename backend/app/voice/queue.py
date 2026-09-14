import time

from .models import Priority, VoiceEvent


class AttentionQueue:
    def __init__(self, limit: int = 100, now=time.monotonic):
        self.limit = limit
        self.now = now
        self.items: list[tuple[float, VoiceEvent]] = []

    def add(self, event: VoiceEvent, delay: float) -> VoiceEvent | None:
        dropped = None
        if len(self.items) >= self.limit:
            lowest = min(self.items, key=lambda item: (item[1].priority, item[0]))
            if lowest[1].priority > event.priority:
                return event
            self.items.remove(lowest)
            dropped = lowest[1]
        self.items.append((self.now() + delay, event))
        return dropped

    def take(self) -> list[VoiceEvent]:
        if not self.items:
            return []
        priority = max(e.priority for _, e in self.items)
        ready = [(due, e) for due, e in self.items if e.priority == priority and due <= self.now()]
        if not ready:
            return []
        # Once an aggregation window closes, include its pending peers.
        selected = [(due, e) for due, e in self.items if e.priority == priority]
        self.items = [(due, e) for due, e in self.items if e.priority != priority]
        return [e for _, e in sorted(selected, key=lambda item: item[0])]

    def clear(self) -> list[VoiceEvent]:
        events = [e for _, e in self.items]
        self.items.clear()
        return events

    def remove(self, event_id: str):
        self.items = [(due, e) for due, e in self.items if e.id != event_id]