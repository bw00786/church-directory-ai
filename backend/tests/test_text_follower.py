"""Tests for Phase 1 song/reading text-follow slide advance."""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.director.action_models import DirectorActionType
from app.director.text_follower import SongFollowerService, TextFollower
from app.domain.service_context import TranscriptLine

SLIDES = [
    "Amazing grace how sweet the sound",
    "That saved a wretch like me",
    "I once was lost but now am found",
    "Was blind but now I see",
]


def _line(text, role="vocalist", t=None):
    return TranscriptLine(speaker_role=role, text=text, timestamp=t or datetime.now(timezone.utc))


# -- TextFollower (pure alignment) ---------------------------------------------
def test_follower_proposes_advance_when_next_slide_heard():
    f = TextFollower(min_confidence=0.72, min_anchor_words=3)
    f.load("song1", SLIDES)
    assert f.observe("amazing grace how sweet") is None  # still on slide 0
    proposal = f.observe("that saved a wretch like me")
    assert proposal is not None
    assert proposal.from_index == 0
    assert proposal.to_index == 1
    assert proposal.confidence >= 0.72


def test_follower_requires_anchor_words():
    f = TextFollower(min_confidence=0.5, min_anchor_words=3)
    f.load("song1", SLIDES)
    # Only two of the next slide's opening words -> below anchor requirement.
    assert f.observe("that saved") is None


def test_follower_does_not_repropose_until_confirmed():
    f = TextFollower(min_confidence=0.72, min_anchor_words=3)
    f.load("song1", SLIDES)
    assert f.observe("that saved a wretch like me") is not None
    # Same target again -> suppressed until confirm/reject.
    assert f.observe("that saved a wretch like me") is None
    f.confirm_advance()
    assert f.current_index == 1
    # Now the NEXT slide can be proposed.
    assert f.observe("i once was lost but now am found") is not None


def test_follower_reject_allows_reproposal():
    f = TextFollower(min_confidence=0.72, min_anchor_words=3)
    f.load("song1", SLIDES)
    assert f.observe("that saved a wretch like me") is not None
    f.reject_advance()
    assert f.observe("that saved a wretch like me") is not None


def test_follower_stops_at_last_slide():
    f = TextFollower(min_confidence=0.5, min_anchor_words=1)
    f.load("song1", SLIDES)
    f.confirm_advance(len(SLIDES) - 1)
    assert f.observe("was blind but now i see") is None


def test_expected_slide_text_matches_loaded_song():
    f = TextFollower()
    f.load("song1", SLIDES)
    assert f.expected_slide_text("song1", 1) == SLIDES[1]
    assert f.expected_slide_text("other", 1) is None
    assert f.expected_slide_text("song1", 99) is None


# -- SongFollowerService (integration wrapper) ---------------------------------
def test_service_disabled_returns_no_actions(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", False)
    svc = SongFollowerService(text_source=lambda: ("song1", SLIDES))
    assert svc.process([_line("that saved a wretch like me")]) == []


def test_service_emits_easyworship_next(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", "vocalist")
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 0.0)
    svc = SongFollowerService(text_source=lambda: ("song1", SLIDES))
    actions = svc.process([_line("that saved a wretch like me")])
    assert len(actions) == 1
    assert actions[0].type == DirectorActionType.EASYWORSHIP_NEXT
    assert actions[0].parameters["source"] == "song_follower"
    assert actions[0].parameters["to_index"] == 1


def test_service_filters_by_role(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", "vocalist")
    svc = SongFollowerService(text_source=lambda: ("song1", SLIDES))
    # A pastor line for the same text should be ignored.
    assert svc.process([_line("that saved a wretch like me", role="pastor")]) == []


def test_service_respects_cooldown(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", "vocalist")
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 10.0)
    clock = {"t": 100.0}
    svc = SongFollowerService(text_source=lambda: ("song1", SLIDES), now=lambda: clock["t"])
    first = svc.process([_line("that saved a wretch like me")])
    assert len(first) == 1
    svc.notify_executed(first[0])
    # Immediately hearing the following slide is suppressed by the cooldown.
    second = svc.process([_line("i once was lost but now am found")])
    assert second == []


def test_service_no_source_is_inert(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    svc = SongFollowerService(text_source=None)
    assert svc.process([_line("that saved a wretch like me")]) == []


def test_service_notify_executed_advances_follower(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", "vocalist")
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 0.0)
    svc = SongFollowerService(text_source=lambda: ("song1", SLIDES))
    actions = svc.process([_line("that saved a wretch like me")])
    svc.notify_executed(actions[0])
    assert svc.follower.current_index == 1
