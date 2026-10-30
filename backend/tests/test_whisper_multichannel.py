"""Tests for MultiChannelTranscriber: role attribution, VAD gating, isolation,
confidence mapping, flush timing, bounded concurrency, singing gate."""

import asyncio
import math
import time
from types import SimpleNamespace

import numpy as np
import pytest

import app.audio.whisper_service as ws
from app.audio.whisper_service import (
    MultiChannelTranscriber,
    WhisperService,
    clean_transcript,
    combine_confidence,
    segment_confidence,
)
from app.config import settings
from app.domain.observations import TranscriptResult
from app.domain.service_context import ServiceContext
from app.events.bus import event_bus


def _set_setting(monkeypatch, name, value):
    """Override a Settings value; tolerates fields not yet declared in app.config."""
    if name in type(settings).model_fields:
        monkeypatch.setattr(settings, name, value)
    else:
        monkeypatch.setitem(settings.__dict__, name, value)


@pytest.fixture(autouse=True)
def _hardening_settings(monkeypatch):
    _set_setting(monkeypatch, "whisper_max_utterance_seconds", 6.0)
    _set_setting(monkeypatch, "whisper_utterance_overlap_seconds", 0.5)
    _set_setting(monkeypatch, "whisper_max_concurrency", 2)
    _set_setting(monkeypatch, "whisper_skip_singing_roles", "")
    _set_setting(monkeypatch, "whisper_min_confidence", 0.25)
    _set_setting(monkeypatch, "whisper_beam_size", 1)
    # Deterministic: synthetic constant-valued test clips are never "singing".
    monkeypatch.setattr(ws, "is_singing", lambda audio, sr: False)


@pytest.mark.parametrize("text", ["You", "you.", " You ", "Thanks for watching!", "Thank you for watching."])
def test_clean_transcript_drops_non_speech_guesses(text):
    assert clean_transcript(text) == ""


@pytest.mark.parametrize(
    "text",
    [
        "[Music]",
        "(applause)",
        "♪ ♪",
        "[BLANK_AUDIO]",
        "Subtitles by the Amara.org community",
        "Transcribed by ESO, translated by —",
        "Please subscribe!",
        "Um.",
        "www.example.com",
    ],
)
def test_clean_transcript_drops_whisper_hallucinations(text):
    assert clean_transcript(text) == ""


def test_clean_transcript_strips_inline_tags_but_keeps_speech():
    assert clean_transcript("[Music] Let us pray.") == "Let us pray."


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

    def __init__(self, confidence: float = 0.9):
        self.confidence = confidence
        self.clips: list[np.ndarray] = []

    def transcribe(self, audio, sample_rate=16000):
        self.clips.append(np.asarray(audio))
        if audio.size and float(audio[0]) == 1.0:  # pastor marker -> failure
            raise RuntimeError("asr backend exploded")
        return TranscriptResult(
            text="hello world", confidence=self.confidence, start_time=0.0, end_time=audio.size / sample_rate
        )


def _speech(value: float, seconds: float = 1.0, sr: int = 16000) -> np.ndarray:
    return np.full(int(sr * seconds), value, dtype=np.float32)


def _events(monkeypatch):
    captured = []
    monkeypatch.setattr(event_bus, "publish", lambda msg: captured.append(msg))
    return captured


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


# --- confidence mapping -------------------------------------------------------


def test_segment_confidence_mapping():
    assert segment_confidence(0.0, 0.0) == pytest.approx(1.0)
    assert segment_confidence(-1.0, 0.0) == pytest.approx(math.exp(-1.0))
    assert segment_confidence(0.0, 0.5) == pytest.approx(0.5)
    assert segment_confidence(-0.3, 0.2) == pytest.approx(math.exp(-0.3) * 0.8)
    assert segment_confidence(None, None) == pytest.approx(1.0)  # backend gave no stats
    assert segment_confidence(float("nan"), 2.0) == pytest.approx(0.0)
    assert 0.0 <= segment_confidence(-5.0, 0.99) < 0.01


