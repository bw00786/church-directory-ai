"""Tests for the assistant tools (query + tiered control) and confirmation flow."""

import json

import pytest

from app.agents import assistant_tools


def _parse(tool_result: str) -> dict:
    return json.loads(tool_result)


def test_list_past_services_returns_json(monkeypatch):
    monkeypatch.setattr(
        assistant_tools.memory_manager, "list_services", lambda limit=20: [{"service_date": "2026-08-16", "observation_count": 3}]
    )
    result = _parse(assistant_tools.list_past_services.invoke({"limit": 20}))
    assert result == [{"service_date": "2026-08-16", "observation_count": 3}]


def test_who_preached_found(monkeypatch):
    monkeypatch.setattr(
        assistant_tools.identity_service,
        "who_was_seen",
        lambda role, service_date: {"person_name": "Pastor Jim", "role": role, "confidence": 0.9, "sighting_count": 4},
    )
    result = _parse(assistant_tools.who_preached.invoke({"service_date": "2026-08-16"}))
    assert result["found"] is True
    assert result["person_name"] == "Pastor Jim"


def test_who_preached_not_found(monkeypatch):
    monkeypatch.setattr(assistant_tools.identity_service, "who_was_seen", lambda role, service_date: None)
    result = _parse(assistant_tools.who_preached.invoke({"service_date": "2026-08-16"}))
    assert result["found"] is False


def test_high_risk_tool_registers_pending_not_executed(monkeypatch):
    """Calling request_start_streaming must NOT touch the real ATEM service."""
    calls = []
    monkeypatch.setattr(
        "app.agents.assistant_tools.get_atem_service_instance",
        lambda: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    result = _parse(assistant_tools.request_start_streaming.invoke({}))
    assert "pending_confirmation" in result
    token = result["pending_confirmation"]
    assert token in assistant_tools.pending_actions
    assert calls == []


@pytest.mark.asyncio
async def test_execute_pending_runs_the_real_action(monkeypatch):
    class FakeAtem:
        async def start_stream(self):
            return True

    monkeypatch.setattr(assistant_tools, "get_atem_service_instance", lambda: FakeAtem())

    result = _parse(assistant_tools.request_start_streaming.invoke({}))
    token = result["pending_confirmation"]

    outcome = await assistant_tools.execute_pending(token)
    assert outcome["ok"] is True
    # Token is single-use.
    assert token not in assistant_tools.pending_actions


@pytest.mark.asyncio
async def test_execute_pending_unknown_token():
    outcome = await assistant_tools.execute_pending("does-not-exist")
    assert outcome["ok"] is False
    assert "Unknown" in outcome["error"]


def test_discard_pending():
    result = _parse(assistant_tools.request_stop_recording.invoke({}))
    token = result["pending_confirmation"]
    assert assistant_tools.discard_pending(token) is True
    assert assistant_tools.discard_pending(token) is False
