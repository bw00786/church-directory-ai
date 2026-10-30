"""Tests for MixerService: meter/analysis/dsp ingest and the software-DSP
control client against a fake mgx-ai-mixer REST API."""

import json
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings
from app.mixer.service import SILENCE_DB, STALE_REASON, MixerService, api_url_from_ws
from app.events.bus import EventBus


def set_setting(monkeypatch, name, value):
    """Patch an app setting that may not exist yet in app.config (pydantic
    refuses unknown instance attributes, so fall back to the class)."""
    if name in type(settings).model_fields:
        monkeypatch.setattr(settings, name, value)
    else:
        monkeypatch.setattr(type(settings), name, value, raising=False)


@pytest.fixture(autouse=True)
def _stale_setting(monkeypatch):
    set_setting(monkeypatch, "mixer_meter_stale_seconds", 1.0)


def test_api_url_from_ws():
    assert api_url_from_ws("ws://127.0.0.1:9000/ws") == "http://127.0.0.1:9000"
    assert api_url_from_ws("wss://mixer.local/ws") == "https://mixer.local"


def test_ingest_meters_analysis_and_dsp():
    svc = MixerService(mock=False, ws_url="ws://127.0.0.1:9000/ws")
    svc._connected = True
    svc._ingest(json.dumps({"type": "meters", "data": [{"channel": 1, "rms_db": -22.5}, {"channel": 4, "rms_db": -40}]}))
    svc._ingest(json.dumps({"type": "analysis", "data": {"lufs_s": -18.2, "channels": [], "masking": []}}))
    svc._ingest(json.dumps({"type": "dsp", "data": {"engaged": True, "channels": {"1": {"hpf": 100}}}}))
    svc._ingest("not json")

    assert svc.channel_rms(1) == -22.5
    assert svc.channel_rms(4) == -40.0
    assert svc.analysis()["lufs_s"] == -18.2
    assert svc.analysis()["age_seconds"] is not None
    assert svc.dsp_state()["engaged"] is True


