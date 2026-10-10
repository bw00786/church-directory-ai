"""Tests for MultiChannelTranscriber: role attribution, VAD gating, isolation."""

import numpy as np
import pytest

import app.audio.whisper_service as ws
from app.audio.whisper_service import MultiChannelTranscriber, clean_transcript
from app.domain.observations import TranscriptResult
from app.domain.service_context import ServiceContext


@pytest.mark.parametrize("text", ["You", "you.", " You ", "Thanks for watching!", "Thank you for watching."])
def test_clean_transcript_drops_non_speech_guesses(text):
    assert clean_transcript(text) == ""


@pytest.mark.parametrize(
    "text",
    ["Thank you.", "Amen", "Test test 1 2 1 2 test test 1 2 1 2 test test 1 2 test test", "you are welcome"],
)
def test_clean_transcript_keeps_real_speech(text):
    assert clean_transcript(text) == text


def test_clean_transcript_caps_runaway_repetition():
    looped = "Testing Room 2, " * 50 + "Testing Room"
    cleaned = clean_transcript(looped)
    assert cleaned.lower().count("testing room 2") == 3
    assert len(cleaned) < len(looped) / 5


def test_noise_never_reaches_the_service_context(monkeypatch):
    class NoiseWhisper:
        available = True

        def transcribe(self, audio, sample_rate=16000):
            return TranscriptResult(text="You", confidence=1.0, start_time=0.0, end_time=1.0)

    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    monkeypatch.setattr(ws, "get_whisper_service", lambda: NoiseWhisper())

    t = MultiChannelTranscriber(roles={"pastor"})
    t.feed("pastor", 1, _speech(0.5), speaking=True, t=0.0)
    t.feed("pastor", 1, _speech(0.0, 0.1), speaking=False, t=1.0)
    assert len(list(ctx.transcript)) == 0


class FakeWhisper:
    """ASR stub: raises for pastor's marker clip, transcribes everything else."""

    available = True

    def transcribe(self, audio, sample_rate=16000):
        if audio.size and float(audio[0]) == 1.0:  # pastor marker -> failure
            raise RuntimeError("asr backend exploded")
        return TranscriptResult(text="hello world", confidence=0.9, start_time=0.0, end_time=1.0)


def _speech(value: float, seconds: float = 1.0, sr: int = 16000) -> np.ndarray:
    return np.full(int(sr * seconds), value, dtype=np.float32)


def test_role_attribution_records_transcript(monkeypatch):
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    monkeypatch.setattr(ws, "get_whisper_service", lambda: FakeWhisper())

    t = MultiChannelTranscriber(roles={"liturgist"})
    t.feed("liturgist", 2, _speech(0.5), speaking=True, t=0.0)
    t.feed("liturgist", 2, _speech(0.0, 0.1), speaking=False, t=1.0)  # end -> flush

    lines = list(ctx.transcript)
    assert len(lines) == 1
    assert lines[0].speaker_role == "liturgist"
    assert lines[0].text == "hello world"


def test_silence_is_not_transcribed(monkeypatch):
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    monkeypatch.setattr(ws, "get_whisper_service", lambda: FakeWhisper())

    t = MultiChannelTranscriber(roles={"liturgist"})
    t.feed("liturgist", 2, _speech(0.0), speaking=False, t=0.0)
    assert len(list(ctx.transcript)) == 0


def test_non_asr_role_ignored(monkeypatch):
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    monkeypatch.setattr(ws, "get_whisper_service", lambda: FakeWhisper())

    t = MultiChannelTranscriber(roles={"pastor", "liturgist"})
    # congregation is not an ASR role -> feed is a no-op.
    t.feed("congregation", 8, _speech(0.5), speaking=True, t=0.0)
    t.feed("congregation", 8, _speech(0.0, 0.1), speaking=False, t=1.0)
    assert len(list(ctx.transcript)) == 0


def test_single_role_failure_isolated(monkeypatch):
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    monkeypatch.setattr(ws, "get_whisper_service", lambda: FakeWhisper())

    t = MultiChannelTranscriber(roles={"pastor", "liturgist"})
    # Pastor's clip marker (1.0) makes ASR raise; liturgist (0.5) succeeds.
    t.feed("pastor", 1, _speech(1.0), speaking=True, t=0.0)
    t.feed("pastor", 1, _speech(0.0, 0.1), speaking=False, t=1.0)
    t.feed("liturgist", 2, _speech(0.5), speaking=True, t=0.0)
    t.feed("liturgist", 2, _speech(0.0, 0.1), speaking=False, t=1.0)

    roles = [line.speaker_role for line in ctx.transcript]
    assert "liturgist" in roles
    assert "pastor" not in roles
    assert "pastor" in t._failed_roles
