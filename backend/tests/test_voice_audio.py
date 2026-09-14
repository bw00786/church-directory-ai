"""PCM decoding and playback tests; no audio device is opened."""

import io
import sys
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app.voice.config import VoiceSettings
from app.voice.models import AudioResult
from app.voice.playback import HeadsetPlayback, decode_audio


def wav_audio(rate=24000, channels=1, width=2, frames=24):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\x00" * frames * channels * width)
    return AudioResult(data=buffer.getvalue())


@pytest.mark.parametrize("rate", [16000, 22050, 24000, 44100, 48000])
def test_voice_decode_supported_pcm_wav_rates(rate):
    samples, decoded_rate = decode_audio(wav_audio(rate=rate))
    assert decoded_rate == rate
    assert samples.dtype == np.float32 and samples.shape == (24,)


def test_voice_decode_normalizes_signed_pcm_samples():
    audio = wav_audio(frames=3)
    audio.data = audio.data[:-6] + b"\x00\x80\x00\x00\xff\x7f"
    samples, _ = decode_audio(audio)
    np.testing.assert_allclose(samples, [-1, 0, 32767 / 32768])


@pytest.mark.parametrize("kind", ["empty", "oversize", "malformed", "truncated", "stereo", "8bit", "rate", "zero_frames", "long"])
def test_voice_decode_rejects_bad_audio(kind):
    audio = {
        "empty": lambda: AudioResult(data=b""),
        "oversize": lambda: AudioResult(data=b"x" * 3_000_001),
        "malformed": lambda: AudioResult(data=b"not a wave file"),
        "truncated": lambda: AudioResult(data=wav_audio().data[:-2]),
        "stereo": lambda: wav_audio(channels=2),
        "8bit": lambda: wav_audio(width=1),
        "rate": lambda: wav_audio(rate=8000),
        "zero_frames": lambda: wav_audio(frames=0),
        "long": lambda: wav_audio(rate=16000, frames=16000 * 45 + 1),
    }[kind]()
    with pytest.raises((ValueError, wave.Error, EOFError)):
        decode_audio(audio)


def test_voice_decode_accepts_duration_upper_bound():
    samples, rate = decode_audio(wav_audio(rate=16000, frames=16000 * 45))
    assert len(samples) == rate * 45


@pytest.fixture
def headset(monkeypatch):
    class CallbackStop(Exception):
        pass

    class CallbackAbort(Exception):
        pass

    sd = SimpleNamespace(
        query_hostapis=Mock(return_value=[{"name": "Core Audio"}, {"name": "Other API"}]),
        query_devices=Mock(return_value=[
            {"name": "Default Speakers", "max_output_channels": 2, "hostapi": 0},
            {"name": "USB Headphones", "max_output_channels": 2, "hostapi": 0},
        ]),
        OutputStream=Mock(), CallbackStop=CallbackStop, CallbackAbort=CallbackAbort,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    playback = HeadsetPlayback(VoiceSettings(
        _env_file=None, output_device="USB Headphones", output_host_api="Core Audio", routing_verified=True,
    ))
    return playback, sd


def test_voice_headset_selects_exact_device_and_host_api_never_default(headset):
    playback, sd = headset
    assert playback.device() == 1
    sd.query_devices.return_value.insert(0, {"name": "USB Headphones", "max_output_channels": 2, "hostapi": 1})
    assert playback.device() == 2


@pytest.mark.parametrize("case", ["missing_name", "missing_api", "unverified", "missing", "ambiguous", "input_only", "wrong_api"])
def test_voice_headset_rejects_unverified_missing_or_ambiguous_device(headset, case):
    playback, sd = headset
    if case == "missing_name":
        playback.config.output_device = ""
    elif case == "missing_api":
        playback.config.output_host_api = ""
    elif case == "unverified":
        playback.config.routing_verified = False
    elif case == "missing":
        sd.query_devices.return_value = []
    elif case == "ambiguous":
        sd.query_devices.return_value.append(dict(sd.query_devices.return_value[1]))
    elif case == "input_only":
        sd.query_devices.return_value[1]["max_output_channels"] = 0
    else:
        playback.config.output_host_api = "Unknown API"
    with pytest.raises(ValueError):
        playback.device()
    sd.OutputStream.assert_not_called()


@pytest.mark.parametrize("tone", [False, True])
async def test_voice_headset_playback_callback_completes_and_closes_stream(headset, tone):
    playback, sd = headset
    blocks = []
    stream = Mock()

    def factory(**kwargs):
        assert kwargs["device"] == 1
        assert kwargs["channels"] == 1 and kwargs["dtype"] == "float32"

        def start():
            for _ in range(100):
                output = np.full((512, 1), np.nan, dtype="float32")
                try:
                    kwargs["callback"](output, 512, None, False)
                except sd.CallbackStop:
                    blocks.append(output.copy())
                    kwargs["finished_callback"]()
                    return
                blocks.append(output.copy())
            raise AssertionError("Callback failed to finish")

        stream.start.side_effect = start
        return stream

    sd.OutputStream.side_effect = factory
    await playback.play(wav_audio(), tone=tone)
    output = np.concatenate(blocks)
    assert np.isfinite(output).all()
    assert bool(np.any(output)) is tone
    if tone:
        assert np.max(np.abs(output)) <= 0.081
    stream.abort.assert_called_once()
    stream.close.assert_called_once()
    assert playback.stream is None


async def test_voice_headset_callback_underflow_reports_failure_and_closes(headset):
    playback, sd = headset
    stream = Mock()

    def factory(**kwargs):
        def start():
            with pytest.raises(sd.CallbackAbort):
                kwargs["callback"](np.zeros((512, 1)), 512, None, True)
        stream.start.side_effect = start
        return stream

    sd.OutputStream.side_effect = factory
    with pytest.raises(RuntimeError, match="before audio completed"):
        await playback.play(wav_audio())
    stream.abort.assert_called_once()
    stream.close.assert_called_once()
    assert playback.stream is None


async def test_voice_headset_start_failure_closes_stream(headset):
    playback, sd = headset
    stream = Mock()
    stream.start.side_effect = RuntimeError("synthetic open failure")
    sd.OutputStream.return_value = stream
    with pytest.raises(RuntimeError, match="synthetic open failure"):
        await playback.play(wav_audio())
    stream.abort.assert_called_once()
    stream.close.assert_called_once()
    assert playback.stream is None


def test_voice_headset_stop_aborts_only_existing_stream(headset):
    playback, sd = headset
    playback.stop()
    sd.OutputStream.assert_not_called()
    stream = Mock()
    playback.stream = stream
    playback.stop()
    stream.abort.assert_called_once()