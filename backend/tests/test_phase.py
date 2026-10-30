"""PhaseReconciler: LLM proposals are gated by confidence and hard signals;
cue engine and EasyWorship move the phase on their own."""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.director.phase import PhaseDecision, PhaseReconciler
from app.director.plan_builder import build_plan_from_script
from app.director.script import build_default_service_script
from app.domain.service_context import ServiceContext
from app.domain.service_state import ServiceState

SCRIPT = build_default_service_script()
CUES = {cue.id: cue for cue in SCRIPT.cues}
ORDER = [cue.id for cue in SCRIPT.cues]


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "ai_director_min_confidence", 0.80)
    monkeypatch.setitem(settings.__dict__, "phase_llm_requires_hard_signal", True)
    monkeypatch.setitem(settings.__dict__, "phase_singing_seconds", 8.0)


class _Director:
    """Stand-in for service_director.status()/current_cue()."""

    def __init__(self, running=False, cue_id=None):
        self.running = running
        self.cue_id = cue_id

    def status(self):
        index = ORDER.index(self.cue_id) if self.cue_id else -1
        return SimpleNamespace(running=self.running, cue_index=index)

    def current_cue(self):
        return CUES[self.cue_id] if self.running and self.cue_id else None


def _setup(running=False, cue_id=None, state=ServiceState.PRE_SERVICE):
    ctx = ServiceContext(plan=build_plan_from_script(SCRIPT))
    ctx.set_state(state, source="init")
    wall = [1000.0]
    ctx._wall = lambda: wall[0]
    director = _Director(running, cue_id)
    rec = PhaseReconciler(context=ctx, director_status=director.status, current_cue=director.current_cue)
    return ctx, rec, director, wall


# -- rule 1: a running cue engine is authoritative ------------------------------
def test_cue_engine_running_blocks_llm_override():
    ctx, rec, _, _ = _setup(running=True, cue_id="sermon", state=ServiceState.SERMON)
    decision = rec.propose("benediction", 0.99, "ai_directed")
    assert decision.accepted is False
    assert decision.state == ServiceState.SERMON
    assert decision.source == "cue"
    assert "cannot override" in decision.reason and "sermon" in decision.reason
    assert ctx.state == ServiceState.SERMON


def test_cue_engine_running_accepts_agreeing_proposal():
    _, rec, _, _ = _setup(running=True, cue_id="sermon", state=ServiceState.SERMON)
    decision = rec.propose("sermon", 0.5, "assisted")  # confidence irrelevant: it agrees
    assert decision.accepted is True and decision.state == ServiceState.SERMON and decision.source == "cue"


def test_cue_entry_moves_state_via_apply_hard_signals():
    ctx, rec, director, _ = _setup(running=True, cue_id="hymn_of_praise", state=ServiceState.OPENING_PRAYER)
    decision = rec.apply_hard_signals("cue")
    assert decision == PhaseDecision(True, ServiceState.HYMN, "cue 'hymn_of_praise' entered", "cue")
    assert ctx.state == ServiceState.HYMN and ctx.state_source == "cue"
    # Re-applying is a no-op that still reports the authoritative state.
    again = rec.apply_hard_signals("cue")
    assert again.accepted is True and "already" in again.reason


def test_cue_entered_payload_state_applies_without_rereading_director():
    ctx, rec, _, _ = _setup(running=False)
    decision = rec.apply_hard_signals("cue", state=ServiceState.OFFERTORY, detail="offertory_liturgist")
    assert decision.accepted and ctx.state == ServiceState.OFFERTORY
    assert decision.reason == "cue 'offertory_liturgist' entered"


def test_cue_signal_requested_but_engine_idle_is_rejected():
    ctx, rec, _, _ = _setup(running=False)
    decision = rec.apply_hard_signals("cue")
    assert decision.accepted is False and "not running" in decision.reason
    assert ctx.state == ServiceState.PRE_SERVICE


def test_cue_wins_over_easyworship_while_running():
    ctx, rec, _, _ = _setup(running=True, cue_id="sermon", state=ServiceState.SERMON)
    ctx.set_easyworship_item("Doxology")
    decision = rec.apply_hard_signals("easyworship")
    assert decision.source == "cue" and ctx.state == ServiceState.SERMON


# -- rule 2: confidence gate + hard-signal agreement ------------------------------
def test_low_confidence_is_rejected():
    ctx, rec, _, _ = _setup(state=ServiceState.SCRIPTURE)
    ctx.set_easyworship_item("Bumper Video")  # a hard signal that *would* agree
    decision = rec.propose("video", 0.79, "assisted")
    assert decision.accepted is False and decision.state == ServiceState.VIDEO
    assert "0.79 < 0.80" in decision.reason
    assert ctx.state == ServiceState.SCRIPTURE


def test_confident_proposal_without_any_agreeing_signal_is_rejected():
    ctx, rec, _, _ = _setup(state=ServiceState.SERMON)
    ctx.set_plan_cursor(ORDER.index("sermon"))
    decision = rec.propose("hymn", 0.95, "assisted")
    assert decision.accepted is False
    assert "no hard signal agrees with hymn" in decision.reason
    assert ctx.state == ServiceState.SERMON


def test_hard_signal_gate_can_be_disabled():
    _, rec, _, _ = _setup(state=ServiceState.SERMON)
    settings.__dict__["phase_llm_requires_hard_signal"] = False
    decision = rec.propose("hymn", 0.95, "assisted")
    assert decision.accepted is True and "none required" in decision.reason


