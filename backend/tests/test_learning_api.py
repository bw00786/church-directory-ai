"""/api/learning routes: operator shots, operator advances, house style."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.director import operator_learning, operator_shots
from app.director.operator_learning import OperatorAdvanceHistory, OperatorAdvanceRecorder
from app.director.operator_shots import OperatorShotHistory, OperatorShotRecorder


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.api.learning as learning_api  # noqa: F401  (import check: the integrator mounts this router)

    monkeypatch.setitem(settings.__dict__, "operator_shots_min_samples", 3)
    monkeypatch.setitem(settings.__dict__, "operator_shots_min_share", 0.6)
    shots = OperatorShotHistory(str(tmp_path / "shots.json"))
    monkeypatch.setattr(operator_shots, "operator_shot_history", shots)
    monkeypatch.setattr(operator_shots, "operator_shot_recorder", OperatorShotRecorder(shots, record_memory=lambda s, t: None))
    advances = OperatorAdvanceHistory(str(tmp_path / "advances.json"))
    monkeypatch.setattr(operator_learning, "operator_advance_history", advances)
    monkeypatch.setattr(operator_learning, "operator_advance_recorder", OperatorAdvanceRecorder(advances))
    api = FastAPI()
    api.include_router(learning_api.router)
    return TestClient(api), shots, advances


def _cut(speaker="pastor", role="pastor", hold=20.0, delay=2.0, at="2026-10-30T10:00:00+00:00"):
    return {"kind": "cut", "at": at, "service_state": "sermon", "speaker_role": speaker, "to_camera_role": role,
            "to_input": 1, "camera_id": 1, "preset_id": 3, "hold_seconds": hold, "delay_since_speaker_change": delay}


def test_operator_shots_route_returns_status_and_history(client):
    http, shots, _ = client
    for i in range(3):
        shots.record(_cut(at=f"2026-10-30T10:00:0{i}+00:00"))
    body = http.get("/api/learning/operator-shots?limit=2").json()
    assert body["enabled"] is True and body["running"] is False and body["recorded_this_run"] == 0
    assert body["consumes"][0] == "PROGRAM_CHANGED"
    history = body["history"]
    assert history["keys"] == 1 and history["samples"] == 3 and len(history["recent"]) == 2
    assert history["recent"][0]["at"] == "2026-10-30T10:00:02+00:00"
    assert history["preferences"][0]["preferred"]["camera_role"] == "pastor"
    assert history["typical_hold_seconds"] == {"sermon": 20.0}
    assert history["speaker_change_latency_seconds"] == {"sermon": 2.0}


def test_operator_advances_route_mirrors_director_route(client):
    http, _, advances = client
    advances.record("Amazing grace how sweet the sound", {"at": "2026-10-30T10:00:00+00:00", "label": "Hymn",
                                                          "from_slide": 1, "dwell": 20.0, "delay": 1.0,
                                                          "role": "vocalist", "tail": "sweet the sound"})
    body = http.get("/api/learning/operator-advances").json()
    assert body["enabled"] is True and "running" in body
    assert body["history"]["slides"] == 1
    assert body["history"]["recent"][0]["labels"] == ["Hymn"]


def test_house_style_route_returns_prompt_bullets_and_learned_shots(client, monkeypatch):
    http, shots, _ = client
    import app.memory.production_memory as pm

    calls = []

    def fake_summary(state, easyworship_item=None, limit=400):
        calls.append((state, easyworship_item))
        return ["During sermon you stay on the pastor shot (preset 3) 92% of the time."]

    monkeypatch.setattr(pm.memory_manager, "summarize_house_style", fake_summary)
    for i in range(3):
        shots.record(_cut(at=f"2026-10-30T10:00:0{i}+00:00"))
    body = http.get("/api/learning/house-style", params={"state": "sermon", "easyworship_item": "Sermon"}).json()
    assert calls == [("sermon", "Sermon")]
    assert body["state"] == "sermon" and body["rules"] == ["During sermon you stay on the pastor shot (preset 3) 92% of the time."]
    learned = body["learned_shots"]
    assert learned["preferred_shot"]["preset_id"] == 3 and learned["preferred_shot"]["share"] == 1.0
    assert learned["typical_hold_seconds"] == 20.0 and learned["speaker_change_latency_seconds"] == 2.0


def test_house_style_route_rejects_unknown_state_and_requires_one(client):
    http, _, _ = client
    response = http.get("/api/learning/house-style", params={"state": "coffee_hour"})
    assert response.status_code == 400
    assert "sermon" in response.json()["detail"]["valid"]
    assert http.get("/api/learning/house-style").status_code == 422


def test_house_style_route_survives_memory_outage(client, monkeypatch):
    http, _, _ = client
    import app.memory.production_memory as pm

    monkeypatch.setattr(pm.memory_manager, "summarize_house_style", lambda *a, **k: [])
    body = http.get("/api/learning/house-style", params={"state": "hymn"}).json()
    assert body["rules"] == [] and body["learned_shots"]["preferred_shot"] is None
