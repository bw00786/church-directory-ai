"""MGX16 USB MAIN multichannel PCM capture (WO-MGX-USB-1).

The Yamaha MGX16's USB MAIN interface presents the desk's channels to the host
as a standard multichannel USB audio device. This module opens that device via
PortAudio (``sounddevice``), extracts the configured role channels, decimates
each to 16 kHz mono float32, and publishes per-channel frames to subscribers
(VAD, Whisper, replay recorder).

Signal path (hardening pass):

* **Anti-aliased, continuous resampling.** Each role channel owns a
  ``StatefulResampler`` — a polyphase FIR decimator (Kaiser-windowed sinc,
  cutoff just under the 16 kHz Nyquist) that carries its filter history and
  output phase across 100 ms blocks, so there is no phase discontinuity at
  block edges and energy above 8 kHz is attenuated instead of folding back
  into the speech band.
* **Hardware timestamps.** Frames carry the PortAudio ADC time of the first
  sample in the block (``time_info.inputBufferAdcTime``) converted to the
  ``time.monotonic()`` domain via an offset measured once against the stream
  clock. If the driver reports no usable ADC time the callback's monotonic
  time is used and ``status()["clock_source"]`` says so.
* **One drain thread.** PortAudio delivers blocks on its own thread into a
  bounded queue; a single long-lived drain thread resamples them and posts the
  finished 16 kHz frames to the event loop with ``call_soon_threadsafe``, so
  subscribers still run on the loop thread and no per-block task is spawned.
  Queue overflow is counted (``dropped_blocks``) rather than swallowed.

Hard rule (WO section 6): a configured channel index outside the device's
channel count is a refusal, not a clamp — wrong-channel audio attributed to the
pastor is worse than no audio.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import queue
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from app.audio.yamaha_capture import configured_role_channels
from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - optional dependency; PortAudio may also
    # fail to initialise at import time on hosts with no audio server (CI).
    sd = None  # type: ignore

TARGET_RATE = 16000  # VAD/ASR sample rate

# Subscriber signature: (role, channel, samples_16k_mono, monotonic_ts)
# ``monotonic_ts`` is the time.monotonic()-domain instant at which the first
# sample of the frame hit the ADC (or, without hardware timing, the callback time).
FrameCallback = Callable[[str, int, np.ndarray, float], None]

_DROP_LOG_EVERY = 50  # log every Nth dropped block, not every one


class UsbCaptureError(RuntimeError):
    """Raised when the capture service cannot start (device/channel/rate)."""


# --------------------------------------------------------------------------
# Resampler
# --------------------------------------------------------------------------


def design_lowpass(num_taps: int, cutoff: float, beta: float = 9.0) -> np.ndarray:
    """Kaiser-windowed sinc low-pass FIR. ``cutoff`` is in cycles/sample (< 0.5).

    Pure numpy so the capture path has no scipy dependency. beta=9 gives
    roughly 80 dB of stop-band rejection once the transition band is passed.
    """
    n = np.arange(num_taps, dtype=np.float64) - (num_taps - 1) / 2.0
    h = 2.0 * cutoff * np.sinc(2.0 * cutoff * n)
    h *= np.kaiser(num_taps, beta)
    return h / np.sum(h)


class StatefulResampler:
    """Polyphase rational resampler (up L, FIR, down M) that keeps state across calls.

    Feed arbitrary-length blocks with :meth:`process`; the FIR history and the
    output sample phase are carried over, so concatenating the outputs is
    identical to resampling the concatenated input in one shot (modulo the
    constant group delay of ``(num_taps - 1) / 2`` input-rate samples / L).
    """

    def __init__(self, sr_in: int, sr_out: int, taps_per_phase: int = 24, beta: float = 9.0):
        if sr_in <= 0 or sr_out <= 0:
            raise ValueError("sample rates must be positive")
        self.sr_in = int(sr_in)
        self.sr_out = int(sr_out)
        g = math.gcd(self.sr_in, self.sr_out)
        self.L = self.sr_out // g  # upsample factor
        self.M = self.sr_in // g  # downsample factor
        self.passthrough = self.L == self.M

        if self.passthrough:
            self._h = np.ones(1, dtype=np.float64)
            self._phases = [self._h]
            self._hist = np.zeros(0, dtype=np.float64)
            self._t = 0
            return

        # Filter at the upsampled rate (sr_in * L). Cutoff a little under the
        # narrower Nyquist so the transition band finishes before aliasing starts.
        num_taps = taps_per_phase * max(self.L, self.M) + 1
        cutoff = 0.5 * min(1.0 / self.L, 1.0 / self.M) * 0.92
        h = design_lowpass(num_taps, cutoff, beta=beta) * self.L  # gain L for zero-stuffing
        self._h = h
        # Polyphase components: phase p uses taps h[p], h[p+L], h[p+2L], ...
        self._phases = [h[p :: self.L] for p in range(self.L)]
        hist_len = max(len(ph) for ph in self._phases) - 1  # input-rate samples of history
        self._hist = np.zeros(hist_len, dtype=np.float64)
        # Next output instant, in upsampled-sample units, relative to the start
        # of (hist + block). Starting at hist_len*L means the first output uses
        # only zero history, i.e. a zero-padded start.
        self._t = hist_len * self.L

    @property
    def group_delay_seconds(self) -> float:
        return ((len(self._h) - 1) / 2.0) / (self.sr_in * self.L)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64).reshape(-1)
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)
        if self.passthrough:
            return x.astype(np.float32)

        buf = np.concatenate([self._hist, x])
        last_t = (buf.size - 1) * self.L
        if self._t > last_t:
            # Block too small to produce an output sample; just accumulate history.
            self._carry(buf)
            return np.zeros(0, dtype=np.float32)

        t = np.arange(self._t, last_t + 1, self.M, dtype=np.int64)
        idx = t // self.L
        phase = t % self.L
        out = np.empty(t.size, dtype=np.float64)
        if self.L == 1:
            full = np.convolve(buf, self._phases[0], mode="full")
            out[:] = full[idx]
        else:
            for p in np.unique(phase):
                sel = phase == p
                full = np.convolve(buf, self._phases[int(p)], mode="full")
                out[sel] = full[idx[sel]]

        next_t = int(t[-1]) + self.M
        self._t = next_t
        self._carry(buf)
        return out.astype(np.float32)

    def _carry(self, buf: np.ndarray) -> None:
        hist_len = self._hist.size
        if hist_len == 0:
            return
        # hist always has hist_len samples, so buf.size >= hist_len here.
        keep = buf[-hist_len:]
        self._t -= (buf.size - keep.size) * self.L
        self._hist = keep


def _resample_to_16k(x: np.ndarray, sr: int, target: int = TARGET_RATE) -> np.ndarray:
    """One-shot anti-aliased resample to 16 kHz (stateless convenience; tests/tools)."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if x.size == 0 or sr == target:
        return x
    return StatefulResampler(sr, target).process(x)


