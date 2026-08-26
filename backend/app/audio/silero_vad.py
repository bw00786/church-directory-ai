"""Silero neural VAD per channel, with a loud energy-VAD fallback (WO-MGX-USB-1).

``SileroChannelVAD`` consumes 16 kHz mono PCM frames and produces the same
speaking/silence transition semantics as the energy ``ChannelVAD`` (shared
``VADState`` + hold time), so ``AudioObserver`` can compose either one.

Silero is lazy-loaded (torch hub) and shared across channels. If it can't be
loaded, ``make_channel_vad`` degrades to the energy detector **loudly** — a
single WARN plus a ``PERCEPTION_DEGRADED`` event on the bus — never silently.
The model is injected as a probability function so the detector is testable
without torch installed.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Callable, Optional, Tuple

import numpy as np

from app.audio.vad import ChannelVAD, VADState
from app.config import settings
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

TARGET_RATE = 16000
_SILERO_WINDOW = 512  # samples per Silero inference at 16 kHz

# Probability function: 16k mono PCM window -> speech probability [0, 1].
ProbFn = Callable[[np.ndarray], float]

_degraded_emitted = False


class SileroChannelVAD:
    """Neural VAD for one channel. update() takes 16 kHz mono PCM frames."""

    provider = "silero"

    def __init__(
        self,
        prob_fn: ProbFn,
        threshold: Optional[float] = None,
        silence_hold_seconds: Optional[float] = None,
    ):
        self._prob_fn = prob_fn
        self.threshold = settings.vad_silero_threshold if threshold is None else threshold
        self.silence_hold_seconds = (
            settings.speech_silence_hold_seconds if silence_hold_seconds is None else silence_hold_seconds
        )
        self._buf = np.zeros(0, dtype=np.float32)
        self._state = VADState(since=time.monotonic())

    def update(self, pcm: np.ndarray) -> VADState:
        """Feed a PCM frame; returns the (possibly updated) speaking state."""
        active = self._frame_is_speech(pcm)
        now = time.monotonic()

        if active:
            self._state._silence_started = None
            if not self._state.speaking:
                self._state = VADState(speaking=True, since=now)
        else:
            if self._state.speaking:
                if self._state._silence_started is None:
                    self._state._silence_started = now
                elif now - self._state._silence_started >= self.silence_hold_seconds:
                    self._state = VADState(speaking=False, since=now)
        return self._state

    def _frame_is_speech(self, pcm: np.ndarray) -> bool:
        self._buf = np.concatenate([self._buf, np.asarray(pcm, dtype=np.float32).reshape(-1)])
        peak = 0.0
        while self._buf.size >= _SILERO_WINDOW:
            window, self._buf = self._buf[:_SILERO_WINDOW], self._buf[_SILERO_WINDOW:]
            try:
                peak = max(peak, float(self._prob_fn(window)))
            except Exception:
                logger.exception("Silero inference failed on window")
                return False
        return peak >= self.threshold

    @property
    def speaking(self) -> bool:
        return self._state.speaking


@lru_cache(maxsize=1)
def _load_silero_prob_fn() -> ProbFn:
    """Load Silero VAD (torch hub) and return a window->probability function.

    Raises on any failure so callers can fall back. Cached so the model loads
    once per process and is shared across channels.
    """
    import torch  # local import: optional heavy dependency

    model, _utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=True
    )
    model.eval()

    def prob_fn(window: np.ndarray) -> float:
        tensor = torch.from_numpy(np.asarray(window, dtype=np.float32))
        with torch.no_grad():
            return float(model(tensor, TARGET_RATE).item())

    logger.info("Silero VAD loaded")
    return prob_fn


def _emit_degraded(reason: str) -> None:
    global _degraded_emitted
    logger.warning("VAD degraded to energy detector", reason=reason)
    if not _degraded_emitted:
        _degraded_emitted = True
        event_bus.publish(
            {"event": "PERCEPTION_DEGRADED", "payload": {"component": "vad", "reason": reason}}
        )


def make_channel_vad(want_silero: bool) -> Tuple[object, str]:
    """Return (vad, provider). Falls back to energy VAD loudly on Silero failure."""
    if not want_silero:
        return ChannelVAD(), "energy"
    try:
        prob_fn = _load_silero_prob_fn()
        return SileroChannelVAD(prob_fn), "silero"
    except Exception as e:  # noqa: BLE001
        _emit_degraded(f"silero load failed: {e}")
        return ChannelVAD(), "energy"


def resolve_provider(usb_active: bool) -> str:
    """Apply the VAD_PROVIDER policy: auto = silero when USB active."""
    provider = settings.vad_provider.lower()
    if provider == "silero":
        return "silero"
    if provider == "energy":
        return "energy"
    return "silero" if usb_active else "energy"  # auto
