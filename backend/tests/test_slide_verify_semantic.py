"""Tests for lyric-aware semantic slide verification (WO-EWVERIFY-3)."""

import asyncio
from typing import Optional

import pytest

from app.config import settings
from app.easyworship.slide_expected import ExpectedTextProvider
from app.easyworship.slide_verification import (
    SlideVerifier,
    SlideVerifyConfigError,
    SlideVerifyOutcome,
    fuzzy_ratio,
    validate_slide_verify_config,
)
from app.events.bus import event_bus


class FakeProvider(ExpectedTextProvider):
    def __init__(self, expected: Optional[str]) -> None:
        self._expected = expected

    def expected_text(self, song_id, slide_index) -> Optional[str]:
        return self._expected


def _advancing_clock(step=0.5):
    t = {"v": 0.0}

    def now():
        t["v"] += step
        return t["v"]

    return now


async def _noop_sleep(_seconds):
    return None


def _verifier_showing(text, expected, before="previous slide text"):
    """A verifier whose OCR immediately reports a stable `text` (different from
    the pre-action `before`), backed by a provider returning `expected`."""
    v = SlideVerifier(provider=FakeProvider(expected), sleep=_noop_sleep, now=_advancing_clock())
    v.last_text = before
    v.current_text = lambda: text
    return v


@pytest.fixture(autouse=True)
def _semantic_on(monkeypatch):
    monkeypatch.setattr(settings, "slide_verify_semantic_enabled", True)
    monkeypatch.setattr(settings, "slide_verify_semantic_threshold", 0.75)


# -- AC-5: semantic requires change-detection --------------------------------
def test_semantic_without_change_detection_is_startup_error(monkeypatch):
    monkeypatch.setattr(settings, "slide_verify_enabled", False)
    monkeypatch.setattr(settings, "slide_verify_semantic_enabled", True)
    with pytest.raises(SlideVerifyConfigError):
        validate_slide_verify_config()


# -- AC-1: double-advance -> mismatch -> alert + halt ------------------------
def test_double_advance_mismatch_halts(monkeypatch):
    published = []
    monkeypatch.setattr(event_bus, "publish", lambda msg: published.append(msg))

    v = _verifier_showing(
        text="Verse three totally different words on screen",
        expected="Amazing grace how sweet the sound",
    )
    result = asyncio.run(v.verify_after_action("next_slide", song_id="song-1", slide_index=2))

    assert result.outcome == SlideVerifyOutcome.VERIFIED_CHANGED_MISMATCH
    assert not result.ok
    events = [m["event"] for m in published]
    assert "EASYWORSHIP_SLIDE_MISMATCH" in events
    assert "SLIDE_VERIFY_HALT" in events


# -- AC-2: correct single advance -> VERIFIED_CORRECT ------------------------
def test_correct_advance_verified():
    v = _verifier_showing(
        text="Amazing grace how sweet the sound",
        expected="Amazing grace how sweet the sound",
    )
    result = asyncio.run(v.verify_after_action("next_slide", song_id="song-1", slide_index=1))
    assert result.outcome == SlideVerifyOutcome.VERIFIED_CORRECT
    assert result.ok
    assert result.score == 1.0


# -- AC-3: sermon slide (no expected text) -> CHANGE_ONLY, no false alarm -----
def test_sermon_change_only(monkeypatch):
    published = []
    monkeypatch.setattr(event_bus, "publish", lambda msg: published.append(msg))

    v = _verifier_showing(text="Point one of the sermon", expected=None)
    result = asyncio.run(v.verify_after_action("next_item", song_id=None, slide_index=None))

    assert result.outcome == SlideVerifyOutcome.CHANGE_ONLY
    assert result.ok
    assert published == []  # no halt / mismatch events


# -- AC-4: threshold boundary just above / just below ------------------------
def test_threshold_boundary(monkeypatch):
    shown = "amazing grace how sweet the sound"
    expected = "amazing grace how sweet the sounds"  # near-miss
    score = fuzzy_ratio(shown, expected)
    assert 0.0 < score < 1.0

    monkeypatch.setattr(settings, "slide_verify_semantic_threshold", score - 0.01)
    v_pass = _verifier_showing(text=shown, expected=expected)
    assert asyncio.run(v_pass.verify_after_action("next_slide")).outcome == (
        SlideVerifyOutcome.VERIFIED_CORRECT
    )

    monkeypatch.setattr(settings, "slide_verify_semantic_threshold", score + 0.01)
    v_fail = _verifier_showing(text=shown, expected=expected)
    assert asyncio.run(v_fail.verify_after_action("next_slide")).outcome == (
        SlideVerifyOutcome.VERIFIED_CHANGED_MISMATCH
    )