def test_proposal_matching_easyworship_item_is_accepted():
    ctx, rec, _, _ = _setup(state=ServiceState.SCRIPTURE)
    ctx.set_easyworship_item("Bumper Video")
    decision = rec.propose("video", 0.9, "assisted")
    assert decision.accepted is True and decision.source == "llm"
    assert "agrees with easyworship" in decision.reason


def test_proposal_matching_next_plan_element_is_accepted():
    ctx, rec, _, _ = _setup(state=ServiceState.SCRIPTURE)
    ctx.set_plan_cursor(ORDER.index("scripture_reading"))  # next is bumper_video -> VIDEO
    assert rec.propose("video", 0.9, "assisted").accepted is True
    assert rec.propose("communion", 0.9, "assisted").accepted is False  # two steps ahead


def test_singing_pattern_agrees_with_song_family():
    ctx, rec, _, wall = _setup(state=ServiceState.ANNOUNCEMENT)
    ctx.set_plan_cursor(ORDER.index("announcements"))
    ctx.set_speaking("congregation", True)
    wall[0] += 5.0
    rejected = rec.propose("hymn", 0.9, "assisted")
    assert rejected.accepted is False  # not singing long enough yet
    wall[0] += 4.0  # 9 s >= phase_singing_seconds
    signals = rec.gather()
    assert ServiceState.HYMN in signals.audio_states and "congregation singing" in signals.audio_reason
    accepted = rec.propose("hymn", 0.9, "assisted")
    assert accepted.accepted is True and "audio" in accepted.reason


def test_pastor_speaking_implies_sermon_only_when_plan_agrees():
    ctx, rec, _, wall = _setup(state=ServiceState.VIDEO)
    ctx.set_speaking("pastor", True)
    wall[0] += 10.0
    # Plan cursor far from the sermon: no sermon-family audio signal.
    ctx.set_plan_cursor(ORDER.index("first_song"))
    assert not rec.gather().audio_states
    assert "no sermon planned" in rec.gather().audio_reason
    # Plan says the sermon is next: the pattern now counts.
    ctx.set_plan_cursor(ORDER.index("bumper_video"))
    assert ServiceState.SERMON in rec.gather().audio_states
    assert rec.propose("sermon", 0.9, "assisted").accepted is True


def test_pastor_speaking_while_congregation_sings_is_not_a_sermon():
    ctx, rec, _, wall = _setup(state=ServiceState.VIDEO)
    ctx.set_plan_cursor(ORDER.index("bumper_video"))
    ctx.set_speaking("pastor", True)
    ctx.set_speaking("vocalist", True)
    wall[0] += 10.0
    assert ServiceState.SERMON not in rec.gather().audio_states


def test_same_state_proposal_is_accepted_as_noop():
    ctx, rec, _, _ = _setup(state=ServiceState.SERMON)
    decision = rec.propose("sermon", 0.85, "assisted")
    assert decision.accepted is True and "already" in decision.reason


def test_unknown_or_missing_state_rejected():
    _, rec, _, _ = _setup()
    assert rec.propose(None, 0.9, "assisted") == PhaseDecision(False, None, "no state proposed", "llm")
    assert rec.propose("coffee_hour", 0.9, "assisted").accepted is False


# -- rule 3: hard signals alone -----------------------------------------------------
def test_easyworship_item_change_moves_state():
    ctx, rec, _, _ = _setup(state=ServiceState.OPENING_PRAYER)
    ctx.set_easyworship_item("Hymn of Praise", 1, 7)
    decision = rec.apply_hard_signals("easyworship")
    assert decision.accepted is True and decision.source == "easyworship"
    assert decision.state == ServiceState.HYMN and ctx.state == ServiceState.HYMN
    assert ctx.state_source == "easyworship"
    assert "Hymn of Praise" in decision.reason


def test_unknown_easyworship_item_does_not_move_state():
    ctx, rec, _, _ = _setup(state=ServiceState.OPENING_PRAYER)
    ctx.set_easyworship_item("Mystery Item")
    decision = rec.apply_hard_signals("easyworship")
    assert decision.accepted is False and ctx.state == ServiceState.OPENING_PRAYER


def test_audio_alone_never_moves_state():
    ctx, rec, _, wall = _setup(state=ServiceState.ANNOUNCEMENT)
    ctx.set_speaking("vocalist", True)
    wall[0] += 60.0
    decision = rec.apply_hard_signals("audio")
    assert decision.accepted is False and decision.source == "audio"
    assert ctx.state == ServiceState.ANNOUNCEMENT


def test_status_reports_last_decision_signals_and_history():
    ctx, rec, _, _ = _setup(state=ServiceState.SCRIPTURE)
    ctx.set_easyworship_item("Bumper Video")
    rec.propose("video", 0.5, "assisted")
    rec.apply_hard_signals("easyworship")
    status = rec.status()
    assert status["last_decision"]["source"] == "easyworship" and status["last_decision"]["accepted"] is True
    assert status["signals"]["easyworship_state"] == "video"
    assert status["context_state"] == "video" and status["context_state_source"] == "easyworship"
    assert [h["accepted"] for h in status["history"]] == [False, True]
    assert status["settings"]["phase_singing_seconds"] == 8.0
    assert status["last_mode"] == "assisted"
