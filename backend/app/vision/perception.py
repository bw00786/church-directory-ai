"""Local perception loop: person detection, ROI framing, frame health (FR-2).

Anonymous detection only (no identity, no biometrics). For each configured
camera role it reads the latest frame from ``FrameCaptureService``, runs a
provider-tiered person detector (yolo -> opencv_hog -> health-only), and derives
``person_present``, ``person_in_roi``, a normalized ``subject_offset`` vs. the
role's ROI centre, and ``frame_health`` (black via mean luma, frozen via an
average hash). Results are published as ``VisionObservation``s into
``ServiceContext`` for the LLM and used by PTZ verification.

The detector is CPU-heavy (hundreds of ms per frame on HOG/YOLO-n), so it runs
on the dedicated ``detector`` thread pool (``app.vision.executors``) rather
than inline on the event loop, and within one tick it runs **once per frame
tag**: every role mapped to the same camera input shares that frame's
detections instead of re-running the model per role.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Callable, Dict, List, Optional, Tuple

from app.config import settings
from app.domain.observations import VisionObservation
from app.domain.service_context import service_context
from app.events.bus import event_bus
from app.logging_config import get_logger
from app.vision.executors import run_in
from app.vision.frame_capture import frame_capture

logger = get_logger(__name__)

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

BLACK_LUMA = 10.0     # mean 0-255 gray below this => black frame
_ROLES = ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide")

# Detector returns normalized [(cx, cy, w, h), ...] boxes (all in [0, 1]).
Detection = Tuple[float, float, float, float]
DetectFn = Callable[[object], List[Detection]]


class RoiError(ValueError):
    """Raised when a role's configured ROI is out of bounds (no clamping)."""


def parse_roi(spec: str, key: str) -> Tuple[float, float, float, float]:
    parts = [p.strip() for p in str(spec).split(",")]
    if len(parts) != 4:
        raise RoiError(f"{key} must be 'x,y,w,h', got: {spec!r}")
    try:
        x, y, w, h = (float(p) for p in parts)
    except ValueError as e:
        raise RoiError(f"{key} has non-numeric value: {spec!r}") from e
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
        raise RoiError(f"{key} components out of [0,1]: {spec!r}")
    if x + w > 1.0 or y + h > 1.0:
        raise RoiError(f"{key} box exceeds frame bounds: {spec!r}")
    return x, y, w, h


def roi_for_role(role: str) -> Tuple[float, float, float, float]:
    key = f"vision_role_roi_{role}"
    spec = getattr(settings, key, None)
    if spec is None:
        raise RoiError(f"no ROI configured for role '{role}' ({key.upper()})")
    return parse_roi(spec, key.upper())


# -- detector tiers ----------------------------------------------------------
class PersonDetector:
    """Provider-tiered anonymous person detector."""

    def __init__(self, provider: Optional[str] = None):
        self.requested = (provider or settings.vision_detector).lower()
        self.provider = "none"
        self._detect: DetectFn = lambda frame: []
        self._select()

    def _select(self) -> None:
        order = {
            "auto": ["yolo", "opencv_hog"],
            "yolo": ["yolo"],
            "opencv_hog": ["opencv_hog"],
        }.get(self.requested, ["yolo", "opencv_hog"])

        for tier in order:
            try:
                self._detect = self._load(tier)
                self.provider = tier
                logger.info("Person detector selected", provider=tier)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("Detector tier unavailable", tier=tier, reason=str(e))
        # No tier available -> health-only, loudly.
        self.provider = "none"
        event_bus.publish(
            {"event": "PERCEPTION_DEGRADED", "payload": {"component": "vision_detector", "reason": "no detector tier available"}}
        )
        logger.warning("No person-detector tier available; vision runs frame-health-only")

    def _load(self, tier: str) -> DetectFn:
        if tier == "yolo":
            from ultralytics import YOLO  # optional heavy dep

            model = YOLO("yolov8n.pt")

            def detect(frame):
                res = model.predict(frame, verbose=False, classes=[0])
                out: List[Detection] = []
                h, w = frame.shape[:2]
                for r in res:
                    for box in getattr(r, "boxes", []):
                        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                        out.append(((x1 + x2) / 2 / w, (y1 + y2) / 2 / h, (x2 - x1) / w, (y2 - y1) / h))
                return out

            return detect

        if tier == "opencv_hog":
            import cv2

            hog = cv2.HOGDescriptor()
            hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

            def detect(frame):
                h, w = frame.shape[:2]
                rects, _ = hog.detectMultiScale(frame, winStride=(8, 8))
                return [((rx + rw / 2) / w, (ry + rh / 2) / h, rw / w, rh / h) for (rx, ry, rw, rh) in rects]

            return detect

        raise ValueError(f"unknown detector tier: {tier}")

    def detect(self, frame) -> List[Detection]:
        """Blocking; call from a worker thread (see :meth:`detect_async`)."""
        try:
            return self._detect(frame)
        except Exception:
            logger.exception("Person detection failed")
            return []

    async def detect_async(self, frame) -> List[Detection]:
        """Run the detector on the shared ``detector`` pool off the event loop."""
        return await run_in("detector", self.detect, frame)


