"""Local perception loop: person detection, ROI framing, frame health (FR-2).

Anonymous detection only (no identity, no biometrics). For each configured
camera role it reads the latest frame from ``FrameCaptureService``, runs a
provider-tiered person detector (yolo -> opencv_hog -> health-only), and derives
``person_present``, ``person_in_roi``, a normalized ``subject_offset`` vs. the
role's ROI centre, and ``frame_health`` (black via mean luma, frozen via an
average hash). Results are published as ``VisionObservation``s into
``ServiceContext`` for Claude and used by PTZ verification.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Callable, Dict, List, Optional, Tuple

from app.config import settings
from app.domain.observations import VisionObservation
from app.domain.service_context import service_context
from app.events.bus import event_bus
from app.logging_config import get_logger
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
        try:
            return self._detect(frame)
        except Exception:
            logger.exception("Person detection failed")
            return []


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


def analyze(frame, roi, detector: PersonDetector, prev_hash) -> Tuple[VisionObservation, object]:
    health, new_hash = frame_health(frame, prev_hash)
    obs = VisionObservation(frame_health=health)
    if health in ("no_frame", "black") or detector.provider == "none":
        return obs, new_hash

    detections = detector.detect(frame)
    obs.person_present = len(detections) > 0
    if detections:
        # Largest detection by area.
        cx, cy, w, h = max(detections, key=lambda d: d[2] * d[3])
        rx, ry, rw, rh = roi
        roi_cx, roi_cy = rx + rw / 2, ry + rh / 2
        obs.subject_dx = round(cx - roi_cx, 4)
        obs.subject_dy = round(cy - roi_cy, 4)
        obs.person_in_roi = (rx <= cx <= rx + rw) and (ry <= cy <= ry + rh)
        obs.confidence = 1.0
    return obs, new_hash


# -- loop --------------------------------------------------------------------
class PerceptionLoop:
    """Runs analysis on the program input and each configured camera role."""

    def __init__(self, fps: float = 1.0):
        self.fps = fps
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._hashes: Dict[str, object] = {}
        self.detector: Optional[PersonDetector] = None
        self._roles: List[Tuple[str, str, tuple]] = []

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
            self.tick_once()
            await asyncio.sleep(interval)

    def tick_once(self) -> None:
        # Program-output health (no ROI; whole-frame occupancy).
        frame, _ = frame_capture.get_frame("program")
        obs, self._hashes["program"] = analyze(
            frame, (0.0, 0.0, 1.0, 1.0), self.detector, self._hashes.get("program")
        )
        obs.input = "program"
        service_context.record_vision(obs)

        for role, tag, roi in self._roles:
            frame, _ = frame_capture.get_frame(tag)
            obs, self._hashes[role] = analyze(frame, roi, self.detector, self._hashes.get(role))
            obs.input = tag
            obs.role = role
            camera_id = getattr(settings, f"camera_role_{role}_camera", None)
            obs.camera_id = camera_id
            service_context.record_vision(obs)

    def status(self) -> dict:
        return {
            "detector": self.detector.provider if self.detector else None,
            "inputs": frame_capture.status(),
            "occupancy": {
                key: {"person_in_roi": o.person_in_roi, "frame_health": o.frame_health}
                for key, o in service_context.vision.items()
            },
        }


# Module-level singleton
perception_loop = PerceptionLoop()
