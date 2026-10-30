"""Tri-state slide verification: OCR *unavailable* is ``unknown`` (never a
halt); OCR *evidence* of an unchanged slide still halts. Also covers the
serialization lock and OCR running off the event loop."""

import asyncio
import threading

import pytest

from app.config import settings
from app.easyworship.slide_verification import SlideVerifier, SlideVerifyOutcome
from app.events.bus import event_bus


def set_setting(monkeypatch, name, value):
    """Patch an app setting that may not exist yet in app.config (pydantic
    refuses unknown instance attributes, so fall back to the class)."""
    if name in type(settings).model_fields:
        monkeypatch.setattr(settings, name, value)
    else:
        monkeypatch.setattr(type(settings), name, value, raising=False)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "slide_verify_semantic_enabled", False)
    monkeypatch.setattr(settings, "slide_verify_poll_ms", 400)
    monkeypatch.setattr(settings, "slide_verify_timeout_seconds", 4.0)
    set_setting(monkeypatch, "slide_ocr_gpu", False)


@pytest.fixture
def published(monkeypatch):
    out = []
    monkeypatch.setattr(event_bus, "publish", lambda msg: out.append(msg))
    return out


@pytest.fixture
def mode_calls(monkeypatch):
    from app.director import ai_director_runtime as rt

    calls = []
    monkeypatch.setattr(rt.ai_director_runtime, "set_mode", lambda mode: calls.append(mode))
    return calls


def _advancing_clock(step=0.5):
    t = {"v": 0.0}

    def now():
        t["v"] += step
        return t["v"]

    return now


async def _noop_sleep(_seconds):
    return None


def _verifier(samples, before="Previous slide"):
    v = SlideVerifier(sleep=_noop_sleep, now=_advancing_clock())
    v.last_text = before
    seq = iter(samples)
    v.current_text = lambda: next(seq, samples[-1])
    return v


def _events(published):
    return [m["event"] for m in published]


# -- unknown: OCR unavailable -------------------------------------------------
def test_no_frames_is_unknown_not_halt(published, mode_calls):
    v = _verifier([None])  # capture device delivering nothing / reader missing
    result = asyncio.run(v.verify_after_action("next_slide"))

    assert result.outcome == SlideVerifyOutcome.UNKNOWN
    assert result.ok and not result.known
    assert "SLIDE_VERIFY_UNKNOWN" in _events(published)
    assert "SLIDE_VERIFY_HALT" not in _events(published)
    assert "EASYWORSHIP_SLIDE_STUCK" not in _events(published)
    assert mode_calls == []  # AI runtime never flipped to manual
    assert v.last_text == "Previous slide"  # we learned nothing; keep it
    assert v.consecutive_unknown == 1


def test_empty_reads_with_frames_is_unknown(published, mode_calls):
    v = _verifier([""])  # frames arrive but OCR sees no text
    result = asyncio.run(v.verify_after_action("next_slide"))

    assert result.outcome == SlideVerifyOutcome.UNKNOWN
    assert "no text" in result.reason
    assert mode_calls == []
    payload = next(m["payload"] for m in published if m["event"] == "SLIDE_VERIFY_UNKNOWN")
    assert payload["action"] == "next_slide"
    assert payload["consecutive_unknown"] == 1


def test_consecutive_unknowns_tracked_and_reset(published, mode_calls):
    v = _verifier([None])
    asyncio.run(v.verify_after_action("next_slide"))
    asyncio.run(v.verify_after_action("next_slide"))
    assert v.consecutive_unknown == 2
    assert v.snapshot()["consecutive_unknown"] == 2

    # OCR comes back and sees a real change -> counter resets.
    seq = iter(["New slide", "New slide"])
    v.current_text = lambda: next(seq, "New slide")
    result = asyncio.run(v.verify_after_action("next_slide"))
    assert result.outcome == SlideVerifyOutcome.VERIFIED
    assert v.consecutive_unknown == 0
    assert v.snapshot()["last_unknown_reason"] is None


def test_single_known_read_then_ocr_dies_is_unknown(published, mode_calls):
    # One real read is not enough evidence to call the slide "stuck".
    v = _verifier(["Previous slide", None])
    result = asyncio.run(v.verify_after_action("next_slide"))
    assert result.outcome == SlideVerifyOutcome.UNKNOWN
    assert mode_calls == []


