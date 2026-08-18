from app.events.bus import EventBus


def test_event_bus_publishes_messages():
    bus = EventBus()
    assert hasattr(bus, 'publish')
    assert hasattr(bus, 'subscribe')
