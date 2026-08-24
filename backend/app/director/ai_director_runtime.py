"""AI Director runtime: polls the AI Service Director and applies its
decisions according to the current operating mode.

Modes (see docs/ai-director.md, config.ai_director_mode):
  - "manual": AI observes and logs decisions but takes no actions.
  - "assisted": AI proposes actions; a human must approve each one.
  - "ai_directed": AI executes approved (policy-gated) actions automatically.

This sits above app.director.engine (the existing cue engine), which remains
available as the manual/fallback script.
"""

import asyncio
import contextlib
from typing import List, Optional

from app.ai.decision import DirectorActionSpec, DirectorDecision
from app.ai.service_director import ai_service_director
from app.config import settings
from app.director.action_engine import ActionEngine, build_action_engine
from app.director.action_models import DirectorAction, DirectorActionType
from app.domain.service_context import service_context
from app.domain.service_state import ServiceState
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

VALID_MODES = ("manual", "assisted", "ai_directed")


def _to_director_action(spec: DirectorActionSpec, decision: DirectorDecision) -> Optional[DirectorAction]:
    try:
        action_type = DirectorActionType(spec.type)
    except ValueError:
        logger.warning("Unknown AI action type", type=spec.type)
        return None

    target = spec.camera_role or spec.easyworship_item
    parameters = dict(spec.parameters)
    if spec.atem_input is not None:
        parameters.setdefault("atem_input", spec.atem_input)

    return DirectorAction(
        type=action_type,
        target=target,
        parameters=parameters,
        confidence=decision.confidence,
        reason=decision.reason,
    )


class AIDirectorRuntime:
    """Owns the AI Director's operating mode and background decision loop."""

    def __init__(self, action_engine: Optional[ActionEngine] = None):
        self._mode = settings.ai_director_mode if settings.ai_director_mode in VALID_MODES else "assisted"
        self._action_engine = action_engine
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.pending_actions: List[DirectorAction] = []

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid AI Director mode: {mode}")
        self._mode = mode
        event_bus.publish({"event": "AI_DIRECTOR_MODE_CHANGED", "payload": {"mode": mode}})

    def _engine(self) -> ActionEngine:
        if self._action_engine is None:
            self._action_engine = build_action_engine()
        return self._action_engine

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("AI Director runtime started", mode=self._mode)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("AI Director tick failed")
            await asyncio.sleep(settings.ai_director_poll_seconds)

    async def tick(self) -> DirectorDecision:
        """Run one decision cycle. Exposed directly for tests/replay mode."""
        decision = await ai_service_director.decide(service_context)
        service_context.last_decision = decision.model_dump(mode="json")

        if decision.service_state:
            with contextlib.suppress(ValueError):
                service_context.set_state(ServiceState(decision.service_state))

        event_bus.publish({"event": "AI_DECISION", "payload": service_context.last_decision})

        if self._mode == "manual" or not decision.actions:
            return decision

        actions = [a for a in (_to_director_action(spec, decision) for spec in decision.actions) if a]

        if self._mode == "assisted":
            self.pending_actions.extend(actions)
            event_bus.publish(
                {
                    "event": "AI_ACTIONS_PENDING_APPROVAL",
                    "payload": [a.model_dump(mode="json") for a in actions],
                }
            )
            return decision

        # ai_directed: execute immediately (still policy-gated per action).
        for action in actions:
            await self._engine().execute(action)

        return decision

    async def approve_pending(self, index: int) -> dict:
        """Execute one pending (assisted-mode) action by index."""
        if index < 0 or index >= len(self.pending_actions):
            raise IndexError("pending action index out of range")
        action = self.pending_actions.pop(index)
        result = await self._engine().execute(action)
        return result.model_dump(mode="json")

    def reject_pending(self, index: int) -> None:
        if 0 <= index < len(self.pending_actions):
            self.pending_actions.pop(index)


# Module-level singleton
ai_director_runtime = AIDirectorRuntime()
