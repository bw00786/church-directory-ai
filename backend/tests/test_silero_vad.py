"""Tests for SileroChannelVAD and the loud energy fallback (WO-MGX-USB-1)."""

import time

import numpy as np

import app.audio.silero_vad as sv
from app.audio.silero_vad import SileroChannelVAD, make_channel_vad
from app.audio.vad import ChannelVAD
from app.events.bus import event_bus


def _frame(value: float, n: int = 512) -> np.ndarray:
    return np.full(n, value, dtype=np.float32)


def test_silero_speaks_when_prob_above_threshold():
    vad = SileroChannelVAD(prob_fn=lambda w: 0.9, threshold=0.5, silence_hold_seconds=0.05)
    state = vad.update(_frame(0.2))
    assert state.speaking is True
    assert vad.speaking is True


def test_silero_stays_silent_on_noise_below_threshold():
    vad = SileroChannelVAD(prob_fn=lambda w: 0.1, threshold=0.5, silence_hold_seconds=0.05)
    state = vad.update(_frame(0.2))
    assert state.speaking is False


def test_silero_honors_silence_hold():
    prob = {"v": 0.9}
    vad = SileroChannelVAD(prob_fn=lambda w: prob["v"], threshold=0.5, silence_hold_seconds=0.1)
    vad.update(_frame(0.2))
    assert vad.speaking is True

    prob["v"] = 0.0
    assert vad.update(_frame(0.2)).speaking is True  # hold not elapsed
    time.sleep(0.15)
    assert vad.update(_frame(0.2)).speaking is False


def test_energy_fallback_when_not_wanting_silero():
    vad, provider = make_channel_vad(want_silero=False)
    assert provider == "energy"
    assert isinstance(vad, ChannelVAD)


def test_silero_load_failure_falls_back_loudly(monkeypatch):
    # Force the model load to fail and capture the emitted event.
    sv._degraded_emitted = False
    monkeypatch.setattr(sv, "_load_silero_prob_fn", lambda: (_ for _ in ()).throw(RuntimeError("no model")))

    events = []
    monkeypatch.setattr(event_bus, "publish", lambda msg: events.append(msg))

    vad, provider = make_channel_vad(want_silero=True)
    assert provider == "energy"
    assert isinstance(vad, ChannelVAD)
    assert any(e.get("event") == "PERCEPTION_DEGRADED" for e in events)
