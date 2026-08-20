"""Voice feature extraction and lightweight speaker diarization.

Pure numpy signal processing (framed FFT spectral energy bands, their
frame-to-frame variability, and pitch statistics) so this works without any
additional native audio dependencies (no librosa/torch/pyaudio required).
It is real DSP math on the samples given to it, not synthetic/mock output --
the tradeoff is that it is far less accurate than a trained speaker-embedding
model (e.g. an x-vector/ECAPA-TDNN network), so it's paired with
conservative match thresholds, a persistent per-person voice profile that
improves as more samples are enrolled, and an enrollment quality gate (see
`assess_enrollment_quality`) that rejects too-short or mostly-silent clips.
"""

from __future__ import annotations

import time

import numpy as np

_NUM_BANDS = 32
_MIN_HZ = 80.0
_MAX_HZ = 4000.0
_FRAME_MS = 30.0
_HOP_MS = 15.0

MIN_ENROLLMENT_SECONDS = 1.0
MIN_ENROLLMENT_VOICED_RATIO = 0.25


def _band_edges(sample_rate: int) -> np.ndarray:
    max_hz = min(_MAX_HZ, sample_rate / 2.0)
    return np.geomspace(_MIN_HZ, max_hz, _NUM_BANDS + 1)


def _frame_signal(samples: np.ndarray, sample_rate: int) -> list[np.ndarray]:
    frame_len = max(32, int(sample_rate * _FRAME_MS / 1000.0))
    hop_len = max(16, int(sample_rate * _HOP_MS / 1000.0))
    if len(samples) <= frame_len:
        return [samples]
    return [samples[start : start + frame_len] for start in range(0, len(samples) - frame_len + 1, hop_len)]


def _band_energies(frame: np.ndarray, sample_rate: int) -> np.ndarray:
    window = np.hanning(len(frame))
    spectrum = np.abs(np.fft.rfft(frame * window))
    freqs = np.fft.rfftfreq(len(frame), d=1.0 / sample_rate)
    edges = _band_edges(sample_rate)
    bands = np.zeros(_NUM_BANDS, dtype=float)
    for i in range(_NUM_BANDS):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        bands[i] = spectrum[mask].sum() if mask.any() else 0.0
    return np.log1p(bands)


