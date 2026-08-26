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
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return _FakeResponse(self.reply)


@pytest.fixture(autouse=True)
def _no_memory_search_by_default(monkeypatch):
    """Isolate tests from any real database -- retrieval returns nothing
    unless a test explicitly monkeypatches it to return results."""
    monkeypatch.setattr("app.memory.production_memory.memory_manager.search", lambda *a, **k: [])


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


async def test_decide_includes_relevant_retrieved_history_in_prompt(monkeypatch):
    fake_llm = _FakeLLM('{"decision": "continue", "confidence": 0.5, "reason": "ok"}')
    monkeypatch.setattr("app.agents.llm.get_llm", lambda: fake_llm)
    monkeypatch.setattr(
        "app.memory.production_memory.memory_manager.search",
        lambda *a, **k: [
            {"service_date": "2026-08-10", "category": "ai_action", "text": "camera -> pastor", "similarity": 0.9}
        ],
    )

    director = AIServiceDirector()
    await director.decide(ServiceContext())

    user_message = fake_llm.messages[1][1]
    assert "2026-08-10" in user_message
    assert "camera -> pastor" in user_message


async def test_decide_filters_out_low_similarity_history(monkeypatch):
    fake_llm = _FakeLLM('{"decision": "continue", "confidence": 0.5, "reason": "ok"}')
    monkeypatch.setattr("app.agents.llm.get_llm", lambda: fake_llm)
    monkeypatch.setattr(
        "app.memory.production_memory.memory_manager.search",
        lambda *a, **k: [
            {"service_date": "2026-08-10", "category": "ai_action", "text": "irrelevant match", "similarity": 0.01}
        ],
    )

    director = AIServiceDirector()
    await director.decide(ServiceContext())

    user_message = fake_llm.messages[1][1]
    assert "irrelevant match" not in user_message
    assert "None found" in user_message


async def test_decide_survives_memory_retrieval_failure(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("database unreachable")

    fake_llm = _FakeLLM('{"decision": "continue", "confidence": 0.5, "reason": "ok"}')
    monkeypatch.setattr("app.agents.llm.get_llm", lambda: fake_llm)
    monkeypatch.setattr("app.memory.production_memory.memory_manager.search", _raise)

    director = AIServiceDirector()
    decision = await director.decide(ServiceContext())

    assert decision.decision == "continue"


async def test_decide_skips_retrieval_when_disabled(monkeypatch):
    from app.config import settings

    def _raise(*a, **k):
        raise AssertionError("search should not be called when RAG is disabled")

    fake_llm = _FakeLLM('{"decision": "continue", "confidence": 0.5, "reason": "ok"}')
    monkeypatch.setattr("app.agents.llm.get_llm", lambda: fake_llm)
    monkeypatch.setattr("app.memory.production_memory.memory_manager.search", _raise)
    monkeypatch.setattr(settings, "ai_director_use_memory_rag", False)

    director = AIServiceDirector()
    decision = await director.decide(ServiceContext())

    assert decision.decision == "continue"
