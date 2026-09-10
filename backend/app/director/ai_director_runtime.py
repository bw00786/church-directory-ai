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
import time
from datetime import datetime
from typing import List, Optional

from app.ai.decision import DirectorActionSpec, DirectorDecision
from app.ai.service_director import ai_service_director
from app.config import settings
from app.director.action_engine import ActionEngine, _category_for, build_action_engine
from app.director.action_models import CAMERA_ACTION_TYPES, DirectorAction, DirectorActionType
from app.director.autonomy import adaptive_confidence, evidence_fusion, learning_recorder
from app.director.predictive_ptz import predictive_preview
from app.director.service_end import run_shutdown_bundle, service_end_recognizer
from app.director.text_follower import song_follower_service
from app.domain.service_context import service_context
from app.domain.service_state import ServiceState
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

VALID_MODES = ("manual", "assisted", "ai_directed")

# Events that, in event-driven mode, wake the decision loop immediately instead
# of waiting for the next poll interval (Phase 2).
NUDGE_EVENTS = {
    "AUDIO_STARTED",
    "AUDIO_STOPPED",
    "EASYWORSHIP_STATE",
    "SERVICE_ENDING",
    "PERCEPTION_DEGRADED",
    "PERCEPTION_RESTORED",
}

# Base confidence thresholds per category (the policy engine enforces these as a
# floor; AdaptiveConfidence may only tighten above them).
_BASE_THRESHOLDS = {
    "camera_change": lambda: settings.confidence_camera_change,
    "slide_change": lambda: settings.confidence_slide_change,
    "atem_transition": lambda: settings.confidence_atem_transition,
}


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
        self._event_task: Optional[asyncio.Task] = None
        self._running = False
        self.pending_actions: List[DirectorAction] = []
        # Phase 2 event-driven ticks.
        self._wake = asyncio.Event()
        self._last_nudge_at = 0.0
        # Phase 1/3: track already-consumed transcript so followers see each
        # line once. None => process whatever backlog exists on the first tick.
        self._last_line_ts: Optional[datetime] = None

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid AI Director mode: {mode}")
        self._mode = mode
        event_bus.publish({"event": "AI_DIRECTOR_MODE_CHANGED", "payload": {"mode": mode}})

    def nudge(self) -> None:
        """Request an immediate decision cycle (Phase 2 event-driven)."""
        self._wake.set()

    def _engine(self) -> ActionEngine:
        if self._action_engine is None:
            self._action_engine = build_action_engine()
        return self._action_engine

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Close the WO-EWVERIFY-3 loop: the song follower is the lyric matcher.
        with contextlib.suppress(Exception):
            from app.easyworship.slide_expected import expected_text_provider

            expected_text_provider.set_matcher(song_follower_service.follower)
        self._task = asyncio.create_task(self._run())
        self._event_task = asyncio.create_task(self._event_listener())
        logger.info("AI Director runtime started", mode=self._mode)

    async def stop(self) -> None:
        self._running = False
        for task in (self._task, self._event_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = None
        self._event_task = None

    async def _run(self) -> None:
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("AI Director tick failed")
            # Wait for the poll interval OR an event nudge (Phase 2), whichever
            # comes first. With event-driven mode off nothing nudges, so this is
            # exactly the old fixed-cadence behavior.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=settings.ai_director_poll_seconds)
            self._wake.clear()

    async def _event_listener(self) -> None:
        """Nudge the decision loop on meaningful perception events (Phase 2)."""
        queue = await event_bus.subscribe()
        try:
            while self._running:
                message = await queue.get()
                if not settings.ai_director_event_driven:
                    continue
                event = message.get("event") if isinstance(message, dict) else None
                if event not in NUDGE_EVENTS:
                    continue
                now = time.monotonic()
                if now - self._last_nudge_at >= settings.ai_director_event_min_interval_seconds:
                    self._last_nudge_at = now
                    self.nudge()
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await event_bus.unsubscribe(queue)

    async def tick(self) -> DirectorDecision:
        """Run one decision cycle. Exposed directly for tests/replay mode."""
        decision = await ai_service_director.decide(service_context)
        service_context.last_decision = decision.model_dump(mode="json")

        if decision.service_state:
            with contextlib.suppress(ValueError):
                service_context.set_state(ServiceState(decision.service_state))

        event_bus.publish({"event": "AI_DECISION", "payload": service_context.last_decision})

        # Feed the autonomy components regardless of mode so their internal
        # state stays in sync; only the resulting actions honor the mode gate.
        new_lines = self._drain_new_transcript()
        service_end_recognizer.observe_state(service_context.state)
        for line in new_lines:
            service_end_recognizer.observe_transcript(line)

        decision_actions = [
            a for a in (_to_director_action(spec, decision) for spec in decision.actions) if a
        ]
        follower_actions = song_follower_service.process(new_lines)
        proposal = service_end_recognizer.evaluate()
        end_actions = list(proposal.actions) if proposal else []
        actions = decision_actions + follower_actions + end_actions

        if self._mode == "manual" or not actions:
            return decision

        # Pre-stage camera shots on preview (never cuts) so the take is instant.
        await self._prestage(actions)

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
            await self._execute_directed(action)

        return decision

    def _drain_new_transcript(self) -> List:
        """Return transcript lines not yet seen by the autonomy components."""
        lines = list(service_context.transcript)
        if self._last_line_ts is not None:
            lines = [line for line in lines if line.timestamp > self._last_line_ts]
        if lines:
            self._last_line_ts = lines[-1].timestamp
        return lines

    async def _prestage(self, actions: List[DirectorAction]) -> None:
        if not settings.ptz_predictive_preview:
            return
        for action in actions:
            if action.type in CAMERA_ACTION_TYPES and action.target:
                await predictive_preview.stage_role(action.target)
                break

    def _adaptive_gate(self, action: DirectorAction) -> tuple:
        """Extra, operator-feedback-driven threshold on top of the policy floor."""
        category = _category_for(action.type)
        if category == "service_state" or category not in _BASE_THRESHOLDS:
            return True, None
        base = _BASE_THRESHOLDS[category]()
        threshold = adaptive_confidence.threshold_for(category, base)
        if action.confidence < threshold:
            return False, f"adaptive '{category}' threshold {threshold:.2f}"
        return True, None

    async def _execute_directed(self, action: DirectorAction) -> None:
        """ai_directed path: fusion + adaptive gates, then the policy-gated engine."""
        ok, reason = evidence_fusion.corroborates(action, service_context.vision)
        if not ok:
            event_bus.publish(
                {"event": "AI_ACTION_REJECTED", "payload": {**action.model_dump(mode="json"), "reason": reason}}
            )
            adaptive_confidence.record_outcome(_category_for(action.type), good=False)
            return

        ok, reason = self._adaptive_gate(action)
        if not ok:
            event_bus.publish(
                {"event": "AI_ACTION_REJECTED", "payload": {**action.model_dump(mode="json"), "reason": reason}}
            )
            return

        result = await self._engine().execute(action)
        if result.executed:
            song_follower_service.notify_executed(action)
            await learning_recorder.record_auto(action)
            adaptive_confidence.record_outcome(_category_for(action.type), good=True)
            await self._maybe_shutdown(action)

    async def _maybe_shutdown(self, action: DirectorAction) -> None:
        if (
            action.type == DirectorActionType.SERVICE_STATE_CHANGE
            and action.parameters.get("source") == "service_end"
            and action.target == ServiceState.POST_SERVICE.value
            and settings.service_end_auto_shutdown
        ):
            await run_shutdown_bundle()

    async def approve_pending(self, index: int) -> dict:
        """Execute one pending (assisted-mode) action by index."""
        if index < 0 or index >= len(self.pending_actions):
            raise IndexError("pending action index out of range")
        action = self.pending_actions.pop(index)
        result = await self._engine().execute(action)
        if result.executed:
            song_follower_service.notify_executed(action)
            await self._maybe_shutdown(action)
        await learning_recorder.record_approval(action)
        adaptive_confidence.record_outcome(_category_for(action.type), good=True)
        return result.model_dump(mode="json")

    def reject_pending(self, index: int) -> None:
        if 0 <= index < len(self.pending_actions):
            action = self.pending_actions.pop(index)
            if action.parameters.get("source") == "song_follower":
                song_follower_service.follower.reject_advance()
            adaptive_confidence.record_outcome(_category_for(action.type), good=False)
            with contextlib.suppress(RuntimeError):
                asyncio.ensure_future(learning_recorder.record_rejection(action))


# Module-level singleton
ai_director_runtime = AIDirectorRuntime()
