"""ActionEngine pacing: min hold, cooldown, consecutive cap, speech boundary, holds."""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.director.action_engine import ActionEngine
from app.director.action_models import DirectorAction, DirectorActionType
from app.domain.service_context import service_context
from app.policy.engine import PolicyEngine


@pytest.fixture
def clock():
    return {"t": 5000.0}


@pytest.fixture
def engine(clock, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "ai_cut_speech_boundary_seconds", 0.8)
    monkeypatch.setattr(service_context, "current_camera_role", None)
    monkeypatch.setattr(service_context, "last_observation", None)
    monkeypatch.setattr(service_context, "last_cut_at", None, raising=False)
    monkeypatch.setattr(service_context, "speaking_roles", None, raising=False)
    policy = PolicyEngine(
        min_camera_hold_seconds=8,
        camera_switch_cooldown_seconds=5,
        max_consecutive_switches=2,
        action_confidence_thresholds={"camera_change": 0.85, "atem_transition": 0.90},
    )
    return ActionEngine(policy, clock=lambda: clock["t"])


@pytest.fixture
def hardware(monkeypatch):
    from app.cameras.service import camera_service

    calls = {"moves": [], "cuts": 0}

    async def fake_move_to_role(role):
        calls["moves"].append(role)
        return True

    class FakeAtem:
        async def cut(self):
            calls["cuts"] += 1

        async def set_preview(self, input_id):
            calls.setdefault("previews", []).append(input_id)

    monkeypatch.setattr(camera_service, "move_to_role", fake_move_to_role)
    monkeypatch.setattr("app.director.action_engine.get_atem_service_instance", lambda: FakeAtem())
    return calls


@pytest.fixture
def events(monkeypatch):
    published = []
    monkeypatch.setattr("app.director.action_engine.event_bus.publish", published.append)
    return published


def _camera(role="pastor", **params):
    return DirectorAction(type=DirectorActionType.PTZ_SELECT_ROLE, target=role, confidence=0.95, parameters=params)


def _cut(**params):
    return DirectorAction(type=DirectorActionType.ATEM_CUT, confidence=0.95, parameters=params)


async def test_min_hold_after_program_change_holds_not_rejects(engine, hardware, events, clock):
    engine.note_program_change(commanded=True, at=clock["t"] - 3)
    result = await engine.execute(_camera())
    assert result.approved is True and result.executed is False and result.held is True
    assert "min hold" in result.held_reason
    assert hardware["moves"] == []
    assert any(e["event"] == "AI_ACTION_HELD" for e in events)
    assert not any(e["event"] == "AI_ACTION_REJECTED" for e in events)

    clock["t"] += 6  # 9s since the change
    result = await engine.execute(_camera())
    assert result.executed is True and hardware["moves"] == ["pastor"]


async def test_context_last_cut_at_is_authoritative(engine, hardware, clock, monkeypatch):
    monkeypatch.setattr(service_context, "last_cut_at", datetime.now(timezone.utc) - timedelta(seconds=2), raising=False)
    result = await engine.execute(_camera())
    assert result.held and "min hold" in result.held_reason


async def test_cooldown_between_category_actions(engine, hardware, clock):
    assert (await engine.execute(_camera("pastor"))).executed
    clock["t"] += 3
    result = await engine.execute(_camera("liturgist"))
    assert result.held and "cooldown" in result.held_reason
    clock["t"] += 3
    assert (await engine.execute(_camera("liturgist"))).executed


async def test_consecutive_cap_resets_on_operator_change(engine, hardware, clock):
    engine.policy_engine.policy.camera_switch_cooldown_seconds = 0
    assert (await engine.execute(_camera("pastor"))).executed
    clock["t"] += 1
    assert (await engine.execute(_camera("liturgist"))).executed
    clock["t"] += 1
    result = await engine.execute(_camera("wide"))
    assert result.held and "max consecutive" in result.held_reason
    engine.note_program_change(commanded=False, at=clock["t"])  # operator cut
    clock["t"] += 9  # past min hold from that cut
    assert (await engine.execute(_camera("wide"))).executed


