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


# -- Phase gating, staleness, latency, cycle ids ------------------------------------

from app.config import settings  # noqa: E402
from app.director.action_models import DirectorActionType  # noqa: E402
from app.director.phase import PhaseDecision  # noqa: E402
from app.domain.service_state import ServiceState  # noqa: E402


class _FakeReconciler:
    def __init__(self, accepted):
        self.accepted = accepted
        self.calls = []

    def propose(self, llm_state, confidence, mode):
        self.calls.append((llm_state, confidence, mode))
        state = ServiceState(llm_state) if llm_state else None
        return PhaseDecision(self.accepted, state, "accepted" if self.accepted else "needs a hard signal", "llm")


class _RaisingAI:
    last_cycle_id = None

    async def decide(self, context):
        raise RuntimeError("boom")


@pytest.fixture
def events(monkeypatch):
    published = []
    monkeypatch.setattr("app.director.ai_director_runtime.event_bus.publish", published.append)
    return published


def _names(events):
    return [e["event"] for e in events]


async def test_phase_proposal_is_reconciled_not_applied(monkeypatch, policy, events):
    decision = DirectorDecision(decision="transition", confidence=0.95, service_state="sermon")
    ctx = ServiceContext()
    monkeypatch.setattr("app.director.ai_director_runtime.ai_service_director", _FakeAIServiceDirector(decision))
    monkeypatch.setattr("app.director.ai_director_runtime.service_context", ctx)
    rejecting = _FakeReconciler(accepted=False)
    monkeypatch.setattr("app.director.ai_director_runtime.phase_reconciler", rejecting)
    runtime = AIDirectorRuntime(action_engine=ActionEngine(policy))
    runtime.set_mode("assisted")
    await runtime.tick()
    assert ctx.state == ServiceState.PRE_SERVICE
    assert rejecting.calls == [("sermon", 0.95, "assisted")]
    proposal = next(e for e in events if e["event"] == "AI_PHASE_PROPOSAL")
    assert proposal["payload"]["accepted"] is False and proposal["payload"]["proposed"] == "sermon"

    monkeypatch.setattr("app.director.ai_director_runtime.phase_reconciler", _FakeReconciler(accepted=True))
    await runtime.tick()
    assert ctx.state == ServiceState.SERMON


async def test_stale_decision_is_discarded(monkeypatch, policy, events, camera_decision):
    monkeypatch.setitem(settings.__dict__, "ai_director_max_context_drift", 3)
    camera_decision.context_version = 5
    ctx = ServiceContext()
    ctx.version = 9  # four mutations after the snapshot the LLM saw
    monkeypatch.setattr("app.director.ai_director_runtime.ai_service_director", _FakeAIServiceDirector(camera_decision))
    monkeypatch.setattr("app.director.ai_director_runtime.service_context", ctx)
    runtime = AIDirectorRuntime(action_engine=ActionEngine(policy))
    runtime.set_mode("assisted")
    await runtime.tick()
    assert runtime.pending_actions == []
    assert "AI_DECISION_STALE" in _names(events)
    stale = next(e for e in events if e["event"] == "AI_DECISION_STALE")["payload"]
    assert stale["drift"] == 4 and stale["max_drift"] == 3
    assert runtime.status()["last_cycle"]["stale"] is True

    ctx.version = 7  # within tolerance
    await runtime.tick()
    assert len(runtime.pending_actions) == 1


async def test_cycle_id_and_latency_set_even_when_decide_fails(monkeypatch, policy, events):
    monkeypatch.setattr("app.director.ai_director_runtime.ai_service_director", _RaisingAI())
    monkeypatch.setattr("app.director.ai_director_runtime.service_context", ServiceContext())
    runtime = AIDirectorRuntime(action_engine=ActionEngine(policy))
    decision = await runtime.tick()
    assert decision.decision == "continue"
    assert runtime.last_cycle_id
    last = runtime.status()["last_cycle"]
    assert last["cycle_id"] == runtime.last_cycle_id and isinstance(last["latency_ms"], int)
    ai_decision = next(e for e in events if e["event"] == "AI_DECISION")["payload"]
    assert ai_decision["cycle_id"] == runtime.last_cycle_id and "latency_ms" in ai_decision


