"""Tests for AIServiceDirector: Claude-backed decision parsing with safe fallback."""

import pytest

from app.ai.decision import DirectorDecision
from app.ai.service_director import AIServiceDirector
from app.domain.service_context import ServiceContext


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply

    async def ainvoke(self, messages):
        return _FakeResponse(self.reply)


async def test_decide_parses_valid_json(monkeypatch):
    reply = (
        '{"decision": "transition", "confidence": 0.92, "reason": "scripture finished", '
        '"service_state": "sermon", "actions": [{"type": "PTZ_SELECT_ROLE", "camera_role": "pastor"}]}'
    )
    monkeypatch.setattr("app.agents.llm.get_llm", lambda: _FakeLLM(reply))

    director = AIServiceDirector()
    decision = await director.decide(ServiceContext())

    assert isinstance(decision, DirectorDecision)
    assert decision.decision == "transition"
    assert decision.confidence == 0.92
    assert decision.service_state == "sermon"
    assert decision.actions[0].type == "PTZ_SELECT_ROLE"
    assert decision.actions[0].camera_role == "pastor"


async def test_decide_falls_back_when_llm_unavailable(monkeypatch):
    def _raise():
        raise ValueError("no api key")

    monkeypatch.setattr("app.agents.llm.get_llm", _raise)

    director = AIServiceDirector()
    decision = await director.decide(ServiceContext())

    assert decision.decision == "continue"
    assert decision.confidence == 0.0
    assert decision.actions == []


async def test_decide_falls_back_on_malformed_json(monkeypatch):
    monkeypatch.setattr("app.agents.llm.get_llm", lambda: _FakeLLM("not json at all"))

    director = AIServiceDirector()
    decision = await director.decide(ServiceContext())

    assert decision.decision == "continue"
    assert decision.confidence == 0.0
