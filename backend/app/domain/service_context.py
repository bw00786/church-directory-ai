"""Authoritative, application-owned service context (short-term memory).

The AI Director is stateless between calls — this class is the "Service
Context Memory" from docs/ai-director.md: it owns the current ServiceState,
the last N transcript lines, and the last known hardware state (camera role,
ATEM program, EasyWorship item), and supplies all of it to the AI as context.
Do not rely on the LLM's own conversational memory.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Optional

from app.domain.observations import AudioObservation
from app.domain.service_plan import ServicePlan, build_default_service_plan
from app.domain.service_state import ServiceState

if False:  # typing only; avoid a hard import cycle at module load
    from app.domain.observations import VisionObservation


@dataclass
class TranscriptLine:
    speaker_role: str
    text: str
    timestamp: datetime


@dataclass
class ServiceContext:
    """Rolling context window + current authoritative state."""

    plan: ServicePlan = field(default_factory=build_default_service_plan)
    state: ServiceState = ServiceState.PRE_SERVICE
    current_camera_role: Optional[str] = None
    current_atem_program: Optional[int] = None
    current_easyworship_item: Optional[str] = None
    transcript: Deque[TranscriptLine] = field(default_factory=lambda: deque(maxlen=30))
    last_actions: Deque[str] = field(default_factory=lambda: deque(maxlen=10))
    last_decision: Optional[dict] = None
    last_observation: Optional[AudioObservation] = None
    # Latest vision observation keyed by role (camera feeds) or input tag.
    vision: dict = field(default_factory=dict)
    # Typed context fields from the optional Claude-vision tier (never actions).
    semantic: dict = field(default_factory=dict)

    def record_audio(self, observation: AudioObservation) -> None:
        self.last_observation = observation
        if observation.speaking and observation.transcript:
            self.transcript.append(
                TranscriptLine(
                    speaker_role=observation.speaker_role,
                    text=observation.transcript,
                    timestamp=observation.timestamp,
                )
            )

    def record_vision(self, observation: "VisionObservation") -> None:
        """Store the latest vision observation, keyed by role or input tag."""
        key = observation.role or observation.input
        self.vision[key] = observation

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

    def record_action(self, description: str) -> None:
        self.last_actions.append(description)

    def set_state(self, state: ServiceState) -> None:
        self.state = state

    def recent_transcript_text(self, limit: int = 8) -> str:
        lines = list(self.transcript)[-limit:]
        return "\n".join(f"{line.speaker_role}: {line.text}" for line in lines)

    def snapshot(self) -> dict:
        """A plain-dict view suitable for the AI prompt and API/WS responses."""
        return {
            "service_state": self.state.value,
            "speaker": self.last_observation.speaker_role if self.last_observation else None,
            "speaking": self.last_observation.speaking if self.last_observation else False,
            "camera_role": self.current_camera_role,
            "atem_program": self.current_atem_program,
            "easyworship_item": self.current_easyworship_item,
            "recent_transcript": self.recent_transcript_text(),
            "last_actions": list(self.last_actions),
            "last_decision": self.last_decision,
            "vision": self.vision_snapshot(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


# Module-level singleton — the application's one authoritative service context.
service_context = ServiceContext()
