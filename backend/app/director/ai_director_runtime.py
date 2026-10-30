"""AI Director runtime: polls the AI Service Director and applies its
decisions according to the current operating mode.

Modes (see docs/ai-director.md, config.ai_director_mode):
  - "manual": AI observes and logs decisions but takes no actions.
  - "assisted": AI proposes actions; a human must approve each one.
  - "ai_directed": AI executes approved (policy-gated) actions automatically.

Every proposal -- from the LLM director, the song follower, the service-end
recognizer or the ``/director/observe`` cue-advance path -- goes through one
``ProposalQueue`` (app.director.proposals): ids, TTL, per-category supersede,
effect-level dedupe and source priority. ``pending_actions`` is a view of it.

This sits above app.director.engine (the existing cue engine), which remains
available as the manual/fallback script.
"""

import asyncio
import contextlib
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

from app.ai.decision import DirectorActionSpec, DirectorDecision
from app.ai.service_director import ai_service_director
from app.config import settings
from app.director.action_engine import ActionEngine, _category_for, build_action_engine
from app.director.action_models import CAMERA_ACTION_TYPES, ActionResult, DirectorAction, DirectorActionType
from app.director.autonomy import adaptive_confidence, evidence_fusion, learning_recorder
from app.director.phase import phase_reconciler
from app.director.predictive_ptz import predictive_preview
from app.director.proposals import Proposal, ProposalQueue, normalize_source
from app.director.service_end import run_shutdown_bundle, service_end_recognizer
from app.director.text_follower import song_follower_service
from app.domain.service_context import TranscriptLine, service_context
from app.domain.service_state import ServiceState
from app.events.bus import event_bus
from app.logging_config import get_logger

from . import cycle_log

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
    if spec.preset_id is not None:
        parameters.setdefault("preset_id", spec.preset_id)

    confidence = spec.confidence if spec.confidence is not None else decision.confidence
    reason = spec.reason if spec.reason else decision.reason
    return DirectorAction(
        type=action_type,
        target=target,
        parameters=parameters,
        confidence=confidence,
        reason=reason,
    )


def _is_follower(action: DirectorAction) -> bool:
    return action.parameters.get("source") == "song_follower"


def _bases() -> Dict[str, float]:
    return {category: fn() for category, fn in _BASE_THRESHOLDS.items()}