async def test_per_action_confidence_and_reason(monkeypatch, policy):
    decision = DirectorDecision(
        decision="transition", confidence=0.95, reason="overall",
        actions=[DirectorActionSpec(type="PTZ_SELECT_ROLE", camera_role="pastor", confidence=0.5, reason="specific")],
    )
    monkeypatch.setattr("app.director.ai_director_runtime.ai_service_director", _FakeAIServiceDirector(decision))
    monkeypatch.setattr("app.director.ai_director_runtime.service_context", ServiceContext())
    runtime = AIDirectorRuntime(action_engine=ActionEngine(policy))
    runtime.set_mode("assisted")
    await runtime.tick()
    action = runtime.pending_actions[0]
    assert action.confidence == 0.5 and action.reason == "specific"


async def test_start_is_noop_when_disabled(monkeypatch, policy):
    monkeypatch.setattr(settings, "enable_ai_director", False)
    runtime = AIDirectorRuntime(action_engine=ActionEngine(policy))
    await runtime.start()
    assert runtime._running is False and runtime._task is None
    await runtime.stop()


# -- Proposal queue integration --------------------------------------------------------


async def _assisted_runtime(monkeypatch, decision, policy):
    runtime, _ = await _run_tick(monkeypatch, "assisted", decision, policy)
    return runtime


async def test_approve_and_reject_by_id(monkeypatch, camera_decision, policy):
    runtime = await _assisted_runtime(monkeypatch, camera_decision, policy)
    pending = runtime.pending_proposals()
    assert len(pending) == 1 and pending[0]["id"] and pending[0]["expires_in_seconds"] > 0
    with pytest.raises(KeyError):
        await runtime.approve_pending("nope")
    with pytest.raises(IndexError):
        await runtime.approve_pending(5)
    result = await runtime.approve_pending(pending[0]["id"])
    assert result["executed"] is True and result["id"] == pending[0]["id"]
    assert runtime.pending_actions == []


async def test_legacy_index_resolves_to_id(monkeypatch, camera_decision, policy):
    runtime = await _assisted_runtime(monkeypatch, camera_decision, policy)
    target = runtime.pending_proposals()[0]["id"]
    assert runtime.reject_pending("0") == {"ok": True, "id": target}
    assert runtime.pending_actions == []


async def test_operator_feedback_drives_adaptive_not_auto_execution(monkeypatch, camera_decision, policy, tmp_path):
    from app.director.autonomy import AdaptiveConfidence

    monkeypatch.setattr(settings, "ai_director_adaptive_confidence", True)
    monkeypatch.setitem(settings.__dict__, "ai_director_adaptive_file", str(tmp_path / "a.json"))
    adaptive = AdaptiveConfidence()
    monkeypatch.setattr("app.director.ai_director_runtime.adaptive_confidence", adaptive)

    runtime, calls = await _run_tick(monkeypatch, "ai_directed", camera_decision, policy)
    assert calls == ["pastor"]
    assert adaptive.snapshot() == {}  # auto-execution is not feedback

    runtime = await _assisted_runtime(monkeypatch, camera_decision, policy)
    runtime.reject_pending(runtime.pending_proposals()[0]["id"])
    assert adaptive.snapshot() == {"camera_change|pre_service": pytest.approx(settings.ai_director_adaptive_step)}
    assert runtime.status()["adaptive_confidence"]["deltas"] == adaptive.snapshot()