def extract_features(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Speaker-discriminative feature vector: mean + std of framed band
    energies (static + dynamic spectral shape) plus pitch statistics.

    Framing preserves temporal structure that a single whole-buffer FFT
    snapshot would average away, which meaningfully improves discrimination
    between speakers even with these classical (non-neural) features.
    """
    samples = np.asarray(samples, dtype=float)
    if samples.size == 0:
        return np.zeros(_NUM_BANDS * 2 + 3, dtype=float)

    frames = _frame_signal(samples, sample_rate)
    band_matrix = np.stack([_band_energies(frame, sample_rate) for frame in frames])
    mean_bands = band_matrix.mean(axis=0)
    std_bands = band_matrix.std(axis=0)

    pitches = [p for p in (estimate_pitch(frame, sample_rate) for frame in frames) if p is not None]
    voiced_ratio = len(pitches) / max(1, len(frames))
    pitch_mean = float(np.mean(pitches)) if pitches else 0.0
    pitch_std = float(np.std(pitches)) if len(pitches) > 1 else 0.0

    feature = np.concatenate(
        [mean_bands, std_bands, [pitch_mean / 500.0, pitch_std / 100.0, voiced_ratio]]
    )
    norm = np.linalg.norm(feature)
    return feature / norm if norm > 0 else feature


def assess_enrollment_quality(samples: np.ndarray, sample_rate: int) -> tuple[bool, str]:
    """Reject voice enrollment clips too short or too quiet/unvoiced to
    produce a reliable profile (garbage in, garbage matches out)."""
    samples = np.asarray(samples, dtype=float)
    duration = samples.size / max(1, sample_rate)
    if duration < MIN_ENROLLMENT_SECONDS:
        return False, f"clip too short ({duration:.2f}s, need >= {MIN_ENROLLMENT_SECONDS:.1f}s)"

    frames = _frame_signal(samples, sample_rate)
    voiced = sum(1 for frame in frames if estimate_pitch(frame, sample_rate) is not None)
    voiced_ratio = voiced / max(1, len(frames))
    if voiced_ratio < MIN_ENROLLMENT_VOICED_RATIO:
        return False, f"mostly silent/unvoiced (voiced_ratio={voiced_ratio:.2f}); re-record with clearer speech"

    return True, "ok"


def estimate_pitch(samples: np.ndarray, sample_rate: int) -> float | None:
    """Autocorrelation-based fundamental frequency estimate, in Hz."""
    samples = np.asarray(samples, dtype=float)
    if samples.size < 32:
        return None
    samples = samples - samples.mean()
    autocorr = np.correlate(samples, samples, mode="full")[len(samples) - 1 :]
    if autocorr[0] <= 0:
        return None
    autocorr = autocorr / autocorr[0]

    min_lag = int(sample_rate / 400.0)  # ~400 Hz upper bound
    max_lag = min(int(sample_rate / 70.0), len(autocorr) - 1)  # ~70 Hz lower bound
    if min_lag >= max_lag:
        return None

    segment = autocorr[min_lag:max_lag]
    peak_index = int(np.argmax(segment)) + min_lag
    if autocorr[peak_index] < 0.3:
        return None
    return sample_rate / float(peak_index)


def rms_level(samples: np.ndarray) -> float:
    samples = np.asarray(samples, dtype=float)
    return float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0


def classify_vocal_activity(
    samples: np.ndarray, sample_rate: int, silence_rms: float = 0.02
) -> tuple[str, float]:
    """Heuristic singing/speech/silence classification.

    Splits the buffer into sub-windows and checks pitch stability: sustained,
    narrow-band pitch reads as singing; fast-varying or absent pitch with
    speech-level energy reads as speech.
    """
    samples = np.asarray(samples, dtype=float)
    level = rms_level(samples)
    if level < silence_rms:
        return "silence", 1.0 - min(1.0, level / max(silence_rms, 1e-9))

    window_size = max(256, sample_rate // 20)
    pitches = []
    for start in range(0, max(1, len(samples) - window_size), window_size):
        pitch = estimate_pitch(samples[start : start + window_size], sample_rate)
        if pitch is not None:
            pitches.append(pitch)

    if len(pitches) < 2:
        return "speech", 0.55

    pitch_array = np.array(pitches)
    stability = 1.0 - min(1.0, float(np.std(pitch_array) / max(1.0, np.mean(pitch_array))))
    voiced_ratio = len(pitches) / max(1, (len(samples) // window_size))

    if stability >= 0.85 and voiced_ratio >= 0.6:
        return "singing", stability
    return "speech", max(0.5, 1.0 - stability)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


class SpeakerDiarizer:
    """Session-local nearest-centroid clustering for un-enrolled voices.

    This never touches the database -- it only tracks "unknown speaker N"
    clusters for the lifetime of the process (or until `ttl_seconds` of
    silence), so an operator can later enroll a matched cluster's samples
    into the persistent roster if desired.
    """

    def __init__(self, match_threshold: float = 0.75, ttl_seconds: float = 300.0):
        self.match_threshold = match_threshold
        self.ttl_seconds = ttl_seconds
        self._centroids: dict[str, np.ndarray] = {}
        self._counts: dict[str, int] = {}
        self._last_seen: dict[str, float] = {}
        self._next_id = 1

    def identify(self, embedding: np.ndarray) -> tuple[str, float, bool]:
        """Return (provisional_speaker_key, similarity, is_new_cluster)."""
        self._expire()
        best_key, best_score = None, 0.0
        for key, centroid in self._centroids.items():
            score = _cosine(embedding, centroid)
            if score > best_score:
                best_score, best_key = score, key

        now = time.time()
        if best_key is not None and best_score >= self.match_threshold:
            count = self._counts[best_key] + 1
            weight = 1.0 / count
            self._centroids[best_key] = self._centroids[best_key] * (1 - weight) + embedding * weight
            self._counts[best_key] = count
            self._last_seen[best_key] = now
            return best_key, best_score, False

        key = f"unknown_speaker_{self._next_id}"
        self._next_id += 1
        self._centroids[key] = embedding
        self._counts[key] = 1
        self._last_seen[key] = now
        return key, 1.0, True

    def _expire(self) -> None:
        now = time.time()
        stale = [key for key, seen in self._last_seen.items() if now - seen > self.ttl_seconds]
        for key in stale:
            self._centroids.pop(key, None)
            self._counts.pop(key, None)
            self._last_seen.pop(key, None)
