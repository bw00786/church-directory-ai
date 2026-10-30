"""Tests for UsbMultichannelCapture (mocked PortAudio) — WO-MGX-USB-1 + hardening."""

import asyncio
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import app.audio.usb_capture as uc
from app.audio.usb_capture import (
    StatefulResampler,
    UsbCaptureError,
    UsbMultichannelCapture,
    _resample_to_16k,
)


class FakeSd:
    """Minimal stand-in for the sounddevice module."""

    def __init__(self, devices, max_channels=8, ok_rates=(48000,)):
        self._devices = devices
        self._max = max_channels
        self._ok = ok_rates

    def query_devices(self, index=None):
        if index is None:
            return self._devices
        return self._devices[index]

    def check_input_settings(self, device=None, channels=None, samplerate=None, dtype=None):
        if samplerate not in self._ok:
            raise ValueError("unsupported rate")

    class InputStream:  # noqa: N801
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.time = 1000.0

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass


def _capture(monkeypatch, fake):
    monkeypatch.setattr(uc, "sd", fake)
    cap = UsbMultichannelCapture()
    cap.enabled = True
    return cap


def _tone(freq: float, sr: int, seconds: float) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    return np.sin(2 * np.pi * freq * t).astype(np.float32)


def _blocks(x: np.ndarray, n: int):
    return [x[i : i + n] for i in range(0, x.size, n)]


# --- resampler ---------------------------------------------------------------


def test_resample_to_16k_downsamples():
    # sr == target: unchanged.
    same = _resample_to_16k(np.ones(16000, dtype=np.float32), 16000)
    assert same.dtype == np.float32
    assert same.size == 16000
    # 48000 Hz -> 16000 Hz decimates by 3.
    out = _resample_to_16k(np.ones(48000, dtype=np.float32), 48000)
    assert out.size == 16000
    assert out.dtype == np.float32
    out2 = _resample_to_16k(np.ones(9600, dtype=np.float32), 48000)
    assert out2.size == 3200  # 9600 * 16000/48000


def test_resampler_is_continuous_across_block_boundaries():
    """Block-wise output must equal one-shot output: no phase reset per block."""
    sr = 48000
    x = _tone(1000.0, sr, 1.0)
    rs = StatefulResampler(sr, 16000)
    blockwise = np.concatenate([rs.process(b) for b in _blocks(x, 4800)])  # 100 ms blocks
    oneshot = StatefulResampler(sr, 16000).process(x)
    assert blockwise.size == oneshot.size == 16000
    np.testing.assert_allclose(blockwise, oneshot, atol=1e-6)

    # And it is the expected 1 kHz tone (after the constant filter delay).
    delay = rs.group_delay_seconds
    t_out = np.arange(blockwise.size) / 16000.0 - delay
    ideal = np.sin(2 * np.pi * 1000.0 * t_out)
    warm = 200  # skip the zero-history start-up transient
    assert np.max(np.abs(blockwise[warm:] - ideal[warm:])) < 1e-3

    # No discontinuity at a block edge: sample-to-sample steps at the boundary
    # are bounded by the tone's own maximum slope.
    max_step = 2 * np.sin(np.pi * 1000.0 / 16000.0) + 1e-3
    for edge in (1600, 3200, 4800):
        assert abs(blockwise[edge] - blockwise[edge - 1]) <= max_step


def test_resampler_rejects_aliasing_above_nyquist():
    """A 20 kHz tone must not fold into the 16 kHz band (>40 dB attenuation)."""
    sr = 48000
    x = _tone(20000.0, sr, 1.0)
    rs = StatefulResampler(sr, 16000)
    out = np.concatenate([rs.process(b) for b in _blocks(x, 4800)])
    in_rms = np.sqrt(np.mean(np.square(x)))
    out_rms = np.sqrt(np.mean(np.square(out[400:])))
    attenuation_db = 20 * np.log10(max(out_rms, 1e-12) / in_rms)
    assert attenuation_db < -40.0


