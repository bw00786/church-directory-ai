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
