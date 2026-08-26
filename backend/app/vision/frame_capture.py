"""Multi-input frame capture (WO-VISION-1 FR-1; shared infra for WO-EWVERIFY-1).

Named inputs (``program``, ``multiview``, ``camera_<id>``) each poll a frame
*provider* on a background task and keep only the latest frame plus a small
bounded diagnostic ring buffer. Nothing is persisted. Subscribers request the
latest frame by tag; per-input liveness is tracked and a stalled input emits
``PERCEPTION_DEGRADED`` tagged ``vision:<input>``.

Providers are pluggable so this is testable without hardware: built-ins cover a
cv2 capture device (program), a PTZOptics HTTP ``snapshot.jpg`` endpoint, and a
directory of images (``FRAME_SOURCE=dir`` for replay). Tests inject a callable.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional, Tuple

from app.config import settings
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore

# A provider returns the latest decoded BGR frame (np.ndarray) or None.
FrameProvider = Callable[[], "Optional[object]"]


class _Input:
    def __init__(self, tag: str, provider: FrameProvider, hz: float, ring_frames: int):
        self.tag = tag
        self.provider = provider
        self.interval = 1.0 / hz if hz > 0 else 1.0
        self.latest = None
        self.latest_ts: Optional[float] = None
        self.ring: Deque[Tuple[float, object]] = deque(maxlen=max(1, ring_frames))
        self._fps_samples: Deque[float] = deque(maxlen=20)
        self._task: Optional[asyncio.Task] = None
        self._degraded = False


class FrameCaptureService:
    """Owns all named vision inputs and their latest frames."""

    def __init__(self) -> None:
        self._inputs: Dict[str, _Input] = {}
        self._running = False
        self.stall_seconds = settings.vision_stall_seconds
        self._ring_frames = max(1, int(settings.vision_ring_minutes * 60 * settings.vision_snapshot_hz))

    # -- registration ---------------------------------------------------------
    def register_input(self, tag: str, provider: FrameProvider, hz: Optional[float] = None) -> None:
        rate = settings.vision_snapshot_hz if hz is None else hz
        self._inputs[tag] = _Input(tag, provider, rate, self._ring_frames)
        logger.info("Vision input registered", tag=tag, hz=rate)

    def push_frame(self, tag: str, frame) -> None:
        """Directly set a frame (used by tests and the dir replay driver)."""
        inp = self._inputs.get(tag)
        if inp is None:
            inp = _Input(tag, lambda: None, settings.vision_snapshot_hz, self._ring_frames)
            self._inputs[tag] = inp
        self._store(inp, frame)

    # -- lifecycle ------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for inp in self._inputs.values():
            if inp._task is None:
                inp._task = asyncio.create_task(self._poll(inp))
        logger.info("Frame capture started", inputs=list(self._inputs))

    async def stop(self) -> None:
        self._running = False
        for inp in self._inputs.values():
            if inp._task is not None:
                inp._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await inp._task
                inp._task = None

    # -- access ---------------------------------------------------------------
    def get_frame(self, tag: str):
        """Return (frame, meta). frame is None if the input has never produced one."""
        inp = self._inputs.get(tag)
        if inp is None or inp.latest is None:
            return None, {"tag": tag, "age": None, "available": False}
        return inp.latest, {
            "tag": tag,
            "age": round(time.monotonic() - (inp.latest_ts or 0.0), 3),
            "available": True,
        }

    def healthy(self, tag: str) -> bool:
        inp = self._inputs.get(tag)
        if inp is None or inp.latest_ts is None:
            return False
        return (time.monotonic() - inp.latest_ts) < self.stall_seconds

    def effective_fps(self, tag: str) -> float:
        inp = self._inputs.get(tag)
        if inp is None or len(inp._fps_samples) < 2:
            return 0.0
        span = inp._fps_samples[-1] - inp._fps_samples[0]
        return round((len(inp._fps_samples) - 1) / span, 2) if span > 0 else 0.0

    def status(self) -> dict:
        return {
            tag: {
                "healthy": self.healthy(tag),
                "fps": self.effective_fps(tag),
                "age": None if inp.latest_ts is None else round(time.monotonic() - inp.latest_ts, 3),
            }
            for tag, inp in self._inputs.items()
        }

    # -- internals ------------------------------------------------------------
    def _store(self, inp: _Input, frame) -> None:
        now = time.monotonic()
        inp.latest = frame
        inp.latest_ts = now
        inp.ring.append((now, frame))
        inp._fps_samples.append(now)
        if inp._degraded:
            inp._degraded = False
            event_bus.publish({"event": "PERCEPTION_RESTORED", "payload": {"source": f"vision:{inp.tag}"}})

    async def _poll(self, inp: _Input) -> None:
        while self._running:
            frame = None
            try:
                frame = await asyncio.to_thread(inp.provider)
            except Exception:
                logger.exception("Vision provider failed", tag=inp.tag)
            if frame is not None:
                self._store(inp, frame)
            elif inp.latest_ts is not None and not inp._degraded:
                if (time.monotonic() - inp.latest_ts) >= self.stall_seconds:
                    inp._degraded = True
                    event_bus.publish(
                        {"event": "PERCEPTION_DEGRADED", "payload": {"source": f"vision:{inp.tag}"}}
                    )
            # Below-min-fps degradation.
            if self.effective_fps(inp.tag) and self.effective_fps(inp.tag) < settings.vision_min_fps:
                logger.warning("Vision input below min fps", tag=inp.tag, fps=self.effective_fps(inp.tag))
            await asyncio.sleep(inp.interval)


# -- built-in provider factories ---------------------------------------------
def device_provider(device) -> FrameProvider:
    """cv2 capture device provider (for the ATEM program via USB/HDMI capture)."""
    cap = {"c": None}

    def provide():
        if cv2 is None:
            return None
        if cap["c"] is None:
            cap["c"] = cv2.VideoCapture(int(device) if str(device).isdigit() else device)
        ok, frame = cap["c"].read()
        return frame if ok else None

    return provide


def snapshot_provider(url: str, auth: Optional[tuple] = None) -> FrameProvider:
    """PTZOptics HTTP snapshot.jpg provider."""

    def provide():
        try:
            import httpx

            kwargs = {"timeout": 3.0}
            if auth:
                kwargs["auth"] = auth
            resp = httpx.get(url, **kwargs)
            if resp.status_code != 200 or np is None or cv2 is None:
                return None
            buf = np.frombuffer(resp.content, dtype=np.uint8)
            return cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception:
            return None

    return provide


def dir_provider(directory: str) -> FrameProvider:
    """Replay provider: iterate image files in a directory, looping."""
    from pathlib import Path

    files = sorted(Path(directory).glob("*.jpg")) + sorted(Path(directory).glob("*.png"))
    idx = {"i": 0}

    def provide():
        if not files or cv2 is None:
            return None
        path = files[idx["i"] % len(files)]
        idx["i"] += 1
        return cv2.imread(str(path))

    return provide


# Module-level singleton
frame_capture = FrameCaptureService()