def test_resampler_handles_non_integer_ratio():
    rs = StatefulResampler(44100, 16000)
    assert (rs.L, rs.M) == (160, 441)
    x = _tone(1000.0, 44100, 1.0)
    out = np.concatenate([rs.process(b) for b in _blocks(x, 4410)])
    assert out.size == 16000
    t_out = np.arange(out.size) / 16000.0 - rs.group_delay_seconds
    assert np.max(np.abs(out[2000:] - np.sin(2 * np.pi * 1000.0 * t_out)[2000:])) < 1e-3


def test_resampler_tiny_blocks_accumulate():
    rs = StatefulResampler(48000, 16000)
    x = _tone(500.0, 48000, 0.1)
    out = np.concatenate([rs.process(b) for b in _blocks(x, 1)])  # one sample at a time
    oneshot = StatefulResampler(48000, 16000).process(x)
    np.testing.assert_allclose(out, oneshot, atol=1e-6)


# --- device / channel map ---------------------------------------------------


def test_device_selection_by_name(monkeypatch):
    devices = [
        {"name": "Built-in Mic", "max_input_channels": 2, "default_samplerate": 44100},
        {"name": "MGX16 USB MAIN", "max_input_channels": 22, "default_samplerate": 48000},
    ]
    cap = _capture(monkeypatch, FakeSd(devices))
    cap.device_index = None
    assert cap._resolve_device() == 1


def test_device_selection_by_index(monkeypatch):
    cap = _capture(monkeypatch, FakeSd([{"name": "x", "max_input_channels": 22}]))
    cap.device_index = 0
    assert cap._resolve_device() == 0


def test_channel_map_out_of_range_refuses(monkeypatch):
    # Device exposes fewer channels than the configured max role channel.
    fake = FakeSd([{"name": "MGX", "max_input_channels": 4, "default_samplerate": 48000}])
    cap = _capture(monkeypatch, fake)
    cap.device_index = 0
    cap._max_channel = 8  # e.g. congregation on ch 8

    with pytest.raises(UsbCaptureError):
        asyncio.run(cap.start())


def test_dispatch_extracts_correct_channels(monkeypatch):
    cap = _capture(monkeypatch, FakeSd([{"name": "MGX", "max_input_channels": 22}]))
    cap._sample_rate = 16000
    received = []
    cap.subscribe(lambda role, ch, samples, t: received.append((role, ch, samples.copy())))

    # Block where each column equals its (1-based) channel number.
    block = np.zeros((100, cap._max_channel), dtype=np.float32)
    for col in range(cap._max_channel):
        block[:, col] = col + 1
    cap._dispatch_block(block)

    got = {(role, ch): samples[0] for role, ch, samples in received}
    for rc in cap._role_channels:
        assert got[(rc.role, rc.channel)] == float(rc.channel)


def test_dispatch_resamples_each_channel_statefully(monkeypatch):
    cap = _capture(monkeypatch, FakeSd([{"name": "MGX", "max_input_channels": 22}]))
    cap._sample_rate = 48000
    ch = cap._role_channels[0].channel
    received = []
    cap.subscribe(lambda role, c, samples, t: received.append(samples) if c == ch else None)

    tone = _tone(1000.0, 48000, 0.5)
    for b in _blocks(tone, 4800):
        block = np.zeros((b.size, cap._max_channel), dtype=np.float32)
        block[:, ch - 1] = b
        cap._dispatch_block(block)
    out = np.concatenate(received)
    assert out.size == 8000
    oneshot = StatefulResampler(48000, 16000).process(tone)
    np.testing.assert_allclose(out, oneshot, atol=1e-6)


def test_stall_detection(monkeypatch):
    cap = _capture(monkeypatch, FakeSd([{"name": "MGX", "max_input_channels": 22}]))
    cap.stall_seconds = 2.0
    cap._running = True
    cap._sample_rate = 16000
    ch = cap._role_channels[0].channel
    assert cap.healthy(ch) is False  # no frames yet

    cap._dispatch_block(np.ones((10, cap._max_channel), dtype=np.float32))
    assert cap.healthy(ch) is True
    assert cap.frame_age(ch) < 2.0


