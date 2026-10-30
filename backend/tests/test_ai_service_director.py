"""Tests for AIServiceDirector: Ollama JSON decisions, hard deadline, backoff,
structured retrieval in the prompt, and safe fallback on every failure mode."""

import asyncio

import pytest

from app.agents import llm as llm_module
from app.ai.decision import DirectorDecision
from app.ai.service_director import AIServiceDirector
from app.config import settings
from app.domain.service_context import ServiceContext


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, reply: str, delay: float = 0.0):
        self.reply = reply
        self.delay = delay
        self.messages = None
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        self.messages = messages
        if self.delay:
            await asyncio.sleep(self.delay)
        return _FakeResponse(self.reply)


CONTINUE = '{"decision": "continue", "confidence": 0.5, "reason": "ok", "actions": []}'


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No database, no backoff state leaking between tests."""
    monkeypatch.setattr("app.memory.production_memory.memory_manager.retrieve_similar_moments", lambda *a, **k: [])
    monkeypatch.setattr("app.memory.production_memory.memory_manager.summarize_house_style", lambda *a, **k: [])
    monkeypatch.setattr("app.memory.production_memory.memory_manager.search", lambda *a, **k: [])
    monkeypatch.setattr(llm_module, "director_backoff", llm_module.InferenceBackoff(prefix="AI_DIRECTOR"))
    yield


def _user_prompt(fake: _FakeLLM) -> str:
    return fake.messages[1][1]


async def test_decide_parses_valid_json_and_stamps_cycle_metadata(monkeypatch):
    reply = (
        '{"decision": "transition", "confidence": 0.92, "reason": "scripture finished", '
        '"service_state": "sermon", "actions": [{"type": "PTZ_SELECT_ROLE", "camera_role": "pastor"}]}'
    )
    fake = _FakeLLM(reply)
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: fake)

    director = AIServiceDirector()
    context = ServiceContext()
    decision = await director.decide(context)

    assert isinstance(decision, DirectorDecision)
    assert decision.decision == "transition"
    assert decision.confidence == 0.92
    assert decision.service_state == "sermon"
    assert decision.actions[0].type == "PTZ_SELECT_ROLE"
    assert decision.actions[0].camera_role == "pastor"
    assert decision.actions[0].confidence == 0.92  # fallback to decision-level
    assert decision.cycle_id == director.last_cycle_id
    assert decision.context_version == context.snapshot().get("version")
    assert decision.latency_ms is not None and decision.latency_ms >= 0
    assert director.last_error is None
    assert fake.messages[0][0] == "system"
    for section in ("TIMING", "WHO IS SPEAKING", "PLAN CURSOR", "ON AIR", "VISION",
                    "RECENT TRANSCRIPT", "RECENT ACTIONS + PREVIOUS DECISION", "HOUSE RULES", "MEMORY"):
        assert f"=== {section} ===" in _user_prompt(fake)
    assert director.last_prompt_tokens and director.last_prompt_tokens > 100


async def test_decide_falls_back_when_llm_unavailable(monkeypatch):
    def _raise():
        raise ValueError("Configure a nonempty Ollama model tag")

    monkeypatch.setattr("app.agents.llm.get_director_llm", _raise)

    director = AIServiceDirector()
    decision = await director.decide(ServiceContext())

    assert decision.decision == "continue"
    assert decision.confidence == 0.0
    assert decision.actions == []
    assert decision.reason.startswith("inference failed")
    assert decision.cycle_id == director.last_cycle_id is not None
    assert director.last_error == decision.reason


async def test_decide_falls_back_on_malformed_json(monkeypatch):
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: _FakeLLM("not json at all"))

    director = AIServiceDirector()
    decision = await director.decide(ServiceContext())

    assert decision.decision == "continue"
    assert decision.confidence == 0.0
    assert "invalid decision" in decision.reason
    # A bad *answer* is not an Ollama outage: no backoff.
    assert llm_module.director_backoff.consecutive_failures == 0


async def test_unknown_action_type_yields_continue_with_clear_reason(monkeypatch):
    reply = '{"decision": "deviate", "confidence": 0.9, "reason": "x", "actions": [{"type": "FIRE_LASERS"}]}'
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: _FakeLLM(reply))

    decision = await AIServiceDirector().decide(ServiceContext())
    assert decision.decision == "continue" and decision.confidence == 0.0
    assert "unknown action type 'FIRE_LASERS'" in decision.reason


async def test_out_of_range_confidence_yields_continue(monkeypatch):
    reply = '{"decision": "transition", "confidence": 1.7, "reason": "x", "actions": []}'
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: _FakeLLM(reply))
    decision = await AIServiceDirector().decide(ServiceContext())
    assert decision.decision == "continue"
    assert "confidence" in decision.reason


async def test_deadline_exceeded_returns_continue_with_cycle_id(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "ai_director_deadline_seconds", 0.05)
    fake = _FakeLLM(CONTINUE, delay=5.0)
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: fake)

    director = AIServiceDirector()
    context = ServiceContext()
    decision = await director.decide(context)

    assert decision.decision == "continue" and decision.confidence == 0.0
    assert "deadline of 0.05s exceeded" in decision.reason
    assert decision.cycle_id == director.last_cycle_id is not None
    assert decision.context_version == context.snapshot().get("version")
    assert decision.latency_ms is not None and decision.latency_ms < 2000
    assert llm_module.director_backoff.consecutive_failures == 1
    assert llm_module.director_backoff.degraded is True


async def test_backoff_skips_inference_until_retry_then_recovers(monkeypatch):
    clock = {"now": 100.0}
    backoff = llm_module.InferenceBackoff(prefix="AI_DIRECTOR", clock=lambda: clock["now"], rng=lambda: 0.0)
    monkeypatch.setattr(llm_module, "director_backoff", backoff)

    class _Flaky:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("refused")
            return _FakeResponse(CONTINUE)

    flaky = _Flaky()
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: flaky)
    monkeypatch.setattr(llm_module.ollama_locator, "request_resolve", lambda: None)
    director = AIServiceDirector()

    first = await director.decide(ServiceContext())
    assert first.reason == "inference failed: ConnectionError"
    assert "refused" not in first.reason  # exception text never leaks into the decision
    assert backoff.seconds_until_retry() == pytest.approx(1.0)

    skipped = await director.decide(ServiceContext())
    assert flaky.calls == 1  # not called while backing off
    assert skipped.decision == "continue" and skipped.reason.startswith("inference skipped: backing off")
    assert skipped.cycle_id == director.last_cycle_id

    clock["now"] += 1.5
    recovered = await director.decide(ServiceContext())
    assert flaky.calls == 2
    assert recovered.confidence == 0.5
    assert backoff.degraded is False and backoff.consecutive_failures == 0


async def test_decide_uses_fast_director_model_and_deadline_not_long_timeout(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "ai_director_deadline_seconds", 0.05)
    monkeypatch.setattr(settings, "llm_timeout_seconds", 120)
    fake = _FakeLLM(CONTINUE, delay=1.0)
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: fake)
    loop = asyncio.get_running_loop()
    started = loop.time()
    decision = await AIServiceDirector().decide(ServiceContext())
    assert loop.time() - started < 0.9
    assert "deadline" in decision.reason


async def test_decide_includes_structured_history_in_prompt(monkeypatch):
    fake = _FakeLLM(CONTINUE)
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: fake)
    calls = {}

    def _retrieve(state, speaker_role=None, category=None, limit=5, *, query=None):
        calls.update(state=state, speaker_role=speaker_role, category=category, limit=limit, query=query)
        return [{"service_date": "2026-08-10", "category": "ai_action", "text": "camera -> pastor",
                 "similarity": 0.9, "metadata": {"service_state": "pre_service"}}]

    monkeypatch.setattr("app.memory.production_memory.memory_manager.retrieve_similar_moments", _retrieve)

    await AIServiceDirector().decide(ServiceContext())

    prompt = _user_prompt(fake)
    assert "2026-08-10" in prompt and "camera -> pastor" in prompt
    assert calls["state"] == "pre_service" and calls["limit"] == settings.ai_director_memory_results
    assert "pre_service" in calls["query"]


async def test_decide_filters_out_low_similarity_history(monkeypatch):
    fake = _FakeLLM(CONTINUE)
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: fake)
    monkeypatch.setattr(
        "app.memory.production_memory.memory_manager.retrieve_similar_moments",
        lambda *a, **k: [{"service_date": "2026-08-10", "category": "ai_action", "text": "irrelevant match", "similarity": 0.01}],
    )
    await AIServiceDirector().decide(ServiceContext())
    prompt = _user_prompt(fake)
    assert "irrelevant match" not in prompt
    assert "None found" in prompt


async def test_decide_appends_house_style_rules(monkeypatch):
    fake = _FakeLLM(CONTINUE)
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: fake)
    monkeypatch.setattr(
        "app.memory.production_memory.memory_manager.summarize_house_style",
        lambda state, item=None, **k: [f"During {state} the operator advances slides after ~18s."],
    )
    await AIServiceDirector().decide(ServiceContext())
    prompt = _user_prompt(fake)
    assert "Learned from this operator" in prompt
    assert "advances slides after ~18s" in prompt


async def test_decide_survives_memory_retrieval_failure(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("database unreachable")

    fake = _FakeLLM(CONTINUE)
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: fake)
    monkeypatch.setattr("app.memory.production_memory.memory_manager.retrieve_similar_moments", _raise)
    monkeypatch.setattr("app.memory.production_memory.memory_manager.summarize_house_style", _raise)

    decision = await AIServiceDirector().decide(ServiceContext())
    assert decision.decision == "continue" and decision.confidence == 0.5
    assert "retrieval unavailable" in _user_prompt(fake)


async def test_decide_skips_retrieval_when_disabled(monkeypatch):
    def _raise(*a, **k):
        raise AssertionError("memory should not be queried when RAG is disabled")

    fake = _FakeLLM(CONTINUE)
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: fake)
    monkeypatch.setattr("app.memory.production_memory.memory_manager.retrieve_similar_moments", _raise)
    monkeypatch.setattr("app.memory.production_memory.memory_manager.summarize_house_style", _raise)
    monkeypatch.setattr(settings, "ai_director_use_memory_rag", False)

    decision = await AIServiceDirector().decide(ServiceContext())
    assert decision.decision == "continue"
    assert "memory retrieval disabled" in _user_prompt(fake)


async def test_status_exposes_cycle_error_and_backoff(monkeypatch):
    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: _FakeLLM("garbage"))
    director = AIServiceDirector()
    await director.decide(ServiceContext())
    status = director.status()
    assert status["last_cycle_id"] == director.last_cycle_id
    assert status["last_error"].startswith("invalid decision")
    assert status["backoff"]["degraded"] is False
