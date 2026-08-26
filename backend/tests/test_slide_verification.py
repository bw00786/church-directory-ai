"""Tests for slide-change verification (WO-EWVERIFY-1/-2).

Covers the WO-EWVERIFY-2 hardening: unified config surface, fail-loud config
loading, enabled-but-unusable-device startup refusal, and the stabilization
poll loop (passes on a slow fade, times out + halts on a stuck slide).
"""

import asyncio
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.easyworship.slide_verification import (
    SlideVerifier,
    SlideVerifyConfigError,
    SlideVerifyOutcome,
    _probe_device,
    fuzzy_ratio,
    normalize,
    start_slide_verification,
    validate_slide_verify_config,
)
from app.events.bus import event_bus

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Old variable names retired by WO-EWVERIFY-2; must appear nowhere in the repo's
# feature surface.
OLD_NAMES = (
    "easyworship_slide_verify_enabled",
    "vision_slides_device",
    "slide_verify_delay_seconds",
    "EASYWORSHIP_SLIDE_VERIFY_ENABLED",
    "VISION_SLIDES_DEVICE",
    "SLIDE_VERIFY_DELAY_SECONDS",
)


def _env_slide_values() -> dict:
    values = {}
    for line in ENV_EXAMPLE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if key.startswith("SLIDE_VERIFY_"):
            values[key] = raw.split("#", 1)[0].strip()
    return values


# -- AC-1: .env.example parses cleanly ---------------------------------------
def test_env_example_slide_values_parse():
    values = _env_slide_values()
    assert values["SLIDE_VERIFY_ENABLED"] == "false"
    assert values["SLIDE_VERIFY_DEVICE"] == ""
    assert int(values["SLIDE_VERIFY_POLL_MS"]) == 400
    assert float(values["SLIDE_VERIFY_TIMEOUT_SECONDS"]) == 4.0
    assert values["SLIDE_VERIFY_SEMANTIC_ENABLED"] == "false"
    assert 0.0 <= float(values["SLIDE_VERIFY_SEMANTIC_THRESHOLD"]) <= 1.0


# -- AC-5: old variable names appear nowhere in the feature surface ----------
def test_no_retired_variable_names_in_repo():
    surface = [
        REPO_ROOT / ".env.example",
        REPO_ROOT / "backend/app/config.py",
        REPO_ROOT / "backend/app/main.py",
        REPO_ROOT / "backend/app/easyworship/service.py",
        REPO_ROOT / "backend/app/easyworship/slide_verification.py",
        REPO_ROOT / "docs/director.md",
        REPO_ROOT / "docs/ai-director.md",
    ]
    for path in surface:
        text = path.read_text()
        for old in OLD_NAMES:
            assert old not in text, f"{old} still present in {path}"


# -- AC-2: config loader fails loud on unparseable values --------------------
def test_malformed_numeric_env_fails_loud(monkeypatch):
    monkeypatch.setenv("SLIDE_VERIFY_TIMEOUT_SECONDS", "1.5.")
    with pytest.raises(Exception) as exc:
        Settings()
    assert "slide_verify_timeout_seconds" in str(exc.value)


# -- AC-3: enabled-but-unusable device refuses to start ----------------------
def test_enabled_empty_device_refuses(monkeypatch):
    monkeypatch.setattr(settings, "slide_verify_enabled", True)
    monkeypatch.setattr(settings, "slide_verify_device", "")
    monkeypatch.setattr(settings, "vision_frame_source", "live")
    with pytest.raises(SlideVerifyConfigError):
        validate_slide_verify_config()


def test_disabled_empty_device_starts(monkeypatch):
    monkeypatch.setattr(settings, "slide_verify_enabled", False)
    monkeypatch.setattr(settings, "slide_verify_semantic_enabled", False)
    monkeypatch.setattr(settings, "slide_verify_device", "")
    # Must not raise and must not touch capture.
    asyncio.run(start_slide_verification())


def test_bogus_device_probe_refuses():
    with pytest.raises(SlideVerifyConfigError):
        asyncio.run(_probe_device(lambda: None, window_seconds=0.05, interval=0.01))


# -- normalization / fuzzy ---------------------------------------------------
def test_normalize_folds_case_punct_ws():
    assert normalize("  Amazing, GRACE!  How\tsweet ") == "amazing grace how sweet"


def test_fuzzy_ratio_bounds():
    assert fuzzy_ratio("amazing grace", "amazing grace") == 1.0
    assert fuzzy_ratio("", "x") == 0.0
    assert fuzzy_ratio("Amazing Grace!", "amazing grace") == 1.0
    assert fuzzy_ratio("", "") == 1.0


# -- AC-4: poll loop verifies on slow fade, times out + halts on stuck slide --
def _advancing_clock(step=0.5):
    t = {"v": 0.0}

    def now():
        t["v"] += step
        return t["v"]

    return now


async def _noop_sleep(_seconds):
    return None


def _make_verifier(samples):
    v = SlideVerifier(sleep=_noop_sleep, now=_advancing_clock())
    seq = iter(samples)
    v.current_text = lambda: next(seq, samples[-1])
    return v


def test_poll_loop_verifies_on_slow_fade():
    v = _make_verifier(["Stanza one line", "…blur…", "Stanza two line", "Stanza two line"])
    v.last_text = "Stanza one line"
    result = asyncio.run(v.verify_after_action("next_slide"))
    assert result.outcome == SlideVerifyOutcome.VERIFIED
    assert result.ok


def test_poll_loop_timeout_halts(monkeypatch):
    published = []
    monkeypatch.setattr(event_bus, "publish", lambda msg: published.append(msg))

    v = SlideVerifier(sleep=_noop_sleep, now=_advancing_clock())
    v.last_text = "Stuck slide"
    v.current_text = lambda: "Stuck slide"  # never changes

    result = asyncio.run(v.verify_after_action("next_slide"))

    assert result.outcome == SlideVerifyOutcome.TIMEOUT
    assert not result.ok
    events = [m["event"] for m in published]
    assert "EASYWORSHIP_SLIDE_STUCK" in events
    assert "SLIDE_VERIFY_HALT" in events


# -- snapshot ----------------------------------------------------------------
def test_snapshot_reflects_flags_and_last_text(monkeypatch):
    monkeypatch.setattr(settings, "slide_verify_enabled", True)
    monkeypatch.setattr(settings, "slide_verify_semantic_enabled", False)
    verifier = SlideVerifier()
    verifier.last_text = "some text"

    snapshot = verifier.snapshot()

    assert snapshot == {
        "enabled": True,
        "semantic_enabled": False,
        "last_ocr_text": "some text",
    }
