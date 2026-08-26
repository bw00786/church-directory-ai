"""Slide-change verification via OCR on a dedicated camera-2 feed (WO-EWVERIFY-1).

EasyWorship has no read-back API (see .service), so `EasyWorshipService`'s
current-item tracking is best-effort keystroke counting only -- it silently
drifts if a keystroke is dropped or EasyWorship is also operated manually.
This adds an independent visual check: after a next/prev slide/item action,
OCR the raw camera-2 feed (tapped *separately* from the switched ATEM
program -- e.g. an HDMI splitter/capture device on the EasyWorship laptop's
own output, configured via `VISION_SLIDES_DEVICE`) and compare the extracted
text to what was on screen just before the action. If the text hasn't
changed, the keystroke almost certainly didn't register, and an
`EASYWORSHIP_SLIDE_STUCK` event is published so an operator/AI can notice.

Important: this does NOT verify the slide is semantically *correct* for the
point in the service -- there's no authoritative source of expected
on-screen text to compare against (lyrics/scripture aren't stored anywhere
in this app), only whether a commanded change visibly took effect. The
extracted text is also exposed via `snapshot()` so a human operator can
cross-check it against what the congregation should be reading.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.events.bus import event_bus
from app.logging_config import get_logger
from app.vision.frame_capture import device_provider, dir_provider, frame_capture
from app.vision.slide_ocr import slide_ocr

logger = get_logger(__name__)

SLIDE_TAG = "slides"

# Actions that change what's on screen and are worth OCR-verifying.
SLIDE_CHANGE_ACTIONS = ("next_slide", "prev_slide", "next_item", "prev_item")


class SlideVerifier:
    """Tracks the last OCR'd slide text and flags no-op slide changes."""

    def __init__(self) -> None:
        self.last_text: str = ""

    def current_text(self) -> str:
        frame, _ = frame_capture.get_frame(SLIDE_TAG)
        return slide_ocr.read_text(frame)

    async def verify_after_action(self, action: str) -> None:
        """Call (as a background task) right after a slide-changing action
        has been sent to EasyWorship."""
        before = self.last_text
        await asyncio.sleep(settings.slide_verify_delay_seconds)
        after = self.current_text()
        self.last_text = after
        if before and after and before == after:
            logger.warning("EasyWorship slide unchanged after action", action=action, text=after)
            event_bus.publish(
                {"event": "EASYWORSHIP_SLIDE_STUCK", "payload": {"action": action, "text": after}}
            )

    def snapshot(self) -> dict:
        return {
            "enabled": settings.easyworship_slide_verify_enabled,
            "last_ocr_text": self.last_text,
        }


# Module-level singleton
slide_verifier = SlideVerifier()


def _register_slide_capture_input() -> None:
    use_dir = settings.vision_frame_source == "dir" and settings.vision_frame_dir
    if use_dir:
        frame_capture.register_input(SLIDE_TAG, dir_provider(f"{settings.vision_frame_dir}/{SLIDE_TAG}"))
    elif settings.vision_slides_device is not None:
        frame_capture.register_input(SLIDE_TAG, device_provider(settings.vision_slides_device))
    else:
        logger.warning(
            "EASYWORSHIP_SLIDE_VERIFY_ENABLED is set but VISION_SLIDES_DEVICE is not configured"
        )


async def start_slide_verification() -> None:
    """Registers the camera-2/slides frame_capture input and ensures capture
    is running. Shares the FrameCaptureService singleton with the broader
    vision layer (WO-VISION-1) if that's also enabled -- both `start()` and
    `register_input()` are idempotent/independent per-tag."""
    if not settings.easyworship_slide_verify_enabled:
        return
    _register_slide_capture_input()
    await frame_capture.start()
    logger.info("Slide verification started", device=settings.vision_slides_device)


async def stop_slide_verification() -> None:
    if not settings.easyworship_slide_verify_enabled:
        return
    await frame_capture.stop()
