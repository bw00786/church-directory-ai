"""Whisper speech-to-text as a standalone service.

Produces transcripts for audio that actually has PCM available: the MGX16 USB
MAIN per-channel stream (``MultiChannelTranscriber`` below) and the legacy
local mic/line-in capture in app.identity.audio_capture. It is structured as
a generic ``transcribe(audio_chunk)`` service so it can be pointed at any raw
audio source without changing callers.

Lazily imports faster-whisper (preferred) or openai-whisper; if neither is
installed, or ENABLE_WHISPER is false, ``transcribe()`` returns None so the
rest of the pipeline degrades gracefully to VAD-only observations.

Confidence mapping (both backends expose per-segment ``avg_logprob`` and
``no_speech_prob``)::

    segment_confidence = clip(exp(avg_logprob), 0, 1) * (1 - no_speech_prob)
    utterance_confidence = duration-weighted mean of segment_confidence

``exp(avg_logprob)`` is the geometric-mean token probability (≈0.9 for clean
speech, ≈0.3 for noise/hallucination); ``no_speech_prob`` scales it down when
the decoder itself thinks the window is not speech. Lines below
``WHISPER_MIN_CONFIDENCE`` are dropped before they reach the service context.
"""

from __future__ import annotations

import asyncio
import math
import re
from functools import lru_cache
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np

from app.config import settings
from app.domain.observations import AudioObservation, TranscriptResult
from app.domain.service_context import service_context
from app.events.bus import event_bus
from app.identity.voice import classify_vocal_activity
from app.logging_config import get_logger

logger = get_logger(__name__)


# --- Confidence --------------------------------------------------------------


def segment_confidence(avg_logprob: Optional[float], no_speech_prob: Optional[float]) -> float:
    """Map Whisper's per-segment stats to [0, 1]. See module docstring."""
    lp = 0.0 if avg_logprob is None or not math.isfinite(avg_logprob) else float(avg_logprob)
    token_prob = min(1.0, max(0.0, math.exp(min(lp, 0.0))))
    nsp = 0.0 if no_speech_prob is None or not math.isfinite(no_speech_prob) else float(no_speech_prob)
    nsp = min(1.0, max(0.0, nsp))
    return token_prob * (1.0 - nsp)


def combine_confidence(segments: Iterable[Tuple[float, float, Optional[float], Optional[float]]]) -> float:
    """Duration-weighted mean of segment confidences; (start, end, avg_logprob, no_speech_prob)."""
    total_w = 0.0
    acc = 0.0
    for start, end, avg_logprob, no_speech_prob in segments:
        w = max(float(end) - float(start), 1e-3)
        acc += w * segment_confidence(avg_logprob, no_speech_prob)
        total_w += w
    return acc / total_w if total_w > 0 else 0.0


