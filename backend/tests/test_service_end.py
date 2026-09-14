"""Tests for Phase 3 service-end recognition + shutdown bundle."""

from datetime import datetime, timezone

import pytest

from app.config import settings
from app.director.action_models import DirectorActionType
from app.director.service_end import ServiceEndRecognizer, run_shutdown_bundle
from app.domain.service_context import TranscriptLine
from app.domain.service_state import ServiceState


def _line(text, role="pastor"):
    return TranscriptLine(speaker_role=role, text=text, timestamp=datetime.now(timezone.utc))


@pytest.fixture(autouse=True)
def enable(monkeypatch):
    monkeypatch.setattr(settings, "service_end_enabled", True)
    monkeypatch.setattr(settings, "service_end_keywords", "benediction,go in peace")
    monkeypatch.setattr(settings, "service_end_silence_seconds", 30.0)


def _clocked():
    clock = {"t": 1000.0}
    rec = ServiceEndRecognizer(now=lambda: clock["t"])
    return rec, clock


def test_disabled_is_inert(monkeypatch):
    monkeypatch.setattr(settings, "service_end_enabled", False)
    rec, _ = _clocked()
    rec.observe_transcript(_line("the benediction now"))
    assert not rec.armed
    assert rec.evaluate() is None


def test_arms_on_keyword():
    rec, _ = _clocked()
    rec.observe_transcript(_line("let us receive the benediction"))
    assert rec.armed


def test_arms_on_benediction_state():
    rec, _ = _clocked()
    rec.observe_state(ServiceState.BENEDICTION)
    assert rec.armed


def test_does_not_fire_before_silence_threshold():
    rec, clock = _clocked()
    rec.observe_transcript(_line("the benediction"))
    clock["t"] += 10.0  # less than 30s
    assert rec.evaluate() is None


def test_fires_after_sustained_silence():
    rec, clock = _clocked()
    rec.observe_transcript(_line("go in peace"))
    clock["t"] += 31.0
    proposal = rec.evaluate()
    assert proposal is not None
    assert len(proposal.actions) == 1
    action = proposal.actions[0]
    assert action.type == DirectorActionType.SERVICE_STATE_CHANGE
    assert action.target == ServiceState.POST_SERVICE.value
    assert action.parameters["source"] == "service_end"


def test_fires_only_once():
    rec, clock = _clocked()
    rec.observe_transcript(_line("benediction"))
    clock["t"] += 31.0
    assert rec.evaluate() is not None
    assert rec.evaluate() is None


def test_note_voice_resets_silence_timer():
    rec, clock = _clocked()
    rec.observe_transcript(_line("benediction"))
    clock["t"] += 20.0
    rec.note_voice()
    clock["t"] += 20.0  # 20s since last voice, below 30s
    assert rec.evaluate() is None


# -- shutdown bundle -----------------------------------------------------------
class _FakePolicy:
    def __init__(self, allowed):
        self._allowed = allowed

    def check_permission(self, permission, actor="ai"):
        return permission in self._allowed


@pytest.fixture
def shutdown_wiring(monkeypatch):
    calls = []

    class _Atem:
        async def stop_stream(self):
            calls.append("stop_stream")
            return True

        async def stop_recording(self):
            calls.append("stop_recording")
            return True

    class _EW:
        async def action(self, name):
            calls.append(f"ew:{name}")
            return True

    class _Cam:
        async def move_to_role(self, role):
            calls.append(f"cam:{role}")
            return True

    import app.dependencies as deps
    import app.easyworship.service as ew_mod
    import app.cameras.service as cam_mod
    from app.agents import assistant_tools

    monkeypatch.setattr(deps, "get_atem_service_instance", lambda: _Atem())
    monkeypatch.setattr(assistant_tools, "get_atem_service_instance", lambda: _Atem())
    monkeypatch.setattr(assistant_tools, "pending_actions", {})
    monkeypatch.setattr(ew_mod, "easyworship_service", _EW())
    monkeypatch.setattr(cam_mod, "camera_service", _Cam())
    return calls


async def test_shutdown_bundle_respects_permissions(shutdown_wiring):
    from app.policy.permissions import Permission

    calls = shutdown_wiring
    policy = _FakePolicy({Permission.STOP_STREAM})  # recording NOT allowed
    done = await run_shutdown_bundle(policy_engine=policy)

    from app.agents import assistant_tools

    token = done["stop_stream"]["pending_confirmation"]
    assert assistant_tools.pending_actions[token]["action"] == "atem_stop_stream"
    assert "stop_recording" not in done
    assert done["easyworship_black"] is True
    assert done["cameras_home"] is True
    assert "stop_stream" not in calls
    assert "stop_recording" not in calls
    assert "ew:black" in calls
    assert "cam:wide" in calls


@pytest.mark.parametrize("voice_enabled", [False, True])
async def test_shutdown_never_auto_stops_even_when_policy_allows(monkeypatch, shutdown_wiring, voice_enabled):
    from app.agents import assistant_tools
    from app.events.bus import EventBus
    from app.policy.engine import PolicyEngine

    monkeypatch.setenv("VOICE_ENABLED", str(voice_enabled).lower())
    bus = EventBus()
    monkeypatch.setattr(assistant_tools, "event_bus", bus)
    queue = await bus.subscribe()
    policy = PolicyEngine(autonomous_stream_stop=True, autonomous_recording=True)
    monkeypatch.setattr(assistant_tools, "get_policy_engine_instance", lambda: policy)
    done = await run_shutdown_bundle(policy_engine=policy)
    assert shutdown_wiring == ["ew:black", "cam:wide"]
    stream_token = done["stop_stream"]["pending_confirmation"]
    record_token = done["stop_recording"]["pending_confirmation"]
    assert stream_token != record_token
    for token, action in [(stream_token, "atem_stop_stream"), (record_token, "atem_stop_recording")]:
        assert queue.get_nowait() == {
            "event": "ASSISTANT_CONFIRMATION_REQUIRED", "payload": {"token": token, "action": action},
        }
    assert await assistant_tools.execute_pending(stream_token) == {"ok": True}
    assert assistant_tools.discard_pending(record_token)
    assert shutdown_wiring == ["ew:black", "cam:wide", "stop_stream"]
    assert not assistant_tools.pending_actions
