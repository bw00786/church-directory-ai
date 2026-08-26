"""Tests for UsbMultichannelCapture (mocked PortAudio) — WO-MGX-USB-1."""

import numpy as np
import pytest

import app.audio.usb_capture as uc
from app.audio.usb_capture import UsbCaptureError, UsbMultichannelCapture, _resample_to_16k


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

        def start(self):
            pass


def _capture(monkeypatch, fake):
    monkeypatch.setattr(uc, "sd", fake)
    cap = UsbMultichannelCapture()
    cap.enabled = True
    return cap


def test_resample_to_16k_downsamples():
    # sr == target: unchanged.
    same = _resample_to_16k(np.ones(16000, dtype=np.float32), 16000)
    assert same.dtype == np.float32
    assert same.size == 16000
    # 48000 Hz -> 16000 Hz decimates by 3.
    out = _resample_to_16k(np.ones(48000, dtype=np.float32), 48000)
    assert out.size == 16000
    out2 = _resample_to_16k(np.ones(9600, dtype=np.float32), 48000)
    assert out2.size == 3200  # 9600 * 16000/48000


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

    import asyncio

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
