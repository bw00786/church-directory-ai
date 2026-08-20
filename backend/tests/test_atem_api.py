"""Tests for the ATEM REST API endpoints (mock bridge)."""

import pytest
from fastapi.testclient import TestClient

from app.atem.service import AtemService
from app.dependencies import get_atem_service
from app.main import app

# Force the mock client regardless of auto-detect/local environment (e.g. a
# real ATEM bridge happening to be reachable on this dev machine).
_forced_mock_service = AtemService(mock=True)
app.dependency_overrides[get_atem_service] = lambda: _forced_mock_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def _connect_atem():
    """Ensure the shared ATEM service is connected (mock) before each test."""
    client.post("/atem/connect")
    yield


def test_atem_program_accepts_json_body():
    response = client.post("/atem/program", json={"input_id": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["state"]["program_input"] == 2


def test_atem_preview_accepts_json_body():
    response = client.post("/atem/preview", json={"input_id": 1})
    assert response.status_code == 200
    assert response.json()["state"]["preview_input"] == 1


def test_atem_stream_start_stop():
    response = client.post("/atem/stream/start")
    assert response.status_code == 200
    assert response.json()["state"]["streaming"] is True

    response = client.post("/atem/stream/stop")
    assert response.status_code == 200
    assert response.json()["state"]["streaming"] is False


def test_atem_record_start_stop():
    response = client.post("/atem/record/start")
    assert response.status_code == 200
    assert response.json()["state"]["recording"] is True

    response = client.post("/atem/record/stop")
    assert response.status_code == 200
    assert response.json()["state"]["recording"] is False


def test_atem_mic_mute():
    response = client.post("/atem/mic/1/mute", json={"muted": True})
    assert response.status_code == 200
    mic1 = next(c for c in response.json()["state"]["audio_channels"] if c["id"] == 1)
    assert mic1["muted"] is True

    response = client.post("/atem/mic/1/mute", json={"muted": False})
    assert response.status_code == 200


def test_atem_mic_mute_invalid_channel():
    response = client.post("/atem/mic/999/mute", json={"muted": True})
    assert response.status_code == 400
