"""Tests for MixerService: meter/analysis/dsp ingest and the software-DSP
control client against a fake mgx-ai-mixer REST API."""

import json

import httpx
import pytest

from app.mixer.service import MixerService, api_url_from_ws


def test_api_url_from_ws():
    assert api_url_from_ws("ws://127.0.0.1:9000/ws") == "http://127.0.0.1:9000"
    assert api_url_from_ws("wss://mixer.local/ws") == "https://mixer.local"


def test_ingest_meters_analysis_and_dsp():
    svc = MixerService(mock=False, ws_url="ws://127.0.0.1:9000/ws")
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