class FakeMgxApi:
    """Records requests and answers like mgx-ai-mixer's FastAPI backend."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.engaged = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path + ("?" + request.url.query.decode() if request.url.query else ""), body))
        path = request.url.path
        if path == "/api/dsp/engage":
            self.engaged = body["engage"]
            return httpx.Response(200, json={"engaged": self.engaged, "master_bypass": not self.engaged, "channels": {}})
        if path == "/api/moves/apply":
            return httpx.Response(200, json={"applied": True, "mode": "software-dsp", "detail": f"{body['action']} {body['param']} {body['amount']}".strip()})
        if path == "/api/command":
            return httpx.Response(200, json={"understood": body["text"], "applied": [{"channel": 2, "notch": "2.4k Hz"}]})
        if path == "/api/autoguard":
            return httpx.Response(200, json={"enabled": body.get("enabled", False), "excluded": body.get("excluded", []), "armed": False})
        if path == "/api/automix":
            return httpx.Response(200, json={"enabled": body["enabled"], "armed": False})
        if path == "/api/advise":
            return httpx.Response(200, json={"summary": "ok", "moves": [{"channel": 4, "action": "hpf", "param": "120 Hz"}]})
        if path == "/api/dsp/reset":
            return httpx.Response(200, json={"engaged": self.engaged, "channels": {}})
        if path == "/api/dsp":
            return httpx.Response(200, json={"engaged": self.engaged, "channels": {}})
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def svc():
    fake = FakeMgxApi()
    service = MixerService(mock=False, ws_url="ws://127.0.0.1:9000/ws")
    service._transport = httpx.MockTransport(fake.handler)
    return service, fake


async def test_engage_and_moves_hit_companion_api(svc):
    service, fake = svc
    state = await service.engage_dsp(True)
    assert state["engaged"] is True
    assert service.dsp_state()["engaged"] is True

    result = await service.apply_move(4, "hpf", param="120 Hz", reason="assistant")
    assert result["applied"] is True
    _, path, body = fake.requests[-1]
    assert path == "/api/moves/apply"
    assert body["channel"] == 4 and body["action"] == "hpf" and body["param"] == "120 Hz"


async def test_apply_move_validates_action(svc):
    service, _ = svc
    with pytest.raises(ValueError):
        await service.apply_move(1, "explode")


async def test_command_guard_keeper_advise_reset(svc):
    service, fake = svc
    cmd = await service.command("channel 2 has feedback, fix it")
    assert cmd["applied"]

    guard = await service.set_feedback_guard(enabled=True, excluded=[4])
    assert guard["enabled"] is True and guard["excluded"] == [4]

    keeper = await service.set_mix_keeper(True)
    assert keeper["enabled"] is True

    advice = await service.advise(apply=True)
    assert advice["moves"]
    assert fake.requests[-1][1] == "/api/advise?apply=true"

    await service.reset_dsp(3)
    assert fake.requests[-1] == ("POST", "/api/dsp/reset", {"channel": 3})


async def test_mock_mode_tracks_dsp_state():
    service = MixerService(mock=True)
    assert service.dsp_state()["engaged"] is False
    await service.engage_dsp(True)
    await service.apply_move(1, "hpf", param="100 Hz")
    guard = await service.set_feedback_guard(enabled=True)
    assert service.dsp_state()["engaged"] is True
    assert "hpf" in service.dsp_state()["channels"]["1"]
    assert guard["armed"] is True
    await service.reset_dsp()
    assert service.dsp_state()["channels"] == {}


@pytest.mark.parametrize("failure", ["closed", "error", "connect"])
async def test_ws_disconnect_publishes_failure(monkeypatch, failure):
    import websockets
    import app.mixer.service as mixer

    bus = EventBus()
    monkeypatch.setattr(mixer, "event_bus", bus)
    queue = await bus.subscribe()
    service = MixerService(mock=False)

    class Connection:
        async def __aenter__(self):
            if failure == "connect":
                raise OSError("private network error")
            return self

        async def __aexit__(self, *args):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            assert service.connected
            if failure == "error":
                raise OSError("private disconnect error")
            raise StopAsyncIteration

    monkeypatch.setattr(websockets, "connect", lambda url: Connection())
    monkeypatch.setattr(mixer.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await service._reader_loop()
    assert not service.connected
    assert queue.get_nowait() == {"event": "MIXER_CONNECTION_FAILED", "payload": {}}
    assert queue.get_nowait() == {
        "event": "PERCEPTION_DEGRADED",
        "payload": {"reason": STALE_REASON, "channel": None},
    }
    assert queue.empty()


async def test_ws_intentional_stop_does_not_publish_failure(monkeypatch):
    import websockets
    import app.mixer.service as mixer

    bus = EventBus()
    monkeypatch.setattr(mixer, "event_bus", bus)
    queue = await bus.subscribe()
    connected = asyncio.Event()

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            connected.set()
            await asyncio.Future()

    monkeypatch.setattr(websockets, "connect", lambda url: Connection())
    service = MixerService(mock=False)
    await service.start()
    try:
        await asyncio.wait_for(connected.wait(), 1)
        assert service.connected
    finally:
        await service.stop()
    assert not service.connected
    assert queue.empty()


# -- meter staleness (WO-HARDEN) --------------------------------------------------
def _meters(*pairs):
    return json.dumps({"type": "meters", "data": [{"channel": c, "rms_db": v} for c, v in pairs]})


def _frozen_clock(monkeypatch):
    import app.mixer.service as mixer

    t = {"v": 1000.0}
    monkeypatch.setattr(mixer.time, "monotonic", lambda: t["v"])
    return t


def test_channel_rms_goes_silent_when_sample_is_stale(monkeypatch):
    clock = _frozen_clock(monkeypatch)
    svc = MixerService(mock=False)
    svc._connected = True
    svc._ingest(_meters((1, -20.0), (4, -35.0)))

    assert svc.channel_rms(1) == -20.0
    assert svc.channel_active(1, -40.0)
    assert svc.levels() == {1: -20.0, 4: -35.0}
    assert svc.meter_age(1) == 0.0
    assert svc.stale is False

    clock["v"] += 0.9
    assert svc.channel_rms(1) == -20.0  # within the window

    clock["v"] += 0.2  # 1.1 s old > 1.0 s threshold
    assert svc.channel_rms(1) == SILENCE_DB
    assert not svc.channel_active(1, -40.0)
    assert svc.levels() == {}  # stale channels omitted
    assert svc.meter_age(1) == pytest.approx(1.1)
    assert svc.stale is True

    # Fresh sample on one channel only: that one is live again.
    svc._ingest(_meters((1, -18.0)))
    assert svc.channel_rms(1) == -18.0
    assert svc.channel_rms(4) == SILENCE_DB
    assert svc.levels() == {1: -18.0}


def test_disconnect_clears_levels_and_reads_silent():
    svc = MixerService(mock=False)
    svc._connected = True
    svc._ingest(_meters((1, -12.0)))
    assert svc.channel_rms(1) == -12.0

    svc._connected = False  # as the reader loop does on a drop
    assert svc.channel_rms(1) == SILENCE_DB  # even though the sample is fresh
    assert svc.levels() == {}
    assert svc.stale is True


def test_status_exposes_age_and_staleness(monkeypatch):
    clock = _frozen_clock(monkeypatch)
    svc = MixerService(mock=False)
    svc._connected = True
    svc._ingest(_meters((2, -30.0)))
    clock["v"] += 0.5

    status = svc.status()
    assert status["connected"] is True and status["mock"] is False
    assert status["stale"] is False
    assert status["stale_seconds"] == 1.0
    assert status["channels"][2] == {"rms_db": -30.0, "age": 0.5, "fresh": True}

    clock["v"] += 1.0
    status = svc.status()
    assert status["stale"] is True
    assert status["channels"][2]["fresh"] is False


def test_stale_publishes_degraded_once_and_restored(monkeypatch):
    import app.mixer.service as mixer

    clock = _frozen_clock(monkeypatch)
    published = []
    monkeypatch.setattr(mixer.event_bus, "publish", lambda msg: published.append(msg))

    svc = MixerService(mock=False)
    svc._connected = True
    svc._ingest(_meters((1, -20.0)))
    svc.channel_rms(1)
    assert published == []

    clock["v"] += 2.0
    svc.channel_rms(1)
    svc.channel_rms(1)
    svc.levels()
    assert published == [
        {"event": "PERCEPTION_DEGRADED", "payload": {"reason": STALE_REASON, "channel": None}}
    ]

    svc._ingest(_meters((1, -21.0)))
    assert published[-1]["event"] == "PERCEPTION_RESTORED"
    assert published[-1]["payload"]["channel"] is None
    assert len(published) == 2

    # Second outage -> second (single) degraded event.
    clock["v"] += 2.0
    svc.channel_rms(1)
    svc.channel_rms(1)
    assert [m["event"] for m in published] == [
        "PERCEPTION_DEGRADED",
        "PERCEPTION_RESTORED",
        "PERCEPTION_DEGRADED",
    ]


def test_connected_feed_with_no_samples_yet_is_not_stale(monkeypatch):
    import app.mixer.service as mixer

    published = []
    monkeypatch.setattr(mixer.event_bus, "publish", lambda msg: published.append(msg))
    svc = MixerService(mock=False)
    svc._connected = True
    assert svc.stale is False
    assert svc.channel_rms(1) == SILENCE_DB
    assert published == []


def test_mock_mode_never_stale():
    svc = MixerService(mock=True)
    assert svc.stale is False
    assert svc.channel_rms(1) == SILENCE_DB
    assert svc.status()["mock"] is True and svc.status()["stale"] is False


async def test_wait_for_song_end_sees_silence_after_feed_drop(monkeypatch):
    import app.mixer.service as mixer

    clock = _frozen_clock(monkeypatch)
    monkeypatch.setattr(settings, "song_end_silence_db", -45.0)
    svc = MixerService(mock=False)
    svc._connected = True
    svc._ingest(_meters((1, -10.0)))  # song playing

    async def tick(_s):
        # Each poll advances the frozen clock; the feed never sends again.
        clock["v"] += 0.2

    monkeypatch.setattr(mixer.asyncio, "sleep", tick)
    ok = await svc.wait_for_song_end([1], hold_seconds=1.0, max_wait_seconds=10.0)
    assert ok is True  # stale meters read as silence -> song end detected
