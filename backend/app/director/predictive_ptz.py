"""Phase 2: predictive PTZ + ATEM-preview staging.

Reactive switching cuts *after* the moment has already started (an on-air pan,
or a cut to an empty podium). Predictive staging pre-positions the shot on the
**preview** bus so the eventual take is instant and clean: it recalls the PTZ
preset for a role and points ATEM preview at that camera -- but it NEVER cuts or
autos, so nothing reaches the program output. The real take still happens later
through the normal policy-gated action path.

Gated by ``settings.ptz_predictive_preview`` (default off). Safe to call in any
mode: preview staging is not an on-air action.
"""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)


def _camera_for_role(role: str) -> Optional[int]:
    return getattr(settings, f"camera_role_{role}_camera", None)


def _preset_for_role(role: str) -> Optional[int]:
    return getattr(settings, f"camera_role_{role}_preset", None)


class PredictivePreview:
    """Stages a role's shot on preview without touching the program bus."""

    def __init__(self) -> None:
        self._staged_role: Optional[str] = None

    @property
    def staged_role(self) -> Optional[str]:
        return self._staged_role

    async def stage_role(self, role: str) -> bool:
        """Recall the PTZ preset for ``role`` and set ATEM preview to it.

        Returns True if staging was attempted (flag on and role resolvable).
        Never cuts; a failure to move/preview is logged, not raised.
        """
        if not settings.ptz_predictive_preview or not role:
            return False
        if role == self._staged_role:
            return True

        camera_id = _camera_for_role(role)
        if camera_id is None:
            return False

        from app.cameras.service import camera_service

        preset_id = _preset_for_role(role)
        try:
            if preset_id is not None:
                await camera_service.move_to_preset(int(camera_id), int(preset_id))
            else:
                await camera_service.move_to_role(role)
        except Exception:
            logger.warning("Predictive PTZ move failed", role=role, exc_info=True)
            return False

        atem_input = self._atem_input_for(int(camera_id))
        if atem_input is not None:
            try:
                from app.dependencies import get_atem_service_instance

                await get_atem_service_instance().set_preview(int(atem_input))
            except Exception:
                logger.warning("Predictive preview set failed", role=role, exc_info=True)

        self._staged_role = role
        event_bus.publish(
            {
                "event": "PTZ_PRESTAGED",
                "payload": {"role": role, "camera_id": camera_id, "atem_preview": atem_input},
            }
        )
        logger.info("Predictive preview staged", role=role, camera_id=camera_id, atem_preview=atem_input)
        return True

    def clear(self) -> None:
        self._staged_role = None

    @staticmethod
    def _atem_input_for(camera_id: int) -> Optional[int]:
        try:
            from app.vision.verification import camera_to_atem_input

            return camera_to_atem_input(camera_id)
        except Exception:
            return None


# Module-level singleton.
predictive_preview = PredictivePreview()