class AIDirectorRuntime:
    """Owns the AI Director's operating mode, proposal queue and decision loop."""

    def __init__(self, action_engine: Optional[ActionEngine] = None, proposal_queue: Optional[ProposalQueue] = None):
        self._mode = settings.ai_director_mode if settings.ai_director_mode in VALID_MODES else "assisted"
        self._action_engine = action_engine
        self.queue = proposal_queue if proposal_queue is not None else ProposalQueue()
        self._task: Optional[asyncio.Task] = None
        self._event_task: Optional[asyncio.Task] = None
        self._follower_task: Optional[asyncio.Task] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._running = False
        # Phase 2 event-driven ticks.
        self._wake = asyncio.Event()
        self._last_nudge_at = 0.0
        # Phase 1/3: track already-consumed transcript so followers see each
        # line once. None => process whatever backlog exists on the first tick.
        self._last_line_ts: Optional[datetime] = None
        # Cycle bookkeeping (set on every cycle, including failed ones).
        self._last_cycle_id: Optional[str] = None
        self._last_cycle: dict = {}
        # Operator pause: the mode to restore on resume (None = not paused).
        self._paused_from: Optional[str] = None

    # -- mode ---------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid AI Director mode: {mode}")
        # An explicit mode choice ends any pause.
        self._paused_from = None
        self._mode = mode
        event_bus.publish({"event": "AI_DIRECTOR_MODE_CHANGED", "payload": {"mode": mode}})

    # -- pause / resume (operator-level; no admin key needed) ------------------------
    @property
    def paused(self) -> bool:
        return self._paused_from is not None

    @property
    def paused_from(self) -> Optional[str]:
        return self._paused_from

    def pause(self) -> dict:
        """Drop to ``manual`` (observe only) remembering the mode to come back to.

        Idempotent: pausing while paused keeps the original ``paused_from``."""
        if self._paused_from is None:
            self._paused_from = self._mode
            self._mode = "manual"
            event_bus.publish({"event": "AI_DIRECTOR_MODE_CHANGED", "payload": {"mode": "manual"}})
            event_bus.publish(
                {"event": "AI_DIRECTOR_PAUSED", "payload": {"mode": "manual", "paused_from": self._paused_from}}
            )
            logger.info("AI Director paused", paused_from=self._paused_from)
        return self.pause_state()

    def resume(self) -> dict:
        """Restore the mode that was active before :meth:`pause`; no-op when not paused."""
        if self._paused_from is not None:
            restored = self._paused_from
            self._paused_from = None
            self._mode = restored
            event_bus.publish({"event": "AI_DIRECTOR_MODE_CHANGED", "payload": {"mode": restored}})
            event_bus.publish({"event": "AI_DIRECTOR_RESUMED", "payload": {"mode": restored}})
            logger.info("AI Director resumed", mode=restored)
        return self.pause_state()

    def pause_state(self) -> dict:
        return {"mode": self._mode, "paused": self.paused, "paused_from": self._paused_from}

    def nudge(self) -> None:
        """Request an immediate decision cycle (Phase 2 event-driven)."""
        self._wake.set()

    def _engine(self) -> ActionEngine:
        if self._action_engine is None:
            self._action_engine = build_action_engine()
        return self._action_engine

    # -- views ----------------------------------------------------------------------
    @property
    def pending_actions(self) -> List[DirectorAction]:
        """Actions awaiting approval/retry, oldest first (a view of the queue)."""
        return [p.action for p in self.queue.pending()]

    def pending_proposals(self) -> List[dict]:
        self._expire()
        return self.queue.snapshot()

    @property
    def last_cycle_id(self) -> Optional[str]:
        return self._last_cycle_id

    def status(self) -> dict:
        return {
            "mode": self._mode,
            "paused": self.paused,
            "paused_from": self._paused_from,
            "pending_actions": self.pending_proposals(),
            "last_cycle": dict(self._last_cycle),
            "adaptive_confidence": adaptive_confidence.status(_bases(), service_context.state.value),
        }

    # -- lifecycle ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        if not settings.enable_ai_director:
            logger.info("AI Director runtime disabled (ENABLE_AI_DIRECTOR=false); not starting")
            return
        self._running = True
        # Close the WO-EWVERIFY-3 loop: the song follower is the lyric matcher.
        with contextlib.suppress(Exception):
            from app.easyworship.slide_expected import expected_text_provider

            expected_text_provider.set_matcher(song_follower_service.follower)
        try:
            from app.director.slide_text_source import live_slide_text_source, start_song_follower_capture

            song_follower_service.set_text_source(live_slide_text_source)
            await start_song_follower_capture()
        except Exception:
            logger.exception("Song follower slide-text source unavailable")
        if settings.operator_learning_enabled:
            from app.director.operator_learning import operator_advance_history

            song_follower_service.set_history(operator_advance_history)
        self._task = asyncio.create_task(self._run())
        self._event_task = asyncio.create_task(self._event_listener())
        self._follower_task = asyncio.create_task(self._follower_listener())
        if settings.song_follower_watch_seconds > 0:
            self._watch_task = asyncio.create_task(self._slide_watch_loop())
        logger.info("AI Director runtime started", mode=self._mode, event_driven=settings.ai_director_event_driven)

    async def stop(self) -> None:
        self._running = False
        for task in (self._task, self._event_task, self._follower_task, self._watch_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = None
        self._event_task = None
        self._follower_task = None
        self._watch_task = None

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
        """Feed pacing/ledger observations and nudge the loop on key events (Phase 2)."""
        queue = await event_bus.subscribe()
        try:
            while self._running:
                message = await queue.get()
                if not isinstance(message, dict):
                    continue
                event = message.get("event")
                payload = message.get("payload") or {}
                try:
                    self.observe_event(event, payload)
                except Exception:
                    logger.debug("AI Director event observation failed", event=event, exc_info=True)
                if not settings.ai_director_event_driven or event not in NUDGE_EVENTS:
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

    def observe_event(self, event: Optional[str], payload: dict) -> None:
        """Bus events the pacing rules and the recent-effects ledger depend on."""
        if event == "PROGRAM_CHANGED":
            commanded = bool(payload.get("commanded"))
            self._engine().note_program_change(commanded=commanded)
            if commanded:
                # The app (cue engine, operator API, or us) changed program:
                # a second AI program change within the dedupe window is a duplicate.
                self.queue.note_program_change()
        elif event in ("AUDIO_STARTED", "AUDIO_STOPPED"):
            role = str(payload.get("speaker_role") or payload.get("role") or "").lower()
            if role:
                self._engine().note_audio(role, event == "AUDIO_STARTED")

    async def _follower_listener(self) -> None:
        """Follow slides line by line as transcripts arrive, not on the slower AI tick."""
        queue = await event_bus.subscribe()
        try:
            while self._running:
                message = await queue.get()
                if not isinstance(message, dict) or message.get("event") != "TRANSCRIPT":
                    continue
                payload = message.get("payload") or {}
                line = TranscriptLine(
                    speaker_role=str(payload.get("role", "")).lower(),
                    text=str(payload.get("text", "")),
                    timestamp=datetime.now(timezone.utc),
                )
                try:
                    await self.follow_transcript([line])
                except Exception:
                    logger.exception("Song follower failed")
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await event_bus.unsubscribe(queue)

    # -- song follower ------------------------------------------------------------
    async def follow_transcript(self, lines: List[TranscriptLine]) -> List[DirectorAction]:
        """Feed transcript lines to the song follower and route its proposals."""
        if not settings.song_follower_enabled or not lines:
            return []
        await song_follower_service.refresh()
        actions = song_follower_service.process(lines)
        self._drop_stale_follower_actions()
        if actions:
            await self._route(actions, source="song_follower")
        return actions

    async def _slide_watch_loop(self) -> None:
        """Notice slide changes made outside the follower even when nobody is singing."""
        while self._running:
            await asyncio.sleep(settings.song_follower_watch_seconds)
            try:
                await self.watch_slides()
            except Exception:
                logger.exception("Slide watcher failed")

    async def watch_slides(self) -> bool:
        """Resync the follower to the live slide (EasyWorship + camera 2); True on a manual change."""
        if not settings.song_follower_enabled:
            return False
        await song_follower_service.refresh()
        moved = song_follower_service.resync()
        self._drop_stale_follower_actions()
        if moved:
            event_bus.publish(
                {"event": "SLIDE_FOLLOWER_RESYNCED", "payload": song_follower_service.follower.status()}
            )
        return moved

    def _drop_stale_follower_actions(self) -> None:
        """Pending slide advances the live slide has moved past would skip a slide if approved."""
        stale = self.queue.remove_where(lambda p: song_follower_service.is_stale(p.action))
        self._publish_dropped(stale, "stale slide advance (live slide changed)")

    def _expire(self) -> None:
        self._publish_dropped(self.queue.expire(), "expired")

    def _publish_dropped(self, proposals: List[Proposal], reason: str) -> None:
        if not proposals:
            return
        for proposal in proposals:
            song_follower_service.notify_rejected(proposal.action)
            cycle_log.log_action_outcome(
                proposal.cycle_id or self._last_cycle_id or "unknown",
                proposal.action.model_dump(mode="json"), "dropped", reason,
            )
        event_bus.publish(
            {
                "event": "AI_ACTIONS_PENDING_DROPPED",
                "payload": [{**p.to_dict(), "drop_reason": reason} for p in proposals],
            }
        )

    # -- decision cycle -------------------------------------------------------------
    async def tick(self) -> DirectorDecision:
        """Run one decision cycle. Exposed directly for tests/replay mode."""
        started = time.perf_counter()
        previous_cycle = getattr(ai_service_director, "last_cycle_id", None)
        try:
            decision = await ai_service_director.decide(service_context)
        except Exception as e:  # the reasoner should never raise; keep the loop alive
            logger.exception("AI Director decide() failed")
            decision = DirectorDecision(
                decision="continue", confidence=0.0, reason=f"AI Director failed: {type(e).__name__}"
            )
        measured_ms = int((time.perf_counter() - started) * 1000)
        latency_ms = decision.latency_ms if decision.latency_ms is not None else measured_ms

        # A cycle id for *every* cycle. The reasoner only exposes its id after a
        # successful parse, so a failed cycle would otherwise be attributed to
        # the previous successful one.
        director_cycle = getattr(ai_service_director, "last_cycle_id", None)
        cycle_id = director_cycle if (director_cycle and director_cycle != previous_cycle) else cycle_log.new_cycle_id()
        self._last_cycle_id = cycle_id

        current_version = getattr(service_context, "version", None)
        drift = None
        if decision.context_version is not None and current_version is not None:
            drift = int(current_version) - int(decision.context_version)
        max_drift = int(getattr(settings, "ai_director_max_context_drift", 3))
        stale = drift is not None and drift > max_drift

        decision_payload = {
            **decision.model_dump(mode="json"),
            "cycle_id": cycle_id,
            "latency_ms": latency_ms,
            "stale": stale,
            "current_context_version": current_version,
        }
        service_context.last_decision = decision_payload
        self._last_cycle = {
            "cycle_id": cycle_id,
            "latency_ms": latency_ms,
            "context_version": decision.context_version,
            "current_context_version": current_version,
            "drift": drift,
            "stale": stale,
            "decision": decision.decision,
            "confidence": decision.confidence,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            "AI Director cycle",
            cycle_id=cycle_id, latency_ms=latency_ms, decision=decision.decision,
            confidence=decision.confidence, stale=stale, drift=drift,
        )

        phase_payload = None
        if stale:
            event_bus.publish(
                {
                    "event": "AI_DECISION_STALE",
                    "payload": {
                        "cycle_id": cycle_id,
                        "context_version": decision.context_version,
                        "current_context_version": current_version,
                        "drift": drift,
                        "max_drift": max_drift,
                        "latency_ms": latency_ms,
                    },
                }
            )
        else:
            phase_payload = self._reconcile_phase(decision, cycle_id)

        event_bus.publish({"event": "AI_DECISION", "payload": decision_payload})
        cycle_log.log_cycle(
            cycle_id,
            latency_ms=latency_ms,
            context_version=decision.context_version,
            current_version=current_version,
            stale=stale,
            mode=self._mode,
            decision=decision.model_dump(mode="json"),
            phase=phase_payload,
        )

        # Feed the autonomy components regardless of mode so their internal
        # state stays in sync; only the resulting actions honor the mode gate.
        new_lines = self._drain_new_transcript()
        service_end_recognizer.observe_state(service_context.state)
        for line in new_lines:
            service_end_recognizer.observe_transcript(line)

        self._expire()
        await self._retry_held()

        decision_actions: List[DirectorAction] = []
        if not stale:
            decision_actions = [
                a for a in (_to_director_action(spec, decision) for spec in decision.actions) if a
            ]
        proposal = service_end_recognizer.evaluate()
        end_actions = list(proposal.actions) if proposal else []
        await self._route(decision_actions + end_actions, source="ai_director")
        return decision

    def _reconcile_phase(self, decision: DirectorDecision, cycle_id: str) -> Optional[dict]:
        """The LLM proposes a service phase; the reconciler decides."""
        try:
            phase = phase_reconciler.propose(decision.service_state, decision.confidence, self._mode)
        except Exception:
            logger.exception("Phase reconciler failed")
            return None
        payload = {
            "cycle_id": cycle_id,
            "proposed": decision.service_state,
            "confidence": decision.confidence,
            "accepted": bool(phase.accepted),
            "state": phase.state.value if phase.state is not None else None,
            "reason": phase.reason,
            "source": phase.source,
            "mode": self._mode,
        }
        if decision.service_state or phase.accepted:
            logger.info("AI phase proposal", **payload)
            event_bus.publish({"event": "AI_PHASE_PROPOSAL", "payload": payload})
        if phase.accepted and phase.state is not None:
            service_context.set_state(phase.state)
        return payload

    def _drain_new_transcript(self) -> List:
        """Return transcript lines not yet seen by the autonomy components."""
        lines = list(service_context.transcript)
        if self._last_line_ts is not None:
            lines = [line for line in lines if line.timestamp > self._last_line_ts]
        if lines:
            self._last_line_ts = lines[-1].timestamp
        return lines

    # -- routing ------------------------------------------------------------------
    async def propose(self, actions: List[DirectorAction], source: str = "ai_director") -> dict:
        """Submit externally-produced actions (e.g. /director/observe) through the mode gate."""
        return await self._route(actions, source=source)

    async def _route(self, actions: List[DirectorAction], source: Optional[str] = None) -> dict:
        """Queue proposals, then apply the operating-mode gate."""
        outcome: dict = {"accepted": False, "mode": self._mode, "proposals": [], "dropped": []}
        if not actions:
            return outcome
        if self._mode == "manual":
            for action in actions:
                song_follower_service.notify_rejected(action)
                outcome["dropped"].append({**action.model_dump(mode="json"), "reason": "manual mode: observe only"})
            return outcome

        accepted: List[Proposal] = []
        for action in actions:
            action_source = normalize_source(source or action.parameters.get("source"))
            submitted = self.queue.submit(action, action_source, cycle_id=self._last_cycle_id)
            self._publish_dropped(submitted.expired, "expired")
            self._publish_dropped(submitted.superseded, "superseded by a newer proposal")
            if not submitted.accepted or submitted.proposal is None:
                song_follower_service.notify_rejected(action)
                payload = {**action.model_dump(mode="json"), "source": action_source, "reason": submitted.reason}
                logger.info("AI proposal dropped", type=action.type.value, target=action.target, reason=submitted.reason)
                cycle_log.log_action_outcome(self._last_cycle_id or "unknown", payload, "dropped", submitted.reason)
                event_bus.publish({"event": "AI_PROPOSAL_DROPPED", "payload": payload})
                outcome["dropped"].append(payload)
                continue
            accepted.append(submitted.proposal)

        if not accepted:
            return outcome
        outcome["accepted"] = True

        # Pre-stage camera shots on preview (never cuts) so the take is instant.
        await self._prestage(accepted)

        if self._mode == "assisted":
            for proposal in accepted:
                cycle_log.log_action_outcome(
                    proposal.cycle_id or "unknown", proposal.action.model_dump(mode="json"), "pending_approval",
                )
            event_bus.publish(
                {"event": "AI_ACTIONS_PENDING_APPROVAL", "payload": [p.to_dict() for p in accepted]}
            )
            outcome["proposals"] = [p.to_dict() for p in accepted]
            return outcome

        # ai_directed: execute immediately (still policy-gated per action).
        for proposal in accepted:
            await self._execute_proposal(proposal)
        outcome["proposals"] = [p.to_dict() for p in accepted]
        return outcome

    async def _retry_held(self) -> None:
        """Held (paced-out) proposals are retried each tick until they run or expire."""
        if self._mode != "ai_directed":
            return
        for proposal in self.queue.held():
            if self.queue.get(proposal.id) is not None:
                await self._execute_proposal(proposal)

    async def _prestage(self, proposals: List[Proposal]) -> None:
        if not settings.ptz_predictive_preview or self._mode == "manual":
            return
        for proposal in proposals:
            action = proposal.action
            if action.type in CAMERA_ACTION_TYPES and action.target:
                if predictive_preview.is_on_program(action.target):
                    logger.info("Prestage skipped: role camera is on program", role=action.target)
                    continue
                await predictive_preview.stage_role(action.target)
                break

    def _adaptive_gate(self, action: DirectorAction) -> tuple:
        """Extra, operator-feedback-driven threshold on top of the policy floor."""
        category = _category_for(action.type)
        if category not in _BASE_THRESHOLDS:
            return True, None
        base = _BASE_THRESHOLDS[category]()
        threshold = adaptive_confidence.threshold_for(category, base, service_context.state.value)
        if action.confidence < threshold:
            return False, f"adaptive '{category}' threshold {threshold:.2f}"
        return True, None

    def _reject(self, proposal: Proposal, reason: Optional[str], *, status: str = "rejected") -> None:
        action = proposal.action
        self.queue.pop(proposal.id)
        cycle_log.log_action_outcome(
            proposal.cycle_id or self._last_cycle_id or "unknown", action.model_dump(mode="json"), status, reason
        )
        event_bus.publish(
            {"event": "AI_ACTION_REJECTED", "payload": {**action.model_dump(mode="json"), "id": proposal.id, "reason": reason}}
        )
        song_follower_service.notify_rejected(action)

    async def _execute_proposal(self, proposal: Proposal) -> Optional[ActionResult]:
        """ai_directed path: ledger + fusion + adaptive gates, then the paced, policy-gated engine."""
        action = proposal.action
        cycle_id = proposal.cycle_id or self._last_cycle_id or "unknown"

        conflict = self.queue.effect_conflict(action)
        if conflict:
            self._reject(proposal, f"duplicate effect: {conflict}", status="dropped")
            return None

        ok, reason = evidence_fusion.corroborates(action, service_context.vision)
        if not ok:
            self._reject(proposal, reason)
            return None

        ok, reason = self._adaptive_gate(action)
        if not ok:
            self._reject(proposal, reason)
            return None

        result = await self._engine().execute(action)
        if result.held:
            # Hold means "try again next tick": keep it queued with the reason.
            self.queue.hold(proposal.id, result.held_reason or result.detail)
            cycle_log.log_action_outcome(cycle_id, action.model_dump(mode="json"), "held", result.held_reason)
            return result

        self.queue.pop(proposal.id)
        if result.executed:
            self.queue.note_effect(action)
            cycle_log.log_action_outcome(cycle_id, action.model_dump(mode="json"), "executed", result.detail)
            song_follower_service.notify_executed(action)
            await learning_recorder.record_auto(action)
            await self._maybe_shutdown(action)
        else:
            cycle_log.log_action_outcome(cycle_id, action.model_dump(mode="json"), "rejected", result.detail)
            song_follower_service.notify_rejected(action)
        return result

    async def _maybe_shutdown(self, action: DirectorAction) -> None:
        if (
            action.type == DirectorActionType.SERVICE_STATE_CHANGE
            and action.parameters.get("source") == "service_end"
            and action.target == ServiceState.POST_SERVICE.value
            and settings.service_end_auto_shutdown
        ):
            await run_shutdown_bundle()

    # -- operator approval ----------------------------------------------------------
    def _resolve(self, ref: Union[int, str]) -> Proposal:
        """Resolve a proposal id (preferred) or a legacy queue index."""
        self._expire()
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            index = int(ref)
            proposal_id = self.queue.resolve_index(index)
            if proposal_id is None:
                raise IndexError("pending action index out of range")
            ref = proposal_id
        proposal = self.queue.get(str(ref))
        if proposal is None:
            raise KeyError(f"no pending proposal {ref}")
        return proposal

    async def approve_pending(self, ref: Union[int, str]) -> dict:
        """Execute one pending (assisted-mode) action by id (or legacy index)."""
        proposal = self._resolve(ref)
        self.queue.pop(proposal.id)
        action = proposal.action
        cycle_id = proposal.cycle_id or self._last_cycle_id or "unknown"
        if song_follower_service.is_stale(action):
            # The live slide moved since this was proposed; advancing now would skip a slide.
            return {
                **ActionResult(
                    action=action, approved=False, executed=False, detail="stale slide advance (live slide changed)"
                ).model_dump(mode="json"),
                "id": proposal.id,
            }
        conflict = self.queue.effect_conflict(action)
        if conflict:
            return {
                **ActionResult(action=action, approved=False, executed=False, detail=f"duplicate effect: {conflict}")
                .model_dump(mode="json"),
                "id": proposal.id,
            }
        # The operator's explicit approval is the pacing decision: run it now.
        result = await self._engine().execute(action, pace=False)
        if result.executed:
            self.queue.note_effect(action)
            cycle_log.log_action_outcome(cycle_id, action.model_dump(mode="json"), "executed_after_approval", result.detail)
            song_follower_service.notify_executed(action)
            await self._maybe_shutdown(action)
        await learning_recorder.record_approval(action)
        adaptive_confidence.record_feedback(_category_for(action.type), service_context.state.value, good=True)
        return {**result.model_dump(mode="json"), "id": proposal.id}

    def reject_pending(self, ref: Union[int, str]) -> dict:
        proposal = self._resolve(ref)
        self.queue.pop(proposal.id)
        action = proposal.action
        song_follower_service.notify_rejected(action)
        cycle_log.log_action_outcome(
            proposal.cycle_id or self._last_cycle_id or "unknown",
            action.model_dump(mode="json"), "rejected_by_operator",
        )
        adaptive_confidence.record_feedback(_category_for(action.type), service_context.state.value, good=False)
        with contextlib.suppress(RuntimeError):
            asyncio.ensure_future(learning_recorder.record_rejection(action))
        return {"ok": True, "id": proposal.id}


# Module-level singleton
ai_director_runtime = AIDirectorRuntime()