# --------------------------------------------------------------------------
# Capture service
# --------------------------------------------------------------------------


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
        self._queue: "queue.Queue[Tuple[np.ndarray, float]]" = queue.Queue(maxsize=64)
        self._drain_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._sample_rate: Optional[int] = None
        self._subscribers: List[FrameCallback] = []
        self._last_frame: Dict[int, float] = {}
        self._resamplers: Dict[int, StatefulResampler] = {}

        # Timing / health counters
        self._clock_offset: Optional[float] = None  # monotonic - stream clock
        self._clock_source = "monotonic"
        self._dropped_blocks = 0
        self._blocks_received = 0
        self._blocks_dispatched = 0
        self._last_status: Optional[str] = None

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

    @property
    def dropped_blocks(self) -> int:
        return self._dropped_blocks

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
        self._resamplers = {
            rc.channel: StatefulResampler(self._sample_rate, TARGET_RATE) for rc in self._role_channels
        }
        self._clock_offset = None
        self._clock_source = "monotonic"
        self._loop = asyncio.get_running_loop()
        self._running = True
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
            self._running = False
            self._stream = None
            raise UsbCaptureError(f"failed to open MGX USB stream: {e}") from e

        self._drain_thread = threading.Thread(
            target=self._drain_loop, name="mgx-usb-drain", daemon=True
        )
        self._drain_thread.start()
        logger.info(
            "MGX USB capture started",
            device=info["name"],
            sample_rate=self._sample_rate,
            channels=self._max_channel,
        )
        return True

    async def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None
        thread = self._drain_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            await asyncio.to_thread(thread.join, 2.0)
        self._drain_thread = None
        self._loop = None

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

    # -- timing ---------------------------------------------------------------
    def _stream_clock(self) -> Optional[float]:
        """Current PortAudio stream time (same domain as inputBufferAdcTime)."""
        stream = self._stream
        if stream is None:
            return None
        try:
            value = float(stream.time)
        except Exception:  # noqa: BLE001
            return None
        return value if math.isfinite(value) and value > 0.0 else None

    def _block_timestamp(self, time_info) -> float:
        """Monotonic-domain time of the first sample in the block.

        Uses the PortAudio ADC timestamp when the driver provides one, mapped
        with a stream-clock -> monotonic offset measured once; otherwise the
        callback's own monotonic time.
        """
        now = time.monotonic()
        adc = getattr(time_info, "inputBufferAdcTime", None)
        if adc is None and isinstance(time_info, dict):
            adc = time_info.get("inputBufferAdcTime")
        try:
            adc = float(adc) if adc is not None else None
        except (TypeError, ValueError):
            adc = None
        if adc is None or not math.isfinite(adc) or adc <= 0.0:
            return now
        if self._clock_offset is None:
            stream_now = self._stream_clock()
            if stream_now is None:
                return now
            self._clock_offset = now - stream_now
            self._clock_source = "adc"
            logger.info("MGX USB clock offset measured", offset=round(self._clock_offset, 6))
        return adc + self._clock_offset

    # -- audio path -----------------------------------------------------------
    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """PortAudio callback thread: stamp, copy, enqueue. Never blocks."""
        if status:
            text = str(status)
            if text != self._last_status:
                self._last_status = text
                logger.warning("MGX USB stream status", status=text)
        ts = self._block_timestamp(time_info)
        self._blocks_received += 1
        try:
            self._queue.put_nowait((np.array(indata, dtype=np.float32, copy=True), ts))
        except queue.Full:
            self._dropped_blocks += 1
            if self._dropped_blocks % _DROP_LOG_EVERY == 1:
                logger.warning(
                    "MGX USB block dropped (consumer behind)", dropped_total=self._dropped_blocks
                )

    def _drain_loop(self) -> None:
        """Single long-lived consumer: resample off-loop, hand frames to the loop."""
        while self._running:
            try:
                block, ts = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                frames = self._resample_block(block)
            except Exception:  # noqa: BLE001
                logger.exception("MGX USB resample failed")
                continue
            loop = self._loop
            if loop is None or loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(self._publish_frames, frames, ts)
            except RuntimeError:  # loop shutting down
                break

    def _resample_block(self, block: np.ndarray) -> List[Tuple[str, int, np.ndarray]]:
        block = np.asarray(block, dtype=np.float32)
        if block.ndim == 1:
            block = block.reshape(-1, 1)
        rate = self._sample_rate or TARGET_RATE
        frames: List[Tuple[str, int, np.ndarray]] = []
        for rc in self._role_channels:
            col = rc.channel - 1
            if col < 0 or col >= block.shape[1]:
                continue
            rs = self._resamplers.get(rc.channel)
            if rs is None or rs.sr_in != rate:
                rs = StatefulResampler(rate, TARGET_RATE)
                self._resamplers[rc.channel] = rs
            samples = rs.process(block[:, col])
            if samples.size == 0:
                continue
            frames.append((rc.role, rc.channel, samples))
        return frames

    def _publish_frames(self, frames: List[Tuple[str, int, np.ndarray]], ts: float) -> None:
        """Loop thread: update health and fan out to subscribers."""
        arrival = time.monotonic()
        self._blocks_dispatched += 1
        for role, channel, samples in frames:
            self._last_frame[channel] = arrival
            for cb in self._subscribers:
                try:
                    cb(role, channel, samples, ts)
                except Exception:  # noqa: BLE001
                    logger.exception("MGX USB subscriber failed", role=role)

    def _dispatch_block(self, block: np.ndarray, ts: Optional[float] = None) -> None:
        """Synchronous resample + publish of one raw block (tests, offline tools)."""
        self._publish_frames(self._resample_block(block), time.monotonic() if ts is None else ts)

    def status(self) -> dict:
        return {
            "available": sd is not None,
            "enabled": self.enabled,
            "running": self._running,
            "device_name": self.device_name,
            "device_index": self.device_index,
            "sample_rate": self._sample_rate,
            "channels": {rc.role: rc.channel for rc in self._role_channels},
            "clock_source": self._clock_source,
            "clock_offset": self._clock_offset,
            "dropped_blocks": self._dropped_blocks,
            "blocks_received": self._blocks_received,
            "blocks_dispatched": self._blocks_dispatched,
            "queue_depth": self._queue.qsize(),
        }


# Module-level singleton
usb_capture = UsbMultichannelCapture()
