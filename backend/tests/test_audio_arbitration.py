"""Tests for the AudioObserver source-arbitration ladder (WO-MGX-USB-1)."""

import numpy as np

import app.audio.audio_observer as ao
from app.audio.audio_observer import AudioObserver
from app.audio.vad import ChannelVAD
from app.domain.service_context import service_context
from app.events.bus import event_bus


def _events(monkeypatch):
    captured = []
    monkeypatch.setattr(event_bus, "publish", lambda msg: captured.append(msg))
    return captured


def test_first_assignment_emits_no_transition(monkeypatch):
    events = _events(monkeypatch)
    obs = AudioObserver()
    obs._arbitrate(1, "usb")
    assert obs._source[1] == "usb"
    assert not any(e.get("event", "").startswith("PERCEPTION") for e in events)


def test_usb_to_meter_emits_degraded(monkeypatch):
    events = _events(monkeypatch)
    obs = AudioObserver()
    obs._arbitrate(1, "usb")   # establish
    obs._arbitrate(1, "meter")  # stall -> fallback
    assert obs._source[1] == "meter"
    assert any(e.get("event") == "PERCEPTION_DEGRADED" for e in events)


def test_meter_to_usb_emits_restored(monkeypatch):
    events = _events(monkeypatch)
    obs = AudioObserver()
    obs._arbitrate(1, "usb")
    obs._arbitrate(1, "meter")
    obs._arbitrate(1, "usb")  # recovery
    assert obs._source[1] == "usb"
    assert any(e.get("event") == "PERCEPTION_RESTORED" for e in events)


def test_channels_are_independent(monkeypatch):
    _events(monkeypatch)
    obs = AudioObserver()
    obs._arbitrate(1, "usb")
    obs._arbitrate(2, "usb")
    obs._arbitrate(1, "meter")
    assert obs._source[1] == "meter"
    assert obs._source[2] == "usb"


def test_usb_frame_records_observation_and_marks_source(monkeypatch):
    _events(monkeypatch)
    # Force the energy VAD so no Silero model is loaded during the test.
    monkeypatch.setattr(ao, "make_channel_vad", lambda want_silero: (ChannelVAD(), "energy"))
    obs = AudioObserver()
    obs._on_usb_frame("pastor", 1, np.zeros(1600, dtype=np.float32), t=0.0)
    assert service_context.last_observation is not None
    assert service_context.last_observation.speaker_role == "pastor"
    assert 1 in obs._usb_vads


def test_perception_status_shape():
    obs = AudioObserver()
    status = obs.perception_status()
    assert "usb_enabled" in status and "channels" in status
    for role, info in status["channels"].items():
        assert set(info) >= {"channel", "source", "vad_provider", "asr", "last_frame_age"}
