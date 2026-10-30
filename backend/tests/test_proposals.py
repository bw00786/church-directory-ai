"""Tests for the ProposalQueue: ids, TTL, supersede, dedupe, priority, effects ledger."""

import pytest

from app.config import settings
from app.director.action_models import DirectorAction, DirectorActionType
from app.director.proposals import ProposalQueue, effect_key


def _camera(role="pastor", confidence=0.9):
    return DirectorAction(type=DirectorActionType.PTZ_SELECT_ROLE, target=role, confidence=confidence)


def _cut():
    return DirectorAction(type=DirectorActionType.ATEM_CUT, confidence=0.95)


def _slide(to_index, source="song_follower"):
    return DirectorAction(
        type=DirectorActionType.EASYWORSHIP_NEXT_SLIDE,
        parameters={"source": source, "song_id": "s1", "from_index": to_index - 1, "to_index": to_index},
        confidence=0.9,
    )


@pytest.fixture
def clock():
    return {"t": 1000.0}


@pytest.fixture
def queue(clock, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "ai_director_pending_ttl_seconds", 90.0)
    monkeypatch.setitem(settings.__dict__, "ai_director_dedupe_seconds", 5.0)
    return ProposalQueue(now=lambda: clock["t"], easyworship_commanded_within=lambda s: False)


def test_submit_assigns_id_and_snapshot_has_ttl(queue):
    result = queue.submit(_camera(), "ai_director", cycle_id="c1")
    assert result.accepted and result.proposal is not None
    assert len(result.proposal.id) == 32
    snap = queue.snapshot()
    assert snap[0]["id"] == result.proposal.id
    assert snap[0]["expires_in_seconds"] == 90.0
    assert snap[0]["cycle_id"] == "c1"
    assert snap[0]["held_reason"] is None
    assert queue.resolve_index(0) == result.proposal.id
    assert queue.resolve_index(1) is None


def test_ttl_expiry(queue, clock):
    p = queue.submit(_camera(), "ai_director").proposal
    clock["t"] += 89
    assert queue.expire() == []
    clock["t"] += 2
    expired = queue.expire()
    assert [e.id for e in expired] == [p.id]
    assert queue.pending() == []


def test_same_category_supersedes_older(queue):
    first = queue.submit(_camera("pastor"), "ai_director").proposal
    second = queue.submit(_camera("liturgist"), "ai_director")
    assert second.accepted
    assert [p.id for p in second.superseded] == [first.id]
    assert [p.id for p in queue.pending()] == [second.proposal.id]


def test_lower_priority_source_cannot_supersede(queue):
    follower = queue.submit(_slide(1), "song_follower").proposal
    llm = queue.submit(DirectorAction(type=DirectorActionType.EASYWORSHIP_NEXT, confidence=0.9), "ai_director")
    assert llm.accepted is False
    assert "outranked" in llm.reason
    assert [p.id for p in queue.pending()] == [follower.id]
    operator = queue.submit(_slide(2), "operator")
    assert operator.accepted and [p.id for p in operator.superseded] == [follower.id]


def test_identical_pending_is_deduped(queue):
    queue.submit(_camera("pastor"), "ai_director")
    dup = queue.submit(_camera("pastor"), "ai_director")
    assert dup.accepted is False and "duplicate of pending" in dup.reason
    assert len(queue) == 1


def test_recent_effect_dedupes_within_window(queue, clock):
    action = _camera("pastor")
    queue.note_effect(action)
    clock["t"] += 2
    assert queue.submit(_camera("pastor"), "ai_director").accepted is False
    clock["t"] += 4
    assert queue.submit(_camera("pastor"), "ai_director").accepted is True


def test_follower_slide_advances_are_distinct_effects(queue, clock):
    assert effect_key(_slide(1)) != effect_key(_slide(2))
    queue.note_effect(_slide(1))
    clock["t"] += 1
    assert queue.submit(_slide(2), "song_follower").accepted is True


def test_easyworship_commanded_blocks_item_nav_but_not_follower(clock, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "ai_director_dedupe_seconds", 5.0)
    queue = ProposalQueue(now=lambda: clock["t"], easyworship_commanded_within=lambda s: True)
    nxt = DirectorAction(type=DirectorActionType.EASYWORSHIP_NEXT, confidence=0.9)
    assert "EasyWorship" in queue.effect_conflict(nxt)
    assert queue.submit(nxt, "ai_director").accepted is False
    assert queue.effect_conflict(_slide(3)) is None


def test_program_change_ledger_blocks_duplicate_cut(queue, clock):
    queue.note_program_change()  # e.g. a cue-engine cut (PROGRAM_CHANGED commanded=True)
    clock["t"] += 2
    assert "ATEM program changed" in queue.effect_conflict(_cut())
    assert queue.submit(_cut(), "ai_director").accepted is False
    clock["t"] += 4
    assert queue.effect_conflict(_cut()) is None
    assert queue.submit(_cut(), "ai_director").accepted is True


def test_hold_pop_and_remove_where(queue):
    p = queue.submit(_camera(), "ai_director").proposal
    held = queue.hold(p.id, "min hold")
    assert held.held_reason == "min hold" and held.hold_count == 1
    assert [h.id for h in queue.held()] == [p.id]
    assert queue.snapshot()[0]["held_reason"] == "min hold"
    removed = queue.remove_where(lambda prop: prop.action.target == "pastor")
    assert [r.id for r in removed] == [p.id]
    assert queue.pop(p.id) is None
