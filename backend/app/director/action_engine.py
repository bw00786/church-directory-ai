"""Executes policy-validated DirectorActions against real services.

This is the only place the AI Director's proposed actions turn into actual
ATEM/PTZ/EasyWorship calls. Every action passes through the policy engine's
per-category confidence threshold first; rejected actions are logged and
never reach hardware. On-air actions (camera moves and ATEM program changes)
are additionally *paced*: minimum camera hold, switch cooldown, a cap on
consecutive AI switches, and a "cut only at a speech boundary" rule. A paced
action is **held** (``ActionResult.held``), which means "try again next tick",
not "rejected". See docs/ai-director.md ("Action engine & policy thresholds").
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from app.config import settings
from app.dependencies import get_atem_service_instance
from app.domain.service_context import service_context
from app.events.bus import event_bus
from app.logging_config import get_logger
from app.policy.engine import PolicyEngine

from .action_models import (
    ATEM_ACTION_TYPES,
    CAMERA_ACTION_TYPES,
    SLIDE_ACTION_TYPES,
    ActionResult,
    DirectorAction,
    DirectorActionType,
)

logger = get_logger(__name__)

# On-air action types subject to pacing. ATEM_SET_PREVIEW is staging only.
PACED_ACTION_TYPES = CAMERA_ACTION_TYPES | {
    DirectorActionType.ATEM_CUT,
    DirectorActionType.ATEM_AUTO,
    DirectorActionType.ATEM_SET_PROGRAM,
}
PROGRAM_CHANGE_TYPES = {
    DirectorActionType.ATEM_CUT,
    DirectorActionType.ATEM_AUTO,
    DirectorActionType.ATEM_SET_PROGRAM,
}


def _category_for(action_type: DirectorActionType) -> str:
    if action_type in CAMERA_ACTION_TYPES:
        return "camera_change"
    if action_type in SLIDE_ACTION_TYPES:
        return "slide_change"
    if action_type in ATEM_ACTION_TYPES:
        return "atem_transition"
    if action_type == DirectorActionType.CUE_ADVANCE:
        return "cue_advance"
    return "service_state"  # SERVICE_STATE_CHANGE — not hardware-gated


def _seconds_since(value, *, now_mono: float, now_wall: float) -> Optional[float]:
    """Elapsed seconds since ``value`` (datetime, epoch float or monotonic float)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        ts = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return max(0.0, now_wall - ts.timestamp())
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # Epoch timestamps are ~1.7e9; monotonic clocks are far smaller.
    reference = now_wall if number > 1e9 else now_mono
    return max(0.0, reference - number)


