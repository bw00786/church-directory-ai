"""Test ATEM service."""

import pytest
import asyncio
from unittest.mock import AsyncMock

import httpx

from app.atem.service import AtemFeatureUnsupported, AtemService
from app.atem.mock import MockAtemClient
from app.config import settings
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
    # Real ATEM Mini SDK ids: HDMI 1..4 -> 1..4 (0 is Black); defaults match config.
    assert state.program_input == 1
    assert state.preview_input == 2
    assert [inp.id for inp in state.inputs] == [1, 2, 3, 4]
    assert settings.atem_camera1_input in {inp.id for inp in state.inputs}
    assert settings.atem_camera2_input in {inp.id for inp in state.inputs}


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


# -- hardening: mock ids, AUTO polling, mic-mute degradation, bus events --------

async def test_mock_rejects_sdk_black_input_zero(mock_atem):
    await mock_atem.connect()
    with pytest.raises(ValueError):
        await mock_atem.set_program(0)


async def test_cut_swaps_program_and_preview_like_the_switcher(mock_atem):
    await mock_atem.connect()
    await mock_atem.set_preview(3)
    state = await mock_atem.cut()
    assert state.program_input == 3
    assert state.preview_input == 1


def test_poll_interval_comes_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "atem_poll_seconds", 0.25)
    assert AtemService(mock=True)._monitor_interval == 0.25
    monkeypatch.setattr(settings, "atem_poll_seconds", 1.0)
    assert AtemService(mock=True)._monitor_interval == 1.0


async def test_auto_polls_transition_flag_instead_of_fixed_sleep(mock_atem):
    await mock_atem.connect()
    mock_atem._transition_poll_seconds = 0.01
    mock_atem._mock_client.auto_transition_polls = 3
    original_status = mock_atem._mock_client.status
    polls = []

    async def status():
        data = await original_status()
        polls.append(data["transition_in_progress"])
        return data

    mock_atem._mock_client.status = status
    await mock_atem.set_preview(3)
    polls.clear()

    start = asyncio.get_running_loop().time()
    state = await mock_atem.auto()
    elapsed = asyncio.get_running_loop().time() - start

    assert state.program_input == 3 and state.transition_in_progress is False
    assert polls == [True, True, True, False], "returned on the first poll showing the flag clear"
    assert elapsed < 0.5, "no fixed 1.2 s sleep"


async def test_auto_returns_promptly_when_flag_already_clear(mock_atem):
    await mock_atem.connect()
    mock_atem._transition_poll_seconds = 0.01
    mock_atem._mock_client.auto_transition_polls = 0  # bridge never shows the flag (fast cut rate)
    original_status = mock_atem._mock_client.status
    polls = []

    async def status():
        data = await original_status()
        polls.append(data["transition_in_progress"])
        return data

    mock_atem._mock_client.status = status
    await mock_atem.set_preview(4)
    polls.clear()
    state = await mock_atem.auto()
    assert state.program_input == 4
    assert polls == [False, False], "a second poll guards against a bridge that lags the start"


async def test_auto_wait_is_capped_when_flag_never_clears(mock_atem):
    await mock_atem.connect()
    mock_atem._transition_poll_seconds = 0.01
    mock_atem._transition_timeout_seconds = 0.06
    mock_atem._mock_client.auto_transition_polls = 10_000
    start = asyncio.get_running_loop().time()
    state = await mock_atem.auto()
    assert asyncio.get_running_loop().time() - start < 0.5
    assert state.transition_in_progress is True


@pytest.fixture
def real_bridge(monkeypatch):
    """AtemService against a fake C++ bridge (httpx MockTransport), no mock client."""
    monkeypatch.setattr(settings, "atem_auto_detect", False)
    monkeypatch.setattr(settings, "enable_mock_atem", False)
    status = {
        "connected": True,
        "program_input": 1,
        "preview_input": 2,
        "streaming": False,
        "recording": False,
        "transition_in_progress": False,
        "inputs": [
            {"id": 1, "name": "Cam 1", "short_name": "C1", "type": "HDMI", "connected": True,
             "program_tally": True, "preview_tally": False},
            {"id": 2, "name": "Cam 2", "short_name": "C2", "type": "HDMI", "connected": True},
        ],
        "audio_channels": [{"id": 1, "name": "Mic 1", "muted": False}],
    }
    requests = []

    def handle(request):
        requests.append(request)
        path = request.url.path
        if path == "/status":
            return httpx.Response(200, json=status)
        if path == "/connect":
            return httpx.Response(200, json={"ok": True})
        if path == "/disconnect":
            return httpx.Response(200, json={"ok": True})
        if path.startswith("/audio/"):
            # cpp-httplib's default 404 has an empty, non-JSON body.
            return httpx.Response(404, text="")
        raise AssertionError(f"unexpected bridge call {path}")

    service = AtemService()
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return service, status, requests