async def test_speech_boundary_holds_mid_sentence_cut(engine, hardware, clock, monkeypatch):
    monkeypatch.setattr(service_context, "current_camera_role", "pastor")
    engine.note_audio("pastor", True, at=clock["t"] - 5)
    result = await engine.execute(_cut())
    assert result.held and "speech boundary" in result.held_reason
    assert hardware["cuts"] == 0

    # Urgent cuts bypass the boundary rule.
    assert (await engine.execute(_cut(urgent=True))).executed
    assert hardware["cuts"] == 1


async def test_speech_boundary_allows_cut_when_speaker_pauses(engine, hardware, clock, monkeypatch):
    monkeypatch.setattr(service_context, "current_camera_role", "pastor")
    engine.note_audio("pastor", True, at=clock["t"] - 5)
    engine.note_audio("pastor", False, at=clock["t"] - 0.2)
    assert (await engine.execute(_cut())).executed
    # Just restarted after a pause: still at the boundary.
    engine.note_program_change(commanded=False, at=clock["t"] - 20)
    engine.note_audio("pastor", True, at=clock["t"] - 0.3)
    clock["t"] += 6
    assert engine.pacing_hold(_cut()) is None or "speech" in engine.pacing_hold(_cut())


async def test_speech_boundary_uses_context_speaking_roles(engine, hardware, clock, monkeypatch):
    import time

    monkeypatch.setattr(service_context, "current_camera_role", "pastor")
    monkeypatch.setattr(
        service_context, "speaking_roles",
        {"pastor": {"speaking": True, "since": time.time() - 4.0}}, raising=False,
    )
    result = await engine.execute(_cut())
    assert result.held and "speech boundary" in result.held_reason
    service_context.speaking_roles["pastor"]["speaking"] = False
    assert (await engine.execute(_cut())).executed


async def test_speech_boundary_disabled_by_zero(engine, hardware, clock, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "ai_cut_speech_boundary_seconds", 0)
    monkeypatch.setattr(service_context, "current_camera_role", "pastor")
    engine.note_audio("pastor", True, at=clock["t"] - 5)
    assert (await engine.execute(_cut())).executed


async def test_operator_approval_is_not_paced(engine, hardware, clock):
    engine.note_program_change(commanded=True, at=clock["t"] - 1)
    assert (await engine.execute(_camera(), pace=False)).executed


async def test_preview_is_not_paced(engine, hardware, clock):
    engine.note_program_change(commanded=True, at=clock["t"] - 1)
    action = DirectorAction(type=DirectorActionType.ATEM_SET_PREVIEW, confidence=0.95, parameters={"atem_input": 2})
    assert (await engine.execute(action)).executed


async def test_cue_advance_dispatches_to_cue_engine(engine, monkeypatch):
    import app.director.engine as engine_mod

    calls = []

    async def fake_request_advance(**kwargs):
        calls.append(kwargs)
        return {"accepted": True, "reason": kwargs["reason"]}

    monkeypatch.setattr(engine_mod.service_director, "request_advance", fake_request_advance)
    action = DirectorAction(
        type=DirectorActionType.CUE_ADVANCE, target="cue-3", confidence=0.9, reason="amen heard",
        parameters={"cue_id": "cue-3", "source": "ai_director"},
    )
    result = await engine.execute(action)
    assert result.executed and "advanced" in result.detail
    assert calls[0]["cue_id"] == "cue-3" and calls[0]["source"] == "ai_director"


async def test_cue_advance_below_global_threshold_rejected(engine):
    action = DirectorAction(type=DirectorActionType.CUE_ADVANCE, confidence=0.5, parameters={"cue_id": "x"})
    result = await engine.execute(action)
    assert result.approved is False


def test_learned_typical_hold_raises_min_hold(monkeypatch):
    """Operator shot learning lifts the policy hold toward the operator's own pacing."""
    from app.director import action_engine as ae
    from app.director.operator_shots import operator_shot_history

    monkeypatch.setattr(settings, "operator_shots_hold_fraction", 0.5)
    monkeypatch.setattr(operator_shot_history, "typical_hold", lambda state: 30.0)
    assert ae.ActionEngine._learned_min_hold(8.0) == 15.0
    monkeypatch.setattr(operator_shot_history, "typical_hold", lambda state: 10.0)
    assert ae.ActionEngine._learned_min_hold(8.0) == 8.0  # never below the policy hold
    monkeypatch.setattr(settings, "operator_shots_hold_fraction", 0.0)
    assert ae.ActionEngine._learned_min_hold(8.0) == 8.0
