"""Phase 4: the trust layer -- learning loop, adaptive confidence, evidence fusion.

These three components are what let a deployment safely move from *assisted* to
*ai_directed*. All are gated off by default; with the flags off the director
behaves exactly as before.

- ``LearningRecorder`` -- every operator approval/rejection (and every
  auto-executed action) is written to production memory as a labeled outcome,
  so the existing retrieval-augmented context (docs/ai-director.md) can surface
  "last time we were here, the operator overrode this" on future decisions.
- ``AdaptiveConfidence`` -- tracks per-category outcomes and nudges the
  effective confidence threshold within bounds: repeated operator rejections
  make a category more conservative; clean approvals relax it.
- ``EvidenceFusion`` -- before a camera cut, require the vision layer to
  corroborate (a person is actually framed for the target role, the feed isn't
  black). Missing vision never vetoes; only contradicting vision does.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from app.config import settings
from app.director.action_models import (
    CAMERA_ACTION_TYPES,
    DirectorAction,
    DirectorActionType,
)
from app.logging_config import get_logger

logger = get_logger(__name__)


class LearningRecorder:
    """Persists operator corrections and auto-executions to production memory."""

    async def record_approval(self, action: DirectorAction) -> None:
        await self._record("operator_approval", action, "operator approved")

    async def record_rejection(self, action: DirectorAction) -> None:
        await self._record("operator_override", action, "operator rejected")

    async def record_auto(self, action: DirectorAction) -> None:
        await self._record("ai_auto_executed", action, "auto-executed")

    async def _record(self, category: str, action: DirectorAction, verb: str) -> None:
        if not settings.ai_director_learning_enabled:
            return
        text = (
            f"AI proposed {action.type.value}"
            f"{f' -> {action.target}' if action.target else ''} "
            f"(confidence {action.confidence:.2f}); {verb}. Reason: {action.reason}"
        )
        try:
            import asyncio

            from app.memory.production_memory import memory_manager

            await asyncio.to_thread(
                memory_manager.record_observation,
                category=category,
                text=text,
                source="ai_director",
            )
        except Exception:
            logger.warning("Learning recorder failed", exc_info=True)


class AdaptiveConfidence:
    """Per-category confidence-threshold adjustment from operator feedback."""

    def __init__(self) -> None:
        self._delta: Dict[str, float] = {}

    def threshold_for(self, category: str, base: float) -> float:
        if not settings.ai_director_adaptive_confidence:
            return base
        adjusted = base + self._delta.get(category, 0.0)
        return max(settings.ai_director_adaptive_min, min(settings.ai_director_adaptive_max, adjusted))

    def record_outcome(self, category: str, good: bool) -> None:
        """good=True (approved/clean auto) relaxes; good=False (rejected) tightens."""
        if not settings.ai_director_adaptive_confidence:
            return
        step = settings.ai_director_adaptive_step
        self._delta[category] = self._delta.get(category, 0.0) + (-step if good else step)
        logger.debug("Adaptive confidence updated", category=category, delta=self._delta[category])

    def snapshot(self) -> Dict[str, float]:
        return dict(self._delta)


class EvidenceFusion:
    """Cross-checks a camera cut against the vision layer's observations."""

    def corroborates(self, action: DirectorAction, vision: dict) -> Tuple[bool, Optional[str]]:
        """Return (ok, reason_if_blocked). Only *contradicting* vision blocks;
        missing vision passes (can't veto on absence of evidence)."""
        if not settings.ai_director_evidence_fusion:
            return True, None
        if action.type not in CAMERA_ACTION_TYPES:
            return True, None

        role = action.target
        if action.type == DirectorActionType.PTZ_PRESET:
            role = self._role_for_preset(action)
        if not role:
            return True, None

        obs = (vision or {}).get(role)
        if obs is None:
            return True, None  # no vision evidence for this role -> don't veto

        health = getattr(obs, "frame_health", None)
        if health in ("black", "no_frame"):
            return False, f"vision: {role} feed is {health}"
        person_present = getattr(obs, "person_present", None)
        if person_present is False:
            return False, f"vision: no person framed for {role}"
        return True, None

    @staticmethod
    def _role_for_preset(action: DirectorAction) -> Optional[str]:
        camera_id = action.parameters.get("camera_id")
        preset_id = action.parameters.get("preset_id")
        if camera_id is None or preset_id is None:
            return None
        for role in ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide"):
            if (
                getattr(settings, f"camera_role_{role}_camera", None) == int(camera_id)
                and getattr(settings, f"camera_role_{role}_preset", None) == int(preset_id)
            ):
                return role
        return None


# Module-level singletons.
learning_recorder = LearningRecorder()
adaptive_confidence = AdaptiveConfidence()
evidence_fusion = EvidenceFusion()
