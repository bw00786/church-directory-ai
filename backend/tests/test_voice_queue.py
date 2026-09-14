"""Deterministic priority queue, aggregation-window, and capacity tests."""

import pytest

from app.voice.models import AttentionInput, Priority, VoiceEvent
from app.voice.queue import AttentionQueue


def event(priority, resource="production"):
    return VoiceEvent(input=AttentionInput(event_type="test", resource=resource),
                      priority=priority, message="Test alert", mode="attention_only")


def test_voice_queue_orders_priority_then_due_time_and_aggregates_peers():
    clock = [100.0]
    queue = AttentionQueue(now=lambda: clock[0])
    attention = event(Priority.ATTENTION)
    later = event(Priority.WARNING, "later")
    earlier = event(Priority.WARNING, "earlier")
    critical = event(Priority.CRITICAL)
    for item, delay in [(attention, 0), (later, 7), (earlier, 5), (critical, 0)]:
        assert queue.add(item, delay) is None
    assert queue.take() == [critical]
    assert queue.take() == []  # Pending highest priority holds lower-priority work.
    clock[0] = 104.999
    assert queue.take() == []
    clock[0] = 105
    assert queue.take() == [earlier, later]  # Closing window includes pending peers.
    assert queue.take() == [attention]
    assert queue.take() == []


@pytest.mark.parametrize("incoming,expected_drop", [
    (Priority.BACKGROUND, "incoming"), (Priority.ATTENTION, "oldest"),
    (Priority.WARNING, "oldest"), (Priority.CRITICAL, "oldest"),
])
def test_voice_queue_bounds_evict_oldest_lowest_priority(incoming, expected_drop):
    queue = AttentionQueue(limit=2, now=lambda: 0)
    oldest, newest = event(Priority.ATTENTION), event(Priority.WARNING)
    queue.add(oldest, 0)
    queue.add(newest, 1)
    added = event(incoming)
    assert queue.add(added, 2) is (added if expected_drop == "incoming" else oldest)
    assert len(queue.items) == 2
    assert newest in [item for _, item in queue.items]


def test_voice_queue_equal_due_times_preserve_insertion_order():
    queue = AttentionQueue(now=lambda: 0)
    items = [event(Priority.WARNING, str(i)) for i in range(3)]
    for item in items:
        queue.add(item, 0)
    assert queue.take() == items


def test_voice_queue_remove_and_clear_are_idempotent():
    queue = AttentionQueue(now=lambda: 0)
    first, second = event(Priority.WARNING), event(Priority.CRITICAL)
    queue.add(first, 0)
    queue.add(second, 0)
    queue.remove(first.id)
    queue.remove(first.id)
    queue.remove("missing")
    assert queue.clear() == [second]
    assert queue.clear() == []
    assert queue.take() == []