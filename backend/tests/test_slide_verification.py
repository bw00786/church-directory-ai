"""Tests for SlideVerifier: OCR-based slide-change verification (WO-EWVERIFY-1)."""

import asyncio

import pytest

from app.config import settings
from app.easyworship.slide_verification import SlideVerifier
from app.events.bus import event_bus


@pytest.fixture(autouse=True)
def _fast_delay(monkeypatch):
    monkeypatch.setattr(settings, "slide_verify_delay_seconds", 0.0)


async def test_verify_after_action_publishes_stuck_event_when_text_unchanged(monkeypatch):
    verifier = SlideVerifier()
    verifier.last_text = "Amazing Grace verse 1"
    monkeypatch.setattr(verifier, "current_text", lambda: "Amazing Grace verse 1")

    queue = await event_bus.subscribe()
    try:
        await verifier.verify_after_action("next_slide")
        message = await asyncio.wait_for(queue.get(), timeout=1.0)
    finally:
        await event_bus.unsubscribe(queue)

    assert message["event"] == "EASYWORSHIP_SLIDE_STUCK"
    assert message["payload"]["action"] == "next_slide"


async def test_verify_after_action_does_not_publish_when_text_changed(monkeypatch):
    verifier = SlideVerifier()
    verifier.last_text = "Amazing Grace verse 1"
    monkeypatch.setattr(verifier, "current_text", lambda: "Amazing Grace verse 2")

    published = []
    monkeypatch.setattr(event_bus, "publish", lambda msg: published.append(msg))

    await verifier.verify_after_action("next_slide")

    assert published == []
    assert verifier.last_text == "Amazing Grace verse 2"


async def test_verify_after_action_skips_check_with_no_prior_text(monkeypatch):
    verifier = SlideVerifier()
    verifier.last_text = ""
    monkeypatch.setattr(verifier, "current_text", lambda: "")

    published = []
    monkeypatch.setattr(event_bus, "publish", lambda msg: published.append(msg))

    await verifier.verify_after_action("next_slide")

    assert published == []


def test_snapshot_reflects_enabled_flag_and_last_text(monkeypatch):
    monkeypatch.setattr(settings, "easyworship_slide_verify_enabled", True)
    verifier = SlideVerifier()
    verifier.last_text = "some text"

    snapshot = verifier.snapshot()

    assert snapshot == {"enabled": True, "last_ocr_text": "some text"}
