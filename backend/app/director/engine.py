"""Service director — executes a scripted service cue by cue.

The director walks a :class:`ServiceScript`, performing each cue's actions
(ATEM program switches and PTZOptics preset recalls) via the existing services
and advancing either manually (operator "Next"), on a timer (the countdown), or
when the mixer reports a song has ended. A per-transition token guards against
races so a manual advance cancels any pending auto-advance.

Cue actions are not run verbatim: :func:`plan_cue` turns a cue's action list
into an ordered execution plan so a PTZ move is never seen on air when it can
be avoided (preset -> settle -> ATEM preview -> cut/auto), see that function.
"""

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.cameras.service import camera_service
from app.config import settings
from app.dependencies import get_atem_service_instance
from app.domain.service_context import service_context
from app.easyworship.service import easyworship_service
from app.events.bus import event_bus
from app.identity.service import identity_service
from app.logging_config import get_logger
from app.mixer.service import mixer_service

from .models import ActionType, AdvanceTrigger, Cue, CueAction, DirectorStatus, ServiceScript, Transition
from .plan_builder import build_plan_from_script, plan_entry_for_cue
from .script import build_default_service_script
from .transcript_triggers import classify_transcript

logger = get_logger(__name__)

# Bus event published when the director enters a cue (payload: cue_id, index,
# total, label, state, speaker_role, camera_role, easyworship_item, followable).
EVENT_CUE_ENTERED = "DIRECTOR_CUE_ENTERED"


# -- cue execution planning ---------------------------------------------------
@dataclass(frozen=True)
class PlanStep:
    """One step of a cue's execution plan (see :func:`plan_cue`).

    ``op`` is one of:

    - ``action``  run the cue action as written (PTZ preset, slide, note)
    - ``program`` hard program switch to ``atem_input`` (cover / plain cut)
    - ``settle``  wait for the PTZ camera to finish moving
    - ``preview`` put ``atem_input`` on the ATEM preview bus
    - ``take``    take preview to program with ``transition`` ("cut" | "auto")
    - ``skip``    program switch dropped because the input is already live
    """

    op: str
    action: Optional[CueAction] = None
    atem_input: Optional[int] = None
    camera_id: Optional[int] = None
    transition: Optional[Transition] = None
    reason: str = ""


