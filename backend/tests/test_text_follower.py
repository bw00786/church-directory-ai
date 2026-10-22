"""Tests for Phase 1 song/reading text-follow slide advance."""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.director.action_models import DirectorActionType
from app.director.text_follower import (
    LiveSlides,
    SongFollowerService,
    TextFollower,
    fuse_confidence,
    parse_role_weights,
)
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


def test_service_emits_easyworship_next_slide(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", "vocalist")
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 0.0)
    svc = SongFollowerService(text_source=lambda: ("song1", SLIDES))
    actions = svc.process([_line("that saved a wretch like me")])
    assert len(actions) == 1
    assert actions[0].type == DirectorActionType.EASYWORSHIP_NEXT_SLIDE
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


# -- End-of-slide trigger (congregation reads from the screen) ------------------
CALL_TO_WORSHIP = [
    "The Lord be with you. And also with you. Lift up your hearts.",
    "We lift them up to the Lord. Let us give thanks to the Lord our God.",
    "It is right to give our thanks and praise.",
]


def test_finishing_current_slide_proposes_before_next_is_read():
    f = TextFollower(min_confidence=0.72, tail_words=8)
    f.load("ctw", CALL_TO_WORSHIP)
    assert f.observe("the lord be with you") is None
    proposal = f.observe("and also with you lift up your hearts")
    assert proposal is not None
    assert (proposal.from_index, proposal.to_index) == (0, 1)


def test_finishing_tolerates_dropped_asr_words():
    f = TextFollower(min_confidence=0.72, tail_words=8)
    f.load("ctw", CALL_TO_WORSHIP)
    # "the", "also" and "your" dropped by ASR.
    assert f.observe("lord be with you and with you lift up hearts") is not None


def test_closing_phrase_heard_early_does_not_fire():
    f = TextFollower(min_confidence=0.72, tail_words=3)
    f.load("ps", ["Praise the Lord. Give thanks for he is good and his love endures forever. Praise the Lord.", "next"])
    # Only the opening words, which equal the closing words, have been read.
    assert f.observe("praise the lord") is None


def test_repeated_chorus_does_not_catch_up_early():
    chorus = "Holy holy holy Lord God almighty"
    f = TextFollower(min_confidence=0.72, min_anchor_words=3, tail_words=8)
    f.load("song", [chorus + " early in the morning", chorus + " our song shall rise to thee"])
    assert f.observe("holy holy holy lord god almighty") is None


def test_unbounded_ocr_slide_proposes_then_waits_for_reload():
    f = TextFollower(min_confidence=0.72, tail_words=8)
    f.load("ocr:1", [CALL_TO_WORSHIP[0]], bounded=False)
    proposal = f.observe("the lord be with you and also with you lift up your hearts")
    assert proposal is not None and proposal.to_index == 1
    f.confirm_advance(1)
    # Same slide text still loaded: nothing until the source reloads.
    assert f.observe("the lord be with you and also with you lift up your hearts") is None
    f.load("ocr:2", [CALL_TO_WORSHIP[1]], bounded=False)
    assert f.observe("we lift them up to the lord let us give thanks to the lord our god") is not None


def test_sync_index_follows_operator_moves():
    f = TextFollower()
    f.load("song1", SLIDES)
    f.sync_index(2)
    assert f.current_index == 2
    assert f.observe("i once was lost but now am found") is not None  # finishes slide 2


def test_service_reloads_and_syncs_index_from_source(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", "congregation")
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 0.0)
    live = {"value": LiveSlides(song_id="lib:ctw:1", slides=CALL_TO_WORSHIP, index=1)}
    svc = SongFollowerService(text_source=lambda: live["value"])
    actions = svc.process([_line("let us give thanks to the lord our god", role="congregation")])
    assert actions and actions[0].parameters["to_index"] == 2


def test_service_notify_rejected_allows_reproposal(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", "vocalist")
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 0.0)
    svc = SongFollowerService(text_source=lambda: ("song1", SLIDES))
    first = svc.process([_line("that saved a wretch like me")])
    svc.notify_rejected(first[0])
    assert svc.process([_line("that saved a wretch like me")])


async def test_service_refresh_and_invalidate_reach_source(monkeypatch):
    calls = []

    class _Source:
        def __call__(self):
            return ("song1", SLIDES)

        async def refresh(self):
            calls.append("refresh")

        def invalidate(self):
            calls.append("invalidate")

    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", "vocalist")
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 0.0)
    svc = SongFollowerService(text_source=_Source())
    await svc.refresh()
    actions = svc.process([_line("that saved a wretch like me")])
    svc.notify_executed(actions[0])
    assert calls == ["refresh", "invalidate"]


