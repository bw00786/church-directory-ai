"""Tests for the PTZ verification state machine (WO-VISION-1 FR-3)."""

import asyncio

import pytest

from app.config import settings
from app.domain.observations import VisionObservation
from app.events.bus import event_bus
from app.vision.verification import PtzVerifier, camera_to_atem_input


async def _noop_sleep(*_a, **_k):
    return None


def _verifier(monkeypatch, observations):
    """A verifier whose _observe yields the given scripted observations."""
    v = PtzVerifier()
    v._sleep = _noop_sleep
    seq = list(observations)
    calls = {"i": 0}

    def fake_observe(role, camera_id, on_program):
        obs = seq[min(calls["i"], len(seq) - 1)]
        calls["i"] += 1
        return obs
    monkeypatch.setattr(v, "_observe", fake_observe)
    return v


def _events(monkeypatch):
    captured = []
    monkeypatch.setattr(event_bus, "publish", lambda m: captured.append(m))
    return captured


def test_verified_when_in_roi(monkeypatch):
    _events(monkeypatch)
    obs = VisionObservation(role="pastor", person_present=True, person_in_roi=True,
                            subject_dx=0.05, subject_dy=0.05, frame_health="ok")
    v = _verifier(monkeypatch, [obs])
    result = asyncio.run(v.verify("pastor", 1, on_preview=True))
    assert result.status == "verified"


def test_bad_framing_blocks_preview_cut(monkeypatch):
    _events(monkeypatch)
    obs = VisionObservation(role="pastor", person_present=True, person_in_roi=False,
                            subject_dx=0.6, subject_dy=0.1, frame_health="ok")
    v = _verifier(monkeypatch, [obs])
    result = asyncio.run(v.verify("pastor", 1, on_preview=True))
    assert result.status == "bad_framing"
    atem_input = camera_to_atem_input(1)
    assert v.is_blocked(atem_input) is True
    # Operator override clears the block.
    v.clear_block(atem_input)
    assert v.is_blocked(atem_input) is False


def test_bad_framing_override_does_not_block(monkeypatch):
    _events(monkeypatch)
    obs = VisionObservation(role="pastor", person_present=True, person_in_roi=False,
                            subject_dx=0.6, frame_health="ok")
    v = _verifier(monkeypatch, [obs])
    asyncio.run(v.verify("pastor", 1, on_preview=True, override=True))
    assert v.is_blocked(camera_to_atem_input(1)) is False


def test_black_frame_fails_and_blocks(monkeypatch):
    _events(monkeypatch)
    obs = VisionObservation(role="pastor", frame_health="black")
    v = _verifier(monkeypatch, [obs])
    result = asyncio.run(v.verify("pastor", 1, on_preview=True))
    assert result.status == "bad_framing"
    assert v.is_blocked(camera_to_atem_input(1)) is True


def test_empty_stage_waits_then_verifies(monkeypatch):
    monkeypatch.setattr(settings, "ptz_subject_wait_seconds", 3.0)
    _events(monkeypatch)
    absent = VisionObservation(role="pastor", person_present=False, frame_health="ok")
    arrived = VisionObservation(role="pastor", person_present=True, person_in_roi=True,
                                subject_dx=0.02, frame_health="ok")
    # First two probes empty, then the subject steps into ROI.
    v = _verifier(monkeypatch, [absent, absent, arrived, arrived])
    result = asyncio.run(v.verify("pastor", 1, on_preview=True))
    assert result.status == "verified"


def test_empty_stage_resolves_to_subject_absent(monkeypatch):
    monkeypatch.setattr(settings, "ptz_subject_wait_seconds", 2.0)
    _events(monkeypatch)
    absent = VisionObservation(role="pastor", person_present=False, frame_health="ok")
    v = _verifier(monkeypatch, [absent])
    result = asyncio.run(v.verify("pastor", 1, on_preview=True))
    assert result.status == "subject_absent"
    # subject_absent never blocks preview->program by itself.
    assert v.is_blocked(camera_to_atem_input(1)) is False


def test_unverified_ladder(monkeypatch):
    monkeypatch.setattr(settings, "ptz_unverified_max", 3)
    events = _events(monkeypatch)
    v = PtzVerifier()
    v._sleep = _noop_sleep
    monkeypatch.setattr(v, "_observe", lambda *a: None)  # no frame -> unverified
    for _ in range(3):
        result = asyncio.run(v.verify("pastor", 1))
        assert result.status == "unverified"
    assert any(
        e.get("event") == "PERCEPTION_DEGRADED" and e["payload"].get("component") == "ptz_verify"
        for e in events
    )


def test_log_mode_does_not_block(monkeypatch):
    monkeypatch.setattr(settings, "ptz_verify_action", "log")
    _events(monkeypatch)
    obs = VisionObservation(role="pastor", person_present=True, person_in_roi=False,
                            subject_dx=0.6, frame_health="ok")
    v = _verifier(monkeypatch, [obs])
    asyncio.run(v.verify("pastor", 1, on_preview=True))
    assert v.is_blocked(camera_to_atem_input(1)) is False
