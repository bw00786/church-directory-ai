"""Deterministic service-phase reconciliation.

The LLM may *propose* a service state; it never sets one by itself. Three
sources of truth about "where in the service are we" used to drift apart --
the cue engine's index, ``ServiceContext.state`` and EasyWorship's live
``pres_no``. This module reconciles them:

1. **Cue engine running** -- the current cue's state (``plan_builder``) is
   authoritative. An LLM proposal that disagrees is rejected with the reason;
   one that agrees is accepted (no-op).
2. **Otherwise** an LLM proposal is accepted only if its confidence clears
   ``settings.ai_director_min_confidence`` *and* at least one hard signal
   agrees (``settings.phase_llm_requires_hard_signal``, default True): the
   live EasyWorship item maps to that state, the state is the current or next
   plan element, or the per-role speaking pattern implies it (vocalist /
   congregation singing for >= ``settings.phase_singing_seconds`` -> song
   family; pastor speaking that long with no one singing -> sermon family,
   only when the plan has a sermon-family element current/next).
3. **Hard signals alone** move the state: the cue engine entering a cue
   (source ``"cue"``) and an EasyWorship item change (source
   ``"easyworship"``), via :meth:`PhaseReconciler.apply_hard_signals`, which
   ``app.director.context_sync`` calls. Audio alone never moves the phase.

Contract used by app.director.ai_director_runtime:

    from app.director.phase import phase_reconciler
    decision = phase_reconciler.propose(llm_state, confidence, mode)
    if decision.accepted: service_context.set_state(decision.state)

Hardware-free: everything is read from ``ServiceContext`` and the director's
status object, both injectable for tests.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Optional

from app.config import settings
from app.domain.service_context import SINGING_ROLES, ServiceContext, service_context
from app.domain.service_state import SERMON_STATES, SONG_STATES, ServiceState

# New settings (add to app.config.Settings):
#   phase_llm_requires_hard_signal: bool = True
#   phase_singing_seconds: float = 8.0
_DEFAULTS = {"phase_llm_requires_hard_signal": True, "phase_singing_seconds": 8.0}


def _setting(name: str):
    return getattr(settings, name, _DEFAULTS[name])


@dataclass(frozen=True)
class PhaseDecision:
    accepted: bool
    state: Optional[ServiceState]
    reason: str
    source: str = "llm"  # llm | cue | easyworship | audio

    def as_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "state": self.state.value if self.state else None,
            "reason": self.reason,
            "source": self.source,
        }


@dataclass
class HardSignals:
    """Everything the reconciler knows that did not come from the LLM."""

    cue_running: bool = False
    cue_id: Optional[str] = None
    cue_index: Optional[int] = None
    cue_state: Optional[ServiceState] = None
    easyworship_item: Optional[str] = None
    easyworship_state: Optional[ServiceState] = None
    plan_states: set = field(default_factory=set)   # current + next plan element
    audio_states: set = field(default_factory=set)
    audio_reason: Optional[str] = None

    def agreement_for(self, state: ServiceState) -> list:
        """Names of the hard signals that agree with ``state``."""
        names = []
        if self.cue_state == state:
            names.append("cue")
        if self.easyworship_state == state:
            names.append("easyworship")
        if state in self.plan_states:
            names.append("plan")
        if state in self.audio_states:
            names.append("audio")
        return names

    def as_dict(self) -> dict:
        return {
            "cue_running": self.cue_running,
            "cue_id": self.cue_id,
            "cue_index": self.cue_index,
            "cue_state": self.cue_state.value if self.cue_state else None,
            "easyworship_item": self.easyworship_item,
            "easyworship_state": self.easyworship_state.value if self.easyworship_state else None,
            "plan_states": sorted(s.value for s in self.plan_states),
            "audio_states": sorted(s.value for s in self.audio_states),
            "audio_reason": self.audio_reason,
        }


class PhaseReconciler:
    """Gate LLM state proposals against confidence and hard evidence, and
    apply hard signals (cue entry, EasyWorship item) directly."""

    def __init__(
        self,
        context: Optional[ServiceContext] = None,
        director_status: Optional[Callable[[], object]] = None,
        current_cue: Optional[Callable[[], object]] = None,
    ) -> None:
        self._context = context
        self._director_status = director_status
        self._current_cue = current_cue
        self._last: Optional[PhaseDecision] = None
        self._last_signals: Optional[HardSignals] = None
        self._last_mode: Optional[str] = None
        self._history: Deque[dict] = deque(maxlen=20)

    # -- inputs ---------------------------------------------------------------
    @property
    def context(self) -> ServiceContext:
        return self._context if self._context is not None else service_context

    def _cue_position(self):
        """``(running, cue, index)`` from the injected callables or the live director."""
        if self._director_status is not None:
            status = self._director_status()
            cue = self._current_cue() if self._current_cue is not None else getattr(status, "current_cue", None)
            return bool(getattr(status, "running", False)), cue, getattr(status, "cue_index", None)
        try:
            from app.director.engine import service_director
        except Exception:  # pragma: no cover - import problems only
            return False, None, None
        status = service_director.status()
        return bool(status.running), service_director.current_cue(), status.cue_index

    def gather(self) -> HardSignals:
        ctx = self.context
        signals = HardSignals()

        running, cue, index = self._cue_position()
        if running and cue is not None:
            from app.director.plan_builder import plan_entry_for_cue

            signals.cue_running = True
            signals.cue_id = cue.id
            signals.cue_index = index
            signals.cue_state = plan_entry_for_cue(cue).state

        item = ctx.current_easyworship_item
        ew_index = ctx.plan.index_of_easyworship_item(item)
        if ew_index is not None:
            signals.easyworship_item = item
            signals.easyworship_state = ctx.plan.elements[ew_index].type

        cursor = ctx.plan_cursor()
        for key in ("current", "next"):
            element = cursor.get(key)
            if element:
                signals.plan_states.add(ServiceState(element["state"]))

        self._audio_signals(ctx, signals)
        return signals

    @staticmethod
    def _audio_signals(ctx: ServiceContext, signals: HardSignals) -> None:
        singing_seconds = float(_setting("phase_singing_seconds"))
        singing = [role for role in SINGING_ROLES if ctx.speaking_for(role) >= singing_seconds]
        if singing:
            signals.audio_states |= set(SONG_STATES)
            signals.audio_reason = f"{'/'.join(singing)} singing for >= {singing_seconds:g}s"
            return
        anyone_singing = any(ctx.speaking_for(role) > 0 for role in SINGING_ROLES)
        pastor = ctx.speaking_for("pastor")
        if pastor >= singing_seconds and not anyone_singing:
            planned = set(SERMON_STATES) & signals.plan_states
            if planned:
                signals.audio_states |= planned
                signals.audio_reason = f"pastor speaking for >= {singing_seconds:g}s and the plan expects a sermon"
            else:
                signals.audio_reason = f"pastor speaking for >= {singing_seconds:g}s but no sermon planned here"

    # -- LLM proposals --------------------------------------------------------
    def propose(self, llm_state: Optional[str], confidence: float, mode: str = "assisted") -> PhaseDecision:
        self._last_mode = mode
        if not llm_state:
            return self._finish(PhaseDecision(False, None, "no state proposed"), record=False)
        try:
            state = ServiceState(str(llm_state).strip().lower())
        except ValueError:
            return self._finish(PhaseDecision(False, None, f"unknown state {llm_state!r}"))

        signals = self.gather()
        self._last_signals = signals
        ctx = self.context

        # Rule 1: a running cue engine owns the phase.
        if signals.cue_running and signals.cue_state is not None:
            if state == signals.cue_state:
                return self._finish(
                    PhaseDecision(True, state, f"matches running cue '{signals.cue_id}'", "cue"), record=False
                )
            return self._finish(
                PhaseDecision(
                    False,
                    signals.cue_state,
                    f"cue engine running: cue '{signals.cue_id}' fixes state "
                    f"{signals.cue_state.value}; LLM proposal {state.value} cannot override it",
                    "cue",
                )
            )

        # Rule 2: confidence gate ...
        threshold = float(settings.ai_director_min_confidence)
        if confidence < threshold:
            return self._finish(
                PhaseDecision(False, state, f"confidence {confidence:.2f} < {threshold:.2f}")
            )
        if state == ctx.state:
            return self._finish(PhaseDecision(True, state, "already in that state"), record=False)

        # ... and at least one hard signal must agree.
        agreeing = signals.agreement_for(state)
        if _setting("phase_llm_requires_hard_signal") and not agreeing:
            return self._finish(
                PhaseDecision(
                    False,
                    state,
                    f"no hard signal agrees with {state.value} "
                    f"(easyworship={signals.easyworship_state.value if signals.easyworship_state else None}, "
                    f"plan={sorted(s.value for s in signals.plan_states)}, "
                    f"audio={sorted(s.value for s in signals.audio_states)})",
                )
            )
        agreed = ", ".join(agreeing) if agreeing else "none required"
        return self._finish(
            PhaseDecision(True, state, f"confidence {confidence:.2f} >= {threshold:.2f}; agrees with {agreed}")
        )

    # -- hard signals ---------------------------------------------------------
    def apply_hard_signals(
        self,
        source: str = "auto",
        state: Optional[ServiceState] = None,
        detail: Optional[str] = None,
    ) -> PhaseDecision:
        """Move the context state from hard evidence. ``source`` is ``"cue"``,
        ``"easyworship"``, ``"audio"`` or ``"auto"`` (whichever applies, cue
        first). For ``"cue"`` the caller may pass the entered cue's ``state``
        (the DIRECTOR_CUE_ENTERED payload) so the move does not depend on
        re-reading the director. Returns ``accepted=True`` when the context is
        now in the signalled state (``reason`` says whether it changed)."""
        ctx = self.context
        signals = self.gather()
        self._last_signals = signals

        if source == "audio":
            return self._finish(
                PhaseDecision(False, None, "audio alone does not move the phase", "audio"), record=False
            )

        cue_state = signals.cue_state if signals.cue_running else None
        if source == "cue" and state is not None:
            cue_state = state
        if cue_state is not None and source in ("auto", "cue", "easyworship"):
            changed = ctx.set_state(cue_state, "cue")
            label = detail or signals.cue_id or "?"
            reason = f"cue '{label}' entered" if changed else f"already in cue '{label}' state"
            return self._finish(PhaseDecision(True, cue_state, reason, "cue"), record=changed)

        if source == "cue":
            return self._finish(PhaseDecision(False, None, "cue engine not running", "cue"), record=False)

        if signals.easyworship_state is not None:
            changed = ctx.set_state(signals.easyworship_state, "easyworship")
            item = signals.easyworship_item
            reason = f"EasyWorship item '{item}' live" if changed else f"already in state for EasyWorship item '{item}'"
            return self._finish(PhaseDecision(True, signals.easyworship_state, reason, "easyworship"), record=changed)

        return self._finish(PhaseDecision(False, None, "no hard signal maps to a state", source), record=False)

    # -- status ---------------------------------------------------------------
    def _finish(self, decision: PhaseDecision, record: bool = True) -> PhaseDecision:
        self._last = decision
        if record:
            entry = decision.as_dict()
            entry["at"] = time.time()
            self._history.append(entry)
        return decision

    def status(self) -> dict:
        signals = self._last_signals
        return {
            "last_decision": self._last.as_dict() if self._last else None,
            "last_mode": self._last_mode,
            "signals": signals.as_dict() if signals else None,
            "context_state": self.context.state.value,
            "context_state_source": self.context.state_source,
            "history": list(self._history),
            "settings": {
                "ai_director_min_confidence": float(settings.ai_director_min_confidence),
                "phase_llm_requires_hard_signal": bool(_setting("phase_llm_requires_hard_signal")),
                "phase_singing_seconds": float(_setting("phase_singing_seconds")),
            },
        }


phase_reconciler = PhaseReconciler()
