"""Local Piper contracts using fake children and real tiny WAVs, never synthesis."""

import asyncio
import io
import os
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.voice.config import TTSSettings
from app.voice.models import AudioResult, ProsodyConfig, VoiceConfig
from app.voice.playback import decode_audio
from app.voice.provider import build_provider
from app.voice.providers import piper
from app.voice.providers.piper import PiperProvider


def tiny_wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x80\x00\x00\xff\x7f")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    """Fail even if a cloud/device fallback would otherwise swallow an exception."""
    def forbidden(*args, **kwargs):
        pytest.fail("Piper tests must not use real processes, HTTP, or audio devices")

    for key in list(os.environ):
        if key.upper().startswith("TTS_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=forbidden))
    monkeypatch.setattr(asyncio, "create_subprocess_shell", AsyncMock(side_effect=forbidden))
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        play=forbidden, OutputStream=forbidden, RawOutputStream=forbidden,
        query_devices=forbidden, query_hostapis=forbidden,
    ))


@pytest.fixture
def provider(tmp_path):
    root = tmp_path / "local voices"
    root.mkdir()
    config = TTSSettings(_env_file=None, piper_model_dir=str(root))
    # Only existence matters: these are deliberately not loadable voice models.
    (root / f"{config.piper_voice}.onnx").write_bytes(b"fake model")
    (root / f"{config.piper_voice}.onnx.json").write_text("{}")
    return PiperProvider(config)


@pytest.fixture
def child_factory(monkeypatch, tmp_path):
    """Write output as the fake child would; track reaping and directory lifetime."""
    real_temporary_directory = piper.tempfile.TemporaryDirectory
    directories = []

    def temporary_directory(**kwargs):
        directory = real_temporary_directory(dir=tmp_path, **kwargs)
        directories.append(Path(directory.name))
        return directory

    monkeypatch.setattr(piper.tempfile, "TemporaryDirectory", temporary_directory)

    def install(payload=None, *, missing=False, returncode=0, blocked=False, kill_race=False):
        process = SimpleNamespace(returncode=None)
        started = asyncio.Event()
        release = asyncio.Event()
        lifecycle = []
        outputs = []

        async def communicate(data):
            output = outputs[0]
            if not missing:
                output.write_bytes(tiny_wav() if payload is None else payload)
            started.set()
            if blocked:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    lifecycle.append("communicate_cancelled")
                    raise
            process.returncode = returncode
            return None, None

        def kill():
            assert outputs[0].parent.is_dir(), "Temp directory removed before child stopped"
            lifecycle.append("kill")
            if kill_race:
                raise ProcessLookupError("Child exited just before kill")

        async def wait():
            assert outputs[0].parent.is_dir(), "Temp directory removed before child reaped"
            lifecycle.append("wait")
            process.returncode = -9
            return -9

        process.communicate = AsyncMock(side_effect=communicate)
        process.kill = Mock(side_effect=kill)
        process.wait = AsyncMock(side_effect=wait)

        async def spawn(*args, **kwargs):
            # Every fake invocation enforces explicit file-only output, not playback.
            assert args[:3] == (sys.executable, "-m", "piper")
            assert args.count("--output-file") == 1
            assert "shell" not in kwargs
            assert kwargs == {
                "stdin": asyncio.subprocess.PIPE,
                "stdout": asyncio.subprocess.DEVNULL,
                "stderr": asyncio.subprocess.DEVNULL,
            }
            outputs.append(Path(args[args.index("--output-file") + 1]))
            assert outputs[0].parent in directories
            return process

        spawn_mock = AsyncMock(side_effect=spawn)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_mock)
        return SimpleNamespace(process=process, spawn=spawn_mock, started=started,
                               outputs=outputs, lifecycle=lifecycle, directories=directories)

    yield install
    assert all(not directory.exists() for directory in directories)


@pytest.mark.parametrize("rate", [.5, .94, 1, 1.5])
async def test_piper_file_only_invocation_utf8_stdin_and_reciprocal_rate(provider, child_factory, rate, monkeypatch):
    child = child_factory()
    wait_for = AsyncMock(wraps=asyncio.wait_for)
    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    text = '  Check A & B <monitor>; $(echo nope) — "headset"\n'
    result = await provider.synthesize(text, VoiceConfig(), ProsodyConfig(speaking_rate=rate))
    assert isinstance(result, AudioResult)
    assert result.data == tiny_wav()
    samples, sample_rate = decode_audio(result)
    assert sample_rate == 24000 and len(samples) == 3
    root = Path(provider.config.piper_model_dir).resolve()
    child.spawn.assert_awaited_once_with(
        sys.executable, "-m", "piper",
        "--model", str(root / f"{provider.config.piper_voice}.onnx"),
        "--config", str(root / f"{provider.config.piper_voice}.onnx.json"),
        "--output-file", str(child.outputs[0]), "--length-scale", str(1 / rate),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    child.process.communicate.assert_awaited_once_with((text.strip() + "\n").encode("utf-8"))
    assert wait_for.await_args.args[1] == 30
    child.process.kill.assert_not_called()
    assert not child.outputs[0].parent.exists()


async def test_piper_explicit_voice_selects_matching_local_model_pair(provider, child_factory):
    root = Path(provider.config.piper_model_dir)
    name = "en_US-other-medium"
    for suffix in (".onnx", ".onnx.json"):
        (root / (name + suffix)).write_text("fake")
    child = child_factory()
    await provider.synthesize("Alert", VoiceConfig(voice_id=name), ProsodyConfig())
    args = child.spawn.await_args.args
    assert args[args.index("--model") + 1] == str((root / (name + ".onnx")).resolve())
    assert args[args.index("--config") + 1] == str((root / (name + ".onnx.json")).resolve())


@pytest.mark.parametrize("voice", [
    "../escape", "/tmp/voice", "voices/name", r"..\escape", r"C:\voices\name",
    "https://example.invalid/voice", "file:///tmp/voice", "%2e%2e%2fescape",
    "voice.onnx", "-option", "voice\n", "voice name", "a" * 101,
])
async def test_piper_rejects_voice_paths_urls_and_invalid_ids_before_child(provider, voice):
    with pytest.raises(ValueError, match="local model name"):
        await provider.synthesize("Alert", VoiceConfig(voice_id=voice), ProsodyConfig())
    asyncio.create_subprocess_exec.assert_not_called()


@pytest.mark.parametrize("suffix", [".onnx", ".onnx.json"])
async def test_piper_rejects_symlinks_escaping_model_directory(provider, tmp_path, suffix):
    outside = tmp_path / ("outside" + suffix)
    outside.write_text("fake")
    path = Path(provider.config.piper_model_dir) / (provider.config.piper_voice + suffix)
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="inside the configured voice directory"):
        await provider.synthesize("Alert", VoiceConfig(), ProsodyConfig())
    asyncio.create_subprocess_exec.assert_not_called()


@pytest.mark.parametrize("missing", ["model", "config", "both", "directory"])
async def test_piper_missing_model_or_config_never_spawns_or_falls_back(provider, missing, monkeypatch):
    monkeypatch.setenv("TTS_AZURE_KEY", "fake-unit-test-key")
    monkeypatch.setenv("TTS_AZURE_REGION", "eastus")
    root = Path(provider.config.piper_model_dir)
    if missing == "directory":
        provider.config.piper_model_dir = str(root / "absent")
    else:
        for label, suffix in (("model", ".onnx"), ("config", ".onnx.json")):
            if missing in (label, "both"):
                (root / (provider.config.piper_voice + suffix)).unlink()
    with pytest.raises(ValueError, match="voice files are missing"):
        await provider.synthesize("Alert", VoiceConfig(), ProsodyConfig())
    asyncio.create_subprocess_exec.assert_not_called()


@pytest.mark.parametrize("text", ["", " \n\t", "x" * 1001])
async def test_piper_rejects_empty_or_oversized_text_before_child(provider, text):
    with pytest.raises(ValueError, match="1..1000 characters"):
        await provider.synthesize(text, VoiceConfig(), ProsodyConfig())
    asyncio.create_subprocess_exec.assert_not_called()


@pytest.mark.parametrize("rate", [.49, 1.51, float("nan"), float("inf"), -float("inf")])
async def test_piper_rejects_invalid_rate_before_child(provider, rate):
    with pytest.raises(ValueError, match="speaking rate"):
        await provider.synthesize("Alert", VoiceConfig(), ProsodyConfig(speaking_rate=rate))
    asyncio.create_subprocess_exec.assert_not_called()


@pytest.mark.parametrize("kind", ["missing", "empty", "malformed", "oversized", "truncated"])
async def test_piper_rejects_invalid_child_output_and_cleans_temp(provider, child_factory, kind):
    payload = {
        "missing": None, "empty": b"", "malformed": b"not a WAV",
        "oversized": b"x" * 3_000_001, "truncated": tiny_wav()[:-2],
    }[kind]
    child = child_factory(payload, missing=kind == "missing")
    with pytest.raises((ValueError, wave.Error, EOFError)):
        await provider.synthesize("Alert", VoiceConfig(), ProsodyConfig())
    child.spawn.assert_awaited_once()
    assert not child.outputs[0].parent.exists()


@pytest.mark.parametrize("returncode", [1, 2, -9])
async def test_piper_nonzero_exit_rejects_even_valid_wav_without_fallback(provider, child_factory, returncode):
    child = child_factory(returncode=returncode)
    with pytest.raises(RuntimeError, match=f"exit {returncode}"):
        await provider.synthesize("Alert", VoiceConfig(), ProsodyConfig())
    child.spawn.assert_awaited_once()
    assert not child.outputs[0].parent.exists()


async def test_piper_timeout_kills_and_reaps_before_temp_cleanup(provider, child_factory):
    # Use the supported minimum, not a 30-second wait or an invalid settings value.
    provider.config = TTSSettings(_env_file=None, piper_model_dir=provider.config.piper_model_dir,
                                  timeout_seconds=1)
    child = child_factory(blocked=True)
    with pytest.raises(TimeoutError):
        await provider.synthesize("Alert", VoiceConfig(), ProsodyConfig())
    child.process.kill.assert_called_once_with()
    child.process.wait.assert_awaited_once_with()
    assert child.lifecycle == ["communicate_cancelled", "kill", "wait"]
    assert not child.outputs[0].parent.exists()


@pytest.mark.parametrize("kill_race", [False, True])
async def test_piper_cancellation_kills_and_reaps_before_temp_cleanup(provider, child_factory, kill_race):
    child = child_factory(blocked=True, kill_race=kill_race)
    task = asyncio.create_task(provider.synthesize("Alert", VoiceConfig(), ProsodyConfig()))
    try:
        await asyncio.wait_for(child.started.wait(), 2)
        assert child.outputs[0].is_file()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    child.process.kill.assert_called_once_with()
    child.process.wait.assert_awaited_once_with()
    assert child.lifecycle == ["communicate_cancelled", "kill", "wait"]
    assert not child.outputs[0].parent.exists()


async def test_piper_spawn_failure_cleans_temp_without_cloud_fallback(provider, child_factory, monkeypatch):
    child = child_factory()
    spawn = AsyncMock(side_effect=FileNotFoundError("Piper runtime missing"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(FileNotFoundError, match="Piper runtime missing"):
        await provider.synthesize("Alert", VoiceConfig(), ProsodyConfig())
    spawn.assert_awaited_once()
    assert len(child.directories) == 1
    assert not child.directories[0].exists()


def test_piper_factory_defaults_to_local_provider_ignoring_legacy_credentials(monkeypatch):
    monkeypatch.setenv("TTS_AZURE_KEY", "fake-unit-test-key")
    monkeypatch.setenv("TTS_AZURE_REGION", "eastus")
    config = TTSSettings(_env_file=None)
    provider = build_provider(config)
    assert isinstance(provider, PiperProvider)
    assert config.provider == "piper"
    assert "azure_key" not in provider.config.model_dump()
    asyncio.create_subprocess_exec.assert_not_called()


@pytest.mark.parametrize("name", ["disabled", "azure", "unknown"])
def test_piper_factory_rejects_disabled_and_unsupported_provider_even_without_validation(name):
    # Deliberately bypass settings validation to exercise the factory's own guard.
    config = TTSSettings(_env_file=None).model_copy(update={"provider": name})
    with pytest.raises(ValueError):
        build_provider(config)
    asyncio.create_subprocess_exec.assert_not_called()