def test_combine_confidence_is_duration_weighted():
    segs = [(0.0, 3.0, 0.0, 0.0), (3.0, 4.0, -1.0, 0.0)]  # 3 s perfect + 1 s weak
    assert combine_confidence(segs) == pytest.approx((3 * 1.0 + 1 * math.exp(-1.0)) / 4)
    assert combine_confidence([]) == 0.0


class FakeFasterWhisperModel:
    def __init__(self, segments):
        self.segments = segments
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return iter(self.segments), SimpleNamespace(language="en")


def _svc(model, backend="faster-whisper") -> WhisperService:
    svc = WhisperService.__new__(WhisperService)
    svc._model = model
    svc._backend = backend
    return svc


def test_faster_whisper_confidence_and_decode_options(monkeypatch):
    _set_setting(monkeypatch, "whisper_beam_size", 3)
    model = FakeFasterWhisperModel(
        [
            SimpleNamespace(start=0.0, end=2.0, text=" Let us ", avg_logprob=-0.2, no_speech_prob=0.1),
            SimpleNamespace(start=2.0, end=3.0, text="pray.", avg_logprob=-0.8, no_speech_prob=0.3),
        ]
    )
    result = _svc(model).transcribe(np.zeros(16000 * 3, dtype=np.float32))
    assert result.text == "Let us pray."
    expected = (2.0 * math.exp(-0.2) * 0.9 + 1.0 * math.exp(-0.8) * 0.7) / 3.0
    assert result.confidence == pytest.approx(expected, abs=1e-4)
    assert result.start_time == 0.0 and result.end_time == 3.0
    assert model.kwargs["vad_filter"] is True
    assert model.kwargs["beam_size"] == 3
    assert model.kwargs["condition_on_previous_text"] is False
    assert model.kwargs["language"] == "en"


def test_faster_whisper_empty_output_has_zero_confidence():
    result = _svc(FakeFasterWhisperModel([])).transcribe(np.zeros(16000, dtype=np.float32))
    assert result.text == "" and result.confidence == 0.0


def test_openai_whisper_confidence_from_segments():
    class Model:
        def transcribe(self, audio, **kwargs):
            self.kwargs = kwargs
            return {
                "text": " hello ",
                "segments": [{"start": 0.0, "end": 1.0, "text": " hello ", "avg_logprob": -0.5, "no_speech_prob": 0.0}],
            }

    model = Model()
    result = _svc(model, backend="openai-whisper").transcribe(np.zeros(16000, dtype=np.float32))
    assert result.text == "hello"
    assert result.confidence == pytest.approx(math.exp(-0.5), abs=1e-4)
    assert model.kwargs["condition_on_previous_text"] is False
    assert model.kwargs["beam_size"] == 1


def test_low_confidence_transcripts_are_dropped(monkeypatch):
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    monkeypatch.setattr(ws, "get_whisper_service", lambda: FakeWhisper(confidence=0.1))
    events = _events(monkeypatch)

    t = MultiChannelTranscriber(roles={"pastor"})
    t.feed("pastor", 1, _speech(0.5), speaking=True, t=0.0)
    t.feed("pastor", 1, _speech(0.0, 0.1), speaking=False, t=1.0)
    assert len(list(ctx.transcript)) == 0
    assert t.dropped_low_confidence == 1
    assert not any(e["event"] == "TRANSCRIPT" for e in events)


# --- payload -------------------------------------------------------------------


def test_transcript_event_payload_fields(monkeypatch):
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    monkeypatch.setattr(ws, "get_whisper_service", lambda: FakeWhisper(confidence=0.77))
    events = _events(monkeypatch)

    t = MultiChannelTranscriber(roles={"pastor"})
    t.feed("pastor", 1, _speech(0.5, 2.0), speaking=True, t=10.0)
    t.feed("pastor", 1, _speech(0.0, 0.1), speaking=False, t=12.0)

    transcripts = [e for e in events if e["event"] == "TRANSCRIPT"]
    assert len(transcripts) == 1
    payload = transcripts[0]["payload"]
    assert payload["role"] == "pastor" and payload["channel"] == 1
    assert payload["text"] == "hello world"
    assert payload["confidence"] == pytest.approx(0.77)
    assert payload["t_start"] == 10.0 and payload["t_end"] == 12.0
    assert payload["duration_ms"] == 2000
    assert payload["is_singing"] is False
    assert ctx.transcript[-1].text == "hello world"
    assert ctx.last_observation.confidence == pytest.approx(0.77)