# -- Manual slide changes, confidence fusion, role weights ----------------------
def _follow_settings(monkeypatch, roles="vocalist,congregation"):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_roles", roles)
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 0.0)
    monkeypatch.setattr(settings, "song_follower_min_confidence", 0.72)
    monkeypatch.setattr(settings, "song_follower_role_weights", "vocalist:1.0,congregation:0.85")
    monkeypatch.setattr(settings, "song_follower_screen_weight", 0.3)


def test_fuse_confidence_and_role_weights():
    assert fuse_confidence(0.8, None, 0.3) == 0.8
    assert fuse_confidence(0.8, 1.0, 0.0) == 0.8
    assert fuse_confidence(0.8, 1.0, 0.3) == pytest.approx(0.86)
    assert fuse_confidence(0.8, 0.0, 0.3) == pytest.approx(0.56)
    assert parse_role_weights("vocalist:1, congregation:0.85,bad,x:abc") == {"vocalist": 1.0, "congregation": 0.85}


def test_sync_index_reports_manual_moves_and_clears_pending():
    f = TextFollower()
    f.load("song1", SLIDES)
    assert f.observe("that saved a wretch like me") is not None  # pending -> 1
    assert f.sync_index(1) is False  # EasyWorship caught up with our own advance
    assert not f.pending
    assert f.sync_index(3) is True  # operator jumped ahead
    assert f.current_index == 3


def test_resync_follows_operator_click_ahead(monkeypatch):
    _follow_settings(monkeypatch)
    live = {"index": 0}
    svc = SongFollowerService(text_source=lambda: LiveSlides(song_id="lib:s:1", slides=SLIDES, index=live["index"]))
    actions = svc.process([_line("that saved a wretch like me")])
    assert actions and actions[0].parameters["from_index"] == 0
    assert svc.resync() is False
    live["index"] = 2  # operator clicked ahead past the proposal
    assert svc.resync() is True
    assert svc.follower.current_index == 2
    assert svc.is_stale(actions[0])


def test_resync_reloads_ocr_slide_changed_by_operator(monkeypatch):
    _follow_settings(monkeypatch)
    live = {"value": LiveSlides(song_id="ocr:1", slides=[SLIDES[0]], bounded=False)}
    svc = SongFollowerService(text_source=lambda: live["value"])
    assert svc.resync() is False  # first load isn't a manual change
    live["value"] = LiveSlides(song_id="ocr:2", slides=[SLIDES[1]], bounded=False)
    assert svc.resync() is True
    assert svc.follower.song_id == "ocr:2"


def test_notify_executed_skips_when_watcher_already_loaded_next_slide(monkeypatch):
    _follow_settings(monkeypatch)
    live = {"value": LiveSlides(song_id="ocr:1", slides=[SLIDES[0]], bounded=False)}
    svc = SongFollowerService(text_source=lambda: live["value"])
    actions = svc.process([_line("amazing grace how sweet the sound")])
    assert actions
    live["value"] = LiveSlides(song_id="ocr:2", slides=[SLIDES[1]], bounded=False)
    assert svc.resync() is False  # our own pending advance, not a manual move
    svc.notify_executed(actions[0])
    assert svc.follower.status()["waiting_for_new_slide"] is False


def test_congregation_needs_stronger_match_than_vocalist(monkeypatch):
    _follow_settings(monkeypatch)
    monkeypatch.setattr(settings, "song_follower_min_confidence", 0.9)
    svc = SongFollowerService(text_source=lambda: ("song1", SLIDES))
    # 0.85 role weight drops a perfect room-mic match below 0.9.
    assert svc.process([_line("amazing grace how sweet the sound", role="congregation")]) == []
    actions = svc.process([_line("amazing grace how sweet the sound", role="vocalist")])
    assert actions and actions[0].parameters["role"] == "vocalist"


class _ScreenSource:
    def __init__(self, agreement):
        self.agreement = agreement

    def __call__(self):
        return LiveSlides(song_id="lib:s:1", slides=SLIDES, index=0)

    def screen_agreement(self, slide_text):
        return self.agreement


def test_screen_agreement_raises_and_lowers_confidence(monkeypatch):
    _follow_settings(monkeypatch)
    agree = SongFollowerService(text_source=_ScreenSource(1.0))
    action = agree.process([_line("amazing grace how sweet the sound")])[0]
    assert action.confidence == 1.0 and action.parameters["screen_agreement"] == 1.0

    # Camera 2 shows a different slide: confidence drops below the follower minimum.
    disagree = SongFollowerService(text_source=_ScreenSource(0.0))
    assert disagree.process([_line("amazing grace how sweet the sound")]) == []


def test_ocr_text_source_has_no_independent_screen_evidence(monkeypatch):
    _follow_settings(monkeypatch)

    class _Ocr(_ScreenSource):
        def __call__(self):
            return LiveSlides(song_id="ocr:1", slides=[SLIDES[0]], bounded=False)

    action = SongFollowerService(text_source=_Ocr(0.0)).process([_line("amazing grace how sweet the sound")])[0]
    assert action.parameters["screen_agreement"] is None
