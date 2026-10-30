"""Tests for the AudioObserver source-arbitration ladder, cross-channel bleed
suppression and the per-role speaking map (WO-MGX-USB-1 + hardening)."""

import numpy as np
import pytest

import app.audio.audio_observer as ao
from app.audio.audio_observer import AudioObserver, frame_rms_db
from app.audio.silero_vad import SileroChannelVAD
from app.audio.vad import ChannelVAD
from app.config import settings
from app.domain.service_context import service_context
from app.events.bus import event_bus


def _set_setting(monkeypatch, name, value):
    """Override a Settings value; tolerates fields not yet declared in app.config."""
    if name in type(settings).model_fields:
        monkeypatch.setattr(settings, name, value)
    else:
        monkeypatch.setitem(settings.__dict__, name, value)


@pytest.fixture(autouse=True)
def _hardening_settings(monkeypatch):
    _set_setting(monkeypatch, "audio_bleed_suppress_enabled", True)
    _set_setting(monkeypatch, "audio_bleed_suppress_db", 12.0)
    _set_setting(monkeypatch, "speech_active_db", -40.0)
    _set_setting(monkeypatch, "speech_silence_hold_seconds", 1.5)


def _events(monkeypatch):
    captured = []
    monkeypatch.setattr(event_bus, "publish", lambda msg: captured.append(msg))
    return captured


def _energy_vads(monkeypatch):
    monkeypatch.setattr(
        ao, "make_channel_vad", lambda want_silero: (ChannelVAD(silence_hold_seconds=0.0), "energy")
    )


def _tone(amplitude: float, n: int = 1600) -> np.ndarray:
    # sine of given peak amplitude -> RMS = amplitude / sqrt(2)
    return (amplitude * np.sin(2 * np.pi * 440.0 * np.arange(n) / 16000.0)).astype(np.float32)


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
    assert "usb_enabled" in status and "channels" in status and "speaking_roles" in status
    for role, info in status["channels"].items():
        assert set(info) >= {"channel", "source", "vad_provider", "asr", "last_frame_age", "rms_db", "bleed_suppressed"}


# --- cross-channel bleed suppression --------------------------------------------


def test_frame_rms_db():
    assert frame_rms_db(np.zeros(100, dtype=np.float32)) == -90.0
    assert frame_rms_db(np.ones(100, dtype=np.float32)) == pytest.approx(0.0)
    assert frame_rms_db(np.array([], dtype=np.float32)) == -90.0


def test_quiet_copy_of_louder_channel_is_treated_as_silence(monkeypatch):
    _events(monkeypatch)
    _energy_vads(monkeypatch)
    fed = []
    monkeypatch.setattr(ao.multichannel_transcriber, "feed", lambda *a, **k: fed.append(a))
    obs = AudioObserver()

    loud = _tone(0.3)                       # pastor, ~ -13.5 dBFS
    bleed = _tone(0.3 * 10 ** (-15 / 20))   # same voice via PA on the congregation mic, 15 dB down
    obs._on_usb_frame("pastor", 1, loud, t=1.0)
    obs._on_usb_frame("congregation", 8, bleed, t=1.0)

    assert obs._usb_vads[1].speaking is True
    assert obs._usb_vads[8].speaking is False          # suppressed: above -40 dB but 15 dB under the leader
    assert obs._bleed_suppressed[8] == 1
    assert obs.speaking_roles["congregation"]["speaking"] is False
    # The transcriber saw zeros for the suppressed frame, never the bled audio.
    cong_feed = [a for a in fed if a[0] == "congregation"][0]
    assert not np.any(cong_feed[2])
    assert cong_feed[3] is False
    status = obs.perception_status()
    assert status["channels"]["congregation"]["bleed_suppressed"] == 1


def test_channel_within_margin_is_not_suppressed(monkeypatch):
    _events(monkeypatch)
    _energy_vads(monkeypatch)
    monkeypatch.setattr(ao.multichannel_transcriber, "feed", lambda *a, **k: None)
    obs = AudioObserver()
    obs._on_usb_frame("pastor", 1, _tone(0.3), t=1.0)
    obs._on_usb_frame("liturgist", 2, _tone(0.3 * 10 ** (-6 / 20)), t=1.0)  # only 6 dB down: a real second talker
    assert obs._usb_vads[2].speaking is True
    assert obs._bleed_suppressed.get(2, 0) == 0


