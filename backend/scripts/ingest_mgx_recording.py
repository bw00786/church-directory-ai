"""Ingest an MGX16 microSD multitrack recording into a replay corpus.

Takes a directory of per-track WAV files for one service, maps tracks to roles
via the channel config, runs the same offline VAD (Silero when available, energy
otherwise) using an audio-timeline clock (so results are deterministic, not
wall-clock dependent), and writes:

    recording.json   -- chronological list of AudioObservation-shaped dicts,
                        directly consumable by scripts/replay_service.py
    manifest.json    -- source files, channel map, config hash, durations

With --whisper, each speaking segment is transcribed and attached to that
segment's observation.

Usage:
    python scripts/ingest_mgx_recording.py <recording_dir> [--out DIR] [--whisper]
    # explicit track override:
    python scripts/ingest_mgx_recording.py <dir> --track pastor=TRACK01.WAV
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.audio.usb_capture import _resample_to_16k
from app.audio.yamaha_capture import configured_role_channels
from app.config import settings

TARGET_RATE = 16000
FRAME_SECONDS = 0.1
_SILERO_WINDOW = 512


# -- WAV loading -------------------------------------------------------------
def load_wav_16k_mono(path: Path) -> np.ndarray:
    """Load a WAV file as 16 kHz mono float32. Uses soundfile if available."""
    try:
        import soundfile as sf  # handles 24-bit / float WAV

        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
    except Exception:
        mono, sr = _load_wav_stdlib(path)
    return _resample_to_16k(mono, sr)


def _load_wav_stdlib(path: Path) -> Tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        width = wf.getsampwidth()
        channels = wf.getnchannels()
        raw = wf.readframes(n)
    if width == 2:
        arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        arr = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported WAV sample width {width} bytes in {path.name}")
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    return arr, sr


# -- track -> role mapping ---------------------------------------------------
def resolve_tracks(directory: Path, overrides: Dict[str, str]) -> Dict[str, Path]:
    """Map each role to a WAV file. Refuses (no guessing) if a role is missing."""
    wavs = sorted(p for p in directory.glob("*.wav")) + sorted(directory.glob("*.WAV"))
    by_name = {p.name.lower(): p for p in wavs}
    mapping: Dict[str, Path] = {}

    for rc in configured_role_channels():
        role = rc.role
        if role in overrides:
            candidate = directory / overrides[role]
            if not candidate.exists():
                raise FileNotFoundError(f"track override for {role} not found: {candidate}")
            mapping[role] = candidate
            continue
        match = _match_track(rc.channel, role, wavs, by_name)
        if match is None:
            raise FileNotFoundError(
                f"no track file for role '{role}' (channel {rc.channel}) in {directory}; "
                f"pass --track {role}=<file> explicitly (refusing to guess)"
            )
        mapping[role] = match
    return mapping


def _match_track(channel: int, role: str, wavs: List[Path], by_name: Dict[str, Path]) -> Optional[Path]:
    # 1) role name in filename; 2) zero-padded/plain channel number token.
    for p in wavs:
        if role in p.name.lower():
            return p
    tokens = {f"{channel:02d}", f"{channel:d}", f"track{channel:02d}", f"track{channel:d}"}
    for p in wavs:
        stem = p.stem.lower()
        if stem in tokens or any(stem.endswith(tok) for tok in tokens):
            return p
    return None


# -- offline VAD -------------------------------------------------------------
def _build_prob_fn(want_silero: bool):
    if not want_silero:
        return None
    try:
        from app.audio.silero_vad import _load_silero_prob_fn

        return _load_silero_prob_fn()
    except Exception:
        return None


def _frame_active(window: np.ndarray, prob_fn, threshold: float, energy_db: float) -> bool:
    if prob_fn is not None:
        peak = 0.0
        buf = window
        for start in range(0, max(1, len(buf) - _SILERO_WINDOW + 1), _SILERO_WINDOW):
            peak = max(peak, float(prob_fn(buf[start:start + _SILERO_WINDOW])))
        return peak >= threshold
    rms = float(np.sqrt(np.mean(window ** 2))) if window.size else 0.0
    db = 20.0 * np.log10(max(rms, 1e-10))
    return db > energy_db


def vad_segments(samples: np.ndarray, prob_fn, threshold: float, energy_db: float, hold: float) -> List[Tuple[float, float]]:
    """Deterministic offline VAD using an audio-timeline clock."""
    frame = int(TARGET_RATE * FRAME_SECONDS)
    speaking = False
    seg_start = 0.0
    silence_since: Optional[float] = None
    segments: List[Tuple[float, float]] = []

    for start in range(0, len(samples), frame):
        window = samples[start:start + frame]
        t = start / TARGET_RATE
        active = _frame_active(window, prob_fn, threshold, energy_db)
        if active:
            silence_since = None
            if not speaking:
                speaking, seg_start = True, t
        elif speaking:
            if silence_since is None:
                silence_since = t
            elif t - silence_since >= hold:
                segments.append((seg_start, t))
                speaking, silence_since = False, None

    if speaking:
        segments.append((seg_start, len(samples) / TARGET_RATE))
    return segments


# -- ingest ------------------------------------------------------------------
def ingest(directory: Path, out_dir: Path, overrides: Dict[str, str], do_whisper: bool) -> None:
    tracks = resolve_tracks(directory, overrides)
    want_silero = settings.vad_provider.lower() != "energy"
    prob_fn = _build_prob_fn(want_silero)
    provider = "silero" if prob_fn is not None else "energy"

    role_channel = {rc.role: rc.channel for rc in configured_role_channels()}
    whisper = None
    if do_whisper:
        from app.audio.whisper_service import get_whisper_service

        whisper = get_whisper_service()

    observations: List[dict] = []
    durations: Dict[str, float] = {}

    for role, path in tracks.items():
        samples = load_wav_16k_mono(path)
        durations[role] = round(len(samples) / TARGET_RATE, 3)
        channel = role_channel[role]
        segments = vad_segments(
            samples,
            prob_fn,
            settings.vad_silero_threshold,
            settings.speech_active_db,
            settings.speech_silence_hold_seconds,
        )
        transcribe_role = whisper is not None and role in _whisper_roles()
        for seg_start, seg_end in segments:
            transcript = ""
            if transcribe_role:
                clip = samples[int(seg_start * TARGET_RATE):int(seg_end * TARGET_RATE)]
                result = whisper.transcribe(clip, TARGET_RATE)
                transcript = result.text if result else ""
            observations.append({
                "_t": round(seg_start, 3),
                "channel": channel,
                "speaker_role": role,
                "speaking": True,
                "transcript": transcript,
                "confidence": 1.0,
                "duration_ms": int(round((seg_end - seg_start) * 1000)),
            })
            observations.append({
                "_t": round(seg_end, 3),
                "channel": channel,
                "speaker_role": role,
                "speaking": False,
                "transcript": "",
                "confidence": 0.0,
                "duration_ms": 0,
            })

    # Deterministic chronological order; strip the sort key from the output.
    observations.sort(key=lambda o: (o["_t"], o["speaker_role"], o["speaking"]))
    recording = [{k: v for k, v in o.items() if k != "_t"} for o in observations]

    config_snapshot = {
        "channel_map": role_channel,
        "vad_provider": provider,
        "vad_silero_threshold": settings.vad_silero_threshold,
        "speech_active_db": settings.speech_active_db,
        "speech_silence_hold_seconds": settings.speech_silence_hold_seconds,
        "frame_seconds": FRAME_SECONDS,
        "whisper": do_whisper,
    }
    manifest = {
        "source_files": {role: path.name for role, path in tracks.items()},
        "channel_map": role_channel,
        "durations_seconds": durations,
        "observation_count": len(recording),
        "config_hash": hashlib.sha256(
            json.dumps(config_snapshot, sort_keys=True).encode()
        ).hexdigest(),
        "config": config_snapshot,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recording.json").write_text(json.dumps(recording, indent=2), encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {len(recording)} observations to {out_dir / 'recording.json'}")
    print(f"Manifest: {out_dir / 'manifest.json'} (provider={provider})")


def _whisper_roles() -> set:
    from app.audio.whisper_service import whisper_roles

    return whisper_roles()


def _parse_overrides(items: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--track expects role=file, got: {item}")
        role, filename = item.split("=", 1)
        out[role.strip().lower()] = filename.strip()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest an MGX16 microSD multitrack recording.")
    parser.add_argument("directory", type=Path, help="Directory of per-track WAV files")
    parser.add_argument("--out", type=Path, default=None, help="Output directory (default: input dir)")
    parser.add_argument("--whisper", action="store_true", help="Transcribe speaking segments")
    parser.add_argument("--track", action="append", default=[], help="Explicit role=file mapping")
    args = parser.parse_args()

    if not args.directory.is_dir():
        raise SystemExit(f"not a directory: {args.directory}")
    ingest(args.directory, args.out or args.directory, _parse_overrides(args.track), args.whisper)


if __name__ == "__main__":
    sys.exit(main())
