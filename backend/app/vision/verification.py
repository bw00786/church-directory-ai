"""PTZ action verification: close the loop on preset recalls (FR-3).

After a ``PTZ_SELECT_ROLE`` / ``PTZ_PRESET`` completes, grab a frame from that
camera (direct snapshot preferred), wait out the motor settle, and classify the
result:

- ``verified``      person in ROI, offset within tolerance, frame healthy
- ``bad_framing``   person present but out of ROI / offset too large / black frame
- ``subject_absent`` no person yet — re-checked at 1 Hz up to the wait window
                    (empty-stage caveat: a walk-up must not hard-fail immediately)
- ``pending``        transient state during the subject wait
- ``unverified``     no frame available (feeds the consecutive-unverified ladder)

On ``bad_framing``/black while the camera is on **preview**, a pending
ATEM cut/auto to it is blocked for ``PTZ_BLOCK_SECONDS`` unless the operator
overrides — never take a known-bad shot to program. ``PTZ_VERIFY_ACTION=log``
makes this observation-only (verdicts + events, no blocking).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.domain.observations import VisionObservation
from app.domain.service_context import service_context
from app.events.bus import event_bus
from app.logging_config import get_logger
from app.vision.frame_capture import frame_capture
from app.vision.perception import PersonDetector, analyze, roi_for_role

logger = get_logger(__name__)


@dataclass
class VerifyResult:
    status: str          # verified | bad_framing | subject_absent | unverified
    reason: str
    role: str
    camera_id: int
    subject_dx: float = 0.0
    subject_dy: float = 0.0
    frame_health: str = "ok"


def camera_to_atem_input(camera_id: int) -> Optional[int]:
    if camera_id == 1:
        return settings.atem_camera1_input
    if camera_id == 2:
        return settings.atem_camera2_input
    return None


class PtzVerifier:
    """Verifies PTZ actions against real frames and gates known-bad cuts."""

    def __init__(self):
        self._blocked: dict = {}          # atem_input -> monotonic deadline
        self._unverified_streak = 0
        self._detector: Optional[PersonDetector] = None
        # Injection points for tests (deterministic clock / frames).
        self._sleep = asyncio.sleep

    def _get_detector(self) -> PersonDetector:
        # Reuse the perception loop's detector when running; else lazy-build.
        from app.vision.perception import perception_loop

        if perception_loop.detector is not None:
            return perception_loop.detector
        if self._detector is None:
            self._detector = PersonDetector()
        return self._detector

    def _observe(self, role: str, camera_id: int, on_program: bool) -> Optional[VisionObservation]:
        tag = f"camera_{camera_id}"
        frame, _ = frame_capture.get_frame(tag)
        if frame is None and on_program:
            frame, _ = frame_capture.get_frame("program")
        if frame is None:
            return None
        obs, _ = analyze(frame, roi_for_role(role), self._get_detector(), None)
        obs.role = role
        obs.camera_id = camera_id
        return obs

    async def verify(
        self,
        role: str,
        camera_id: int,
        *,
        on_program: bool = False,
        on_preview: bool = False,
        override: bool = False,
    ) -> VerifyResult:
        await self._sleep(settings.ptz_verify_delay_ms / 1000.0)

        obs = self._observe(role, camera_id, on_program)
        if obs is None:
            return self._resolve_unverified(role, camera_id)

        if obs.frame_health in ("black", "frozen"):
            return self._resolve_fail(
                role, camera_id, "bad_framing", obs, on_preview, override, block=obs.frame_health == "black"
            )

        # Empty-stage caveat: wait for a subject before failing.
        waited = 0.0
        while not obs.person_present and waited < settings.ptz_subject_wait_seconds:
            event_bus.publish({"event": "PTZ_VERIFY_PENDING", "payload": {"role": role, "camera": camera_id}})
            await self._sleep(1.0)
            waited += 1.0
            probe = self._observe(role, camera_id, on_program)
            if probe is not None:
                obs = probe

        if not obs.person_present:
            return self._resolve_subject_absent(role, camera_id, obs)

        in_frame = obs.person_in_roi and obs.offset_magnitude <= settings.ptz_offset_max and obs.frame_health == "ok"
        if in_frame:
            return self._resolve_verified(role, camera_id, obs)
        return self._resolve_fail(role, camera_id, "bad_framing", obs, on_preview, override, block=True)

    # -- resolutions ----------------------------------------------------------
    def _resolve_verified(self, role, camera_id, obs) -> VerifyResult:
        self._unverified_streak = 0
        result = VerifyResult("verified", "ok", role, camera_id, obs.subject_dx, obs.subject_dy, obs.frame_health)
        service_context.record_action(f"PTZ_VERIFY: {role} verified (dx={obs.subject_dx}, dy={obs.subject_dy})")
        event_bus.publish({"event": "PTZ_VERIFIED", "payload": self._payload(result)})
        return result

    def _resolve_fail(self, role, camera_id, status, obs, on_preview, override, block) -> VerifyResult:
        self._unverified_streak = 0
        result = VerifyResult(status, status, role, camera_id, obs.subject_dx, obs.subject_dy, obs.frame_health)
        event_bus.publish({"event": "PTZ_VERIFY_FAILED", "payload": self._payload(result)})
        logger.warning("PTZ verify failed", role=role, camera=camera_id, reason=status, health=obs.frame_health)
        if block and on_preview and not override and settings.ptz_verify_action != "log":
            atem_input = camera_to_atem_input(camera_id)
            if atem_input is not None:
                self._blocked[atem_input] = time.monotonic() + settings.ptz_block_seconds
                logger.warning("Blocking preview->program to bad shot", atem_input=atem_input)
        return result

    def _resolve_subject_absent(self, role, camera_id, obs) -> VerifyResult:
        # Never blocks preview->program by itself (unless frame_health is bad,
        # handled above). Resolves to a fail-with-reason after the wait window.
        self._unverified_streak = 0
        result = VerifyResult("subject_absent", "subject_absent", role, camera_id, frame_health=obs.frame_health)
        event_bus.publish({"event": "PTZ_VERIFY_FAILED", "payload": self._payload(result)})
        return result

    def _resolve_unverified(self, role, camera_id) -> VerifyResult:
        self._unverified_streak += 1
        result = VerifyResult("unverified", "no_frame", role, camera_id, frame_health="no_frame")
        event_bus.publish({"event": "PTZ_UNVERIFIED", "payload": self._payload(result)})
        if self._unverified_streak >= settings.ptz_unverified_max:
            self._drop_to_assisted()
        return result

    def _drop_to_assisted(self) -> None:
        logger.warning("Unverified ladder tripped; dropping camera_change to assisted",
                       streak=self._unverified_streak)
        event_bus.publish({"event": "PERCEPTION_DEGRADED", "payload": {"component": "ptz_verify", "reason": "unverified_ladder"}})
        with_runtime = True
        try:
            from app.director.ai_director_runtime import ai_director_runtime

            if getattr(ai_director_runtime, "mode", None) == "ai_directed":
                ai_director_runtime.set_mode("assisted")
        except Exception:
            with_runtime = False
        if not with_runtime:
            logger.info("AI director runtime unavailable for ladder demotion")

    # -- block state (preview -> program gate) --------------------------------
    def is_blocked(self, atem_input: int) -> bool:
        deadline = self._blocked.get(atem_input)
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            self._blocked.pop(atem_input, None)
            return False
        return True

    def clear_block(self, atem_input: int) -> None:
        """Operator override."""
        self._blocked.pop(atem_input, None)

    def blocked_inputs(self) -> list:
        now = time.monotonic()
        return [i for i, d in self._blocked.items() if d > now]

    @staticmethod
    def _payload(result: VerifyResult) -> dict:
        return {
            "status": result.status,
            "reason": result.reason,
            "role": result.role,
            "camera_id": result.camera_id,
            "subject_dx": result.subject_dx,
            "subject_dy": result.subject_dy,
            "frame_health": result.frame_health,
        }


# Module-level singleton
ptz_verifier = PtzVerifier()
