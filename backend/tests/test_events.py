import asyncio

import pytest

from app.events.bus import EventBus


def test_event_bus_publishes_messages():
    bus = EventBus()
    assert hasattr(bus, 'publish')
    assert hasattr(bus, 'subscribe')


@pytest.mark.asyncio
async def test_worker_publish_wakes_waiting_subscriber():
    bus = EventBus()
    queue = await bus.subscribe(maxsize=1)
    loop = asyncio.get_running_loop()
    debug = loop.get_debug()
    loop.set_debug(True)
    try:
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        await asyncio.to_thread(bus.publish, {"event": "test"})
        assert await asyncio.wait_for(waiter, 1) == {"event": "test"}
        assert queue.maxsize == 1
    finally:
        loop.set_debug(debug)
        await bus.unsubscribe(queue)


@pytest.mark.asyncio
async def test_bounded_delivery_and_unsubscribe():
    bus = EventBus()
    queue = await bus.subscribe(maxsize=1)
    other = await bus.subscribe()
    bus.publish(1)
    bus.publish(2)
    assert queue.get_nowait() == 1
    assert queue.empty()
    assert [other.get_nowait(), other.get_nowait()] == [1, 2]
    await bus.unsubscribe(queue)
    bus.publish(3)
    assert queue.empty()
    await bus.unsubscribe(other)
    assert not bus.subscribers


def test_publish_tolerates_closed_subscriber_loop():
    bus = EventBus()
    asyncio.run(bus.subscribe())
    bus.publish("ignored")
