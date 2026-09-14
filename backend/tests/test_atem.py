"""Test ATEM service."""

import pytest
import asyncio
from unittest.mock import AsyncMock

from app.atem.service import AtemService
from app.atem.mock import MockAtemClient
from app.events.bus import EventBus


@pytest.fixture
def mock_atem():
    """Create a mock ATEM service."""
    return AtemService(mock=True)


@pytest.mark.asyncio
async def test_atem_connect(mock_atem):
    """Test connecting to ATEM."""
    connected = await mock_atem.connect()
    assert connected is True
    assert await mock_atem.is_connected() is True


@pytest.mark.asyncio
async def test_atem_get_state(mock_atem):
    """Test getting ATEM state."""
    await mock_atem.connect()
    state = await mock_atem.get_state()
    
    assert state.connected is True
    assert state.program_input == 0
    assert state.preview_input == 1
    assert len(state.inputs) > 0


@pytest.mark.asyncio
async def test_atem_set_program(mock_atem):
    """Test switching program input."""
    await mock_atem.connect()
    
    new_state = await mock_atem.set_program(2)
    assert new_state.program_input == 2


@pytest.mark.asyncio
async def test_atem_set_preview(mock_atem):
    """Test switching preview input."""
    await mock_atem.connect()
    
    new_state = await mock_atem.set_preview(3)
    assert new_state.preview_input == 3


@pytest.mark.asyncio
async def test_atem_cut(mock_atem):
    """Test CUT transition."""
    await mock_atem.connect()
    
    # Set preview to different input first
    await mock_atem.set_preview(2)
    
    # Perform cut
    new_state = await mock_atem.cut()
    
    # After cut, program should match preview
    assert new_state.program_input == 2


@pytest.mark.asyncio
async def test_atem_auto(mock_atem):
    """Test AUTO transition."""
    await mock_atem.connect()
    
    # Set preview to different input
    await mock_atem.set_preview(3)
    
    # Perform auto
    new_state = await mock_atem.auto()
    
    # After auto, program should match preview
    assert new_state.program_input == 3


@pytest.mark.asyncio
async def test_atem_invalid_input(mock_atem):
    """Test setting invalid input."""
    await mock_atem.connect()
    
    with pytest.raises(ValueError):
        await mock_atem.set_program(999)


@pytest.mark.asyncio
async def test_atem_stream_and_record(mock_atem):
    """Test starting/stopping streaming and recording."""
    await mock_atem.connect()

    assert await mock_atem.start_stream() is True
    state = await mock_atem.get_state()
    assert state.streaming is True

    assert await mock_atem.stop_stream() is True
    state = await mock_atem.get_state()
    assert state.streaming is False

    assert await mock_atem.start_recording() is True
    state = await mock_atem.get_state()
    assert state.recording is True

    assert await mock_atem.stop_recording() is True
    state = await mock_atem.get_state()
    assert state.recording is False


@pytest.mark.asyncio
async def test_atem_mic_mute(mock_atem):
    """Test muting/unmuting mic channels."""
    await mock_atem.connect()

    state = await mock_atem.get_state()
    assert len(state.audio_channels) == 2
    assert all(not chan.muted for chan in state.audio_channels)

    state = await mock_atem.set_mic_muted(1, True)
    mic1 = next(c for c in state.audio_channels if c.id == 1)
    assert mic1.muted is True

    state = await mock_atem.set_mic_muted(1, False)
    mic1 = next(c for c in state.audio_channels if c.id == 1)
    assert mic1.muted is False

    with pytest.raises(ValueError):
        await mock_atem.set_mic_muted(999, True)


@pytest.fixture
async def output_rig(monkeypatch):
    import app.atem.service as atem_module

    bus = EventBus()
    monkeypatch.setattr(atem_module, "event_bus", bus)
    atem = AtemService(mock=True)
    await atem.connect()
    try:
        yield atem, await bus.subscribe()
    finally:
        await atem._client.aclose()


@pytest.mark.parametrize("output,start,event", [
    ("streaming", "start_stream", "STREAM_FAILURE"),
    ("recording", "start_recording", "RECORDING_FAILURE"),
])
@pytest.mark.parametrize("failure", ["stopped", "disconnected", "status_error"])
async def test_detected_output_failure_once_and_recovery(output_rig, monkeypatch, output, start, event, failure):
    atem, queue = output_rig
    await atem.get_state()
    assert queue.empty()  # Idle is not failure.
    await getattr(atem, start)()
    await atem.get_state()
    original_status = atem._mock_client.status

    async def lose_output():
        if failure == "status_error":
            monkeypatch.setattr(atem._mock_client, "status", AsyncMock(side_effect=OSError("private")))
        else:
            setattr(atem._mock_client, "_connected" if failure == "disconnected" else f"_{output}", False)
        for _ in range(2):
            if failure == "status_error":
                with pytest.raises(ConnectionError):
                    await atem.get_state()
            else:
                await atem.get_state()

    await lose_output()
    assert queue.get_nowait() == {"event": event, "payload": {}}
    assert queue.empty()
    monkeypatch.setattr(atem._mock_client, "status", original_status)
    atem._mock_client._connected = True
    setattr(atem._mock_client, f"_{output}", True)
    await atem.get_state()
    await lose_output()
    assert queue.get_nowait() == {"event": event, "payload": {}}
    assert queue.empty()


@pytest.mark.parametrize("start,stop,output,event", [
    ("start_stream", "stop_stream", "streaming", "STREAM_FAILURE"),
    ("start_recording", "stop_recording", "recording", "RECORDING_FAILURE"),
])
@pytest.mark.parametrize("stop_result", ["success", "false", "exception", "in_flight"])
async def test_intentional_stops_not_announced(output_rig, monkeypatch, start, stop, output, event, stop_result):
    atem, queue = output_rig
    await getattr(atem, start)()
    await atem.get_state()
    if stop_result == "false":
        monkeypatch.setattr(atem._mock_client, stop, AsyncMock(return_value={"ok": False}))
    elif stop_result == "exception":
        monkeypatch.setattr(atem._mock_client, stop, AsyncMock(side_effect=OSError("private")))
    elif stop_result == "in_flight":
        async def in_flight_stop():
            setattr(atem._mock_client, f"_{output}", False)
            await atem.get_state()
            return {"ok": True}
        monkeypatch.setattr(atem._mock_client, stop, in_flight_stop)

    if stop_result == "exception":
        with pytest.raises(OSError):
            await getattr(atem, stop)()
    else:
        assert await getattr(atem, stop)() is (stop_result != "false")
    setattr(atem._mock_client, f"_{output}", False)
    await atem.get_state()
    if stop_result in ("false", "exception"):
        assert queue.get_nowait() == {"event": event, "payload": {}}
    assert queue.empty()
    await getattr(atem, start)()
    await atem.get_state()
    setattr(atem._mock_client, f"_{output}", False)
    await atem.get_state()
    assert queue.get_nowait() == {"event": event, "payload": {}}
    assert queue.empty()