# -- unchanged: OCR evidence still halts ---------------------------------------
def test_unchanged_text_still_halts(published, mode_calls):
    v = _verifier(["Previous slide"])
    result = asyncio.run(v.verify_after_action("next_slide"))

    assert result.outcome == SlideVerifyOutcome.TIMEOUT
    assert not result.ok
    assert "EASYWORSHIP_SLIDE_STUCK" in _events(published)
    assert "SLIDE_VERIFY_HALT" in _events(published)
    assert mode_calls == ["manual"]
    assert v.consecutive_unknown == 0


def test_unchanged_with_intermittent_unknowns_still_halts(published, mode_calls):
    v = _verifier(["Previous slide", None, "Previous slide", "", "Previous slide", "Previous slide"])
    result = asyncio.run(v.verify_after_action("next_slide"))
    assert result.outcome == SlideVerifyOutcome.TIMEOUT
    assert mode_calls == ["manual"]


def test_unknown_gap_does_not_fake_stability(published):
    # Same text before and after an OCR dropout must not count as two
    # *consecutive* stable samples.
    v = _verifier(["New slide", None, "Other slide", None, "Third", None, "Fourth", None])
    result = asyncio.run(v.verify_after_action("next_slide"))
    assert result.outcome == SlideVerifyOutcome.TIMEOUT  # unstable but known


# -- current_text(): real frame_capture / slide_ocr integration ------------------
def test_current_text_none_when_no_frame_or_reader(monkeypatch):
    from app.easyworship import slide_verification as sv
    from app.vision.slide_ocr import OcrRead

    monkeypatch.setattr(sv.frame_capture, "get_frame", lambda tag: (None, {}))
    assert SlideVerifier().current_text() is None

    monkeypatch.setattr(sv.frame_capture, "get_frame", lambda tag: (object(), {}))
    monkeypatch.setattr(sv.slide_ocr, "read", lambda frame: OcrRead(ok=False, reason="reader_unavailable"))
    assert SlideVerifier().current_text() is None

    monkeypatch.setattr(sv.slide_ocr, "read", lambda frame: OcrRead(ok=True, segments=("Amazing", "Grace")))
    assert SlideVerifier().current_text() == "Amazing Grace"

    monkeypatch.setattr(sv.slide_ocr, "read", lambda frame: OcrRead(ok=True, segments=()))
    assert SlideVerifier().current_text() == ""


# -- OCR off the event loop ---------------------------------------------------
def test_current_text_runs_in_worker_thread():
    threads = []

    def sampler():
        threads.append(threading.current_thread())
        return "New slide"

    v = SlideVerifier(sleep=_noop_sleep, now=_advancing_clock())
    v.last_text = "Previous slide"
    v.current_text = sampler

    async def main():
        loop_thread = threading.current_thread()
        result = await v.verify_after_action("next_slide")
        return loop_thread, result

    loop_thread, result = asyncio.run(main())
    assert result.outcome == SlideVerifyOutcome.VERIFIED
    assert threads and all(t is not loop_thread for t in threads)


# -- serialization ----------------------------------------------------------------
def test_concurrent_verifications_are_serialized(published):
    active = {"n": 0, "max": 0}
    order = []

    v = SlideVerifier(now=_advancing_clock())

    async def slow_sleep(_s):
        await asyncio.sleep(0.001)

    v._sleep = slow_sleep
    texts = iter(["A", "A", "B", "B"])

    def sample():
        return next(texts, "B")

    v.current_text = sample

    orig = v._verify_locked

    async def tracked(action, song_id, slide_index):
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        order.append(("start", action))
        try:
            return await orig(action, song_id, slide_index)
        finally:
            active["n"] -= 1
            order.append(("end", action))

    v._verify_locked = tracked

    async def main():
        return await asyncio.gather(
            v.verify_after_action("first"), v.verify_after_action("second")
        )

    r1, r2 = asyncio.run(main())
    assert active["max"] == 1
    assert order == [("start", "first"), ("end", "first"), ("start", "second"), ("end", "second")]
    assert r1.outcome == SlideVerifyOutcome.VERIFIED and r1.ocr_text == "A"
    assert r2.outcome == SlideVerifyOutcome.VERIFIED and r2.ocr_text == "B"
    assert v.last_text == "B"


def test_lock_rebinds_across_event_loops():
    v = _verifier(["X", "X"])
    asyncio.run(v.verify_after_action("a"))
    v.current_text = lambda: "Y"
    asyncio.run(v.verify_after_action("b"))  # second loop; must not raise
    assert v.last_text == "Y"
    assert v.busy is False
