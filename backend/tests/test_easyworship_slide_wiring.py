"""Tests for EasyWorshipService's OCR slide-verification wiring."""

import asyncio

import pytest

from app.config import settings
from app.easyworship.driver import MockDriver
from app.easyworship.service import EasyWorshipService


@pytest.fixture(autouse=True)
def _fast_delay(monkeypatch):
    monkeypatch.setattr(settings, "slide_verify_delay_seconds", 0.0)


async def test_action_triggers_verification_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "easyworship_slide_verify_enabled", True)
    from app.easyworship import service as service_module

    calls = []

    async def fake_verify(action):
        calls.append(action)

    monkeypatch.setattr(service_module.slide_verifier, "verify_after_action", fake_verify)

    svc = EasyWorshipService(driver=MockDriver())
    await svc.start()
    await svc.action("next_slide")
    await asyncio.sleep(0)  # let the background task run

    assert calls == ["next_slide"]


async def test_action_skips_verification_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "easyworship_slide_verify_enabled", False)
    from app.easyworship import service as service_module

    calls = []

    async def fake_verify(action):
        calls.append(action)

    monkeypatch.setattr(service_module.slide_verifier, "verify_after_action", fake_verify)

    svc = EasyWorshipService(driver=MockDriver())
    await svc.start()
    await svc.action("next_slide")
    await asyncio.sleep(0)

    assert calls == []


async def test_status_includes_slide_verification_snapshot():
    svc = EasyWorshipService(driver=MockDriver())
    await svc.start()

    status = svc.status()

    assert "slide_verification" in status
    assert "enabled" in status["slide_verification"]
    assert "last_ocr_text" in status["slide_verification"]
