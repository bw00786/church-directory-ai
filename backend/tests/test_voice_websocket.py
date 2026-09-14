"""Voice envelopes over the existing WebSocket event stream, without a server."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect

from app.api import websocket as websocket_module
from app.events.bus import EventBus
from app.voice.models import AttentionInput, Priority, VoiceEvent


@pytest.mark.parametrize("kind", ["voice_attention", "voice_status", "voice_queue", "voice_acknowledgement"])
async def test_voice_websocket_forwards_envelopes_and_unsubscribes_on_disconnect(monkeypatch, kind):
    event = VoiceEvent(input=AttentionInput(event_type="camera_failure"),
                       priority=Priority.WARNING, message="Check the camera", mode="attention_only")
    envelope = {"type": kind, "event": event.model_dump(mode="json"), "status": {"muted": False}}

    class PrimedBus(EventBus):
        async def subscribe(self, maxsize=0):
            queue = await super().subscribe(maxsize)
            queue.put_nowait(envelope)
            queue.put_nowait({"type": "voice_status"})
            return queue

    bus = PrimedBus()
    monkeypatch.setattr(websocket_module, "event_bus", bus)
    socket = SimpleNamespace(accept=AsyncMock(), send_json=AsyncMock(side_effect=[None, WebSocketDisconnect()]))
    await asyncio.wait_for(websocket_module.websocket_vision(socket), 1)
    socket.accept.assert_awaited_once()
    assert socket.send_json.await_args_list[0].args == (envelope,)
    assert not bus.subscribers


async def test_voice_websocket_cancellation_removes_idle_subscriber(monkeypatch):
    subscribed = asyncio.Event()

    class ObservableBus(EventBus):
        async def subscribe(self, maxsize=0):
            queue = await super().subscribe(maxsize)
            subscribed.set()
            return queue

    bus = ObservableBus()
    monkeypatch.setattr(websocket_module, "event_bus", bus)
    socket = SimpleNamespace(accept=AsyncMock(), send_json=AsyncMock())
    task = asyncio.create_task(websocket_module.websocket_vision(socket))
    try:
        await asyncio.wait_for(subscribed.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not bus.subscribers
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)