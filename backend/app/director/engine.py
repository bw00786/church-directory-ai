"""Service director — executes a scripted service cue by cue.

The director walks a :class:`ServiceScript`, performing each cue's actions
(ATEM program switches and PTZOptics preset recalls) via the existing services
and advancing either manually (operator "Next"), on a timer (the countdown), or
when the mixer reports a song has ended. A per-transition token guards against
races so a manual advance cancels any pending auto-advance.
"""

import asyncio
import contextlib
from typing import List, Optional

from app.cameras.service import camera_service
from app.config import settings
from app.dependencies import get_atem_service_instance
from app.events.bus import event_bus
from app.logging_config import get_logger
from app.mixer.service import mixer_service

from .models import ActionType, AdvanceTrigger, Cue, CueAction, DirectorStatus, ServiceScript
from .script import build_default_service_script

logger = get_logger(__name__)


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

    # -- public API -----------------------------------------------------------
    @property
    def script(self) -> ServiceScript:
        return self._script

    def load_script(self, script: ServiceScript) -> None:
        self._script = script
        self._index = -1

    async def start(self, autonomous: bool = True) -> DirectorStatus:
        if self._running:
            return self.status()

        self._autonomous = autonomous
        self._running = True

        atem = get_atem_service_instance()
        if not await atem.is_connected():
            await atem.connect()
        if not mixer_service.connected:
            await mixer_service.start()

        logger.info("Service director started", script=self._script.name, autonomous=autonomous)
        await self._enter_cue(0)
        return self.status()

    async def stop(self) -> DirectorStatus:
        self._running = False
        self._cancel_advance()
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

        for action in cue.actions:
            await self._execute_action(action)

        await self._broadcast()
        self._schedule_advance(cue, token)

    async def _execute_action(self, action: CueAction) -> None:
        try:
            if action.type == ActionType.ATEM_PROGRAM and action.atem_input is not None:
                atem = get_atem_service_instance()
                await atem.set_program(action.atem_input)
                self._publish("atem_program", f"Program -> input {action.atem_input}", action)

            elif action.type == ActionType.PTZ_PRESET and action.preset_id is not None:
                ok = await camera_service.move_to_preset(
                    action.camera_id or 1, action.preset_id
                )
                self._publish(
                    "ptz_preset",
                    f"Preset {action.preset_id} ({'ok' if ok else 'failed'})",
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
