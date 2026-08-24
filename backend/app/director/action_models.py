"""Typed action model for the AI Service Director's action engine.

Distinct from app.director.models.ActionType (the cue-engine's per-cue
actions) — this is the broader, AI-proposed action vocabulary from
docs/ai-director.md.
"""

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class DirectorActionType(str, Enum):
    ATEM_CUT = "ATEM_CUT"
    ATEM_AUTO = "ATEM_AUTO"
    ATEM_SET_PROGRAM = "ATEM_SET_PROGRAM"
    ATEM_SET_PREVIEW = "ATEM_SET_PREVIEW"
    PTZ_SELECT_ROLE = "PTZ_SELECT_ROLE"
    PTZ_PRESET = "PTZ_PRESET"
    EASYWORSHIP_NEXT = "EASYWORSHIP_NEXT"
    EASYWORSHIP_PREVIOUS = "EASYWORSHIP_PREVIOUS"
    EASYWORSHIP_SELECT = "EASYWORSHIP_SELECT"
    SERVICE_STATE_CHANGE = "SERVICE_STATE_CHANGE"


# Action types gated by the "camera_change" confidence threshold.
CAMERA_ACTION_TYPES = {DirectorActionType.PTZ_SELECT_ROLE, DirectorActionType.PTZ_PRESET}
# Action types gated by the "slide_change" confidence threshold.
SLIDE_ACTION_TYPES = {
    DirectorActionType.EASYWORSHIP_NEXT,
    DirectorActionType.EASYWORSHIP_PREVIOUS,
    DirectorActionType.EASYWORSHIP_SELECT,
}
# Action types gated by the "atem_transition" confidence threshold.
ATEM_ACTION_TYPES = {
    DirectorActionType.ATEM_CUT,
    DirectorActionType.ATEM_AUTO,
    DirectorActionType.ATEM_SET_PROGRAM,
    DirectorActionType.ATEM_SET_PREVIEW,
}


class DirectorAction(BaseModel):
    """A single typed action proposed by the AI Director."""

    type: DirectorActionType
    target: Optional[str] = None  # e.g. camera role, EasyWorship item label
    parameters: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""


class ActionResult(BaseModel):
    action: DirectorAction
    approved: bool
    executed: bool
    detail: str = ""
