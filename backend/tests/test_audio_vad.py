"""Tests for ChannelVAD (energy-threshold speaking detection on mixer RMS)."""

import time

from app.audio.vad import ChannelVAD


def test_vad_starts_not_speaking():
    vad = ChannelVAD(active_threshold_db=-40.0, silence_hold_seconds=0.05)
    assert vad.speaking is False


def test_vad_becomes_speaking_immediately_above_threshold():
    vad = ChannelVAD(active_threshold_db=-40.0, silence_hold_seconds=0.05)
    state = vad.update(-10.0)
    assert state.speaking is True
    assert vad.speaking is True


def test_vad_requires_hold_time_before_silence():
    vad = ChannelVAD(active_threshold_db=-40.0, silence_hold_seconds=0.1)
    vad.update(-10.0)
    assert vad.speaking is True

    # Drops below threshold, but hold time hasn't elapsed yet.
    state = vad.update(-90.0)
    assert state.speaking is True

    time.sleep(0.15)
    state = vad.update(-90.0)
    assert state.speaking is False


def test_vad_stays_silent_below_threshold():
    vad = ChannelVAD(active_threshold_db=-40.0, silence_hold_seconds=0.05)
    state = vad.update(-90.0)
    assert state.speaking is False