# --- flush timing ---------------------------------------------------------------


def test_long_utterance_flushes_at_max_window_with_overlap(monkeypatch):
    _set_setting(monkeypatch, "whisper_max_utterance_seconds", 2.0)
    _set_setting(monkeypatch, "whisper_utterance_overlap_seconds", 0.5)
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    fake = FakeWhisper()
    monkeypatch.setattr(ws, "get_whisper_service", lambda: fake)
    events = _events(monkeypatch)

    t = MultiChannelTranscriber(roles={"pastor"})
    # 3 s of continuous speech in 0.5 s frames, then silence.
    for i in range(6):
        t.feed("pastor", 1, _speech(0.5, 0.5), speaking=True, t=i * 0.5)
        if i < 3:
            assert t.flushes == 0  # nothing transcribed until the window fills
    assert t.flushes == 1  # at 2.0 s, long before the old 15 s cap
    t.feed("pastor", 1, _speech(0.0, 0.1), speaking=False, t=3.0)
    assert t.flushes == 2

    transcripts = [e["payload"] for e in events if e["event"] == "TRANSCRIPT"]
    assert [(p["t_start"], p["t_end"]) for p in transcripts] == [(0.0, 2.0), (1.5, 3.0)]
    assert [c.size for c in fake.clips] == [2 * 16000, int(1.5 * 16000)]  # 0.5 s overlap carried


def test_pause_flush_follows_vad_stop(monkeypatch):
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    monkeypatch.setattr(ws, "get_whisper_service", lambda: FakeWhisper())

    t = MultiChannelTranscriber(roles={"pastor"})
    t.feed("pastor", 1, _speech(0.5, 0.5), speaking=True, t=0.0)
    t.feed("pastor", 1, _speech(0.5, 0.5), speaking=True, t=0.5)
    assert t.flushes == 0
    t.feed("pastor", 1, _speech(0.0, 0.1), speaking=False, t=1.0)  # VAD pause -> immediate flush
    assert t.flushes == 1
    assert len(list(ctx.transcript)) == 1


def test_too_short_utterance_is_ignored(monkeypatch):
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    monkeypatch.setattr(ws, "get_whisper_service", lambda: FakeWhisper())
    t = MultiChannelTranscriber(roles={"pastor"})
    t.feed("pastor", 1, _speech(0.5, 0.2), speaking=True, t=0.0)
    t.feed("pastor", 1, _speech(0.0, 0.1), speaking=False, t=0.2)
    assert t.flushes == 0 and len(list(ctx.transcript)) == 0


# --- singing gate ---------------------------------------------------------------


def test_singing_roles_skip_whisper_when_configured(monkeypatch):
    _set_setting(monkeypatch, "whisper_skip_singing_roles", "vocalist")
    monkeypatch.setattr(ws, "is_singing", lambda audio, sr: True)
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    fake = FakeWhisper()
    monkeypatch.setattr(ws, "get_whisper_service", lambda: fake)
    events = _events(monkeypatch)

    t = MultiChannelTranscriber(roles={"vocalist", "pastor"})
    t.feed("vocalist", 4, _speech(0.5), speaking=True, t=0.0)
    t.feed("vocalist", 4, _speech(0.0, 0.1), speaking=False, t=1.0)
    t.feed("pastor", 1, _speech(0.5), speaking=True, t=0.0)
    t.feed("pastor", 1, _speech(0.0, 0.1), speaking=False, t=1.0)

    assert len(fake.clips) == 1  # vocalist never reached Whisper
    assert t.skipped_singing == {"vocalist": 1}
    transcripts = [e["payload"] for e in events if e["event"] == "TRANSCRIPT"]
    assert [p["role"] for p in transcripts] == ["pastor"]
    assert transcripts[0]["is_singing"] is True  # gate off for pastor, but flagged