def plan_cue(
    actions: List[CueAction],
    current_program: Optional[int],
    camera_inputs: Dict[int, int],
    transition: Transition = "cut",
    cover_with: Optional[int] = None,
) -> List[PlanStep]:
    """Order a cue's actions so a PTZ move is staged off air whenever possible.

    Pure function: ``actions`` is the cue's action list, ``current_program`` the
    ATEM program input when the cue starts (None = unknown), ``camera_inputs``
    maps PTZ ``camera_id`` -> the ATEM input that camera feeds, ``transition``
    is the cue default for ATEM program changes and ``cover_with`` an optional
    ATEM input used to hide a move when the PTZ camera is already live.

    Rules (``PTZ`` = a PTZ_PRESET action; ``SWITCH`` = an ATEM_PROGRAM action
    to the input that PTZ camera feeds):

    1. ``PTZ`` + ``SWITCH``, camera **off** program (or program unknown):
       preset -> settle -> preview(input) -> take(transition). The pan happens
       on preview; the congregation only sees the finished frame.
    2. ``PTZ`` + ``SWITCH``, camera **already on** program: the move would be
       visible either way. With ``cover_with`` set (and not that camera's
       input) the plan hides it: program -> cover_with, preset -> settle ->
       preview(input) -> take(transition). Without ``cover_with`` it just
       recalls the preset (the redundant program switch is dropped).
    3. ``PTZ`` alone with the camera on program and ``cover_with`` set is
       covered the same way; otherwise the preset is simply recalled.
    4. ``SWITCH`` without a ``PTZ`` move: a plain program change -- ``cut`` is a
       direct program switch, ``auto`` goes preview -> auto. Dropped when the
       input is already on program.
    5. Everything else (slides, notes) keeps its original relative order; the
       staged sequence sits where the first of its two actions was.
    """
    def is_ptz(a: CueAction) -> bool:
        return a.type == ActionType.PTZ_PRESET and (a.preset_id is not None or a.role is not None)

    def is_switch(a: CueAction) -> bool:
        return a.type == ActionType.ATEM_PROGRAM and a.atem_input is not None

    def input_for(a: CueAction) -> Optional[int]:
        return camera_inputs.get(a.camera_id or 1)

    def take_transition(atem_action: Optional[CueAction]) -> Transition:
        if atem_action is not None and atem_action.transition is not None:
            return atem_action.transition
        return transition

    # First PTZ move per ATEM input and first program switch per input; an
    # input present in both is a staged pair.
    ptz_idx: Dict[int, int] = {}
    switch_idx: Dict[int, int] = {}
    for idx, a in enumerate(actions):
        if is_ptz(a):
            cam_input = input_for(a)
            if cam_input is not None:
                ptz_idx.setdefault(cam_input, idx)
        elif is_switch(a):
            switch_idx.setdefault(a.atem_input, idx)
    pairs = {cam_input: (ptz_idx[cam_input], switch_idx[cam_input]) for cam_input in ptz_idx if cam_input in switch_idx}
    pair_at = {min(p): cam_input for cam_input, p in pairs.items()}
    consumed = {i for p in pairs.values() for i in p}

    def staged(ptz: CueAction, cam_input: int, trans: Transition, atem_action: Optional[CueAction]) -> List[PlanStep]:
        """Rules 1-3: hide the PTZ move, covering first if the camera is live."""
        on_program = current_program is not None and current_program == cam_input
        steps: List[PlanStep] = []
        if on_program:
            if cover_with is None or cover_with == cam_input:
                return [
                    PlanStep("action", action=ptz, camera_id=ptz.camera_id, reason="camera already on program; moving live")
                ]
            steps.append(
                PlanStep("program", action=atem_action or ptz, atem_input=cover_with, reason=f"cover PTZ move: program -> input {cover_with}")
            )
        steps.append(PlanStep("action", action=ptz, camera_id=ptz.camera_id, reason="recall preset off air"))
        steps.append(PlanStep("settle", action=ptz, camera_id=ptz.camera_id, reason="wait for PTZ to settle"))
        steps.append(PlanStep("preview", action=atem_action or ptz, atem_input=cam_input, reason="stage on preview"))
        steps.append(PlanStep("take", action=atem_action or ptz, atem_input=cam_input, transition=trans, reason=f"take ({trans})"))
        return steps

    plan: List[PlanStep] = []
    for idx, action in enumerate(actions):
        if idx in pair_at:
            cam_input = pair_at[idx]
            ptz, atem_action = actions[ptz_idx[cam_input]], actions[switch_idx[cam_input]]
            plan.extend(staged(ptz, cam_input, take_transition(atem_action), atem_action))
            continue
        if idx in consumed:
            continue
        if is_switch(action):
            target = action.atem_input
            if current_program is not None and current_program == target:
                plan.append(PlanStep("skip", action=action, atem_input=target, reason="already on program"))
            elif take_transition(action) == "auto":
                plan.append(PlanStep("preview", action=action, atem_input=target, reason="stage on preview"))
                plan.append(PlanStep("take", action=action, atem_input=target, transition="auto", reason="take (auto)"))
            else:
                plan.append(PlanStep("program", action=action, atem_input=target, reason="program switch (cut)"))
            continue
        if is_ptz(action):
            cam_input = input_for(action)
            on_program = cam_input is not None and current_program is not None and current_program == cam_input
            if on_program and cover_with is not None and cover_with != cam_input:
                plan.extend(staged(action, cam_input, transition, None))
            else:
                plan.append(PlanStep("action", action=action, camera_id=action.camera_id, reason="recall preset"))
            continue
        plan.append(PlanStep("action", action=action))
    return plan


