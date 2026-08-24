"""Tests for AIDirectorRuntime mode gating (manual / assisted / ai_directed)."""

import pytest

from app.ai.decision import DirectorActionSpec, DirectorDecision
from app.director.action_engine import ActionEngine
from app.director.ai_director_runtime import AIDirectorRuntime
from app.domain.service_context import ServiceContext
from app.policy.engine import PolicyEngine


class _FakeAIServiceDirector:
    def __init__(self, decision: DirectorDecision):
        self.decision = decision

    async def decide(self, context):
        return self.decision


@pytest.fixture
def camera_decision():
    return DirectorDecision(
        decision="transition",
        confidence=0.95,
        reason="pastor is speaking",
        actions=[DirectorActionSpec(type="PTZ_SELECT_ROLE", camera_role="pastor")],
    )


@pytest.fixture
def policy():
    return PolicyEngine(action_confidence_thresholds={"camera_change": 0.85})


async def _run_tick(monkeypatch, mode, decision, policy):
    monkeypatch.setattr(
        "app.director.ai_director_runtime.ai_service_director", _FakeAIServiceDirector(decision)
    )
    monkeypatch.setattr("app.director.ai_director_runtime.service_context", ServiceContext())

    calls = []

    async def fake_move_to_role(role):
        calls.append(role)
        return True

    from app.cameras.service import camera_service

    monkeypatch.setattr(camera_service, "move_to_role", fake_move_to_role)

    runtime = AIDirectorRuntime(action_engine=ActionEngine(policy))
    runtime.set_mode(mode)
    await runtime.tick()
    return runtime, calls


async def test_manual_mode_takes_no_action(monkeypatch, camera_decision, policy):
    runtime, calls = await _run_tick(monkeypatch, "manual", camera_decision, policy)
    assert calls == []
    assert runtime.pending_actions == []


async def test_assisted_mode_queues_pending_action(monkeypatch, camera_decision, policy):
    runtime, calls = await _run_tick(monkeypatch, "assisted", camera_decision, policy)
    assert calls == []
    assert len(runtime.pending_actions) == 1


async def test_ai_directed_mode_executes_immediately(monkeypatch, camera_decision, policy):
    runtime, calls = await _run_tick(monkeypatch, "ai_directed", camera_decision, policy)
    assert calls == ["pastor"]
    assert runtime.pending_actions == []


async def test_invalid_mode_rejected(policy):
    runtime = AIDirectorRuntime(action_engine=ActionEngine(policy))
    with pytest.raises(ValueError):
        runtime.set_mode("bogus")