async def test_duplicate_cut_after_cue_engine_cut_is_dropped(monkeypatch, policy, events):
    decision = DirectorDecision(decision="transition", confidence=0.95, actions=[DirectorActionSpec(type="ATEM_CUT")])
    cuts = []

    class FakeAtem:
        async def cut(self):
            cuts.append("cut")

    monkeypatch.setattr("app.director.action_engine.get_atem_service_instance", lambda: FakeAtem())
    monkeypatch.setattr("app.director.ai_director_runtime.ai_service_director", _FakeAIServiceDirector(decision))
    monkeypatch.setattr("app.director.ai_director_runtime.service_context", ServiceContext())
    policy = PolicyEngine(min_camera_hold_seconds=0, action_confidence_thresholds={"atem_transition": 0.9})
    runtime = AIDirectorRuntime(action_engine=ActionEngine(policy))
    runtime.set_mode("ai_directed")
    runtime.observe_event("PROGRAM_CHANGED", {"program_input": 2, "commanded": True})  # cue engine just cut
    await runtime.tick()
    assert cuts == []
    dropped = next(e for e in events if e["event"] == "AI_PROPOSAL_DROPPED")["payload"]
    assert "ATEM program changed" in dropped["reason"]


async def test_held_action_is_retried_next_tick(monkeypatch, camera_decision, events):
    clock = {"t": 100.0}
    policy = PolicyEngine(min_camera_hold_seconds=8, action_confidence_thresholds={"camera_change": 0.85})
    engine = ActionEngine(policy, clock=lambda: clock["t"])
    monkeypatch.setattr("app.director.ai_director_runtime.ai_service_director", _FakeAIServiceDirector(camera_decision))
    monkeypatch.setattr("app.director.ai_director_runtime.service_context", ServiceContext())
    calls = []

    async def fake_move_to_role(role):
        calls.append(role)
        return True

    from app.cameras.service import camera_service

    monkeypatch.setattr(camera_service, "move_to_role", fake_move_to_role)
    runtime = AIDirectorRuntime(action_engine=engine)
    runtime.set_mode("ai_directed")
    engine.note_program_change(commanded=False, at=clock["t"] - 2)  # operator cut 2s ago
    await runtime.tick()
    assert calls == []
    pending = runtime.pending_proposals()
    assert len(pending) == 1 and "min hold" in pending[0]["held_reason"]
    assert "AI_ACTION_REJECTED" not in _names(events)

    clock["t"] += 10
    await runtime.tick()
    assert calls == ["pastor"]
    assert runtime.pending_actions == []


async def test_prestage_skips_on_program_camera_and_manual_mode(monkeypatch, camera_decision, policy):
    monkeypatch.setattr(settings, "ptz_predictive_preview", True)
    staged = []

    async def fake_stage_role(role):
        staged.append(role)
        return True

    from app.director.predictive_ptz import predictive_preview

    monkeypatch.setattr(predictive_preview, "stage_role", fake_stage_role)
    monkeypatch.setattr(predictive_preview, "is_on_program", lambda role: True)
    await _run_tick(monkeypatch, "assisted", camera_decision, policy)
    assert staged == []

    monkeypatch.setattr(predictive_preview, "is_on_program", lambda role: False)
    await _run_tick(monkeypatch, "manual", camera_decision, policy)
    assert staged == []
    await _run_tick(monkeypatch, "assisted", camera_decision, policy)
    assert staged == ["pastor"]


async def test_observe_path_honours_mode_via_queue(monkeypatch, policy):
    import app.agents.director_ai as mod
    from app.director.ai_director_runtime import ai_director_runtime as singleton

    class Cue:
        id = "cue-2"
        name = "Scripture"
        exit_hint = ""

    monkeypatch.setattr(mod.service_director, "current_cue", lambda: Cue())
    monkeypatch.setattr(mod.DirectorAI, "_decide_with_llm", lambda self, *a, **k: _none())
    monkeypatch.setattr(singleton, "_action_engine", ActionEngine(policy))
    monkeypatch.setattr(singleton, "queue", type(singleton.queue)())
    singleton.set_mode("manual")
    out = await mod.DirectorAI().observe("thanks be to God")
    assert out["accepted"] is False and out["decision"]["advance"] is True and out["mode"] == "manual"
    assert singleton.pending_actions == []

    singleton.set_mode("assisted")
    out = await mod.DirectorAI().observe("thanks be to God")
    assert out["accepted"] is True
    assert singleton.pending_actions[0].type == DirectorActionType.CUE_ADVANCE
    assert singleton.pending_actions[0].parameters["cue_id"] == "cue-2"
    singleton.queue.clear()


async def _none():
    return None
