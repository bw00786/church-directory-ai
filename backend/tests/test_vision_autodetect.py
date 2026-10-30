"""Tests for vision auto-detect (real RTSP cameras vs test-pattern only)."""

import pytest

from app.vision.manager import VisionManager


def test_has_real_camera_source_false_by_default():
    manager = VisionManager()
    manager.settings.camera_1_rtsp_url = None
    manager.settings.camera_2_rtsp_url = None
    manager.settings.camera_3_rtsp_url = None
    manager.settings.camera_4_rtsp_url = None
    assert manager.has_real_camera_source() is False


def test_has_real_camera_source_true_when_configured():
    manager = VisionManager()
    manager.settings.camera_1_rtsp_url = "rtsp://192.168.1.50/stream"
    assert manager.has_real_camera_source() is True


@pytest.mark.asyncio
async def test_start_is_noop_without_flag_or_real_camera(monkeypatch):
    manager = VisionManager()
    monkeypatch.setattr(manager.settings, "vision_enabled", False)
    monkeypatch.setattr(manager, "has_real_camera_source", lambda: False)

    await manager.start()

    assert manager.running is False


# -- never run on synthetic frames (WO-HARDEN) -----------------------------------
def test_no_test_pattern_sources_by_default(monkeypatch):
    for n in range(1, 5):
        monkeypatch.delenv(f"CAMERA_{n}_RTSP_URL", raising=False)
    monkeypatch.delenv("VISION_TEST_PATTERN", raising=False)
    manager = VisionManager()
    assert manager.settings.vision_enabled is False
    assert manager.settings.vision_test_pattern is False
    assert manager.sources == []
    assert manager.uses_test_pattern() is False
    assert manager.can_start() is False
    assert manager.get_status()["cameras"] == 0
    assert manager.get_status()["test_pattern"] is False


@pytest.mark.asyncio
async def test_start_refuses_without_real_camera_even_if_enabled(monkeypatch):
    from app.vision import manager as manager_module

    for n in range(1, 5):
        monkeypatch.delenv(f"CAMERA_{n}_RTSP_URL", raising=False)
    monkeypatch.delenv("VISION_TEST_PATTERN", raising=False)
    published = []
    monkeypatch.setattr(manager_module.event_bus, "publish", lambda msg: published.append(msg))

    manager = VisionManager()
    monkeypatch.setattr(manager.settings, "vision_enabled", True)  # legacy flag alone is not enough
    await manager.start()

    assert manager.running is False
    assert manager._task is None
    events = [m["event"] for m in published]
    assert "VISION_STARTED" not in events
    assert "VISION_NOT_STARTED" in events
    await manager.stop()  # must be safe when never started


def test_test_pattern_only_on_explicit_opt_in(monkeypatch):
    for n in range(1, 5):
        monkeypatch.delenv(f"CAMERA_{n}_RTSP_URL", raising=False)
    monkeypatch.setenv("VISION_TEST_PATTERN", "true")
    manager = VisionManager()
    assert manager.settings.vision_test_pattern is True
    assert len(manager.sources) == 3
    assert manager.uses_test_pattern() is True
    assert manager.can_start() is True
    assert manager.get_status()["test_pattern"] is True


def test_real_camera_builds_rtsp_source_not_test_pattern(monkeypatch):
    from app.vision.frame_source import RTSPSource

    monkeypatch.setenv("CAMERA_1_RTSP_URL", "rtsp://192.168.1.50/stream")
    monkeypatch.delenv("VISION_TEST_PATTERN", raising=False)
    manager = VisionManager()
    assert [type(s) for s in manager.sources] == [RTSPSource]
    assert manager.uses_test_pattern() is False
    assert manager.can_start() is True
