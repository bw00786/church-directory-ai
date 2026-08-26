"""MGX16 USB MAIN multichannel PCM capture (WO-MGX-USB-1).

The Yamaha MGX16's USB MAIN interface presents the desk's channels to the host
as a standard multichannel USB audio device. This module opens that device via
PortAudio (``sounddevice``), extracts the configured role channels, decimates
each to 16 kHz mono float32, and publishes per-channel frames to subscribers
(VAD, Whisper, replay recorder).

Isolation: PortAudio delivers blocks on its own thread into a queue; a
dedicated asyncio task drains and processes them off the decision loop, so a
stalled or disconnected device can never block the director. Per-channel
last-frame timestamps drive the health/degradation ladder in ``AudioObserver``.

Hard rule (WO section 6): a configured channel index outside the device's
channel count is a refusal, not a clamp — wrong-channel audio attributed to the
pastor is worse than no audio.
"""

from __future__ import annotations

import asyncio
import contextlib
import queue
import time
from typing import Callable, Dict, List, Optional

import numpy as np

from app.audio.yamaha_capture import configured_role_channels
from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - optional dependency
    sd = None  # type: ignore

TARGET_RATE = 16000  # VAD/ASR sample rate

# Subscriber signature: (role, channel, samples_16k_mono, monotonic_ts)
FrameCallback = Callable[[str, int, np.ndarray, float], None]


class UsbCaptureError(RuntimeError):
    """Raised when the capture service cannot start (device/channel/rate)."""


class UsbMultichannelCapture:
    """Captures configured role channels from the MGX16 USB MAIN device."""

    def __init__(self) -> None:
        self.enabled = settings.mgx_usb_enabled
        self.device_name = settings.mgx_usb_device_name
        self.device_index = settings.mgx_usb_device_index
        self.configured_rate = settings.mgx_usb_sample_rate
        self.stall_seconds = settings.mgx_usb_stall_seconds

        # role channels are 1-based desk indices; device columns are 0-based.
        self._role_channels = configured_role_channels()
        self._max_channel = max((rc.channel for rc in self._role_channels), default=0)

        self._stream = None
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=64)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._sample_rate: Optional[int] = None
        self._subscribers: List[FrameCallback] = []
        self._last_frame: Dict[int, float] = {}

    # -- subscription ---------------------------------------------------------
    def subscribe(self, callback: FrameCallback) -> None:
        self._subscribers.append(callback)

    # -- health ---------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    @property
    def sample_rate(self) -> Optional[int]:
        return self._sample_rate

    def frame_age(self, channel: int) -> float:
        ts = self._last_frame.get(channel)
        return float("inf") if ts is None else time.monotonic() - ts

    def healthy(self, channel: int) -> bool:
        return self._running and self.frame_age(channel) < self.stall_seconds

    # -- lifecycle ------------------------------------------------------------
    async def start(self) -> bool:
        """Open the device and begin capture. Returns True if capturing.

        Raises UsbCaptureError on channel-map or sample-rate misconfiguration
        so the capture service (not the whole app) fails visibly.
        """
        if not self.enabled:
            logger.info("MGX USB capture disabled (MGX_USB_ENABLED=false)")
            return False
        if sd is None:
            logger.warning("`sounddevice` not installed; MGX USB capture unavailable")
            return False
        if self._running:
            return True

        device = self._resolve_device()
        info = sd.query_devices(device)
        max_ch = int(info["max_input_channels"])
        if self._max_channel > max_ch:
            raise UsbCaptureError(
                f"channel map exceeds device: configured channel {self._max_channel} "
                f"but device '{info['name']}' exposes {max_ch} input channels"
            )

        self._sample_rate = self._negotiate_rate(device, info)
        try:
            self._stream = sd.InputStream(
                device=device,
                channels=self._max_channel,
                samplerate=self._sample_rate,
                dtype="float32",
                blocksize=int(self._sample_rate * 0.1),
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as e:  # noqa: BLE001
            raise UsbCaptureError(f"failed to open MGX USB stream: {e}") from e

        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info(
            "MGX USB capture started",
            device=info["name"],
            sample_rate=self._sample_rate,
            channels=self._max_channel,
        )
        return True

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None

    # -- device / rate resolution --------------------------------------------
    def _resolve_device(self):
        if self.device_index is not None:
            return self.device_index
        target = self.device_name.lower()
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0 and target in dev["name"].lower():
                return idx
        raise UsbCaptureError(
            f"no input device matching name '{self.device_name}' "
            "(set MGX_USB_DEVICE_INDEX to override)"
        )

    def _negotiate_rate(self, device, info) -> int:
        candidates = [self.configured_rate, 48000, int(info.get("default_samplerate", 48000))]
        seen: set[int] = set()
        for rate in candidates:
            if rate in seen:
                continue
            seen.add(rate)
            try:
                sd.check_input_settings(
                    device=device, channels=self._max_channel, samplerate=rate, dtype="float32"
                )
                logger.info("MGX USB sample rate negotiated", rate=rate)
                return rate
            except Exception:
                continue
        raise UsbCaptureError(
            f"no usable sample rate for device (tried {sorted(seen)})"
        )

    # -- audio path -----------------------------------------------------------
    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning("MGX USB stream status", status=str(status))
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(indata.copy())

    async def _process_loop(self) -> None:
        while self._running:
            try:
                block = await asyncio.to_thread(self._queue.get, True, 1.0)
            except queue.Empty:
                continue
            except Exception:  # noqa: BLE001
                continue
            self._dispatch_block(block)

    def _dispatch_block(self, block: np.ndarray) -> None:
        now = time.monotonic()
        for rc in self._role_channels:
            col = rc.channel - 1
            if col < 0 or col >= block.shape[1]:
                continue
            samples = _resample_to_16k(block[:, col], self._sample_rate or TARGET_RATE)
            if samples.size == 0:
                continue
            self._last_frame[rc.channel] = now
            for cb in self._subscribers:
                try:
                    cb(rc.role, rc.channel, samples, now)
                except Exception:  # noqa: BLE001
                    logger.exception("MGX USB subscriber failed", role=rc.role)

    def status(self) -> dict:
        return {
            "available": sd is not None,
            "enabled": self.enabled,
            "running": self._running,
            "device_name": self.device_name,
            "device_index": self.device_index,
            "sample_rate": self._sample_rate,
            "channels": {rc.role: rc.channel for rc in self._role_channels},
        }


def _resample_to_16k(x: np.ndarray, sr: int, target: int = TARGET_RATE) -> np.ndarray:
    """Linear-interpolation decimation to 16 kHz mono float32."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if x.size == 0 or sr == target:
        return x
    n = int(round(x.size * target / sr))
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    src = np.linspace(0.0, 1.0, num=x.size, endpoint=False)
    dst = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(dst, src, x).astype(np.float32)


# Module-level singleton
usb_capture = UsbMultichannelCapture()
