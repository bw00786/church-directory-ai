"""Tests for the assistant chat API endpoints (agent mocked to avoid real LLM calls)."""

import pytest
from fastapi.testclient import TestClient

import app.api.assistant as assistant_api
from app.agents import assistant_tools
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_pending_actions(monkeypatch):
    monkeypatch.setattr(assistant_tools, "pending_actions", {})


def test_pending_empty_without_chat():
    response = client.get("/api/assistant/pending")
    assert response.status_code == 200
    assert response.json() == []


def test_pending_lists_oldest_first_without_arguments_or_secrets(monkeypatch):
    async def unexpected_execution(token):
        pytest.fail("Listing approvals must not execute them")

    monkeypatch.setattr(assistant_api, "execute_pending", unexpected_execution)
    first = assistant_tools._register_pending(
        "atem_stop_stream", {"password": "secret-value"}, "Stop the live stream"
    )
    second = assistant_tools._register_pending("atem_stop_recording", {}, "Stop recording")
    assistant_tools.pending_actions[first]["internal_secret"] = "private-value"
    expected = [
        {"token": first, "action": "atem_stop_stream", "description": "Stop the live stream"},
        {"token": second, "action": "atem_stop_recording", "description": "Stop recording"},
    ]

    for _ in range(2):
        response = client.get("/api/assistant/pending")
        assert response.status_code == 200
        assert response.json() == expected
    assert list(assistant_tools.pending_actions) == [first, second]


def test_pending_refreshes_after_confirm_and_cancel_without_reexecution(monkeypatch):
    calls = []

    class FakeAtem:
        async def start_stream(self):
            calls.append("start_stream")
            return True

    monkeypatch.setattr(assistant_tools, "get_atem_service_instance", lambda: FakeAtem())
    first = assistant_tools._register_pending("atem_start_stream", {}, "Start stream")
    second = assistant_tools._register_pending("atem_stop_recording", {}, "Stop recording")

    assert client.post(f"/api/assistant/confirm/{first}").json()["ok"] is True
    assert [item["token"] for item in client.get("/api/assistant/pending").json()] == [second]
    assert client.post(f"/api/assistant/confirm/{first}").status_code == 404
    assert client.post(f"/api/assistant/cancel/{second}").json()["ok"] is True
    assert client.get("/api/assistant/pending").json() == []
    assert calls == ["start_stream"]


def test_failed_confirmation_is_not_pending_anymore(monkeypatch):
    class FakeAtem:
        async def start_stream(self):
            return False

    monkeypatch.setattr(assistant_tools, "get_atem_service_instance", lambda: FakeAtem())
    token = assistant_tools._register_pending("atem_start_stream", {}, "Start stream")
    response = client.post(f"/api/assistant/confirm/{token}")
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert client.get("/api/assistant/pending").json() == []


def test_chat_returns_mocked_reply(monkeypatch):
    async def fake_run_assistant(messages):
        assert messages[-1]["content"] == "who preached last Sunday?"
        return {"reply": "Pastor Jim preached.", "pending_confirmation": None}

    monkeypatch.setattr(assistant_api, "run_assistant", fake_run_assistant)

    response = client.post(
        "/api/assistant/chat",
        json={"messages": [{"role": "user", "content": "who preached last Sunday?"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Pastor Jim preached."
    assert body["pending_confirmation"] is None


def test_chat_returns_503_without_api_key(monkeypatch):
    async def fake_run_assistant(messages):
        raise ValueError("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr(assistant_api, "run_assistant", fake_run_assistant)

    response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 503


def test_confirm_executes_pending_action(monkeypatch):
    class FakeAtem:
        async def start_stream(self):
            return True

    monkeypatch.setattr(assistant_tools, "get_atem_service_instance", lambda: FakeAtem())
    token = assistant_tools._register_pending("atem_start_stream", {}, "Start the live stream")

    response = client.post(f"/api/assistant/confirm/{token}")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_confirm_unknown_token_404():
    response = client.post("/api/assistant/confirm/does-not-exist")
    assert response.status_code == 404


def test_cancel_pending_action():
    token = assistant_tools._register_pending("atem_stop_recording", {}, "Stop recording")
    response = client.post(f"/api/assistant/cancel/{token}")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_cancel_unknown_token_404():
    response = client.post("/api/assistant/cancel/does-not-exist")
    assert response.status_code == 404
