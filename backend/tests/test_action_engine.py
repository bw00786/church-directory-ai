"""Tests for ActionEngine: policy-gated dispatch to (mocked) hardware services."""

import pytest

from app.director.action_engine import ActionEngine
from app.director.action_models import DirectorAction, DirectorActionType
from app.policy.engine import PolicyEngine


@pytest.fixture
def policy():
    return PolicyEngine(
        action_confidence_thresholds={
            "camera_change": 0.85,
            "slide_change": 0.85,
            "atem_transition": 0.90,
        },
    )


@pytest.fixture
def engine(policy):
    return ActionEngine(policy)


async def test_camera_action_rejected_below_confidence(engine):
    action = DirectorAction(
        type=DirectorActionType.PTZ_SELECT_ROLE,
        target="pastor",
        confidence=0.5,
        reason="low confidence guess",
    )
    result = await engine.execute(action)
    assert result.approved is False
    assert result.executed is False


async def test_camera_action_executed_above_confidence(engine, monkeypatch):
    from app.cameras.service import camera_service

    calls = []

    async def fake_move_to_role(role):
        calls.append(role)
        return True

    monkeypatch.setattr(camera_service, "move_to_role", fake_move_to_role)

    action = DirectorAction(
        type=DirectorActionType.PTZ_SELECT_ROLE,
        target="pastor",
        confidence=0.95,
        reason="pastor is speaking",
    )
    result = await engine.execute(action)
    assert result.approved is True
    assert result.executed is True
    assert calls == ["pastor"]


async def test_atem_action_uses_higher_threshold(engine, monkeypatch):
    from app import dependencies

    class FakeAtem:
        def __init__(self):
            self.cut_called = False

        async def cut(self):
            self.cut_called = True

    fake_atem = FakeAtem()
    monkeypatch.setattr(dependencies, "get_atem_service_instance", lambda: fake_atem)
    monkeypatch.setattr(
        "app.director.action_engine.get_atem_service_instance", lambda: fake_atem
    )

    low_confidence_action = DirectorAction(
        type=DirectorActionType.ATEM_CUT, confidence=0.85, reason="maybe"
    )
    result = await engine.execute(low_confidence_action)
    assert result.approved is False
    assert fake_atem.cut_called is False

    high_confidence_action = DirectorAction(
        type=DirectorActionType.ATEM_CUT, confidence=0.95, reason="scripture ended"
    )
    result = await engine.execute(high_confidence_action)
    assert result.approved is True
    assert fake_atem.cut_called is True


async def test_easyworship_action_dispatch(engine, monkeypatch):
    from app.easyworship.service import easyworship_service

    calls = []

    async def fake_next_item():
        calls.append("next_item")
        return True

    monkeypatch.setattr(easyworship_service, "next_item", fake_next_item)

    action = DirectorAction(
        type=DirectorActionType.EASYWORSHIP_NEXT, confidence=0.9, reason="segment complete"
    )
    result = await engine.execute(action)
    assert result.approved is True
    assert calls == ["next_item"]


async def test_service_state_change_not_confidence_gated(engine):
    action = DirectorAction(
        type=DirectorActionType.SERVICE_STATE_CHANGE,
        target="sermon",
        confidence=0.0,
        reason="pastor began preaching",
    )
    result = await engine.execute(action)
    assert result.approved is True
    assert result.executed is True
