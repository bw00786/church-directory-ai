"""Executes policy-validated DirectorActions against real services.

This is the only place the AI Director's proposed actions turn into actual
ATEM/PTZ/EasyWorship calls. Every action passes through the policy engine's
per-category confidence threshold first; rejected actions are logged and
never reach hardware. See docs/ai-director.md.
"""

from typing import Optional

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


def _category_for(action_type: DirectorActionType) -> str:
    if action_type in CAMERA_ACTION_TYPES:
        return "camera_change"
    if action_type in SLIDE_ACTION_TYPES:
        return "slide_change"
    if action_type in ATEM_ACTION_TYPES:
        return "atem_transition"
    return "service_state"  # SERVICE_STATE_CHANGE — not hardware-gated


class ActionEngine:
    """Validates and dispatches DirectorActions to the real services."""

    def __init__(self, policy_engine: PolicyEngine):
        self.policy_engine = policy_engine

    async def execute(self, action: DirectorAction) -> ActionResult:
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

        event_bus.publish(
            {"event": "AI_ACTION_APPROVED", "payload": action.model_dump(mode="json")}
        )
        detail = await self._dispatch(action)
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

        return "unhandled action type"

    # -- PTZ verification hooks (WO-VISION-1; gated by vision_enabled) --------
    @staticmethod
    def _camera_for_role(role: str):
        from app.config import settings

        return getattr(settings, f"camera_role_{role}_camera", None)

    @staticmethod
    def _role_for_preset(camera_id: int, preset_id: int):
        from app.config import settings

        for role in ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide"):
            if (
                getattr(settings, f"camera_role_{role}_camera", None) == camera_id
                and getattr(settings, f"camera_role_{role}_preset", None) == preset_id
            ):
                return role
        return None

    def _schedule_ptz_verify(self, role, camera_id) -> None:
        from app.config import settings

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
        from app.config import settings

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