async def test_mic_mute_404_degrades_to_clear_unsupported_error(real_bridge):
    service, status, requests = real_bridge
    assert await service.connect("10.0.0.5")
    assert service.mic_mute_supported is None

    with pytest.raises(AtemFeatureUnsupported) as excinfo:
        await service.set_mic_muted(1, True)
    assert "does not support mic mute" in str(excinfo.value)
    assert isinstance(excinfo.value, NotImplementedError)
    assert service.mic_mute_supported is False
    assert await service.is_connected(), "an unsupported feature is not a connection failure"

    # Remembered: the second call does not hit the bridge again.
    with pytest.raises(AtemFeatureUnsupported):
        await service.set_mic_muted(1, False)
    assert sum(r.url.path.startswith("/audio/") for r in requests) == 1

    # A reconnect re-detects (a newer bridge may have it).
    assert await service.connect("10.0.0.5")
    assert service.mic_mute_supported is None
    await service._client.aclose()


async def test_mic_mute_feature_detected_from_status_capabilities(real_bridge):
    service, status, requests = real_bridge
    status["capabilities"] = {"audio_mute": False, "tally": True}
    assert await service.connect("10.0.0.5")
    assert service.mic_mute_supported is False
    with pytest.raises(AtemFeatureUnsupported):
        await service.set_mic_muted(1, True)
    assert not any(r.url.path.startswith("/audio/") for r in requests)
    state = await service.get_state()
    assert state.inputs[0].program_tally is True and state.inputs[1].program_tally is False
    await service._client.aclose()


@pytest.fixture
async def bus_rig(monkeypatch):
    """Mock ATEM with a private bus subscribed *before* connecting."""
    import app.atem.service as atem_module

    bus = EventBus()
    monkeypatch.setattr(atem_module, "event_bus", bus)
    atem = AtemService(mock=True)
    queue = await bus.subscribe()
    try:
        yield atem, queue
    finally:
        await atem._client.aclose()


def drain(queue):
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


async def test_connection_changed_published_on_connect_and_disconnect(bus_rig):
    atem, queue = bus_rig
    await atem.connect()
    events = drain(queue)
    assert len(events) == 1
    assert events[0]["event"] == "ATEM_CONNECTION_CHANGED"
    payload = events[0]["payload"]
    assert payload["connected"] is True and payload["previous"] is False
    assert payload["source"] == "connect" and payload["mock"] is True
    assert payload["atem_ip"] and payload["timestamp"]

    await atem.get_state()
    assert queue.empty(), "steady state is silent"

    await atem.disconnect()
    events = drain(queue)
    assert [e["event"] for e in events] == ["ATEM_CONNECTION_CHANGED"]
    assert events[0]["payload"] == {**events[0]["payload"], "connected": False, "previous": True, "source": "disconnect"}


async def test_program_changed_emitted_when_poll_shows_operator_cut(bus_rig):
    atem, queue = bus_rig
    await atem.connect()
    drain(queue)

    # The operator pressed a button on the ATEM: no command went through us.
    atem._mock_client._program_input = 3
    await atem.get_state()
    events = drain(queue)
    assert len(events) == 1
    assert events[0]["event"] == "PROGRAM_CHANGED"
    payload = events[0]["payload"]
    assert payload["program_input"] == 3
    assert payload["previous_input"] == 1
    assert payload["preview_input"] == 2
    assert payload["source"] == "poll"
    assert payload["commanded"] is False
    assert payload["mock"] is True
    assert payload["transition_in_progress"] is False
    assert payload["timestamp"]

    await atem.get_state()
    assert queue.empty(), "only transitions are announced, not every poll"


async def test_preview_changed_and_commanded_attribution(bus_rig):
    atem, queue = bus_rig
    await atem.connect()
    drain(queue)

    await atem.set_preview(4)
    events = drain(queue)
    assert [e["event"] for e in events] == ["PREVIEW_CHANGED"]
    assert events[0]["payload"]["preview_input"] == 4
    assert events[0]["payload"]["previous_input"] == 2
    assert events[0]["payload"]["commanded"] is True
    assert events[0]["payload"]["source"] == "poll"

    await atem.set_program(4)
    events = drain(queue)
    assert [e["event"] for e in events] == ["PROGRAM_CHANGED"]
    assert events[0]["payload"]["commanded"] is True

    # A cut swaps both; both are attributed to our command.
    await atem.set_preview(2)
    drain(queue)
    await atem.cut()
    events = {e["event"]: e["payload"] for e in drain(queue)}
    assert set(events) == {"PROGRAM_CHANGED", "PREVIEW_CHANGED"}
    assert events["PROGRAM_CHANGED"]["program_input"] == 2 and events["PROGRAM_CHANGED"]["commanded"] is True
    assert events["PREVIEW_CHANGED"]["preview_input"] == 4 and events["PREVIEW_CHANGED"]["commanded"] is True

    # An operator change to a *different* input than we commanded is not ours.
    atem._mock_client._program_input = 3
    await atem.get_state()
    events = drain(queue)
    assert events[0]["payload"]["program_input"] == 3 and events[0]["payload"]["commanded"] is False


async def test_program_changed_emitted_on_reconnect_if_state_moved(bus_rig):
    atem, queue = bus_rig
    await atem.connect()
    await atem.disconnect()
    drain(queue)
    atem._mock_client._program_input = 4  # moved while we were away
    await atem.connect()
    events = [e for e in drain(queue) if e["event"] == "PROGRAM_CHANGED"]
    assert len(events) == 1 and events[0]["payload"]["previous_input"] == 1
