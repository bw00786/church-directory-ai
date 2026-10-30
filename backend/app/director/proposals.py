"""Single proposal queue for every AI-runtime action source.

Before this module, four decision makers could each reach hardware on their
own: the LLM director, the song follower, the service-end recognizer and the
``/director/observe`` cue-advance path. Each AI-runtime proposal now goes
through one ``ProposalQueue`` which

- assigns a stable ``id`` (uuid) so the operator approves/rejects *that*
  proposal, not "whatever is at index 0 right now";
- expires proposals after ``settings.ai_director_pending_ttl_seconds``;
- supersedes: a newer proposal in the same category replaces an older pending
  one, unless the pending one came from a higher-priority source;
- dedupes at the effect level: the same ``type + target`` proposed again
  within ``settings.ai_director_dedupe_seconds`` of a pending/executed copy is
  dropped;
- keeps a "recent effects" ledger of what the application itself just did
  (EasyWorship navigation, ATEM program changes) so an AI cut two seconds after
  a cue-engine cut is recognised as a duplicate and never executed.

The queue is pure (no event bus, no hardware); the runtime publishes events
from the results it returns. See docs/ai-director.md ("Proposal queue").
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from app.config import settings
from app.director.action_models import (
    ATEM_ACTION_TYPES,
    CAMERA_ACTION_TYPES,
    SLIDE_ACTION_TYPES,
    DirectorAction,
    DirectorActionType,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

# Lower number = higher priority. Operator always wins; vision only advises.
SOURCE_PRIORITY: Dict[str, int] = {
    "operator": 0,
    "cue_engine": 1,
    "song_follower": 2,
    "ai_director": 3,
    "vision": 4,
}
DEFAULT_SOURCE = "ai_director"

# Action types whose effect is an EasyWorship navigation command.
EASYWORSHIP_NAV_TYPES = {
    DirectorActionType.EASYWORSHIP_NEXT,
    DirectorActionType.EASYWORSHIP_NEXT_SLIDE,
    DirectorActionType.EASYWORSHIP_PREVIOUS,
    DirectorActionType.EASYWORSHIP_SELECT,
}
# Action types whose effect is a change of the ATEM program bus.
PROGRAM_CHANGE_TYPES = {
    DirectorActionType.ATEM_CUT,
    DirectorActionType.ATEM_AUTO,
    DirectorActionType.ATEM_SET_PROGRAM,
}


def category_for(action_type: DirectorActionType) -> str:
    if action_type in CAMERA_ACTION_TYPES:
        return "camera_change"
    if action_type in SLIDE_ACTION_TYPES:
        return "slide_change"
    if action_type in ATEM_ACTION_TYPES:
        return "atem_transition"
    if action_type == DirectorActionType.CUE_ADVANCE:
        return "cue_advance"
    return "service_state"


def source_priority(source: str) -> int:
    return SOURCE_PRIORITY.get(source, SOURCE_PRIORITY[DEFAULT_SOURCE])


def normalize_source(source: Optional[str]) -> str:
    """Map an action's ``parameters.source`` tag to a queue source."""
    if source in SOURCE_PRIORITY:
        return source
    return DEFAULT_SOURCE


# Parameters that distinguish one effect from another of the same type+target.
_EFFECT_PARAMS = ("song_id", "to_index", "atem_input", "camera_id", "preset_id", "cue_id")


def effect_key(action: DirectorAction) -> tuple:
    """What the action *does*: type + target + the parameters that select the effect."""
    params = action.parameters or {}
    return (action.type.value, action.target) + tuple(params.get(k) for k in _EFFECT_PARAMS)


def _ttl_seconds() -> float:
    return float(getattr(settings, "ai_director_pending_ttl_seconds", 90.0))


def _dedupe_seconds() -> float:
    return float(getattr(settings, "ai_director_dedupe_seconds", 5.0))


@dataclass
class Proposal:
    """One queued action plus the bookkeeping the operator UI needs."""

    action: DirectorAction
    source: str
    category: str
    created_at: float
    expires_at: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cycle_id: Optional[str] = None
    held_reason: Optional[str] = None
    hold_count: int = 0

    @property
    def target(self) -> Optional[str]:
        return self.action.target

    def effect_key(self) -> tuple:
        return effect_key(self.action)

    def to_dict(self, now: Optional[float] = None) -> dict:
        now = time.monotonic() if now is None else now
        payload = self.action.model_dump(mode="json")
        payload.update(
            {
                "id": self.id,
                "source": self.source,
                "category": self.category,
                "cycle_id": self.cycle_id,
                "held_reason": self.held_reason,
                "hold_count": self.hold_count,
                "age_seconds": round(max(0.0, now - self.created_at), 1),
                "expires_in_seconds": round(max(0.0, self.expires_at - now), 1),
            }
        )
        return payload


@dataclass
class SubmitResult:
    accepted: bool
    reason: str
    proposal: Optional[Proposal] = None
    superseded: List[Proposal] = field(default_factory=list)
    expired: List[Proposal] = field(default_factory=list)


