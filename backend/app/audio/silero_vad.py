"""Silero neural VAD per channel, with a loud energy-VAD fallback (WO-MGX-USB-1).

``SileroChannelVAD`` consumes 16 kHz mono PCM frames and produces the same
speaking/silence transition semantics as the energy ``ChannelVAD`` (shared
``VADState`` + hold time), so ``AudioObserver`` can compose either one.

Silero is **stateful** (LSTM + a small context window carried between 512-
sample inferences), so one model instance must never be shared across
channels — interleaving the pastor's and the congregation's frames through a
single model corrupts both probability streams. ``make_channel_vad`` therefore
builds a fresh model per channel. Weights come from the pip ``silero-vad``
package (bundled, works offline); ``torch.hub`` is only tried if that import
fails. Each detector calls ``reset_states()`` after sustained silence so stale
context from the previous utterance does not bias the next one.

If Silero can't be loaded at all, ``make_channel_vad`` degrades to the energy
detector **loudly** — a single WARN plus a ``PERCEPTION_DEGRADED`` event on
the bus — never silently. The model is injected as a probability function so
the detector is testable without torch installed.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Callable, Optional, Protocol, Tuple

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
ResetFn = Callable[[], None]

_degraded_emitted = False


class StatefulProbFn(Protocol):
    """A per-channel Silero probability function that can clear its LSTM state."""

    def __call__(self, window: np.ndarray) -> float: ...

    def reset(self) -> None: ...


class SileroChannelVAD:
    """Neural VAD for one channel. update() takes 16 kHz mono PCM frames.

    ``prob_fn`` must be private to this detector (Silero carries state). If it
    exposes ``reset()`` (or ``reset_fn`` is given) the model state is cleared
    once the channel has been silent for ``reset_silence_seconds``.
    """

    provider = "silero"

    def __init__(
        self,
        prob_fn: ProbFn,
        threshold: Optional[float] = None,
        silence_hold_seconds: Optional[float] = None,
        reset_fn: Optional[ResetFn] = None,
        reset_silence_seconds: Optional[float] = None,
    ):
        self._prob_fn = prob_fn
        self._reset_fn: Optional[ResetFn] = reset_fn or getattr(prob_fn, "reset", None)
        self.threshold = settings.vad_silero_threshold if threshold is None else threshold
        self.silence_hold_seconds = (
            settings.speech_silence_hold_seconds if silence_hold_seconds is None else silence_hold_seconds
        )
        self.reset_silence_seconds = (
            settings.vad_silero_reset_silence_seconds
            if reset_silence_seconds is None
            else reset_silence_seconds
        )
        self._buf = np.zeros(0, dtype=np.float32)
        self._state = VADState(since=time.monotonic())
        self._last_active: Optional[float] = None
        self._reset_pending = False  # model has seen speech since the last reset
        self.resets = 0

    @property
    def prob_fn(self) -> ProbFn:
        return self._prob_fn

    def update(self, pcm: np.ndarray) -> VADState:
        """Feed a PCM frame; returns the (possibly updated) speaking state."""
        active = self._frame_is_speech(pcm)
        now = time.monotonic()

        if active:
            self._last_active = now
            self._reset_pending = True
            self._state._silence_started = None
            if not self._state.speaking:
                self._state = VADState(speaking=True, since=now)
        else:
            if self._state.speaking:
                if self._state._silence_started is None:
                    self._state._silence_started = now
                elif now - self._state._silence_started >= self.silence_hold_seconds:
                    self._state = VADState(speaking=False, since=now)
            self._maybe_reset(now)
        return self._state

    def _maybe_reset(self, now: float) -> None:
        """Clear Silero's recurrent state after sustained silence."""
        if not self._reset_pending or self._state.speaking or self._last_active is None:
            return
        if now - self._last_active < self.reset_silence_seconds:
            return
        self._reset_pending = False
        if self._reset_fn is None:
            return
        try:
            self._reset_fn()
            self.resets += 1
        except Exception:  # noqa: BLE001
            logger.exception("Silero reset_states failed")

    def reset(self) -> None:
        """Force a model-state reset (e.g. on source switch)."""
        self._buf = np.zeros(0, dtype=np.float32)
        self._reset_pending = False
        if self._reset_fn is not None:
            try:
                self._reset_fn()
                self.resets += 1
            except Exception:  # noqa: BLE001
                logger.exception("Silero reset_states failed")

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


# --------------------------------------------------------------------------
# Model loading: one instance per channel
# --------------------------------------------------------------------------


class _TorchProbFn:
    """Wraps one Silero model instance as a resettable probability function."""

    def __init__(self, model, torch_module):
        self._model = model
        self._torch = torch_module

    def __call__(self, window: np.ndarray) -> float:
        tensor = self._torch.from_numpy(np.ascontiguousarray(window, dtype=np.float32))
        with self._torch.no_grad():
            return float(self._model(tensor, TARGET_RATE).item())

    def reset(self) -> None:
        reset = getattr(self._model, "reset_states", None)
        if callable(reset):
            reset()


@lru_cache(maxsize=1)
def _silero_model_factory() -> Callable[[], object]:
    """Resolve how to build a fresh Silero model; cached so the probe runs once.

    Prefers the pip ``silero-vad`` package (weights bundled in the wheel, so it
    works on an offline church network); falls back to ``torch.hub`` which
    needs GitHub access on first use.
    """
    try:
        from silero_vad import load_silero_vad  # type: ignore

        def _from_package():
            return load_silero_vad()

        logger.info("Silero VAD backend: silero-vad package (bundled weights)")
        return _from_package
    except ImportError:
        pass

    import torch  # noqa: F401  (probe: raises ImportError if torch is missing)

    def _from_hub():
        import torch as _torch

        model, _utils = _torch.hub.load(
            repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=True
        )
        return model

    logger.warning("Silero VAD backend: torch.hub (requires network on first load)")
    return _from_hub


def _load_silero_prob_fn() -> StatefulProbFn:
    """Create a **new** Silero model instance and return its probability function.

    Not cached: every channel must get its own model because Silero is stateful.
    Raises on any failure so callers can fall back.
    """
    import torch  # local import: optional heavy dependency

    model = _silero_model_factory()()
    if hasattr(model, "eval"):
        model.eval()
    return _TorchProbFn(model, torch)


def _emit_degraded(reason: str) -> None:
    global _degraded_emitted
    logger.warning("VAD degraded to energy detector", reason=reason)
    if not _degraded_emitted:
        _degraded_emitted = True
        event_bus.publish(
            {"event": "PERCEPTION_DEGRADED", "payload": {"component": "vad", "reason": reason}}
        )


def make_channel_vad(want_silero: bool) -> Tuple[object, str]:
    """Return (vad, provider) with a channel-private model. Falls back loudly."""
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
