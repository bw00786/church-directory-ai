"""Phase 4: the trust layer -- learning loop, adaptive confidence, evidence fusion.

These three components are what let a deployment safely move from *assisted* to
*ai_directed*. All are gated off by default; with the flags off the director
behaves exactly as before.

- ``LearningRecorder`` -- every operator approval/rejection (and every
  auto-executed action) is written to production memory as a labeled outcome,
  so the existing retrieval-augmented context (docs/ai-director.md) can surface
  "last time we were here, the operator overrode this" on future decisions.
- ``AdaptiveConfidence`` -- nudges the effective confidence threshold per
  ``(category, service_state)`` from **explicit operator feedback only**
  (approve / reject / override). Auto-executions are not feedback. It only ever
  tightens *above* the policy base threshold and persists its deltas to
  ``settings.ai_director_adaptive_file`` so a restart does not forget them.
- ``EvidenceFusion`` -- before a camera cut, require the vision layer to
  corroborate (a person is actually framed for the target role, the feed isn't
  black). Missing *or stale* vision never vetoes; only fresh contradicting
  vision does.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.config import settings
from app.director.action_models import (
    CAMERA_ACTION_TYPES,
    DirectorAction,
    DirectorActionType,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

_ANY_STATE = "*"


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


def _adaptive_path() -> Path:
    return Path(str(getattr(settings, "ai_director_adaptive_file", "data/adaptive-confidence.json")))


class AdaptiveConfidence:
    """Per-(category, service_state) threshold tightening from operator feedback.

    Deltas are keyed ``"category|service_state"`` and persisted as JSON. The
    effective threshold is ``clamp(base + delta, base, adaptive_max)``: the
    policy base is a hard floor, so a run of approvals can at most restore the
    base, never undercut it.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path
        self._delta: Dict[str, float] = {}
        self._feedback: Dict[str, Dict[str, int]] = {}
        self._loaded = False
        self._lock = threading.Lock()

    # -- persistence -----------------------------------------------------------
    def _file(self) -> Path:
        return self._path if self._path is not None else _adaptive_path()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self._file()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                self._delta = {str(k): float(v) for k, v in (data.get("deltas") or {}).items()}
                self._feedback = {
                    str(k): {"approved": int(v.get("approved", 0)), "rejected": int(v.get("rejected", 0))}
                    for k, v in (data.get("feedback") or {}).items()
                }
        except Exception:
            logger.warning("Adaptive confidence file unreadable; starting fresh", path=str(path), exc_info=True)
            self._delta, self._feedback = {}, {}

    def _save(self) -> None:
        path = self._file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "deltas": self._delta,
                "feedback": self._feedback,
            }
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            logger.warning("Adaptive confidence save failed", path=str(path), exc_info=True)

    def reload(self) -> None:
        self._loaded = False
        self._ensure_loaded()

    # -- API -------------------------------------------------------------------
    @staticmethod
    def _key(category: str, service_state: Optional[str]) -> str:
        return f"{category}|{service_state or _ANY_STATE}"

    def delta_for(self, category: str, service_state: Optional[str] = None) -> float:
        self._ensure_loaded()
        specific = self._delta.get(self._key(category, service_state))
        if specific is not None:
            return specific
        return self._delta.get(self._key(category, None), 0.0)

    def threshold_for(self, category: str, base: float, service_state: Optional[str] = None) -> float:
        if not settings.ai_director_adaptive_confidence:
            return base
        adjusted = base + self.delta_for(category, service_state)
        # Never below the policy base; capped at adaptive_max (but a base above
        # adaptive_max still wins -- the floor is absolute).
        return max(base, min(max(settings.ai_director_adaptive_max, base), adjusted))

    def record_feedback(self, category: str, service_state: Optional[str], good: bool) -> None:
        """Explicit operator feedback: approve (good) relaxes toward the base,
        reject/override (not good) tightens. Auto-executions must NOT call this."""
        if not settings.ai_director_adaptive_confidence:
            return
        self._ensure_loaded()
        step = settings.ai_director_adaptive_step
        key = self._key(category, service_state)
        with self._lock:
            current = self._delta.get(key, 0.0) + (-step if good else step)
            # Deltas are tightening-only: a negative delta would undercut the base.
            cap = max(0.0, settings.ai_director_adaptive_max - settings.ai_director_adaptive_min)
            self._delta[key] = round(min(cap, max(0.0, current)), 4)
            counts = self._feedback.setdefault(key, {"approved": 0, "rejected": 0})
            counts["approved" if good else "rejected"] += 1
            self._save()
        logger.debug("Adaptive confidence updated", key=key, delta=self._delta[key])

    def record_outcome(self, category: str, good: bool, service_state: Optional[str] = None) -> None:
        """Backward-compatible alias for :meth:`record_feedback`."""
        self.record_feedback(category, service_state, good)

    def snapshot(self) -> Dict[str, float]:
        self._ensure_loaded()
        return dict(self._delta)

    def status(self, bases: Optional[Dict[str, float]] = None, service_state: Optional[str] = None) -> dict:
        self._ensure_loaded()
        thresholds = {}
        for category, base in (bases or {}).items():
            thresholds[category] = {
                "base": base,
                "effective": round(self.threshold_for(category, base, service_state), 4),
            }
        return {
            "enabled": bool(settings.ai_director_adaptive_confidence),
            "file": str(self._file()),
            "service_state": service_state,
            "deltas": dict(self._delta),
            "feedback": {k: dict(v) for k, v in self._feedback.items()},
            "thresholds": thresholds,
        }


class EvidenceFusion:
    """Cross-checks a camera cut against the vision layer's observations."""

    def corroborates(self, action: DirectorAction, vision: dict) -> Tuple[bool, Optional[str]]:
        """Return (ok, reason_if_blocked). Only *fresh, contradicting* vision
        blocks; missing or stale vision passes (can't veto on absent evidence)."""
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
        if self._is_stale(obs):
            return True, None  # stale evidence is no evidence -> don't veto

        health = getattr(obs, "frame_health", None)
        if health in ("black", "no_frame"):
            return False, f"vision: {role} feed is {health}"
        person_present = getattr(obs, "person_present", None)
        if person_present is False:
            return False, f"vision: no person framed for {role}"
        return True, None

    @staticmethod
    def _is_stale(obs) -> bool:
        max_age = float(getattr(settings, "ai_director_evidence_max_age_seconds", 3.0))
        if max_age <= 0:
            return False
        ts = getattr(obs, "timestamp", None)
        if not isinstance(ts, datetime):
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() > max_age

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
