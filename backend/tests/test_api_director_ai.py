"""AI Director REST routes: status shape, id-based approve/reject, legacy index."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.director.action_engine import ActionEngine
from app.director.action_models import DirectorAction, DirectorActionType
from app.director.ai_director_runtime import AIDirectorRuntime
from app.policy.engine import PolicyEngine


@pytest.fixture
def client(monkeypatch):
    import app.api.director as director_api

    runtime = AIDirectorRuntime(action_engine=ActionEngine(PolicyEngine(action_confidence_thresholds={"camera_change": 0.85})))
    runtime.set_mode("assisted")
    monkeypatch.setattr(director_api, "ai_director_runtime", runtime)
    # director_ai resolves the runtime singleton lazily from its module.
    monkeypatch.setattr("app.director.ai_director_runtime.ai_director_runtime", runtime)
    api = FastAPI()
    api.include_router(director_api.router)
    return TestClient(api), runtime


def _queue_camera(runtime, role="pastor"):
    action = DirectorAction(type=DirectorActionType.PTZ_SELECT_ROLE, target=role, confidence=0.95, reason=f"{role} speaking")
    return runtime.queue.submit(action, "ai_director", cycle_id="cyc").proposal


def test_status_exposes_queue_cycle_and_adaptive(client):
    http, runtime = client
    proposal = _queue_camera(runtime)
    runtime.queue.hold(proposal.id, "min hold: 3.0s")
    body = http.get("/director/ai/status").json()
    assert body["mode"] == "assisted"
    pending = body["pending_actions"][0]
    assert pending["id"] == proposal.id
    assert pending["held_reason"] == "min hold: 3.0s"
    assert pending["reason"] == "pastor speaking"
    assert pending["expires_in_seconds"] > 0
    assert "adaptive_confidence" in body and "last_cycle" in body
    assert http.get("/director/ai/pending").json()["pending_actions"][0]["id"] == proposal.id


def test_approve_by_id_and_errors(client, monkeypatch):
    http, runtime = client
    from app.cameras.service import camera_service

    moves = []

    async def fake_move_to_role(role):
        moves.append(role)
        return True

    monkeypatch.setattr(camera_service, "move_to_role", fake_move_to_role)
    proposal = _queue_camera(runtime)
    assert http.post("/director/ai/pending/doesnotexist/approve").status_code == 404
    assert http.post("/director/ai/pending/7/approve").status_code == 400
    resp = http.post(f"/director/ai/pending/{proposal.id}/approve")
    assert resp.status_code == 200 and resp.json()["executed"] is True and resp.json()["id"] == proposal.id
    assert moves == ["pastor"]
    assert http.get("/director/ai/status").json()["pending_actions"] == []


def test_reject_by_legacy_index_and_id(client):
    http, runtime = client
    first = _queue_camera(runtime, "pastor")
    resp = http.post("/director/ai/pending/0/reject")
    assert resp.status_code == 200 and resp.json() == {"ok": True, "id": first.id}
    second = _queue_camera(runtime, "liturgist")
    assert http.post(f"/director/ai/pending/{second.id}/reject").json()["id"] == second.id
    assert http.post("/director/ai/pending/0/reject").status_code == 400
    assert http.post(f"/director/ai/pending/{second.id}/reject").status_code == 404


def test_observe_route_returns_mode_and_proposals(client, monkeypatch):
    http, runtime = client
    import app.agents.director_ai as mod

    class Cue:
        id = "cue-1"
        name = "Reading"
        exit_hint = ""

    async def no_llm(self, *a, **k):
        return None

    monkeypatch.setattr(mod.service_director, "current_cue", lambda: Cue())
    monkeypatch.setattr(mod.DirectorAI, "_decide_with_llm", no_llm)
    body = http.post("/director/observe", json={"text": "the word of the Lord"}).json()
    assert body["accepted"] is True and body["mode"] == "assisted"
    assert body["proposals"][0]["type"] == "CUE_ADVANCE"
    assert runtime.pending_actions[0].parameters["heuristic"] is True
