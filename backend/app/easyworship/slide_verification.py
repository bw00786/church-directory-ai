"""Slide-change verification via OCR on a dedicated camera-2 feed
(WO-EWVERIFY-1/-2/-3).

EasyWorship has no read-back API (see .service), so `EasyWorshipService`'s
current-item tracking is best-effort keystroke counting only -- it silently
drifts if a keystroke is dropped or EasyWorship is also operated manually.
This adds an independent visual check: after a next/prev slide/item action,
OCR the raw camera-2 feed (tapped *separately* from the switched ATEM
program -- e.g. an HDMI splitter/capture device on the EasyWorship laptop's
own output, configured via `SLIDE_VERIFY_DEVICE`) and confirm the extracted
text actually changed.

WO-EWVERIFY-2 hardening: instead of a single fixed delay + one-shot compare
(which false-fails on slow fades and false-passes if OCR fires mid-transition),
the post-action text is polled every `SLIDE_VERIFY_POLL_MS` until it is stable
across two consecutive samples AND differs from the pre-action text. If that
never happens within `SLIDE_VERIFY_TIMEOUT_SECONDS`, automation is **halted**
for operator attention -- the keystroke is never auto-retried, because a wrong
double-advance in front of the congregation is worse than a missed advance.

WO-EWVERIFY-3 semantic: when `SLIDE_VERIFY_SEMANTIC_ENABLED`, the stable
post-action text is additionally fuzzy-matched against the expected lyric text
for the current song position (via `slide_expected.expected_text_provider`).
A changed-but-wrong slide (e.g. a double-advance showing the wrong stanza) is
the highest-severity state this system can produce, so it also halts. When no
authoritative expected text exists (sermon slides, announcements) the result
is change-only and no correctness judgement is made.

Hardening: OCR being *unavailable* (easyocr missing, model not loaded, no
frame from the capture device, a read raising, or only empty reads while the
device is delivering frames) is a third state, ``unknown`` -- it is logged,
published as ``SLIDE_VERIFY_UNKNOWN`` and counted in ``snapshot()``, but never
halts automation: halting is reserved for *evidence* that the slide did not
change (or changed to the wrong text). Concurrent verifications are
serialized (they share ``last_text``) and OCR runs off the event loop.

The last extracted text is exposed via `snapshot()` so a human operator can
cross-check it against what the congregation should be reading.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Awaitable, Callable, Optional

from app.config import settings
from app.easyworship.slide_expected import ExpectedTextProvider, expected_text_provider
from app.events.bus import event_bus
from app.logging_config import get_logger
from app.vision.executors import run_in
from app.vision.frame_capture import device_provider, dir_provider, frame_capture
from app.vision.slide_ocr import slide_ocr

logger = get_logger(__name__)

SLIDE_TAG = "slides"

# Actions that change what's on screen and are worth OCR-verifying.
SLIDE_CHANGE_ACTIONS = ("next_slide", "prev_slide", "next_item", "prev_item")

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


class SlideVerifyConfigError(RuntimeError):
    """Raised at startup when slide verification is enabled but unusable."""


def normalize(text: str) -> str:
    """Case-fold, strip punctuation, collapse whitespace. Shared by both the
    change-detection stability check and the semantic lyric comparison so the
    two agree on what "the same text" means."""
    if not text:
        return ""
    folded = _PUNCT.sub(" ", text.casefold())
    return _WS.sub(" ", folded).strip()


def fuzzy_ratio(a: str, b: str) -> float:
    """Similarity in [0.0, 1.0] between two strings after normalization.
    1.0 if both are empty, 0.0 if exactly one is empty."""
    na, nb = normalize(a), normalize(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


class SlideVerifyOutcome(str, Enum):
    VERIFIED = "verified"                          # changed; semantic off
    VERIFIED_CORRECT = "verified_correct"          # changed AND matches expected
    VERIFIED_CHANGED_MISMATCH = "verified_changed_mismatch"  # changed but wrong
    CHANGE_ONLY = "change_only"                     # changed; no expected text
    TIMEOUT = "timeout"                             # never visibly changed
    UNKNOWN = "unknown"                             # OCR unavailable; no judgement


# Outcomes that halt automation for operator attention.
_HALTING = frozenset({SlideVerifyOutcome.TIMEOUT, SlideVerifyOutcome.VERIFIED_CHANGED_MISMATCH})


@dataclass
class SlideVerifyResult:
    outcome: SlideVerifyOutcome
    ocr_text: str = ""
    score: Optional[float] = None
    expected: Optional[str] = None
    song_id: Optional[str] = None
    slide_index: Optional[int] = None
    reason: Optional[str] = None   # why an UNKNOWN outcome could not judge

    @property
    def ok(self) -> bool:
        return self.outcome not in _HALTING

    @property
    def known(self) -> bool:
        """True when OCR produced evidence either way (not UNKNOWN)."""
        return self.outcome is not SlideVerifyOutcome.UNKNOWN


class _Poll(str, Enum):
    """Tri-state result of the stabilization poll."""

    VERIFIED = "verified"    # stable text that differs from the pre-action text
    UNCHANGED = "unchanged"  # OCR worked; text stayed the same (or never settled)
    UNKNOWN = "unknown"      # OCR unavailable for (nearly) the whole window


class SlideVerifier:
    """Tracks the last stable slide text and verifies each commanded change.

    `_sleep`/`_now` are injectable so the poll loop is testable without real
    time; `provider` is injectable so semantic tests can supply expected text.
    """

    def __init__(
        self,
        provider: Optional[ExpectedTextProvider] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.last_text: str = ""
        self.consecutive_unknown: int = 0
        self.last_unknown_reason: Optional[str] = None
        self._provider = provider or expected_text_provider
        self._sleep = sleep
        self._now = now
        # verify_after_action tasks share last_text; serialize them. The lock
        # is bound lazily to the running loop (the singleton outlives test loops).
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    @property
    def busy(self) -> bool:
        return self._lock is not None and self._lock.locked()

    def current_text(self) -> Optional[str]:
        """One OCR sample of the slides feed. ``None`` means *unknown*: no
        frame from the capture device or the OCR reader could not run. A
        string (possibly empty) means OCR ran on a real frame. Blocking
        (1-5 s on CPU) -- callers run it via :meth:`_sample`."""
        frame, _ = frame_capture.get_frame(SLIDE_TAG)
        if frame is None:
            return None
        read = slide_ocr.read(frame)
        if not read.ok:
            return None
        return read.text

    async def _sample(self) -> Optional[str]:
        """``current_text()`` off the event loop, on the shared OCR pool."""
        try:
            return await run_in("ocr", self.current_text)
        except Exception:
            logger.warning("Slide OCR sample failed", exc_info=True)
            return None

    async def _poll_until_stable(self, before_norm: str) -> tuple[_Poll, Optional[str], Optional[str]]:
        """Poll OCR text until it is stable across two consecutive samples and
        differs from the pre-action text.

        Returns ``(VERIFIED, raw_text, None)`` on success. On timeout returns
        ``(UNCHANGED, last_known_text, None)`` when OCR delivered enough
        evidence (two or more real reads) that the slide did not visibly
        change, or ``(UNKNOWN, None, reason)`` when OCR was unavailable --
        no frame, reader missing/failed, or only empty reads while the capture
        device was delivering frames -- so no judgement is possible.
        """
        poll = max(0.0, settings.slide_verify_poll_ms / 1000.0)
        deadline = self._now() + settings.slide_verify_timeout_seconds
        prev_norm: Optional[str] = None
        last_known: Optional[str] = None
        known = 0
        no_frame = 0
        empty = 0
        while self._now() < deadline:
            await self._sleep(poll)
            raw = await self._sample()
            if raw is None:
                no_frame += 1
                prev_norm = None  # stability needs two *consecutive* real reads
                continue
            norm = normalize(raw)
            if not norm:
                # Device has frames but OCR sees no text (blank/transition or a
                # reader that cannot see this feed) -- not evidence either way.
                empty += 1
                prev_norm = None
                continue
            known += 1
            last_known = raw
            if prev_norm is not None and norm == prev_norm and norm != before_norm:
                return _Poll.VERIFIED, raw, None
            prev_norm = norm
        if known >= 2:
            return _Poll.UNCHANGED, last_known, None
        if known == 0 and no_frame and not empty:
            reason = "no frame from slides capture" if slide_ocr.available else "OCR reader unavailable or no frame"
        elif known == 0 and empty:
            reason = "OCR returned no text while frames were available"
        else:
            reason = "insufficient OCR reads within timeout"
        return _Poll.UNKNOWN, None, reason

    def _classify(
        self, text: str, song_id: Optional[str], slide_index: Optional[int]
    ) -> tuple[SlideVerifyOutcome, Optional[str], Optional[float]]:
        if not settings.slide_verify_semantic_enabled:
            return SlideVerifyOutcome.VERIFIED, None, None
        expected = self._provider.expected_text(song_id, slide_index)
        if expected is None:
            return SlideVerifyOutcome.CHANGE_ONLY, None, None
        score = fuzzy_ratio(text, expected)
        if score >= settings.slide_verify_semantic_threshold:
            return SlideVerifyOutcome.VERIFIED_CORRECT, expected, score
        return SlideVerifyOutcome.VERIFIED_CHANGED_MISMATCH, expected, score

    async def verify_after_action(
        self,
        action: str,
        song_id: Optional[str] = None,
        slide_index: Optional[int] = None,
    ) -> SlideVerifyResult:
        """Call (as a background task) right after a slide-changing action has
        been sent to EasyWorship. Polls for a stable visible change, then (if
        semantic verification is on) checks it against the expected lyric.
        Concurrent calls run one at a time, in order."""
        async with self._get_lock():
            return await self._verify_locked(action, song_id, slide_index)

    async def _verify_locked(
        self, action: str, song_id: Optional[str], slide_index: Optional[int]
    ) -> SlideVerifyResult:
        before = self.last_text
        state, text, reason = await self._poll_until_stable(normalize(before))

        if state is _Poll.UNKNOWN:
            # No evidence: keep last_text (we learned nothing), never halt.
            self.consecutive_unknown += 1
            self.last_unknown_reason = reason
            result = SlideVerifyResult(SlideVerifyOutcome.UNKNOWN, ocr_text=before, reason=reason)
            self._finish(action, result)
            return result

        self.consecutive_unknown = 0
        self.last_unknown_reason = None
        if state is _Poll.UNCHANGED:
            self.last_text = text or before
            result = SlideVerifyResult(SlideVerifyOutcome.TIMEOUT, ocr_text=self.last_text)
            self._finish(action, result)
            return result

        stable = text or ""
        self.last_text = stable
        outcome, expected, score = self._classify(stable, song_id, slide_index)
        result = SlideVerifyResult(
            outcome,
            ocr_text=stable,
            score=score,
            expected=expected,
            song_id=song_id,
            slide_index=slide_index,
        )
        self._finish(action, result)
        return result

    def _finish(self, action: str, result: SlideVerifyResult) -> None:
        # Provenance: no outcome without its evidence.
        log = logger.warning if (not result.ok or not result.known) else logger.info
        log(
            "Slide verification",
            action=action,
            outcome=result.outcome.value,
            song_id=result.song_id,
            expected_slide_index=result.slide_index,
            ocr_text=result.ocr_text[:120],
            score=result.score,
            threshold=settings.slide_verify_semantic_threshold,
            reason=result.reason,
            consecutive_unknown=self.consecutive_unknown,
        )
        if result.outcome is SlideVerifyOutcome.UNKNOWN:
            # Visible, but not a halt: OCR being down is a perception problem,
            # not evidence that EasyWorship ignored the keystroke.
            event_bus.publish(
                {
                    "event": "SLIDE_VERIFY_UNKNOWN",
                    "payload": {
                        "action": action,
                        "reason": result.reason,
                        "consecutive_unknown": self.consecutive_unknown,
                    },
                }
            )
        elif result.outcome is SlideVerifyOutcome.TIMEOUT:
            event_bus.publish(
                {"event": "EASYWORSHIP_SLIDE_STUCK", "payload": {"action": action, "text": result.ocr_text}}
            )
            self._halt(f"slide unchanged after {action} within timeout")
        elif result.outcome is SlideVerifyOutcome.VERIFIED_CHANGED_MISMATCH:
            event_bus.publish(
                {
                    "event": "EASYWORSHIP_SLIDE_MISMATCH",
                    "payload": {
                        "action": action,
                        "text": result.ocr_text,
                        "expected": result.expected,
                        "score": result.score,
                    },
                }
            )
            self._halt(f"slide mismatch after {action} (score {result.score})")

    def _halt(self, reason: str) -> None:
        """Halt automation for operator attention. Visible failure over silent
        green -- the operator decides; we never auto-retry the keystroke."""
        logger.warning("Halting automation for operator attention", reason=reason)
        event_bus.publish({"event": "SLIDE_VERIFY_HALT", "payload": {"reason": reason}})
        try:
            from app.director.ai_director_runtime import ai_director_runtime

            ai_director_runtime.set_mode("manual")
        except Exception:
            logger.warning("Could not switch AI Director to manual", exc_info=True)

    def snapshot(self) -> dict:
        return {
            "enabled": settings.slide_verify_enabled,
            "semantic_enabled": settings.slide_verify_semantic_enabled,
            "last_ocr_text": self.last_text,
            "ocr_available": slide_ocr.available,
            "consecutive_unknown": self.consecutive_unknown,
            "last_unknown_reason": self.last_unknown_reason,
        }


# Module-level singleton
slide_verifier = SlideVerifier()


def validate_slide_verify_config() -> None:
    """Fail loud at startup on unusable configuration. Runs unconditionally so
    that even a *disabled* change-detector with semantic wrongly enabled is
    caught here rather than silently doing nothing."""
    if settings.slide_verify_semantic_enabled and not settings.slide_verify_enabled:
        raise SlideVerifyConfigError(
            "SLIDE_VERIFY_SEMANTIC_ENABLED=true requires SLIDE_VERIFY_ENABLED=true "
            "(semantic verification builds on change-detection)"
        )
    if not settings.slide_verify_enabled:
        return
    use_dir = settings.vision_frame_source == "dir" and bool(settings.vision_frame_dir)
    if not use_dir and not settings.slide_verify_device:
        raise SlideVerifyConfigError(
            "SLIDE_VERIFY_ENABLED=true but SLIDE_VERIFY_DEVICE is empty; set the "
            "cv2 device index/name for the camera-2/EasyWorship-laptop slides tap"
        )


def register_slide_capture_input() -> None:
    use_dir = settings.vision_frame_source == "dir" and settings.vision_frame_dir
    if use_dir:
        frame_capture.register_input(SLIDE_TAG, dir_provider(f"{settings.vision_frame_dir}/{SLIDE_TAG}"))
    else:
        frame_capture.register_input(SLIDE_TAG, device_provider(settings.slide_verify_device))


async def _probe_device(
    get_frame: Callable[[], object],
    window_seconds: float = 2.0,
    interval: float = 0.1,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], float] = time.monotonic,
) -> None:
    """Confirm the slides input actually produces a frame within a short
    window, so an enabled-but-dead capture fails at *startup* rather than at
    the first verification attempt mid-service."""
    deadline = now() + window_seconds
    while now() < deadline:
        if get_frame() is not None:
            return
        await sleep(interval)
    raise SlideVerifyConfigError(
        f"SLIDE_VERIFY_DEVICE={settings.slide_verify_device!r} produced no frames "
        f"within {window_seconds}s; check the capture device / HDMI tap"
    )


async def start_slide_verification() -> None:
    """Validate config (always), then -- if enabled -- register the
    camera-2/slides frame_capture input, start capture, and probe for a live
    frame. Shares the FrameCaptureService singleton with the broader vision
    layer (WO-VISION-1); `start()` and `register_input()` are idempotent."""
    validate_slide_verify_config()
    if not settings.slide_verify_enabled:
        return
    register_slide_capture_input()
    await frame_capture.start()
    await _probe_device(lambda: frame_capture.get_frame(SLIDE_TAG)[0])
    logger.info(
        "Slide verification started",
        device=settings.slide_verify_device,
        semantic=settings.slide_verify_semantic_enabled,
    )


async def stop_slide_verification() -> None:
    if not settings.slide_verify_enabled:
        return
    await frame_capture.stop()