class ProposalQueue:
    """Ordered pending proposals + the recent-effects ledger."""

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
        easyworship_commanded_within: Optional[Callable[[float], bool]] = None,
    ) -> None:
        self._now = now
        self._pending: List[Proposal] = []
        # effect key -> monotonic time the effect was last applied by this app
        self._effects: Dict[tuple, float] = {}
        self._last_program_change_at: Optional[float] = None
        self._ew_commanded_within = easyworship_commanded_within

    # -- queries ---------------------------------------------------------------
    def pending(self) -> List[Proposal]:
        return list(self._pending)

    def __len__(self) -> int:
        return len(self._pending)

    def get(self, proposal_id: str) -> Optional[Proposal]:
        for proposal in self._pending:
            if proposal.id == proposal_id:
                return proposal
        return None

    def resolve_index(self, index: int) -> Optional[str]:
        """Legacy index -> id (index routes kept for one release)."""
        if 0 <= index < len(self._pending):
            return self._pending[index].id
        return None

    def held(self) -> List[Proposal]:
        return [p for p in self._pending if p.held_reason]

    def snapshot(self) -> List[dict]:
        now = self._now()
        return [p.to_dict(now) for p in self._pending]

    # -- mutation --------------------------------------------------------------
    def submit(
        self,
        action: DirectorAction,
        source: str = DEFAULT_SOURCE,
        *,
        cycle_id: Optional[str] = None,
    ) -> SubmitResult:
        now = self._now()
        expired = self.expire(now)
        category = category_for(action.type)
        key = effect_key(action)
        priority = source_priority(source)

        # Effect-level dedupe: recently *done* by the app?
        conflict = self.effect_conflict(action, now=now)
        if conflict:
            return SubmitResult(False, conflict, expired=expired)

        # Effect-level dedupe: identical proposal already pending?
        for pending in self._pending:
            if pending.effect_key() == key:
                return SubmitResult(False, f"duplicate of pending proposal {pending.id}", expired=expired)

        # Per-category supersede, honouring source priority.
        superseded: List[Proposal] = []
        for pending in self._pending:
            if pending.category != category:
                continue
            if source_priority(pending.source) < priority:
                return SubmitResult(
                    False,
                    f"outranked by pending {pending.source} proposal {pending.id} in '{category}'",
                    expired=expired,
                )
            superseded.append(pending)
        if superseded:
            ids = {p.id for p in superseded}
            self._pending = [p for p in self._pending if p.id not in ids]

        proposal = Proposal(
            action=action,
            source=source,
            category=category,
            created_at=now,
            expires_at=now + _ttl_seconds(),
            cycle_id=cycle_id,
        )
        self._pending.append(proposal)
        return SubmitResult(True, "queued", proposal=proposal, superseded=superseded, expired=expired)

    def expire(self, now: Optional[float] = None) -> List[Proposal]:
        now = self._now() if now is None else now
        expired = [p for p in self._pending if p.expires_at <= now]
        if expired:
            ids = {p.id for p in expired}
            self._pending = [p for p in self._pending if p.id not in ids]
        return expired

    def pop(self, proposal_id: str) -> Optional[Proposal]:
        for i, proposal in enumerate(self._pending):
            if proposal.id == proposal_id:
                return self._pending.pop(i)
        return None

    def remove_where(self, predicate: Callable[[Proposal], bool]) -> List[Proposal]:
        removed = [p for p in self._pending if predicate(p)]
        if removed:
            ids = {p.id for p in removed}
            self._pending = [p for p in self._pending if p.id not in ids]
        return removed

    def hold(self, proposal_id: str, reason: str) -> Optional[Proposal]:
        """Keep the proposal queued for the next tick (hold != reject)."""
        proposal = self.get(proposal_id)
        if proposal is not None:
            proposal.held_reason = reason
            proposal.hold_count += 1
        return proposal

    def clear(self) -> None:
        self._pending.clear()

    # -- recent-effects ledger -------------------------------------------------
    def note_effect(self, action: DirectorAction, at: Optional[float] = None) -> None:
        at = self._now() if at is None else at
        self._effects[effect_key(action)] = at
        if action.type in PROGRAM_CHANGE_TYPES:
            self._last_program_change_at = at

    def note_program_change(self, at: Optional[float] = None) -> None:
        """An ATEM program change happened (ours or the cue engine's)."""
        self._last_program_change_at = self._now() if at is None else at

    @property
    def last_program_change_at(self) -> Optional[float]:
        return self._last_program_change_at

    def program_changed_within(self, seconds: float, now: Optional[float] = None) -> bool:
        if self._last_program_change_at is None:
            return False
        now = self._now() if now is None else now
        return now - self._last_program_change_at <= seconds

    def _easyworship_commanded_within(self, seconds: float) -> bool:
        if self._ew_commanded_within is not None:
            return bool(self._ew_commanded_within(seconds))
        try:
            from app.easyworship.service import easyworship_service

            return bool(easyworship_service.commanded_within(seconds))
        except Exception:
            return False

    def effect_conflict(self, action: DirectorAction, now: Optional[float] = None) -> Optional[str]:
        """Why executing ``action`` now would duplicate something just done, else None."""
        now = self._now() if now is None else now
        window = _dedupe_seconds()
        if window <= 0:
            return None
        last = self._effects.get(effect_key(action))
        if last is not None and now - last <= window:
            return f"same effect applied {now - last:.1f}s ago"
        # The song follower's slide advances carry song_id/from_index and are
        # staleness-checked against the live slide; the coarse "anything was
        # commanded" check would only block legitimate fast slide sequences.
        follower = (action.parameters or {}).get("source") == "song_follower"
        if action.type in EASYWORSHIP_NAV_TYPES and not follower and self._easyworship_commanded_within(window):
            return f"EasyWorship was commanded within the last {window:g}s"
        if action.type in PROGRAM_CHANGE_TYPES and self.program_changed_within(window, now):
            return f"ATEM program changed within the last {window:g}s"
        return None
