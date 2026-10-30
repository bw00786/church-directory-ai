"""Rehearsal mode (replay through a private runtime, mock dispatch) and AI pause/resume."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.decision import DirectorActionSpec, DirectorDecision
from app.config import settings
from app.director import rehearsal as mod
from app.director.action_engine import ActionEngine
from app.director.action_models import DirectorAction, DirectorActionType
from app.director.ai_director_runtime import AIDirectorRuntime
from app.director.rehearsal import (
    RehearsalActionEngine,
    RehearsalService,
    list_samples,
    load_recording,
    rehearsal_allowed,
    resolve_recording_path,
)
from app.events.bus import event_bus
from app.policy.engine import PolicyEngine


def _override(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setitem(settings.__dict__, name, value)


@pytest.fixture(autouse=True)
def mocked(monkeypatch):
    _override(monkeypatch, enable_mock_atem=True, enable_mock_mixer=True, enable_mock_easyworship=True,
              force_mock_during_rehearsal=False, rehearsal_default_gap_seconds=0.0)


async def _no_sleep(_seconds):
    return None


def _decider(decisions):
    """Return decisions in order; a camera cut on the first pastor line."""
    calls = []

    async def decide(context):
        calls.append(context.snapshot()["recent_transcript"])
        index = min(len(calls) - 1, len(decisions) - 1)
        return decisions[index]

    decide.calls = calls
    return decide


def _service(decide, mode="ai_directed"):
    return RehearsalService(decide=decide, sleep=_no_sleep,
                            runtime_factory=lambda m: _runtime(m))


def _runtime(mode):
    runtime = mod.RehearsalRuntime(action_engine=RehearsalActionEngine(PolicyEngine()))
    runtime.set_mode(mode)
    return runtime


CUT = DirectorDecision(
    decision="transition", confidence=0.95, reason="pastor began preaching", service_state="sermon",
    actions=[DirectorActionSpec(type="PTZ_SELECT_ROLE", camera_role="pastor", confidence=0.95, reason="pastor speaking")],
    cycle_id="cyc-1", latency_ms=120,
)
CONTINUE = DirectorDecision(decision="continue", confidence=0.3, reason="nothing to do", cycle_id="cyc-2")


# -- gating ---------------------------------------------------------------------------


def test_refuses_with_real_hardware_unless_forced(monkeypatch):
    _override(monkeypatch, enable_mock_atem=False)
    allowed, why = rehearsal_allowed()
    assert allowed is False and "FORCE_MOCK_DURING_REHEARSAL" in why
    _override(monkeypatch, force_mock_during_rehearsal=True)
    assert rehearsal_allowed()[0] is True


def test_refuses_while_service_running(monkeypatch):
    from app.director.engine import service_director

    monkeypatch.setattr(service_director, "_running", True)
    allowed, why = rehearsal_allowed()
    assert allowed is False and "running" in why


# -- recordings -------------------------------------------------------------------------


def test_bundled_sample_exists_and_loads():
    samples = list_samples()
    assert any(s["id"] == "default" and s["kind"] == "sample" and s["entries"] >= 10 for s in samples)
    path = resolve_recording_path(sample="default", file=None)
    meta, entries = load_recording(json.loads(path.read_text(encoding="utf-8")))
    assert meta["name"] and entries[0]["speaker_role"] == "liturgist"


def test_file_must_live_in_recordings_dir(tmp_path, monkeypatch):
    _override(monkeypatch, rehearsal_recordings_dir=str(tmp_path / "rec"))
    (tmp_path / "rec").mkdir()
    good = tmp_path / "rec" / "sunday.json"
    good.write_text("[]", encoding="utf-8")
    outside = tmp_path / "secret.json"
    outside.write_text("[]", encoding="utf-8")
    assert resolve_recording_path(sample=None, file="sunday.json") == good.resolve()
    with pytest.raises(FileNotFoundError):
        resolve_recording_path(sample=None, file=str(outside))
    with pytest.raises(FileNotFoundError):
        resolve_recording_path(sample=None, file="../secret.json")
    with pytest.raises(FileNotFoundError):
        resolve_recording_path(sample="nope", file=None)


def test_load_recording_shapes():
    assert load_recording([{"role": "pastor", "text": "hi"}])[1] == [{"role": "pastor", "text": "hi"}]
    meta, entries = load_recording({"name": "x", "entries": [{"speaker_role": "pastor"}]})
    assert meta["name"] == "x" and len(entries) == 1
    with pytest.raises(ValueError):
        load_recording({"entries": []})
    with pytest.raises(ValueError):
        load_recording("nonsense")


# -- replay -----------------------------------------------------------------------------


async def _run_to_completion(service, **kwargs):
    await service.start(**kwargs)
    for _ in range(200):
        if not service.running:
            break
        await asyncio.sleep(0)
    assert not service.running


async def test_replay_executes_against_mock_engine_only(monkeypatch):
    moved = []

    async def forbidden(*args, **kwargs):  # pragma: no cover - must never run
        moved.append(args)
        return True

    from app.cameras.service import camera_service
    from app.easyworship.service import easyworship_service

    monkeypatch.setattr(camera_service, "move_to_role", forbidden)
    monkeypatch.setattr(easyworship_service, "next_slide_in_item", forbidden)
    monkeypatch.setattr(ActionEngine, "_dispatch", forbidden)

    received = []
    queue = await event_bus.subscribe()
    decide = _decider([CUT, CONTINUE])
    service = _service(decide)
    await _run_to_completion(service, sample="default", speed=10.0)
    while not queue.empty():
        received.append(queue.get_nowait()["event"])
    await event_bus.unsubscribe(queue)

    status = service.status()
    assert status["state"] == "finished" and status["position"] == status["total"] == 12
    assert status["progress"] == 1.0 and status["mock_only"] is True
    assert len(status["decisions"]) == 12
    first = status["decisions"][0]
    assert first["decision"] == "transition" and first["actions"][0]["type"] == "PTZ_SELECT_ROLE"
    assert first["service_state"] == "sermon"
    assert status["executed"] and status["executed"][0]["detail"] == "mock PTZ_SELECT_ROLE -> pastor"
    assert moved == []
    assert "REHEARSAL_STARTED" in received and "REHEARSAL_STOPPED" in received
    # The live context is untouched by the rehearsal.
    from app.domain.service_context import service_context

    assert service_context.last_observation is None or service_context.last_observation.transcript != "Good morning and welcome to worship. Please stand as we begin with our opening hymn."


async def test_assisted_mode_queues_proposals_instead_of_executing():
    service = _service(_decider([CUT, CONTINUE]))
    await _run_to_completion(service, sample="default", speed=10.0, mode="assisted")
    status = service.status()
    assert status["mode"] == "assisted" and status["executed"] == []
    assert status["proposals"] and status["proposals"][0]["type"] == "PTZ_SELECT_ROLE"
    assert status["decisions"][0]["proposals"] == [status["proposals"][0]["id"]]


async def test_stop_mid_replay_and_restart(monkeypatch):
    gate = asyncio.Event()

    async def slow_sleep(_seconds):
        await gate.wait()

    service = RehearsalService(decide=_decider([CONTINUE]), sleep=slow_sleep, runtime_factory=_runtime)
    await service.start(sample="default")
    await asyncio.sleep(0)
    assert service.running
    with pytest.raises(RuntimeError):
        await service.start(sample="default")
    status = await service.stop()
    assert status["state"] == "stopped" and status["running"] is False and status["position"] < status["total"]
    gate.set()
    await _run_to_completion(service, sample="default", speed=50.0)
    assert service.status()["state"] == "finished"


async def test_start_errors(monkeypatch):
    service = _service(_decider([CONTINUE]))
    with pytest.raises(ValueError):
        await service.start(sample="default", speed=0.0)
    with pytest.raises(ValueError):
        await service.start(sample="default", mode="turbo")
    _override(monkeypatch, enable_mock_mixer=False)
    with pytest.raises(PermissionError):
        await service.start(sample="default")


async def test_decide_failure_is_recorded_not_raised():
    async def broken(context):
        raise RuntimeError("ollama down")

    service = RehearsalService(decide=broken, sleep=_no_sleep, runtime_factory=_runtime)
    await _run_to_completion(service, sample="default", speed=50.0)
    status = service.status()
    assert status["state"] == "finished"
    assert status["decisions"][0]["reason"] == "decide failed: RuntimeError"


def test_rehearsal_engine_applies_policy_and_pacing():
    engine = RehearsalActionEngine(PolicyEngine(action_confidence_thresholds={"camera_change": 0.85}))
    weak = DirectorAction(type=DirectorActionType.PTZ_SELECT_ROLE, target="pastor", confidence=0.5, reason="meh")
    strong = DirectorAction(type=DirectorActionType.PTZ_SELECT_ROLE, target="pastor", confidence=0.95, reason="yes")
    rejected = asyncio.run(engine.execute(weak))
    assert rejected.approved is False and engine.executed == []
    done = asyncio.run(engine.execute(strong))
    assert done.executed is True and engine.executed[0]["target"] == "pastor"
    again = asyncio.run(engine.execute(strong))
    assert again.held is True and "hold" in (again.held_reason or "").lower()


# -- routes -----------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    import app.api.director as director_api

    service = _service(_decider([CUT, CONTINUE]))
    monkeypatch.setattr(director_api, "rehearsal_service", service)
    runtime = AIDirectorRuntime(action_engine=ActionEngine(PolicyEngine()))
    runtime.set_mode("ai_directed")
    monkeypatch.setattr(director_api, "ai_director_runtime", runtime)
    api = FastAPI()
    api.include_router(director_api.router)
    return TestClient(api), service, runtime


def test_rehearsal_routes(client, monkeypatch):
    http, service, _ = client
    body = http.get("/director/rehearsal/samples").json()
    assert body["allowed"] is True and body["samples"][0]["id"] == "default"
    assert http.post("/director/rehearsal/start", json={"sample": "missing"}).status_code == 404
    assert http.post("/director/rehearsal/start", json={"sample": "default", "mode": "turbo"}).status_code == 422
    assert http.post("/director/rehearsal/start", json={"sample": "default", "speed": 0}).status_code == 422
    _override(monkeypatch, enable_mock_atem=False)
    assert http.post("/director/rehearsal/start", json={"sample": "default"}).status_code == 409
    _override(monkeypatch, enable_mock_atem=True)
    with http:
        started = http.post("/director/rehearsal/start", json={"sample": "default", "speed": 50}).json()
        assert started["state"] == "running" and started["total"] == 12
        stopped = http.post("/director/rehearsal/stop").json()
        assert stopped["running"] is False
        status = http.get("/director/rehearsal/status").json()
        assert status["state"] in ("stopped", "finished") and "decisions" in status and "proposals" in status


def test_pause_and_resume_routes(client):
    http, _, runtime = client
    status = http.get("/director/ai/status").json()
    assert status["paused"] is False and status["paused_from"] is None
    paused = http.post("/director/ai/pause").json()
    assert paused == {"mode": "manual", "paused": True, "paused_from": "ai_directed"}
    # Idempotent: a second pause keeps the original mode to go back to.
    assert http.post("/director/ai/pause").json()["paused_from"] == "ai_directed"
    assert http.get("/director/ai/status").json()["mode"] == "manual"
    resumed = http.post("/director/ai/resume").json()
    assert resumed == {"mode": "ai_directed", "paused": False, "paused_from": None}
    assert http.post("/director/ai/resume").json()["mode"] == "ai_directed"
    # An explicit mode change while paused ends the pause.
    http.post("/director/ai/pause")
    runtime.set_mode("assisted")
    assert http.get("/director/ai/status").json()["paused"] is False


async def test_pause_publishes_events():
    runtime = AIDirectorRuntime(action_engine=ActionEngine(PolicyEngine()))
    runtime.set_mode("assisted")
    queue = await event_bus.subscribe()
    try:
        runtime.pause()
        runtime.resume()
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        await event_bus.unsubscribe(queue)
    names = [e["event"] for e in events]
    assert "AI_DIRECTOR_PAUSED" in names and "AI_DIRECTOR_RESUMED" in names
    paused = next(e for e in events if e["event"] == "AI_DIRECTOR_PAUSED")
    assert paused["payload"] == {"mode": "manual", "paused_from": "assisted"}
    assert runtime.mode == "assisted" and not runtime.paused
