"""Tests for SileroChannelVAD and the loud energy fallback (WO-MGX-USB-1 + hardening)."""

import sys
import time
import types

import numpy as np
import pytest

import app.audio.silero_vad as sv
from app.audio.silero_vad import SileroChannelVAD, make_channel_vad
from app.audio.vad import ChannelVAD
from app.config import settings
from app.events.bus import event_bus


def _set_setting(monkeypatch, name, value):
    """Override a Settings value; tolerates fields not yet declared in app.config."""
    if name in type(settings).model_fields:
        monkeypatch.setattr(settings, name, value)
    else:
        monkeypatch.setitem(settings.__dict__, name, value)


@pytest.fixture(autouse=True)
def _hardening_settings(monkeypatch):
    _set_setting(monkeypatch, "vad_silero_reset_silence_seconds", 3.0)
    _set_setting(monkeypatch, "vad_silero_threshold", 0.5)


def _frame(value: float, n: int = 512) -> np.ndarray:
    return np.full(n, value, dtype=np.float32)


class FakeProb:
    """Stateful stand-in for one Silero model instance."""

    created = 0

    def __init__(self, prob: float = 0.9):
        FakeProb.created += 1
        self.id = FakeProb.created
        self.prob = prob
        self.calls = 0
        self.resets = 0

    def __call__(self, window):
        self.calls += 1
        return self.prob

    def reset(self):
        self.resets += 1


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


# --- hardening: one model per channel + reset on silence ------------------------


def test_each_channel_gets_its_own_model_instance(monkeypatch):
    monkeypatch.setattr(sv, "_load_silero_prob_fn", lambda: FakeProb())
    vad1, p1 = make_channel_vad(want_silero=True)
    vad2, p2 = make_channel_vad(want_silero=True)
    assert p1 == p2 == "silero"
    assert isinstance(vad1, SileroChannelVAD) and isinstance(vad2, SileroChannelVAD)
    assert vad1.prob_fn is not vad2.prob_fn
    assert vad1.prob_fn.id != vad2.prob_fn.id

    # Feeding one channel never touches the other channel's model.
    vad1.update(_frame(0.2))
    assert vad1.prob_fn.calls == 1
    assert vad2.prob_fn.calls == 0


def test_model_state_reset_after_sustained_silence():
    prob = FakeProb(0.9)
    vad = SileroChannelVAD(prob_fn=prob, threshold=0.5, silence_hold_seconds=0.05, reset_silence_seconds=0.1)
    vad.update(_frame(0.2))
    assert vad.speaking is True
    assert prob.resets == 0

    prob.prob = 0.0
    vad.update(_frame(0.2))  # silence begins; hold not elapsed, no reset yet
    assert prob.resets == 0
    time.sleep(0.15)
    vad.update(_frame(0.2))  # hold elapsed -> not speaking; silence >= reset window -> reset
    assert vad.speaking is False
    assert prob.resets == 1
    assert vad.resets == 1

    # Continued silence does not reset again; the next utterance re-arms it.
    vad.update(_frame(0.2))
    assert prob.resets == 1
    prob.prob = 0.9
    vad.update(_frame(0.2))
    prob.prob = 0.0
    vad.update(_frame(0.2))  # hold starts
    time.sleep(0.15)
    vad.update(_frame(0.2))  # hold elapsed + reset window elapsed
    assert prob.resets == 2


def test_no_reset_before_any_speech():
    prob = FakeProb(0.0)
    vad = SileroChannelVAD(prob_fn=prob, threshold=0.5, silence_hold_seconds=0.01, reset_silence_seconds=0.0)
    for _ in range(5):
        vad.update(_frame(0.0))
    assert prob.resets == 0


def test_reset_fn_is_optional():
    vad = SileroChannelVAD(prob_fn=lambda w: 0.9, threshold=0.5, silence_hold_seconds=0.01, reset_silence_seconds=0.0)
    vad.update(_frame(0.2))
    time.sleep(0.02)
    vad.update(_frame(0.2))
    vad.reset()  # no reset_fn: must not raise
    assert vad.resets == 0


def test_factory_prefers_bundled_silero_vad_package(monkeypatch):
    """The pip package (offline weights) is used before torch.hub."""
    built = []

    class FakeModel:
        def __init__(self):
            built.append(self)

        def eval(self):
            pass

        def reset_states(self):
            pass

    fake_pkg = types.ModuleType("silero_vad")
    fake_pkg.load_silero_vad = lambda **kw: FakeModel()
    monkeypatch.setitem(sys.modules, "silero_vad", fake_pkg)
    hub_calls = []
    fake_torch = types.ModuleType("torch")
    fake_torch.hub = types.SimpleNamespace(load=lambda **kw: hub_calls.append(kw) or (FakeModel(), None))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    sv._silero_model_factory.cache_clear()
    try:
        factory = sv._silero_model_factory()
        a, b = factory(), factory()
        assert a is not b  # a fresh instance per call -> per channel
        assert hub_calls == []
        fn1 = sv._load_silero_prob_fn()
        fn2 = sv._load_silero_prob_fn()
        assert fn1._model is not fn2._model
        assert callable(fn1.reset)
    finally:
        sv._silero_model_factory.cache_clear()