# -- pure analysis -----------------------------------------------------------
def frame_health(frame, prev_hash) -> Tuple[str, object]:
    if frame is None or np is None:
        return "no_frame", prev_hash
    arr = np.asarray(frame)
    gray = arr.mean(axis=2) if arr.ndim == 3 else arr
    if float(gray.mean()) < BLACK_LUMA:
        return "black", prev_hash
    # Average hash on an 8x8 downsample for frozen-frame detection.
    small = gray[:: max(1, gray.shape[0] // 8), :: max(1, gray.shape[1] // 8)]
    ahash = (small > small.mean()).astype("uint8").tobytes()
    if prev_hash is not None and ahash == prev_hash:
        return "frozen", ahash
    return "ok", ahash


def needs_detection(health: str, detector: Optional[PersonDetector]) -> bool:
    return health not in ("no_frame", "black") and detector is not None and detector.provider != "none"


def observation_from(health: str, detections: List[Detection], roi) -> VisionObservation:
    """Build the per-role observation from a frame's health and its (shared)
    detections. Pure: the same detections can be projected onto several ROIs."""
    obs = VisionObservation(frame_health=health)
    if not detections:
        return obs
    obs.person_present = True
    # Largest detection by area.
    cx, cy, w, h = max(detections, key=lambda d: d[2] * d[3])
    rx, ry, rw, rh = roi
    roi_cx, roi_cy = rx + rw / 2, ry + rh / 2
    obs.subject_dx = round(cx - roi_cx, 4)
    obs.subject_dy = round(cy - roi_cy, 4)
    obs.person_in_roi = (rx <= cx <= rx + rw) and (ry <= cy <= ry + rh)
    obs.confidence = 1.0
    return obs


def analyze(frame, roi, detector: PersonDetector, prev_hash) -> Tuple[VisionObservation, object]:
    """Blocking single-frame analysis (health + detection + ROI projection).
    Used by PTZ verification from a worker thread and by tests; the perception
    loop uses :meth:`PerceptionLoop.tick_once` which shares detections per tag."""
    health, new_hash = frame_health(frame, prev_hash)
    if not needs_detection(health, detector):
        return VisionObservation(frame_health=health), new_hash
    return observation_from(health, detector.detect(frame), roi), new_hash


# -- loop --------------------------------------------------------------------
class PerceptionLoop:
    """Runs analysis on the program input and each configured camera role."""

    def __init__(self, fps: float = 1.0):
        self.fps = fps
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._hashes: Dict[str, object] = {}   # frame tag -> last average hash
        self.detector: Optional[PersonDetector] = None
        self._roles: List[Tuple[str, str, tuple]] = []
        self.last_tick_detector_calls = 0      # detector runs in the latest tick
        self.last_tick_seconds = 0.0

    def _build_roles(self) -> None:
        """role -> (input_tag, roi). Raises RoiError on misconfig (no guessing)."""
        self._roles = []
        for role in _ROLES:
            camera_id = getattr(settings, f"camera_role_{role}_camera", None)
            if camera_id is None:
                continue
            roi = roi_for_role(role)  # raises on out-of-bounds
            self._roles.append((role, f"camera_{camera_id}", roi))

    async def start(self) -> None:
        if self._running:
            return
        self._build_roles()  # validate ROIs before starting (may raise)
        self.detector = PersonDetector()
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Perception loop started", detector=self.detector.provider, roles=len(self._roles))

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        interval = 1.0 / self.fps if self.fps > 0 else 1.0
        while self._running:
            try:
                await self.tick_once()
            except Exception:
                logger.exception("Perception tick failed")
            await asyncio.sleep(interval)

    async def _frame_result(self, tag: str, cache: Dict[str, Tuple[str, List[Detection]]]) -> Tuple[str, List[Detection]]:
        """Health + detections for a frame tag, computed at most once per tick.
        Detection runs on the ``detector`` pool, never on the event loop."""
        hit = cache.get(tag)
        if hit is not None:
            return hit
        frame, _ = frame_capture.get_frame(tag)
        health, self._hashes[tag] = frame_health(frame, self._hashes.get(tag))
        detections: List[Detection] = []
        if needs_detection(health, self.detector):
            detections = await run_in("detector", self.detector.detect, frame)
            self.last_tick_detector_calls += 1
        cache[tag] = (health, detections)
        return cache[tag]

    async def tick_once(self) -> None:
        """One perception pass over the program input and every configured
        role. Roles sharing a camera input share one detector run."""
        started = time.monotonic()
        self.last_tick_detector_calls = 0
        cache: Dict[str, Tuple[str, List[Detection]]] = {}

        # Program-output health (no ROI; whole-frame occupancy).
        health, detections = await self._frame_result("program", cache)
        obs = observation_from(health, detections, (0.0, 0.0, 1.0, 1.0))
        obs.input = "program"
        service_context.record_vision(obs)

        for role, tag, roi in self._roles:
            health, detections = await self._frame_result(tag, cache)
            obs = observation_from(health, detections, roi)
            obs.input = tag
            obs.role = role
            obs.camera_id = getattr(settings, f"camera_role_{role}_camera", None)
            service_context.record_vision(obs)
        self.last_tick_seconds = round(time.monotonic() - started, 4)

    def status(self) -> dict:
        return {
            "detector": self.detector.provider if self.detector else None,
            "last_tick_detector_calls": self.last_tick_detector_calls,
            "last_tick_seconds": self.last_tick_seconds,
            "inputs": frame_capture.status(),
            "occupancy": {
                key: {"person_in_roi": o.person_in_roi, "frame_health": o.frame_health}
                for key, o in service_context.vision.items()
            },
        }


# Module-level singleton
perception_loop = PerceptionLoop()