class ServiceDirector:
    """Runs a scripted service and drives the ATEM and PTZOptics camera."""

    def __init__(self, script: Optional[ServiceScript] = None):
        self._script = script or build_default_service_script()
        self._index = -1
        self._running = False
        self._autonomous = True
        self._advance_task: Optional[asyncio.Task] = None
        self._token = 0
        self._pending_suggestion: Optional[dict] = None
        self._transcript_task: Optional[asyncio.Task] = None
        self._sync_plan()

    # -- public API -----------------------------------------------------------
    @property
    def script(self) -> ServiceScript:
        return self._script

    def load_script(self, script: ServiceScript) -> None:
        self._script = script
        self._index = -1
        self._sync_plan()

    def _sync_plan(self) -> None:
        # Keep the AI Director's expected structure in step with the cue sheet.
        service_context.plan = build_plan_from_script(self._script)

    async def broadcast_status(self) -> None:
        await self._broadcast()

    async def start(self, autonomous: bool = True) -> DirectorStatus:
        if self._running:
            return self.status()

        self._autonomous = autonomous
        self._running = True
        self._transcript_task = asyncio.create_task(self._listen_for_transcripts())

        atem = get_atem_service_instance()
        if not await atem.is_connected():
            await atem.connect()
        if not mixer_service.connected:
            await mixer_service.start()
        if not easyworship_service.connected:
            await easyworship_service.start()

        logger.info("Service director started", script=self._script.name, autonomous=autonomous)
        await self._enter_cue(0)
        return self.status()

    async def stop(self) -> DirectorStatus:
        self._running = False
        self._cancel_advance()
        if self._transcript_task is not None:
            self._transcript_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._transcript_task
            self._transcript_task = None
        logger.info("Service director stopped")
        await self._broadcast()
        return self.status()

    async def next(self) -> DirectorStatus:
        if not self._running:
            return self.status()
        await self._enter_cue(self._index + 1)
        return self.status()

    async def goto(self, index: int) -> DirectorStatus:
        if not self._running:
            return self.status()
        if index < 0 or index >= len(self._script.cues):
            raise IndexError(f"cue index out of range: {index}")
        await self._enter_cue(index)
        return self.status()

    def current_cue(self) -> Optional[Cue]:
        cues = self._script.cues
        return cues[self._index] if 0 <= self._index < len(cues) else None

    async def request_advance(
        self,
        source: str,
        reason: str,
        confidence: float = 1.0,
        cue_id: Optional[str] = None,
    ) -> dict:
        """Advance suggestion from the LLM/vision layer.

        Auto-advances only when the director is running in autonomous mode, the
        current cue is AI-eligible, and confidence clears the policy threshold.
        Otherwise the suggestion is recorded/broadcast for the operator to
        accept (via ``next``). Returns a decision dict.
        """
        cue = self.current_cue()
        if not self._running or cue is None:
            return {"accepted": False, "reason": "not running"}
        if cue_id is not None and cue_id != cue.id:
            return {"accepted": False, "reason": "stale cue"}

        threshold = settings.min_ai_action_confidence
        suggestion = {
            "source": source,
            "reason": reason,
            "confidence": confidence,
            "cue_id": cue.id,
            "cue_index": self._index,
        }

        eligible = self._autonomous and cue.ai_enabled and confidence >= threshold
        if not eligible:
            self._pending_suggestion = suggestion
            event_bus.publish({"type": "director_suggestion", "data": suggestion})
            await self._broadcast()
            return {"accepted": False, "suggestion": suggestion}

        logger.info("AI advance", cue_id=cue.id, reason=reason, confidence=confidence)
        event_bus.publish(
            {"type": "director_action", "data": {"action": "ai_advance", "detail": reason, "cue_index": self._index}}
        )
        await self._enter_cue(self._index + 1)
        return {"accepted": True, "reason": reason}

    # -- cue execution --------------------------------------------------------
    async def _enter_cue(self, index: int) -> None:
        self._cancel_advance()
        self._token += 1
        token = self._token

        if index >= len(self._script.cues):
            self._running = False
            self._index = len(self._script.cues)
            logger.info("Service complete")
            await self._broadcast()
            return

        self._index = index
        cue = self._script.cues[index]
        self._pending_suggestion = None
        logger.info("Entering cue", cue_id=cue.id, name=cue.name, index=index)
        self._publish_cue_entered(cue, index)

        plan = plan_cue(
            cue.actions,
            await self._current_program(),
            self._camera_inputs(cue.actions),
            transition=cue.transition,
            cover_with=cue.cover_with,
        )
        for step in plan:
            if token != self._token:
                # Operator/AI moved on while we waited for the PTZ to settle;
                # never take the superseded cue's shot to program.
                logger.info("Cue superseded mid-plan", cue_id=cue.id, step=step.op)
                return
            await self._execute_step(step)

        if token != self._token:
            return
        await self._broadcast()
        self._schedule_advance(cue, token)

    # -- plan helpers ---------------------------------------------------------
    async def _current_program(self) -> Optional[int]:
        """ATEM program input as the cue starts (None when unknown)."""
        try:
            atem = get_atem_service_instance()
            connected = getattr(atem, "is_connected", None)
            if connected is None or await connected():
                state = await atem.get_state()
                program = getattr(state, "program_input", None)
                if program is not None and int(program) > 0:
                    return int(program)
        except Exception as e:
            logger.debug("ATEM program unknown", error=str(e))
        # Last program we (or the AI Director's action engine) set, if any.
        return service_context.current_atem_program

    @staticmethod
    def _camera_inputs(actions: List[CueAction]) -> Dict[int, int]:
        """PTZ camera id -> ATEM input it feeds, for the cameras this cue moves."""
        from app.vision.verification import camera_to_atem_input

        mapping: Dict[int, int] = {}
        for action in actions:
            if action.type != ActionType.PTZ_PRESET:
                continue
            camera_id = action.camera_id or settings.ptz_camera_id
            atem_input = camera_to_atem_input(camera_id)
            if atem_input is None and camera_id == settings.ptz_camera_id:
                atem_input = settings.atem_camera1_input
            if atem_input is not None:
                mapping[camera_id] = int(atem_input)
        return mapping

    async def _execute_step(self, step: PlanStep) -> None:
        action = step.action or CueAction(type=ActionType.NOTE, description=step.reason)
        try:
            if step.op == "action":
                await self._execute_action(step.action)
            elif step.op == "program" and step.atem_input is not None:
                await get_atem_service_instance().set_program(step.atem_input)
                service_context.set_program(step.atem_input)
                self._publish("atem_program", f"Program -> input {step.atem_input} ({step.reason})", action)
            elif step.op == "settle":
                await self._settle(step.camera_id or settings.ptz_camera_id)
            elif step.op == "preview" and step.atem_input is not None:
                await get_atem_service_instance().set_preview(step.atem_input)
                self._publish("atem_preview", f"Preview -> input {step.atem_input}", action)
            elif step.op == "take" and step.atem_input is not None:
                atem = get_atem_service_instance()
                if step.transition == "auto":
                    await atem.auto()
                else:
                    await atem.cut()
                service_context.set_program(step.atem_input)
                self._publish("atem_program", f"Program -> input {step.atem_input} ({step.transition})", action)
            elif step.op == "skip":
                self._publish("atem_program", f"Input {step.atem_input} already on program", action)
        except Exception as e:
            logger.warning("Cue step failed", op=step.op, error=str(e))
            self._publish("error", f"{step.op}: {e}", action)

    async def _settle(self, camera_id: int) -> None:
        """Wait for the PTZ move to finish: the camera service's move-completion
        hook when it offers one, else the configured motor-settle delay."""
        waiter = getattr(camera_service, "wait_for_move", None)
        if callable(waiter):
            try:
                await waiter(camera_id)
                return
            except Exception as e:
                logger.debug("PTZ move-completion wait failed; using delay", error=str(e))
        await asyncio.sleep(max(0, int(settings.ptz_verify_delay_ms)) / 1000.0)

    async def _execute_action(self, action: CueAction) -> None:
        try:
            if action.type == ActionType.ATEM_PROGRAM and action.atem_input is not None:
                atem = get_atem_service_instance()
                await atem.set_program(action.atem_input)
                service_context.set_program(action.atem_input)
                self._publish("atem_program", f"Program -> input {action.atem_input}", action)

            elif action.type == ActionType.PTZ_PRESET and (action.preset_id is not None or action.role):
                camera_id = action.camera_id or 1
                preset_id = action.preset_id
                resolution = "configured"
                if action.role:
                    learned = identity_service.best_preset_for_role(action.role, camera_id)
                    if learned is not None:
                        preset_id, resolution = learned, "learned from identity recognition"
                if preset_id is None:
                    self._publish(
                        "ptz_preset",
                        f"No preset available for role '{action.role}' (nothing learned yet, no fallback)",
                        action,
                    )
                else:
                    note_command = getattr(camera_service, "note_command", None)
                    if callable(note_command):
                        note_command(camera_id, preset_id)  # the app, not the operator, is reframing
                    ok = await camera_service.move_to_preset(camera_id, preset_id)
                    if ok and action.role:
                        service_context.set_camera_role(action.role)
                    self._publish(
                        "ptz_preset",
                        f"Preset {preset_id} via {resolution} ({'ok' if ok else 'failed'})",
                        action,
                    )

            elif action.type == ActionType.SLIDE and action.slide_op is not None:
                ok = await easyworship_service.action(action.slide_op)
                self._publish(
                    "slide",
                    f"EasyWorship {action.slide_op} ({'ok' if ok else 'failed'})",
                    action,
                )

            elif action.type == ActionType.NOTE:
                self._publish("note", action.note or "", action)
        except Exception as e:
            logger.warning("Cue action failed", type=action.type, error=str(e))
            self._publish("error", f"{action.type}: {e}", action)

    # -- auto-advance ---------------------------------------------------------
    def _schedule_advance(self, cue: Cue, token: int) -> None:
        if cue.advance == AdvanceTrigger.TIMER and cue.timer_seconds:
            self._advance_task = asyncio.create_task(self._auto_timer(token, cue.timer_seconds))
        elif cue.advance == AdvanceTrigger.SONG_END:
            self._advance_task = asyncio.create_task(self._auto_song_end(token, cue.channels))

    async def _auto_timer(self, token: int, seconds: int) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(seconds)
            if token == self._token and self._running:
                await self._enter_cue(self._index + 1)

    async def _auto_song_end(self, token: int, channels: List[int]) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            ended = await mixer_service.wait_for_song_end(channels)
            if ended and token == self._token and self._running:
                await self._enter_cue(self._index + 1)

    async def _listen_for_transcripts(self) -> None:
        queue = await event_bus.subscribe()
        try:
            while self._running:
                message = await queue.get()
                if not isinstance(message, dict) or message.get("event") != "TRANSCRIPT":
                    continue
                payload = message.get("payload") or {}
                current = self.current_cue()
                result = classify_transcript(
                    current.id if current else "",
                    str(payload.get("role", "")).lower(),
                    str(payload.get("text", "")),
                )
                if result is None or current is None:
                    continue
                reason, confidence = result
                await self.request_advance(
                    source="transcript",
                    reason=reason,
                    confidence=confidence,
                    cue_id=current.id,
                )
        finally:
            await event_bus.unsubscribe(queue)

    def _cancel_advance(self) -> None:
        if self._advance_task is not None:
            self._advance_task.cancel()
            self._advance_task = None

    # -- status / broadcast ---------------------------------------------------
    def status(self) -> DirectorStatus:
        cues = self._script.cues
        current = cues[self._index] if 0 <= self._index < len(cues) else None
        nxt = cues[self._index + 1] if 0 <= self._index + 1 < len(cues) else None
        return DirectorStatus(
            running=self._running,
            autonomous=self._autonomous,
            script_name=self._script.name,
            cue_index=self._index,
            total_cues=len(cues),
            current_cue=current,
            next_cue=nxt,
            pending_suggestion=self._pending_suggestion,
        )

    def _publish_cue_entered(self, cue: Cue, index: int) -> None:
        """``DIRECTOR_CUE_ENTERED``: the cue engine's position as a hard signal
        for the context sync / phase reconciler (see app.director.context_sync)."""
        entry = plan_entry_for_cue(cue)
        event_bus.publish(
            {
                "event": EVENT_CUE_ENTERED,
                "payload": {
                    "cue_id": cue.id,
                    "index": index,
                    "total": len(self._script.cues),
                    "label": cue.name,
                    "state": entry.state.value,
                    "speaker_role": entry.speaker_role,
                    "camera_role": entry.camera_role,
                    "easyworship_item": entry.easyworship_item,
                    "followable": entry.followable,
                    "script": self._script.name,
                },
            }
        )

    def _publish(self, action: str, detail: str, cue_action: CueAction) -> None:
        event_bus.publish(
            {
                "type": "director_action",
                "data": {
                    "action": action,
                    "detail": detail,
                    "description": cue_action.description,
                    "cue_index": self._index,
                },
            }
        )

    async def _broadcast(self) -> None:
        event_bus.publish({"type": "director", "data": self.status().model_dump()})


# Module-level singleton
service_director = ServiceDirector()
