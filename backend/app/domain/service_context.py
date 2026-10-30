"""Authoritative, application-owned service context (short-term memory).

The AI Director is stateless between calls — this class is the "Service
Context Memory" from docs/ai-director.md: it owns the current ServiceState,
the last N transcript lines, and the last known hardware state (camera role,
ATEM program/preview, EasyWorship item), and supplies all of it to the AI as
context. Do not rely on the LLM's own conversational memory.

Who writes here:

- ``app.director.context_sync.ContextSync`` keeps the hardware fields current
  from bus events (ATEM program/preview changes, EasyWorship read-back, VAD
  start/stop, cue-engine cue entry) -- whoever caused the change (cue engine,
  operator panel, AI action engine, a hand on the ATEM).
- ``app.audio`` records VAD observations and transcripts (``record_audio``).
- ``app.director.phase.PhaseReconciler`` is the only thing that should move
  ``state`` (``set_state``); the LLM merely proposes.

Every mutation bumps ``version`` and ``updated_at`` so a decision produced
against a stale snapshot can be detected (``snapshot()["version"]``).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, Optional

from app.domain.observations import AudioObservation
from app.domain.service_plan import ServicePlan, build_default_service_plan
from app.domain.service_state import ServiceState

if False:  # typing only; avoid a hard import cycle at module load
    from app.domain.observations import VisionObservation


# Roles whose "speaking" is singing with the congregation.
SINGING_ROLES = ("vocalist", "congregation", "choir")

TRANSCRIPT_RECENT_LINES = 8


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TranscriptLine:
    speaker_role: str
    text: str
    timestamp: datetime
    confidence: float = 0.0
    t_start: Optional[float] = None
    t_end: Optional[float] = None
    is_singing: bool = False


@dataclass
class ServiceContext:
    """Rolling context window + current authoritative state."""

    plan: ServicePlan = field(default_factory=build_default_service_plan)
    state: ServiceState = ServiceState.PRE_SERVICE
    # Where the current state came from: llm | cue | easyworship | audio | operator | init
    state_source: str = "init"
    current_camera_role: Optional[str] = None
    current_atem_program: Optional[int] = None
    current_atem_preview: Optional[int] = None
    current_easyworship_item: Optional[str] = None
    current_easyworship_slide: Optional[int] = None
    current_easyworship_pres_no: Optional[int] = None
    # Cue-engine position mirrored into the plan (one element per cue), None = unknown.
    plan_index: Optional[int] = None
    transcript: Deque[TranscriptLine] = field(default_factory=lambda: deque(maxlen=30))
    last_actions: Deque[str] = field(default_factory=lambda: deque(maxlen=10))
    last_decision: Optional[dict] = None
    last_observation: Optional[AudioObservation] = None
    # Latest vision observation keyed by role (camera feeds) or input tag.
    vision: dict = field(default_factory=dict)
    # Typed context fields from the optional vision LLM (never actions).
    semantic: dict = field(default_factory=dict)
    # role -> {"speaking": bool, "since": float|None, "last_spoke_at": float|None,
    #          "is_singing": bool|None}  (wall-clock seconds, time.time()).
    speaking_roles: Dict[str, dict] = field(default_factory=dict)
    # Change tracking.
    version: int = 0
    updated_at: datetime = field(default_factory=_utcnow)
    # Monotonic timestamps (seconds) + wall-clock copies for display.
    state_since: float = field(default_factory=time.monotonic)
    state_since_wall: datetime = field(default_factory=_utcnow)
    last_cut_at: Optional[float] = None
    last_cut_at_wall: Optional[datetime] = None
    # Injectable clocks (tests).
    _mono: Callable[[], float] = field(default=time.monotonic, repr=False)
    _wall: Callable[[], float] = field(default=time.time, repr=False)

    # -- change tracking --------------------------------------------------------
    def _touch(self) -> None:
        self.version += 1
        self.updated_at = _utcnow()

    # -- audio ------------------------------------------------------------------
    def record_audio(self, observation: AudioObservation) -> None:
        """Record a VAD observation (no transcript) or a transcribed segment.

        Pure VAD observations (empty transcript) drive ``speaking_roles``;
        transcript observations only append a line (the segment has already
        ended when Whisper reports it, so they say nothing about "now").
        """
        self.last_observation = observation
        if not observation.transcript:
            self.set_speaking(observation.speaker_role, observation.speaking)
            return
        if observation.speaking:
            self.transcript.append(
                TranscriptLine(
                    speaker_role=observation.speaker_role,
                    text=observation.transcript,
                    timestamp=observation.timestamp,
                    confidence=float(getattr(observation, "confidence", 0.0) or 0.0),
                    t_start=getattr(observation, "t_start", None),
                    t_end=getattr(observation, "t_end", None),
                    is_singing=bool(getattr(observation, "is_singing", False)),
                )
            )
            entry = self.speaking_roles.get(observation.speaker_role)
            if entry is not None:
                entry["last_spoke_at"] = self._wall()
            self._touch()

    def annotate_transcript(
        self,
        role: str,
        text: str,
        *,
        confidence: Optional[float] = None,
        t_start: Optional[float] = None,
        t_end: Optional[float] = None,
        is_singing: Optional[bool] = None,
    ) -> bool:
        """Back-fill timing/singing on the newest transcript line matching role+text
        (the TRANSCRIPT bus event carries fields the AudioObservation does not)."""
        for line in reversed(self.transcript):
            if line.speaker_role == role and line.text == text:
                if confidence is not None:
                    line.confidence = float(confidence)
                if t_start is not None:
                    line.t_start = float(t_start)
                if t_end is not None:
                    line.t_end = float(t_end)
                if is_singing is not None:
                    line.is_singing = bool(is_singing)
                    entry = self.speaking_roles.get(role)
                    if entry is not None:
                        entry["is_singing"] = bool(is_singing)
                self._touch()
                return True
        return False

    def set_speaking(self, role: str, speaking: bool, at: Optional[float] = None) -> bool:
        """Update ``speaking_roles[role]``; bumps the version only on a change."""
        if not role:
            return False
        now = self._wall() if at is None else at
        entry = self.speaking_roles.get(role)
        if entry is None:
            self.speaking_roles[role] = {
                "speaking": bool(speaking),
                "since": now,
                "last_spoke_at": now if speaking else None,
                "is_singing": None,
            }
            self._touch()
            return True
        if entry["speaking"] == bool(speaking):
            if speaking:
                entry["last_spoke_at"] = now
            return False
        entry["speaking"] = bool(speaking)
        entry["since"] = now
        entry["last_spoke_at"] = now
        self._touch()
        return True

    def active_speakers(self) -> list:
        """Roles speaking now, most recently started first."""
        active = [(role, entry) for role, entry in self.speaking_roles.items() if entry.get("speaking")]
        active.sort(key=lambda item: item[1].get("since") or 0.0, reverse=True)
        return [role for role, _ in active]

    def current_speaker(self) -> Optional[str]:
        """Most recently started active role, else the role that spoke most recently."""
        active = self.active_speakers()
        if active:
            return active[0]
        spoke = [(role, entry.get("last_spoke_at")) for role, entry in self.speaking_roles.items()]
        spoke = [(role, at) for role, at in spoke if at is not None]
        if not spoke:
            return None
        spoke.sort(key=lambda item: item[1], reverse=True)
        return spoke[0][0]

    def speaking_for(self, role: str) -> float:
        """Seconds ``role`` has been continuously speaking (0 when silent/unknown)."""
        entry = self.speaking_roles.get(role)
        if not entry or not entry.get("speaking") or entry.get("since") is None:
            return 0.0
        return max(0.0, self._wall() - entry["since"])

    def silent_for(self, role: str) -> Optional[float]:
        """Seconds since ``role`` last spoke; None when it never spoke or is speaking."""
        entry = self.speaking_roles.get(role)
        if not entry or entry.get("speaking") or entry.get("last_spoke_at") is None:
            return None
        return max(0.0, self._wall() - entry["last_spoke_at"])

    # -- vision -------------------------------------------------------------------
    def record_vision(self, observation: "VisionObservation") -> None:
        """Store the latest vision observation, keyed by role or input tag."""
        key = observation.role or observation.input
        self.vision[key] = observation
        self._touch()

    def vision_snapshot(self) -> dict:
        """Compact vision block for the AI prompt / status endpoint."""
        program = self.vision.get("program")
        cameras = {
            key: {
                "person_present": obs.person_present,
                "person_in_roi": obs.person_in_roi,
                "frame_health": obs.frame_health,
            }
            for key, obs in self.vision.items()
            if key != "program"
        }
        return {
            "program_health": program.frame_health if program else None,
            "cameras": cameras,
            "semantic": dict(self.semantic),
        }

    # -- hardware state -----------------------------------------------------------
    def set_program(self, atem_input: Optional[int], camera_role: Optional[str] = None) -> bool:
        """Record the ATEM program input (+ the framing role now on air, when known).
        ``last_cut_at`` moves only when the program actually changes."""
        value = int(atem_input) if atem_input is not None else None
        changed = value != self.current_atem_program
        if changed:
            self.current_atem_program = value
            self.last_cut_at = self._mono()
            self.last_cut_at_wall = _utcnow()
        if camera_role is not None and camera_role != self.current_camera_role:
            self.current_camera_role = camera_role
            changed = True
        if changed:
            self._touch()
        return changed

    def set_preview(self, atem_input: Optional[int]) -> bool:
        value = int(atem_input) if atem_input is not None else None
        if value == self.current_atem_preview:
            return False
        self.current_atem_preview = value
        self._touch()
        return True

    def set_camera_role(self, role: Optional[str]) -> bool:
        if role == self.current_camera_role:
            return False
        self.current_camera_role = role
        self._touch()
        return True

    def set_easyworship_item(
        self,
        label: Optional[str],
        slide_no: Optional[int] = None,
        pres_no: Optional[int] = None,
    ) -> bool:
        """Record the live EasyWorship schedule item (service-plan label) and slide."""
        changed = False
        if label != self.current_easyworship_item:
            self.current_easyworship_item = label
            changed = True
        if slide_no != self.current_easyworship_slide:
            self.current_easyworship_slide = slide_no
            changed = True
        if pres_no != self.current_easyworship_pres_no:
            self.current_easyworship_pres_no = pres_no
            changed = True
        if changed:
            self._touch()
        return changed

    def set_plan_cursor(self, index: Optional[int]) -> bool:
        if index == self.plan_index:
            return False
        self.plan_index = index
        self._touch()
        return True

    # -- actions / state ------------------------------------------------------------
    def record_action(self, description: str) -> None:
        self.last_actions.append(description)
        self._touch()

    def set_state(self, state: ServiceState, source: str = "llm") -> bool:
        """Move the service state; ``state_since`` resets only on a real change."""
        if state == self.state:
            if source != self.state_source:
                self.state_source = source
            return False
        self.state = state
        self.state_source = source
        self.state_since = self._mono()
        self.state_since_wall = _utcnow()
        self._touch()
        return True

    def seconds_in_state(self) -> float:
        return max(0.0, self._mono() - self.state_since)

    def seconds_since_last_cut(self) -> Optional[float]:
        if self.last_cut_at is None:
            return None
        return max(0.0, self._mono() - self.last_cut_at)

    # -- plan cursor ----------------------------------------------------------------
    def resolve_plan_index(self) -> Optional[int]:
        """Best-known position in the plan: the cue engine's index when it has
        reported one, else the element whose EasyWorship item is live, else the
        first element in the current state."""
        elements = self.plan.elements
        if self.plan_index is not None and 0 <= self.plan_index < len(elements):
            return self.plan_index
        index = self.plan.index_of_easyworship_item(self.current_easyworship_item)
        if index is not None:
            return index
        return self.plan.first_index_of_state(self.state)

    def plan_cursor(self) -> dict:
        """``{index, current, next, total, source}`` -- element summaries for the prompt."""
        elements = self.plan.elements
        total = len(elements)
        index = self.resolve_plan_index()
        if index is None:
            return {
                "index": None,
                "current": None,
                "next": elements[0].summary() if elements else None,
                "total": total,
                "source": "none",
            }
        source = "cue" if self.plan_index is not None else (
            "easyworship" if self.plan.index_of_easyworship_item(self.current_easyworship_item) == index else "state"
        )
        nxt = elements[index + 1].summary() if index + 1 < total else None
        return {
            "index": index,
            "current": elements[index].summary(),
            "next": nxt,
            "total": total,
            "source": source,
        }

    # -- views --------------------------------------------------------------------
    def recent_transcript_text(self, limit: int = TRANSCRIPT_RECENT_LINES) -> str:
        lines = list(self.transcript)[-limit:]
        return "\n".join(f"{line.speaker_role}: {line.text}" for line in lines)

    def transcript_recent(self, limit: int = TRANSCRIPT_RECENT_LINES) -> list:
        now = _utcnow()
        out = []
        for line in list(self.transcript)[-limit:]:
            ts = line.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out.append(
                {
                    "role": line.speaker_role,
                    "text": line.text,
                    "age_seconds": round(max(0.0, (now - ts).total_seconds()), 1),
                    "confidence": round(float(line.confidence), 3),
                    "is_singing": bool(line.is_singing),
                }
            )
        return out

    def speaking_roles_snapshot(self) -> dict:
        return {role: dict(entry) for role, entry in self.speaking_roles.items()}

    def snapshot(self) -> dict:
        """A plain-dict view suitable for the AI prompt and API/WS responses."""
        active = self.active_speakers()
        since_cut = self.seconds_since_last_cut()
        return {
            "service_state": self.state.value,
            "service_state_source": self.state_source,
            "speaker": self.current_speaker(),
            "speaking": bool(active),
            "active_speakers": active,
            "speaking_roles": self.speaking_roles_snapshot(),
            "camera_role": self.current_camera_role,
            "atem_program": self.current_atem_program,
            "atem_preview": self.current_atem_preview,
            "easyworship_item": self.current_easyworship_item,
            "easyworship_slide": self.current_easyworship_slide,
            "seconds_in_state": round(self.seconds_in_state(), 1),
            "seconds_since_last_cut": None if since_cut is None else round(since_cut, 1),
            "plan_cursor": self.plan_cursor(),
            "recent_transcript": self.recent_transcript_text(),
            "transcript_recent": self.transcript_recent(),
            "last_actions": list(self.last_actions),
            "last_decision": self.last_decision,
            "previous_decision": self.last_decision,
            "vision": self.vision_snapshot(),
            "version": self.version,
            "updated_at": self.updated_at.isoformat(),
        }


# Module-level singleton — the application's one authoritative service context.
service_context = ServiceContext()