class ActionEngine:
    """Validates, paces and dispatches DirectorActions to the real services."""

    def __init__(self, policy_engine: PolicyEngine, *, clock: Callable[[], float] = time.monotonic):
        self.policy_engine = policy_engine
        self._clock = clock
        # Pacing state (monotonic seconds).
        self._last_program_change_at: Optional[float] = None
        self._last_by_category: Dict[str, float] = {}
        self._streak: Dict[str, int] = {}
        self._streak_last_at: Dict[str, float] = {}
        # Speech state from AUDIO_STARTED/AUDIO_STOPPED, keyed by role.
        self._speaking_since: Dict[str, float] = {}
        self._last_stopped_at: Dict[str, float] = {}

    # -- observations fed by the runtime ----------------------------------------
    def note_program_change(self, *, commanded: bool, at: Optional[float] = None) -> None:
        """An ATEM program change was observed. Operator cuts reset the AI streak."""
        at = self._clock() if at is None else at
        self._last_program_change_at = at
        if not commanded:
            self._streak.pop("atem_transition", None)
            self._streak.pop("camera_change", None)

    def note_audio(self, role: str, speaking: bool, at: Optional[float] = None) -> None:
        if not role:
            return
        at = self._clock() if at is None else at
        if speaking:
            self._speaking_since.setdefault(role, at)
        else:
            self._speaking_since.pop(role, None)
            self._last_stopped_at[role] = at

    # -- pacing -----------------------------------------------------------------
    @property
    def last_program_change_at(self) -> Optional[float]:
        return self._last_program_change_at

    def _seconds_since_last_cut(self, now: float) -> Optional[float]:
        """Prefer the context's authoritative ``last_cut_at``; fall back to our log."""
        context_value = getattr(service_context, "last_cut_at", None)
        elapsed = _seconds_since(context_value, now_mono=now, now_wall=time.time())
        own = None if self._last_program_change_at is None else max(0.0, now - self._last_program_change_at)
        if elapsed is None:
            return own
        if own is None:
            return elapsed
        return min(elapsed, own)

    def _on_air_role(self) -> Optional[str]:
        role = service_context.current_camera_role
        if role:
            return role
        obs = service_context.last_observation
        if obs is not None and obs.speaking and obs.speaker_role:
            return obs.speaker_role
        return None

    def _role_speaking_since(self, role: str, now: float) -> Optional[float]:
        """Monotonic time the role started its current utterance, or None if quiet."""
        roles = getattr(service_context, "speaking_roles", None)
        if isinstance(roles, (set, frozenset, list, tuple)):
            # Plain collection of roles currently speaking; onset comes from our audio events.
            return self._speaking_since.get(role, now) if role in roles else None
        entry = roles.get(role) if isinstance(roles, dict) else None
        if isinstance(entry, dict) and "speaking" in entry:
            if not entry.get("speaking"):
                return None
            since = _seconds_since(entry.get("since"), now_mono=now, now_wall=time.time())
            return now - since if since is not None else self._speaking_since.get(role, now)
        if role in self._speaking_since:
            return self._speaking_since[role]
        obs = service_context.last_observation
        if obs is not None and obs.speaking and obs.speaker_role == role:
            return self._speaking_since.get(role, now)
        return None

    def _speech_boundary_hold(self, action: DirectorAction, now: float) -> Optional[str]:
        boundary = float(getattr(settings, "ai_cut_speech_boundary_seconds", 0.8))
        if boundary <= 0 or action.parameters.get("urgent"):
            return None
        role = self._on_air_role()
        if not role:
            return None
        since = self._role_speaking_since(role, now)
        if since is None:
            return None  # on-air speaker is quiet: a natural boundary
        stopped = self._last_stopped_at.get(role)
        last_stop_age = (now - stopped) if stopped is not None else None
        continuous_for = now - since
        if continuous_for >= boundary and (last_stop_age is None or last_stop_age >= boundary):
            return f"speech boundary: {role} speaking continuously for {continuous_for:.1f}s"
        return None

    def pacing_hold(self, action: DirectorAction, now: Optional[float] = None) -> Optional[str]:
        """Why ``action`` must wait (None = go). Only on-air action types are paced."""
        if action.type not in PACED_ACTION_TYPES:
            return None
        now = self._clock() if now is None else now
        policy = self.policy_engine.policy
        category = _category_for(action.type)

        since_cut = self._seconds_since_last_cut(now)
        if since_cut is not None and since_cut < policy.min_camera_hold_seconds:
            return f"min hold: last program change {since_cut:.1f}s ago (< {policy.min_camera_hold_seconds}s)"

        last = self._last_by_category.get(category)
        if last is not None and now - last < policy.camera_switch_cooldown_seconds:
            return (
                f"cooldown: last '{category}' {now - last:.1f}s ago "
                f"(< {policy.camera_switch_cooldown_seconds}s)"
            )

        streak_last = self._streak_last_at.get(category)
        if streak_last is not None and now - streak_last > policy.min_camera_hold_seconds * 2:
            self._streak.pop(category, None)  # quiet long enough: the streak is over
        if self._streak.get(category, 0) >= policy.max_consecutive_switches:
            return f"max consecutive AI '{category}' actions ({policy.max_consecutive_switches}) reached"

        return self._speech_boundary_hold(action, now)

    def _record_paced(self, action: DirectorAction, now: float) -> None:
        if action.type not in PACED_ACTION_TYPES:
            return
        category = _category_for(action.type)
        self._last_by_category[category] = now
        self._streak[category] = self._streak.get(category, 0) + 1
        self._streak_last_at[category] = now
        if action.type in PROGRAM_CHANGE_TYPES:
            self._last_program_change_at = now

    # -- execute ----------------------------------------------------------------
    async def execute(self, action: DirectorAction, *, pace: bool = True) -> ActionResult:
        """Gate, pace and dispatch. ``pace=False`` is for explicit operator
        approvals: the operator's click is the pacing decision."""
        category = _category_for(action.type)
        if category == "service_state":
            # Bookkeeping only (no hardware touched) — not confidence-gated.
            allowed, reason = True, None
        else:
            allowed, reason = self.policy_engine.check_ai_decision(category, action.confidence)

        event_bus.publish(
            {"event": "AI_ACTION_PROPOSED", "payload": action.model_dump(mode="json")}
        )

        if not allowed:
            logger.info("AI action rejected by policy", type=action.type.value, reason=reason)
            event_bus.publish(
                {
                    "event": "AI_ACTION_REJECTED",
                    "payload": {**action.model_dump(mode="json"), "reason": reason},
                }
            )
            return ActionResult(action=action, approved=False, executed=False, detail=reason or "rejected")

        now = self._clock()
        held_reason = self.pacing_hold(action, now) if pace else None
        if held_reason:
            logger.info("AI action held", type=action.type.value, target=action.target, reason=held_reason)
            event_bus.publish(
                {
                    "event": "AI_ACTION_HELD",
                    "payload": {**action.model_dump(mode="json"), "held_reason": held_reason},
                }
            )
            return ActionResult(
                action=action, approved=True, executed=False, detail=held_reason,
                held=True, held_reason=held_reason,
            )

        event_bus.publish(
            {"event": "AI_ACTION_APPROVED", "payload": action.model_dump(mode="json")}
        )
        detail = await self._dispatch(action)
        self._record_paced(action, self._clock())
        self.policy_engine.record_action(action.type.value, 0)
        service_context.record_action(f"{action.type.value}: {detail}")
        logger.info(
            "AI action executed",
            type=action.type.value,
            target=action.target,
            confidence=action.confidence,
            detail=detail,
        )
        return ActionResult(action=action, approved=True, executed=True, detail=detail)

    async def _dispatch(self, action: DirectorAction) -> str:
        from app.cameras.service import camera_service
        from app.easyworship.service import easyworship_service

        atem = get_atem_service_instance()

        if action.type == DirectorActionType.ATEM_CUT:
            blocked = await self._blocked_preview(atem, action)
            if blocked:
                return blocked
            await atem.cut()
            return "cut"
        if action.type == DirectorActionType.ATEM_AUTO:
            blocked = await self._blocked_preview(atem, action)
            if blocked:
                return blocked
            await atem.auto()
            return "auto"
        if action.type == DirectorActionType.ATEM_SET_PROGRAM:
            input_id = action.parameters.get("atem_input")
            if input_id is None:
                return "no atem_input given"
            await atem.set_program(int(input_id))
            service_context.current_atem_program = int(input_id)
            return f"program -> {input_id}"
        if action.type == DirectorActionType.ATEM_SET_PREVIEW:
            input_id = action.parameters.get("atem_input")
            if input_id is None:
                return "no atem_input given"
            await atem.set_preview(int(input_id))
            return f"preview -> {input_id}"
        if action.type == DirectorActionType.PTZ_SELECT_ROLE:
            role = action.target
            if not role:
                return "no role given"
            ok = await camera_service.move_to_role(role)
            if ok:
                service_context.current_camera_role = role
                self._schedule_ptz_verify(role, self._camera_for_role(role))
            return f"camera -> {role} ({'ok' if ok else 'failed'})"
        if action.type == DirectorActionType.PTZ_PRESET:
            camera_id = action.parameters.get("camera_id")
            preset_id = action.parameters.get("preset_id")
            if camera_id is None or preset_id is None:
                return "no camera_id/preset_id given"
            ok = await camera_service.move_to_preset(int(camera_id), int(preset_id))
            if ok:
                role = self._role_for_preset(int(camera_id), int(preset_id))
                if role:
                    self._schedule_ptz_verify(role, int(camera_id))
            return f"preset {preset_id} on camera {camera_id} ({'ok' if ok else 'failed'})"
        if action.type == DirectorActionType.EASYWORSHIP_NEXT:
            ok = await easyworship_service.next_item()
            return f"easyworship next_item ({'ok' if ok else 'failed'})"
        if action.type == DirectorActionType.EASYWORSHIP_PREVIOUS:
            ok = await easyworship_service.previous_item()
            return f"easyworship prev_item ({'ok' if ok else 'failed'})"
        if action.type == DirectorActionType.EASYWORSHIP_NEXT_SLIDE:
            ok = await easyworship_service.next_slide_in_item()
            return f"easyworship next_slide ({'ok' if ok else 'failed'})"
        if action.type == DirectorActionType.EASYWORSHIP_SELECT:
            label = action.target
            if not label:
                return "no item label given"
            ok = await easyworship_service.select_item(label)
            if ok:
                service_context.current_easyworship_item = label
            return f"easyworship -> {label} ({'ok' if ok else 'failed'})"
        if action.type == DirectorActionType.SERVICE_STATE_CHANGE:
            from app.domain.service_state import ServiceState

            new_state = action.target
            if new_state:
                try:
                    service_context.set_state(ServiceState(new_state))
                    return f"service_state -> {new_state}"
                except ValueError:
                    return f"unknown service state '{new_state}'"
            return "no service state given"
        if action.type == DirectorActionType.CUE_ADVANCE:
            from app.director.engine import service_director

            result = await service_director.request_advance(
                source=str(action.parameters.get("source") or "ai_director"),
                reason=action.reason or "AI decision",
                confidence=float(action.confidence),
                cue_id=action.parameters.get("cue_id"),
            )
            if result.get("accepted"):
                return "cue advance (advanced)"
            return f"cue advance ({result.get('reason', 'suggested to operator')})"

        return "unhandled action type"

    # -- PTZ verification hooks (WO-VISION-1; gated by vision_enabled) --------
    @staticmethod
    def _camera_for_role(role: str):
        return getattr(settings, f"camera_role_{role}_camera", None)

    @staticmethod
    def _role_for_preset(camera_id: int, preset_id: int):
        for role in ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide"):
            if (
                getattr(settings, f"camera_role_{role}_camera", None) == camera_id
                and getattr(settings, f"camera_role_{role}_preset", None) == preset_id
            ):
                return role
        return None

    def _schedule_ptz_verify(self, role, camera_id) -> None:
        if not settings.vision_enabled or camera_id is None:
            return
        import asyncio

        from app.vision.verification import camera_to_atem_input, ptz_verifier

        atem_input = camera_to_atem_input(camera_id)
        on_program = atem_input is not None and service_context.current_atem_program == atem_input
        with_loop = True
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            with_loop = False
        if with_loop:
            asyncio.create_task(
                ptz_verifier.verify(role, camera_id, on_program=on_program, on_preview=not on_program)
            )

    async def _blocked_preview(self, atem, action) -> Optional[str]:
        if not settings.vision_enabled or action.parameters.get("override"):
            return None
        from app.vision.verification import ptz_verifier

        try:
            state = await atem.get_state()
            preview = getattr(state, "preview_input", None)
        except Exception:
            return None
        if preview is not None and ptz_verifier.is_blocked(int(preview)):
            event_bus.publish(
                {"event": "ATEM_CUT_BLOCKED", "payload": {"preview_input": int(preview), "reason": "ptz_verify_failed"}}
            )
            return f"blocked: preview input {preview} failed PTZ verification"
        return None


def build_action_engine() -> ActionEngine:
    from app.dependencies import get_policy_engine_instance

    return ActionEngine(get_policy_engine_instance())