def test_bleed_check_only_considers_co_active_window(monkeypatch):
    _events(monkeypatch)
    _energy_vads(monkeypatch)
    monkeypatch.setattr(ao.multichannel_transcriber, "feed", lambda *a, **k: None)
    obs = AudioObserver()
    obs._on_usb_frame("pastor", 1, _tone(0.3), t=1.0)
    # 1 s later the pastor's loud frame is stale: the quiet channel stands on its own.
    obs._on_usb_frame("congregation", 8, _tone(0.3 * 10 ** (-15 / 20)), t=2.0)
    assert obs._usb_vads[8].speaking is True
    assert obs._bleed_suppressed.get(8, 0) == 0


def test_bleed_suppression_can_be_disabled(monkeypatch):
    _set_setting(monkeypatch, "audio_bleed_suppress_enabled", False)
    _events(monkeypatch)
    _energy_vads(monkeypatch)
    monkeypatch.setattr(ao.multichannel_transcriber, "feed", lambda *a, **k: None)
    obs = AudioObserver()
    obs._on_usb_frame("pastor", 1, _tone(0.3), t=1.0)
    obs._on_usb_frame("congregation", 8, _tone(0.3 * 10 ** (-15 / 20)), t=1.0)
    assert obs._usb_vads[8].speaking is True
    assert obs._bleed_suppressed.get(8, 0) == 0


def test_bleed_suppression_feeds_silero_zeros(monkeypatch):
    _events(monkeypatch)
    seen = {}

    def make(want_silero):
        probe = {"frames": []}

        def prob(window):
            probe["frames"].append(window.copy())
            return 0.9 if np.any(window) else 0.0

        vad = SileroChannelVAD(prob_fn=prob, threshold=0.5, silence_hold_seconds=0.0, reset_silence_seconds=5.0)
        seen[id(vad)] = probe
        return vad, "silero"

    monkeypatch.setattr(ao, "make_channel_vad", make)
    monkeypatch.setattr(ao.multichannel_transcriber, "feed", lambda *a, **k: None)
    obs = AudioObserver()
    obs._on_usb_frame("pastor", 1, _tone(0.3), t=1.0)
    obs._on_usb_frame("congregation", 8, _tone(0.3 * 10 ** (-15 / 20)), t=1.0)
    assert obs._usb_vads[1].speaking is True
    assert obs._usb_vads[8].speaking is False
    cong_probe = seen[id(obs._usb_vads[8])]
    assert cong_probe["frames"] and all(not np.any(w) for w in cong_probe["frames"])


def test_silence_level_frames_are_never_suppressed(monkeypatch):
    """A channel already below the speech threshold is plain silence, not 'bleed'."""
    _events(monkeypatch)
    _energy_vads(monkeypatch)
    monkeypatch.setattr(ao.multichannel_transcriber, "feed", lambda *a, **k: None)
    obs = AudioObserver()
    obs._on_usb_frame("pastor", 1, _tone(0.3), t=1.0)
    obs._on_usb_frame("congregation", 8, _tone(0.001), t=1.0)
    assert obs._bleed_suppressed.get(8, 0) == 0


# --- per-role speaking map --------------------------------------------------------


def test_speaking_map_tracks_transitions(monkeypatch):
    events = _events(monkeypatch)
    obs = AudioObserver()
    obs._record(1, "pastor", speaking=True, previous=False, source="usb")
    entry = obs.speaking_roles["pastor"]
    assert entry["speaking"] is True and entry["channel"] == 1 and entry["source"] == "usb"
    started = entry["since"]
    assert entry["last_spoke_at"] == started

    obs._record(1, "pastor", speaking=True, previous=True, source="usb")  # still speaking: no change
    assert obs.speaking_roles["pastor"]["since"] == started
    assert obs.speaking_roles["pastor"]["last_spoke_at"] >= started

    obs._record(1, "pastor", speaking=False, previous=True, source="usb")
    entry = obs.speaking_roles["pastor"]
    assert entry["speaking"] is False
    assert entry["since"] >= started
    assert entry["last_spoke_at"] is not None

    # service_context still receives every observation (compat) and transitions still emit.
    assert service_context.last_observation.speaker_role == "pastor"
    kinds = [e["event"] for e in events]
    assert kinds == ["AUDIO_STARTED", "AUDIO_STOPPED"]


def test_speaking_map_silent_role_has_no_last_spoke(monkeypatch):
    _events(monkeypatch)
    obs = AudioObserver()
    obs._record(8, "congregation", speaking=False, previous=False, source="meter")
    entry = obs.speaking_roles["congregation"]
    assert entry["speaking"] is False and entry["last_spoke_at"] is None
    assert obs.perception_status()["speaking_roles"]["congregation"]["source"] == "meter"