def test_singing_gate_off_by_default_keeps_lyrics_for_song_follower(monkeypatch):
    monkeypatch.setattr(ws, "is_singing", lambda audio, sr: True)
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)
    monkeypatch.setattr(ws, "get_whisper_service", lambda: FakeWhisper())
    events = _events(monkeypatch)

    t = MultiChannelTranscriber(roles={"congregation"})
    t.feed("congregation", 8, _speech(0.5), speaking=True, t=0.0)
    t.feed("congregation", 8, _speech(0.0, 0.1), speaking=False, t=1.0)
    transcripts = [e["payload"] for e in events if e["event"] == "TRANSCRIPT"]
    assert len(transcripts) == 1 and transcripts[0]["is_singing"] is True


def test_is_singing_heuristic_on_real_signals(monkeypatch):
    monkeypatch.undo()  # use the real classifier
    sr = 16000
    t = np.arange(sr * 2) / sr
    sustained = (0.4 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)  # steady pitch
    assert ws.is_singing(sustained, sr) is True
    rng = np.random.default_rng(0)
    noise = (0.3 * rng.standard_normal(sr * 2)).astype(np.float32)
    assert ws.is_singing(noise, sr) is False
    assert ws.is_singing(np.zeros(sr, dtype=np.float32), sr) is False


# --- concurrency ------------------------------------------------------------------


async def test_whisper_concurrency_is_bounded(monkeypatch):
    _set_setting(monkeypatch, "whisper_max_concurrency", 1)
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)

    class SlowWhisper:
        available = True

        def __init__(self):
            self.active = 0
            self.peak = 0

        def transcribe(self, audio, sample_rate=16000):
            self.active += 1
            self.peak = max(self.peak, self.active)
            time.sleep(0.05)
            self.active -= 1
            return TranscriptResult(text="hello", confidence=0.9, start_time=0.0, end_time=1.0)

    slow = SlowWhisper()
    monkeypatch.setattr(ws, "get_whisper_service", lambda: slow)

    t = MultiChannelTranscriber(roles={"pastor", "liturgist", "vocalist"})
    for role, ch in (("pastor", 1), ("liturgist", 2), ("vocalist", 4)):
        t.feed(role, ch, _speech(0.5), speaking=True, t=0.0)
        t.feed(role, ch, _speech(0.0, 0.1), speaking=False, t=1.0)
    assert len(t._tasks) == 3  # references retained, not fire-and-forget
    assert t.status()["pending"] == 3
    await asyncio.gather(*list(t._tasks))
    assert slow.peak == 1
    assert len(list(ctx.transcript)) == 3
    assert len(t._tasks) == 0


async def test_semaphore_allows_configured_parallelism(monkeypatch):
    _set_setting(monkeypatch, "whisper_max_concurrency", 2)
    ctx = ServiceContext()
    monkeypatch.setattr(ws, "service_context", ctx)

    class SlowWhisper:
        available = True
        active = 0
        peak = 0

        def transcribe(self, audio, sample_rate=16000):
            SlowWhisper.active += 1
            SlowWhisper.peak = max(SlowWhisper.peak, SlowWhisper.active)
            time.sleep(0.1)
            SlowWhisper.active -= 1
            return TranscriptResult(text="hello", confidence=0.9, start_time=0.0, end_time=1.0)

    monkeypatch.setattr(ws, "get_whisper_service", lambda: SlowWhisper())
    t = MultiChannelTranscriber(roles={"pastor", "liturgist", "vocalist"})
    for role, ch in (("pastor", 1), ("liturgist", 2), ("vocalist", 4)):
        t.feed(role, ch, _speech(0.5), speaking=True, t=0.0)
        t.feed(role, ch, _speech(0.0, 0.1), speaking=False, t=1.0)
    await asyncio.gather(*list(t._tasks))
    assert SlowWhisper.peak == 2
