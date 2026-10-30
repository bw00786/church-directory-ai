"""Structured decision models returned by the AI Service Director.

Validation is strict on the fields the runtime relies on: ``decision`` is a
closed vocabulary, confidences are clamped to [0, 1] by schema (not silently
coerced), and every action carries its own confidence/reason (falling back to
the decision-level values) so the runtime can gate actions individually.
Unknown action types are rejected with a readable message instead of reaching
the action engine.
"""

from functools import lru_cache
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

DecisionKind = Literal["continue", "transition", "deviate"]

CAMERA_ROLES = ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide")


@lru_cache(maxsize=1)
def known_action_types() -> Tuple[str, ...]:
    """DirectorActionType values. Imported lazily: app.director pulls in the
    cue engine and hardware services, which this reasoning-only package must
    not load at import time."""
    from app.director.action_models import DirectorActionType

    return tuple(t.value for t in DirectorActionType)


class DirectorActionSpec(BaseModel):
    """One action the AI proposes; validated/executed by the policy-gated
    action engine (app.director.action_engine), never executed directly."""

    type: str  # matches app.director.action_models.DirectorActionType values
    camera_role: Optional[str] = None
    preset_id: Optional[int] = None
    atem_input: Optional[int] = None
    easyworship_item: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    # Per-action confidence/reason. None falls back to the decision-level values
    # (DirectorDecision fills them in, so a validated decision always has both).
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason: Optional[str] = None

    @field_validator("type", mode="before")
    @classmethod
    def _known_action_type(cls, value: Any) -> str:
        text = str(value or "").strip().upper()
        allowed = known_action_types()
        if text not in allowed:
            raise ValueError(
                f"unknown action type {value!r}; expected one of {', '.join(allowed)}"
            )
        return text

    @field_validator("camera_role", mode="before")
    @classmethod
    def _normalize_role(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip().lower()
        return text or None


class DirectorDecision(BaseModel):
    """Structured output from AIServiceDirector.decide()."""

    decision: DecisionKind = "continue"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    service_state: Optional[str] = None  # new ServiceState, if transitioning
    actions: List[DirectorActionSpec] = Field(default_factory=list)
    # ServiceContext.version the decision was computed from; the runtime drops
    # decisions whose context has moved on (stale-snapshot guard).
    context_version: Optional[int] = None
    latency_ms: Optional[int] = None
    cycle_id: Optional[str] = None

    @field_validator("decision", mode="before")
    @classmethod
    def _normalize_decision(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("service_state", mode="before")
    @classmethod
    def _normalize_state(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip().lower()
        return None if text in ("", "null", "none", "unchanged") else text

    @model_validator(mode="after")
    def _fill_action_confidence(self) -> "DirectorDecision":
        for action in self.actions:
            if action.confidence is None:
                action.confidence = self.confidence
            if not action.reason:
                action.reason = self.reason
        return self


@lru_cache(maxsize=1)
def director_decision_json_schema() -> Dict[str, Any]:
    """Ollama structured-output schema (format=<schema>). Hand-written and flat
    (no $defs/$ref) so llama.cpp's grammar conversion handles it; pydantic
    above remains the authority -- the schema only steers generation."""
    return {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["continue", "transition", "deviate"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
            "service_state": {"type": ["string", "null"]},
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": list(known_action_types())},
                        "camera_role": {"type": ["string", "null"]},
                        "atem_input": {"type": ["integer", "null"]},
                        "easyworship_item": {"type": ["string", "null"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["type", "confidence", "reason"],
                },
            },
        },
        "required": ["decision", "confidence", "reason", "actions"],
    }
