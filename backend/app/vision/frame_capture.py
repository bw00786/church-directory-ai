"""Multi-input frame capture (WO-VISION-1 FR-1; shared infra for WO-EWVERIFY-1).

Named inputs (``program``, ``multiview``, ``camera_<id>``) each poll a frame
*provider* on a background task and keep only the latest frame plus a small
bounded diagnostic ring buffer. Nothing is persisted. Subscribers request the
latest frame by tag; per-input liveness is tracked and a stalled input emits
``PERCEPTION_DEGRADED`` tagged ``vision:<input>``.

Providers are pluggable so this is testable without hardware: built-ins cover a
cv2 capture device (program), a PTZOptics HTTP ``snapshot.jpg`` endpoint, and a
directory of images (``FRAME_SOURCE=dir`` for replay). Tests inject a callable.

Freshness: a cv2 device decodes a stream at 25-60 fps into a driver buffer; one
``read()`` per 1 Hz poll would drain that buffer one frame at a time and serve
frames seconds old. ``device_provider`` therefore runs a :class:`DeviceGrabber`
thread per device that continuously ``grab()``s (cheap, no decode) and only
``retrieve()``s the newest frame when a poll asks for it. Snapshot providers
reuse one persistent ``httpx.Client`` instead of a connection per poll.
Providers may expose a ``close()`` attribute; the service calls it on stop.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional, Tuple

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
        self._closed = False


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
        old = self._inputs.get(tag)
        if old is not None:
            if old._task is not None:
                old._task.cancel()
            if old.provider is not provider:
                self._close_input(old)
        inp = _Input(tag, provider, rate, self._ring_frames)
        self._inputs[tag] = inp
        if self._running:
            # start() already ran; poll the late-registered input too.
            with contextlib.suppress(RuntimeError):
                inp._task = asyncio.get_running_loop().create_task(self._poll(inp))
        logger.info("Vision input registered", tag=tag, hz=rate)

    def has_input(self, tag: str) -> bool:
        return tag in self._inputs

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
            self._close_input(inp)

    @staticmethod
    def _close_input(inp: _Input) -> None:
        if inp._closed:
            return
        inp._closed = True
        _close_provider(inp.provider)

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
def _close_provider(provider: Any) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.warning("Vision provider close failed", exc_info=True)


class DeviceGrabber:
    """Continuously ``grab()`` a cv2 capture on a background thread and keep
    only the newest frame; ``latest()`` ``retrieve()``s it on demand.

    ``capture_factory(device)`` must return an object with ``isOpened()``,
    ``grab()``, ``retrieve() -> (ok, frame)`` and ``release()`` (cv2
    ``VideoCapture`` or a test fake). After ``reopen_after`` consecutive failed
    grabs the device is released and reopened (unplugged/replugged HDMI tap).
    """

    def __init__(
        self,
        device,
        capture_factory: Optional[Callable[[Any], Any]] = None,
        reopen_after: int = 30,
        reopen_delay: float = 1.0,
        idle_sleep: float = 0.001,
    ) -> None:
        self.device = int(device) if str(device).isdigit() else device
        self._factory = capture_factory or self._cv2_factory
        self._reopen_after = max(1, reopen_after)
        self._reopen_delay = reopen_delay
        self._idle_sleep = idle_sleep
        self._cap = None
        self._lock = threading.Lock()      # guards the capture object
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pending = False              # a grabbed frame awaits retrieve()
        self._grab_ts: Optional[float] = None
        self._frame = None                 # last retrieved frame
        self._frame_ts: Optional[float] = None
        self.grabs = 0
        self.failures = 0
        self.reopens = 0

    @staticmethod
    def _cv2_factory(device):
        if cv2 is None:
            raise RuntimeError("opencv-python is required for device capture")
        return cv2.VideoCapture(device)

    # -- lifecycle ------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"frame-grab-{self.device}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None
        self._release()

    close = stop

    def _open(self) -> bool:
        try:
            cap = self._factory(self.device)
        except Exception:
            logger.warning("Capture device open failed", device=self.device, exc_info=True)
            return False
        if cap is None or not cap.isOpened():
            logger.warning("Capture device not opened", device=self.device)
            with contextlib.suppress(Exception):
                if cap is not None:
                    cap.release()
            return False
        with self._lock:
            self._cap = cap
            self._pending = False
        return True

    def _release(self) -> None:
        with self._lock:
            cap, self._cap = self._cap, None
            self._pending = False
        if cap is not None:
            with contextlib.suppress(Exception):
                cap.release()

    def _run(self) -> None:
        consecutive = 0
        while not self._stop.is_set():
            if self._cap is None:
                if not self._open():
                    self._stop.wait(self._reopen_delay)
                    continue
                consecutive = 0
            with self._lock:
                cap = self._cap
                ok = False
                if cap is not None:
                    try:
                        ok = bool(cap.grab())
                    except Exception:
                        ok = False
                if ok:
                    self._pending = True
                    self._grab_ts = time.monotonic()
            if ok:
                self.grabs += 1
                consecutive = 0
                if self._idle_sleep:
                    # grab() on a live device blocks for a frame interval;
                    # on a fake/finished source yield so we don't spin hot.
                    self._stop.wait(self._idle_sleep)
            else:
                self.failures += 1
                consecutive += 1
                if consecutive >= self._reopen_after:
                    logger.warning("Capture device stalled; reopening", device=self.device)
                    self._release()
                    self.reopens += 1
                    consecutive = 0
                    self._stop.wait(self._reopen_delay)
                else:
                    self._stop.wait(0.01)

    # -- access ---------------------------------------------------------------
    def latest(self):
        """Decode and return the most recently grabbed frame, or the last
        decoded one if nothing new was grabbed; None before the first frame."""
        with self._lock:
            if self._pending and self._cap is not None:
                try:
                    ok, frame = self._cap.retrieve()
                except Exception:
                    ok, frame = False, None
                self._pending = False
                if ok and frame is not None:
                    self._frame = frame
                    self._frame_ts = self._grab_ts
            return self._frame

    def frame_age(self) -> Optional[float]:
        return None if self._frame_ts is None else max(0.0, time.monotonic() - self._frame_ts)


def device_provider(device, capture_factory: Optional[Callable[[Any], Any]] = None) -> FrameProvider:
    """cv2 capture device provider (for the ATEM program via USB/HDMI capture).

    Backed by a :class:`DeviceGrabber` thread so every poll gets the *newest*
    frame rather than the next one queued in the driver buffer."""
    grabber = DeviceGrabber(device, capture_factory=capture_factory)

    def provide():
        if cv2 is None and capture_factory is None:
            return None
        if not grabber.running:
            grabber.start()
        return grabber.latest()

    provide.close = grabber.stop  # type: ignore[attr-defined]
    provide.grabber = grabber     # type: ignore[attr-defined]
    return provide


def snapshot_provider(url: str, auth: Optional[tuple] = None, transport=None) -> FrameProvider:
    """PTZOptics HTTP snapshot.jpg provider with one persistent connection."""
    state: Dict[str, Any] = {"client": None}
    lock = threading.Lock()

    def client():
        import httpx

        with lock:
            if state["client"] is None:
                kwargs: Dict[str, Any] = {"timeout": 3.0}
                if auth:
                    kwargs["auth"] = auth
                if transport is not None:
                    kwargs["transport"] = transport
                state["client"] = httpx.Client(**kwargs)
            return state["client"]

    def close():
        with lock:
            c, state["client"] = state["client"], None
        if c is not None:
            with contextlib.suppress(Exception):
                c.close()

    def provide():
        try:
            resp = client().get(url)
            if resp.status_code != 200 or np is None or cv2 is None:
                return None
            buf = np.frombuffer(resp.content, dtype=np.uint8)
            return cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception:
            # Drop the (possibly broken) connection; the next poll reconnects.
            close()
            return None

    provide.close = close  # type: ignore[attr-defined]
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
