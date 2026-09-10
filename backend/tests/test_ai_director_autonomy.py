"""Integration tests: autonomy components wired through AIDirectorRuntime."""

from datetime import datetime, timezone

import pytest

from app.ai.decision import DirectorActionSpec, DirectorDecision
from app.config import settings
from app.director.action_engine import ActionEngine
from app.director.action_models import DirectorActionType
from app.director.ai_director_runtime import AIDirectorRuntime
from app.director.service_end import ServiceEndRecognizer
from app.director.text_follower import SongFollowerService
from app.domain.observations import VisionObservation
from app.domain.service_context import ServiceContext, TranscriptLine
from app.policy.engine import PolicyEngine

SLIDES = [
    "Amazing grace how sweet the sound",
    "That saved a wretch like me",
    "I once was lost but now am found",
]


class _FakeAI:
    def __init__(self, decision):
        self.decision = decision

    async def decide(self, context):
        return self.decision


def _continue():
    return DirectorDecision(decision="continue", confidence=0.0)


def _ctx_with(lines):
    ctx = ServiceContext()
    for role, text in lines:
        ctx.transcript.append(
            TranscriptLine(speaker_role=role, text=text, timestamp=datetime.now(timezone.utc))
        )
    return ctx


def _wire(monkeypatch, *, ai_decision, ctx, song_follower=None, service_end=None):
    monkeypatch.setattr("app.director.ai_director_runtime.ai_service_director", _FakeAI(ai_decision))
    monkeypatch.setattr("app.director.ai_director_runtime.service_context", ctx)
    if song_follower is not None:
        monkeypatch.setattr("app.director.ai_director_runtime.song_follower_service", song_follower)
    if service_end is not None:
        monkeypatch.setattr("app.director.ai_director_runtime.service_end_recognizer", service_end)


async def test_follower_proposal_queued_in_assisted_mode(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", "vocalist")
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 0.0)
    ctx = _ctx_with([("vocalist", "that saved a wretch like me")])
    svc = SongFollowerService(text_source=lambda: ("song1", SLIDES))
    _wire(monkeypatch, ai_decision=_continue(), ctx=ctx, song_follower=svc,
          service_end=ServiceEndRecognizer())

    runtime = AIDirectorRuntime(action_engine=ActionEngine(PolicyEngine()))
    runtime.set_mode("assisted")
    await runtime.tick()

    assert len(runtime.pending_actions) == 1
    action = runtime.pending_actions[0]
    assert action.type == DirectorActionType.EASYWORSHIP_NEXT
    assert action.parameters["source"] == "song_follower"


async def test_follower_executes_and_advances_in_ai_directed(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", "vocalist")
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 0.0)

    ew_calls = []

    class _EW:
        async def next_item(self):
            ew_calls.append("next_item")
            return True

    import app.easyworship.service as ew_mod
    monkeypatch.setattr(ew_mod, "easyworship_service", _EW())

    ctx = _ctx_with([("vocalist", "that saved a wretch like me")])
    svc = SongFollowerService(text_source=lambda: ("song1", SLIDES))
    _wire(monkeypatch, ai_decision=_continue(), ctx=ctx, song_follower=svc,
          service_end=ServiceEndRecognizer())

    policy = PolicyEngine(action_confidence_thresholds={"slide_change": 0.85})
    runtime = AIDirectorRuntime(action_engine=ActionEngine(policy))
    runtime.set_mode("ai_directed")
    await runtime.tick()

    assert ew_calls == ["next_item"]
    assert svc.follower.current_index == 1  # follower advanced after execution


async def test_evidence_fusion_blocks_camera_cut(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_evidence_fusion", True)
    monkeypatch.setattr(settings, "song_follower_enabled", False)

    move_calls = []

    async def fake_move_to_role(role):
        move_calls.append(role)
        return True

    from app.cameras.service import camera_service
    monkeypatch.setattr(camera_service, "move_to_role", fake_move_to_role)

    ctx = ServiceContext()
    ctx.vision = {"pastor": VisionObservation(role="pastor", person_present=False)}
    decision = DirectorDecision(
        decision="transition",
        confidence=0.95,
        actions=[DirectorActionSpec(type="PTZ_SELECT_ROLE", camera_role="pastor")],
    )
    _wire(monkeypatch, ai_decision=decision, ctx=ctx, service_end=ServiceEndRecognizer())

    policy = PolicyEngine(action_confidence_thresholds={"camera_change": 0.85})
    runtime = AIDirectorRuntime(action_engine=ActionEngine(policy))
    runtime.set_mode("ai_directed")
    await runtime.tick()

    assert move_calls == []  # vision veto prevented the cut


async def test_service_end_transition_proposed(monkeypatch):
    monkeypatch.setattr(settings, "service_end_enabled", True)
    monkeypatch.setattr(settings, "service_end_keywords", "benediction")
    monkeypatch.setattr(settings, "service_end_silence_seconds", 10.0)
    monkeypatch.setattr(settings, "song_follower_enabled", False)

    clock = {"t": 500.0}
    recognizer = ServiceEndRecognizer(now=lambda: clock["t"])
    ctx = _ctx_with([("pastor", "and now receive the benediction")])
    _wire(monkeypatch, ai_decision=_continue(), ctx=ctx, service_end=recognizer)

    runtime = AIDirectorRuntime(action_engine=ActionEngine(PolicyEngine()))
    runtime.set_mode("assisted")

    await runtime.tick()          # arms on the keyword
    assert recognizer.armed
    clock["t"] += 11.0            # sustained quiet
    await runtime.tick()          # fires the transition proposal

    assert any(
        a.type == DirectorActionType.SERVICE_STATE_CHANGE
        and a.parameters.get("source") == "service_end"
        for a in runtime.pending_actions
    )


async def test_nudge_sets_wake_event(monkeypatch):
    runtime = AIDirectorRuntime(action_engine=ActionEngine(PolicyEngine()))
    assert not runtime._wake.is_set()
    runtime.nudge()
    assert runtime._wake.is_set()
