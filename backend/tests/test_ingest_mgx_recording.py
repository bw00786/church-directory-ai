"""Tests for the MGX microSD ingest CLI (WO-MGX-USB-1)."""

import json
import wave

import numpy as np
import pytest

from app.config import settings
from app.domain.observations import AudioObservation
from scripts.ingest_mgx_recording import ingest, resolve_tracks


def _write_wav(path, samples, sr=16000):
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data)


def _service_dir(tmp_path, include_congregation=True):
    speech = np.concatenate([np.full(16000, 0.5, dtype=np.float32), np.zeros(32000, dtype=np.float32)])
    silent = np.zeros(48000, dtype=np.float32)
    _write_wav(tmp_path / "TRACK01.wav", speech)      # pastor (ch 1)
    _write_wav(tmp_path / "TRACK02.wav", silent)      # liturgist (ch 2)
    _write_wav(tmp_path / "TRACK04.wav", silent)      # vocalist (ch 4)
    if include_congregation:
        _write_wav(tmp_path / "TRACK08.wav", silent)  # congregation (ch 8)
    return tmp_path


@pytest.fixture(autouse=True)
def _force_energy_vad(monkeypatch):
    # Deterministic, no torch/network model download during tests.
    monkeypatch.setattr(settings, "vad_provider", "energy")


def test_missing_role_track_refuses(tmp_path):
    _service_dir(tmp_path, include_congregation=False)
    with pytest.raises(FileNotFoundError):
        resolve_tracks(tmp_path, {})


def test_ingest_produces_replay_compatible_recording(tmp_path):
    directory = _service_dir(tmp_path)
    out = tmp_path / "out"
    ingest(directory, out, overrides={}, do_whisper=False)

    recording = json.loads((out / "recording.json").read_text())
    assert isinstance(recording, list)
    assert len(recording) >= 2  # at least one start/end for the pastor burst
    # replay_service.py builds AudioObservation(**entry) from each item.
    for entry in recording:
        AudioObservation(**entry)
    assert any(e["speaker_role"] == "pastor" and e["speaking"] for e in recording)


def test_manifest_has_config_hash_and_sources(tmp_path):
    directory = _service_dir(tmp_path)
    out = tmp_path / "out"
    ingest(directory, out, overrides={}, do_whisper=False)

    manifest = json.loads((out / "manifest.json").read_text())
    assert "config_hash" in manifest and len(manifest["config_hash"]) == 64
    assert set(manifest["source_files"]) == {"pastor", "liturgist", "vocalist", "congregation"}
    assert manifest["channel_map"]["pastor"] == settings.mixer_pastor_channel


def test_ingest_is_deterministic(tmp_path):
    directory = _service_dir(tmp_path)
    out1, out2 = tmp_path / "a", tmp_path / "b"
    ingest(directory, out1, overrides={}, do_whisper=False)
    ingest(directory, out2, overrides={}, do_whisper=False)
    assert (out1 / "recording.json").read_text() == (out2 / "recording.json").read_text()