class WhisperService:
    """Transcribes PCM audio chunks. No-op if whisper isn't installed/enabled."""

    def __init__(self) -> None:
        self._model = None
        self._backend: Optional[str] = None
        if settings.enable_whisper:
            self._model, self._backend = self._load_model()

    def _load_model(self):
        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(
                settings.whisper_model_size,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
            logger.info("Whisper backend loaded", backend="faster-whisper", model=settings.whisper_model_size)
            return model, "faster-whisper"
        except ImportError:
            pass
        try:
            import whisper

            model = whisper.load_model(settings.whisper_model_size)
            logger.info("Whisper backend loaded", backend="openai-whisper", model=settings.whisper_model_size)
            return model, "openai-whisper"
        except ImportError:
            logger.warning(
                "ENABLE_WHISPER=true but neither faster-whisper nor openai-whisper is installed; "
                "transcription disabled"
            )
            return None, None

    @property
    def available(self) -> bool:
        return self._model is not None

    @staticmethod
    def _beam_size() -> int:
        return max(1, int(settings.whisper_beam_size))

    def transcribe(self, audio_chunk: np.ndarray, sample_rate: int = 16000) -> Optional[TranscriptResult]:
        """Transcribe one PCM float32 audio segment. Returns None if unavailable."""
        if not self.available:
            return None

        audio = np.ascontiguousarray(np.asarray(audio_chunk, dtype=np.float32).reshape(-1))
        chunk_seconds = audio.size / float(sample_rate)
        try:
            if self._backend == "faster-whisper":
                segments, _info = self._model.transcribe(
                    audio,
                    language="en",
                    beam_size=self._beam_size(),
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                stats = [
                    (
                        float(getattr(seg, "start", 0.0)),
                        float(getattr(seg, "end", chunk_seconds)),
                        getattr(seg, "avg_logprob", None),
                        getattr(seg, "no_speech_prob", None),
                        str(getattr(seg, "text", "")).strip(),
                    )
                    for seg in segments
                ]
            else:  # openai-whisper
                result = self._model.transcribe(
                    audio,
                    language="en",
                    fp16=False,
                    beam_size=self._beam_size(),
                    condition_on_previous_text=False,
                )
                stats = [
                    (
                        float(seg.get("start", 0.0)),
                        float(seg.get("end", chunk_seconds)),
                        seg.get("avg_logprob"),
                        seg.get("no_speech_prob"),
                        str(seg.get("text", "")).strip(),
                    )
                    for seg in (result.get("segments") or [])
                ]
                if not stats and result.get("text"):
                    stats = [(0.0, chunk_seconds, None, None, str(result["text"]).strip())]
        except Exception:
            logger.exception("Whisper transcription failed")
            return None

        return self._to_result(stats, chunk_seconds)

    @staticmethod
    def _to_result(
        stats: Sequence[Tuple[float, float, Optional[float], Optional[float], str]], chunk_seconds: float
    ) -> TranscriptResult:
        text = " ".join(s[4] for s in stats if s[4]).strip()
        if not text:
            return TranscriptResult(text="", confidence=0.0, start_time=0.0, end_time=chunk_seconds)
        confidence = combine_confidence((s[0], s[1], s[2], s[3]) for s in stats if s[4])
        return TranscriptResult(
            text=text,
            confidence=round(confidence, 4),
            start_time=min(s[0] for s in stats),
            end_time=max(s[1] for s in stats),
        )


@lru_cache(maxsize=1)
def get_whisper_service() -> WhisperService:
    return WhisperService()


# --- Multichannel, per-role, VAD-gated transcription (WO-MGX-USB-1) ---------

MIN_UTTERANCE_SECONDS = 0.4

# Whisper's usual output for silence/noise/music. "thank you" alone is excluded
# because it's real speech in a service. Keys are lower-case letters+spaces only.
_NON_SPEECH_GUESSES = {
    "you",
    "thanks for watching",
    "thank you for watching",
    "thank you for listening",
    "thanks for listening",
    "thank you so much for watching",
    "please subscribe",
    "subscribe",
    "like and subscribe",
    "see you next time",
    "see you in the next video",
    "see you in the next one",
    "bye",
    "bye bye",
    "goodbye",
    "the end",
    "so",
    "oh",
    "uh",
    "um",
    "hmm",
    "mm",
    "music",
    "applause",
    "laughter",
    "silence",
    "blank audio",
    "inaudible",
    "foreign",
    "i",
    "a",
    "the",
}
_NON_SPEECH_PREFIXES = (
    "subtitles by",
    "subtitle by",
    "subtitled by",
    "transcribed by",
    "transcription by",
    "translated by",
    "translation by",
    "captions by",
    "captioned by",
    "copyright",
    "www",
    "amara org",
)
_TAG_RE = re.compile(r"[\[\(][^\]\)]*[\]\)]|♪[^♪]*♪|♪")
_MAX_REPEATS = 3


def clean_transcript(text: str) -> str:
    """Drop Whisper's non-speech guesses and cap runaway repeats of the same phrase."""
    stripped = _TAG_RE.sub(" ", text).strip()  # [music], (applause), ♪ ... ♪
    key = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", stripped.lower())).strip()
    if not key or key in _NON_SPEECH_GUESSES or key.startswith(_NON_SPEECH_PREFIXES):
        return ""
    words = stripped.split()
    for n in range(1, 7):
        keys = [re.sub(r"\W+", "", w.lower()) for w in words]
        out: list[str] = []
        i = 0
        while i < len(words):
            chunk = keys[i : i + n]
            count = 1
            if len(chunk) == n and any(chunk):
                while keys[i + count * n : i + (count + 1) * n] == chunk:
                    count += 1
            if count > _MAX_REPEATS:
                out.extend(words[i : i + n * _MAX_REPEATS])
                i += n * count
            else:
                out.append(words[i])
                i += 1
        words = out
    return " ".join(words)


def _csv_roles(value: str) -> set[str]:
    return {r.strip().lower() for r in (value or "").split(",") if r.strip()}


def whisper_roles() -> set[str]:
    """Roles that receive per-channel ASR: WHISPER_ROLES plus, when the song
    follower is on, SONG_FOLLOWER_ROLES (e.g. the congregation channel)."""
    roles = _csv_roles(settings.whisper_roles)
    if settings.song_follower_enabled:
        roles |= _csv_roles(settings.song_follower_roles)
    return roles


def is_singing(audio: np.ndarray, sample_rate: int) -> bool:
    """Pitch-stability heuristic (app.identity.voice) on a level-normalised copy."""
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size < sample_rate // 4:
        return False
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak <= 1e-6:
        return False
    try:
        kind, _ = classify_vocal_activity(arr * (0.5 / peak), sample_rate)
    except Exception:  # noqa: BLE001
        return False
    return kind == "singing"


class MultiChannelTranscriber:
    """Accumulates VAD-gated speech per role and transcribes each independently.

    ``feed`` is called per USB frame with the channel's current VAD state; speech
    is buffered while a role is speaking and flushed (transcribed) when the VAD
    reports a pause (``SPEECH_SILENCE_HOLD_SECONDS``) **or** the utterance reaches
    ``WHISPER_MAX_UTTERANCE_SECONDS`` — in which case the last
    ``WHISPER_UTTERANCE_OVERLAP_SECONDS`` are carried into the next utterance so a
    word split at the boundary is still heard whole. Whisper runs in worker
    threads bounded by ``WHISPER_MAX_CONCURRENCY``. A Whisper failure on one role
    marks only that role degraded — other roles keep transcribing (WO section 6).
    Silence is never streamed into Whisper.

    Every flushed utterance is classified speech/singing; roles listed in
    ``WHISPER_SKIP_SINGING_ROLES`` skip Whisper while singing (saves CPU, avoids
    lyric "transcripts" as sermon text). Off by default so the song follower
    still receives lyrics; ``is_singing`` is published on the TRANSCRIPT event.
    """

    def __init__(self, roles: Optional[set[str]] = None, sample_rate: int = 16000):
        self.roles = roles if roles is not None else whisper_roles()
        self.sample_rate = sample_rate
        self._buffers: dict[str, list[np.ndarray]] = {}
        self._start_ts: dict[str, float] = {}
        self._prev_speaking: dict[str, bool] = {}
        self._failed_roles: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._semaphore_size: Optional[int] = None
        self.skipped_singing: dict[str, int] = {}
        self.dropped_low_confidence = 0
        self.flushes = 0

    # -- settings (read lazily so import never depends on them) -----------------
    @property
    def max_utterance_seconds(self) -> float:
        return float(settings.whisper_max_utterance_seconds)

    @property
    def overlap_seconds(self) -> float:
        return max(0.0, float(settings.whisper_utterance_overlap_seconds))

    @property
    def max_concurrency(self) -> int:
        return max(1, int(settings.whisper_max_concurrency))

    @property
    def min_confidence(self) -> float:
        return float(settings.whisper_min_confidence)

    @property
    def skip_singing_roles(self) -> set[str]:
        return _csv_roles(settings.whisper_skip_singing_roles)

    def transcribes(self, role: str) -> bool:
        return role in self.roles

    # -- buffering ----------------------------------------------------------------
    def feed(self, role: str, channel: int, pcm: np.ndarray, speaking: bool, t: float) -> None:
        """``t`` is the monotonic-domain time of the first sample in ``pcm``."""
        if role not in self.roles:
            return
        arr = np.asarray(pcm, dtype=np.float32).reshape(-1)
        prev = self._prev_speaking.get(role, False)
        if speaking:
            if not prev or role not in self._buffers:
                self._start_ts[role] = t
                self._buffers[role] = []
            self._buffers[role].append(arr)
            total = sum(b.size for b in self._buffers[role])
            if total >= self.max_utterance_seconds * self.sample_rate:
                self._flush(role, channel, t + arr.size / self.sample_rate, keep_overlap=True)
        elif prev and role in self._buffers:
            self._flush(role, channel, t)
        self._prev_speaking[role] = speaking

    def _flush(self, role: str, channel: int, t_end: float, keep_overlap: bool = False) -> None:
        chunks = self._buffers.pop(role, [])
        t_start = self._start_ts.pop(role, t_end)
        if not chunks:
            return
        audio = np.concatenate(chunks)
        if keep_overlap:
            n_keep = int(self.overlap_seconds * self.sample_rate)
            if 0 < n_keep < audio.size:
                self._buffers[role] = [audio[-n_keep:].copy()]
                self._start_ts[role] = t_end - n_keep / self.sample_rate
            else:
                self._buffers[role] = []
                self._start_ts[role] = t_end
        if audio.size < MIN_UTTERANCE_SECONDS * self.sample_rate:
            return
        self.flushes += 1
        self._schedule(role, channel, audio, t_start, t_end)

    def _schedule(self, role, channel, audio, t_start, t_end) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(self._transcribe_async(role, channel, audio, t_start, t_end))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        else:
            result, singing = self._run_whisper(role, audio)
            self._emit(role, channel, result, t_start, t_end, singing)

    def _get_semaphore(self) -> asyncio.Semaphore:
        size = self.max_concurrency
        if self._semaphore is None or self._semaphore_size != size:
            self._semaphore = asyncio.Semaphore(size)
            self._semaphore_size = size
        return self._semaphore

    async def _transcribe_async(self, role, channel, audio, t_start, t_end) -> None:
        async with self._get_semaphore():
            result, singing = await asyncio.to_thread(self._run_whisper, role, audio)
        self._emit(role, channel, result, t_start, t_end, singing)

    # -- worker thread ------------------------------------------------------------
    def _run_whisper(self, role: str, audio: np.ndarray) -> Tuple[Optional[TranscriptResult], bool]:
        singing = is_singing(audio, self.sample_rate)
        if singing and role in self.skip_singing_roles:
            self.skipped_singing[role] = self.skipped_singing.get(role, 0) + 1
            return None, True
        try:
            return get_whisper_service().transcribe(audio, self.sample_rate), singing
        except Exception:
            logger.exception("Whisper failed for role; degrading it to VAD-only", role=role)
            self._failed_roles.add(role)
            return None, singing

    # -- publish ------------------------------------------------------------------
    def _emit(
        self,
        role,
        channel,
        result: Optional[TranscriptResult],
        t_start,
        t_end,
        singing: bool = False,
    ) -> None:
        text = clean_transcript(result.text) if result is not None else ""
        if not text:
            return
        confidence = float(result.confidence)
        if confidence < self.min_confidence:
            self.dropped_low_confidence += 1
            logger.debug("Transcript dropped: low confidence", role=role, confidence=confidence)
            return
        duration_ms = int(max(0.0, t_end - t_start) * 1000)
        service_context.record_audio(
            AudioObservation(
                channel=channel,
                speaker_role=role,
                speaking=True,
                transcript=text,
                confidence=confidence,
                duration_ms=duration_ms,
            )
        )
        event_bus.publish(
            {
                "event": "TRANSCRIPT",
                "payload": {
                    "role": role,
                    "channel": channel,
                    "text": text,
                    "confidence": confidence,
                    "t_start": t_start,
                    "t_end": t_end,
                    "duration_ms": duration_ms,
                    "is_singing": bool(singing),
                },
            }
        )

    def status(self) -> dict:
        return {
            "roles": sorted(self.roles),
            "failed_roles": sorted(self._failed_roles),
            "asr_available": get_whisper_service().available,
            "pending": len(self._tasks),
            "skipped_singing": dict(self.skipped_singing),
            "dropped_low_confidence": self.dropped_low_confidence,
        }


# Module-level singleton
multichannel_transcriber = MultiChannelTranscriber()