# --- timestamps --------------------------------------------------------------


def test_adc_timestamp_mapped_to_monotonic_domain(monkeypatch):
    cap = _capture(monkeypatch, FakeSd([{"name": "MGX", "max_input_channels": 22}]))
    cap._stream = FakeSd.InputStream()
    cap._stream.time = 1000.0  # stream clock "now"

    before = time.monotonic()
    ts = cap._block_timestamp(SimpleNamespace(inputBufferAdcTime=999.9))  # block hit ADC 100 ms ago
    after = time.monotonic()
    assert cap._clock_source == "adc"
    assert before - 0.1 - 0.01 <= ts <= after - 0.1 + 0.01

    # Offset is measured once; later blocks use it even as the stream clock advances.
    offset = cap._clock_offset
    cap._stream.time = 1005.0
    ts2 = cap._block_timestamp(SimpleNamespace(inputBufferAdcTime=1001.0))
    assert cap._clock_offset == offset
    assert ts2 == pytest.approx(1001.0 + offset)
    assert cap.status()["clock_source"] == "adc"


def test_timestamp_falls_back_to_monotonic_without_adc_time(monkeypatch):
    cap = _capture(monkeypatch, FakeSd([{"name": "MGX", "max_input_channels": 22}]))
    cap._stream = FakeSd.InputStream()
    before = time.monotonic()
    ts = cap._block_timestamp(SimpleNamespace(inputBufferAdcTime=0.0))
    assert before <= ts <= time.monotonic()
    assert cap._clock_source == "monotonic"
    ts_none = cap._block_timestamp(None)
    assert ts_none >= before


def test_callback_timestamp_propagates_to_subscribers(monkeypatch):
    cap = _capture(monkeypatch, FakeSd([{"name": "MGX", "max_input_channels": 22}]))
    cap._sample_rate = 16000
    cap._stream = FakeSd.InputStream()
    cap._on_audio(np.ones((16, cap._max_channel), dtype=np.float32), 16, SimpleNamespace(inputBufferAdcTime=999.5), None)
    block, ts = cap._queue.get_nowait()
    assert ts == pytest.approx(999.5 + cap._clock_offset)


# --- drops ---------------------------------------------------------------------


def test_queue_overflow_is_counted_not_swallowed(monkeypatch):
    cap = _capture(monkeypatch, FakeSd([{"name": "MGX", "max_input_channels": 22}]))
    cap._queue = queue.Queue(maxsize=1)
    block = np.zeros((16, cap._max_channel), dtype=np.float32)
    cap._on_audio(block, 16, None, None)
    cap._on_audio(block, 16, None, None)
    cap._on_audio(block, 16, None, None)
    assert cap.dropped_blocks == 2
    status = cap.status()
    assert status["dropped_blocks"] == 2
    assert status["blocks_received"] == 3
    assert status["queue_depth"] == 1


# --- drain thread ----------------------------------------------------------------


async def test_single_drain_thread_posts_frames_to_loop(monkeypatch):
    cap = _capture(monkeypatch, FakeSd([{"name": "MGX", "max_input_channels": 22}]))
    cap._sample_rate = 16000
    cap._loop = asyncio.get_running_loop()
    cap._running = True
    received = []
    threads = set()

    def on_frame(role, ch, samples, t):
        threads.add(threading.current_thread().name)
        received.append((ch, t))

    cap.subscribe(on_frame)
    thread = threading.Thread(target=cap._drain_loop, daemon=True)
    thread.start()
    try:
        for i in range(3):
            cap._queue.put((np.ones((160, cap._max_channel), dtype=np.float32), 100.0 + i))
        deadline = time.monotonic() + 2.0
        while len(received) < 3 * len(cap._role_channels) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
    finally:
        cap._running = False
        thread.join(2.0)

    assert len(received) == 3 * len(cap._role_channels)
    assert sorted({t for _, t in received}) == [100.0, 101.0, 102.0]
    # Subscribers run on the loop thread, not on the drain thread.
    assert threads == {threading.current_thread().name}
    assert cap.status()["blocks_dispatched"] == 3
