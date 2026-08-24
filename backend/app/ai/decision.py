"""Structured decision models returned by the AI Service Director."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DirectorActionSpec(BaseModel):
    """One action the AI proposes; validated/executed by the policy-gated
    action engine (app.director.action_engine), never executed directly."""

    type: str  # matches app.director.action_models.DirectorActionType values
    camera_role: Optional[str] = None
    preset_id: Optional[int] = None
    atem_input: Optional[int] = None
    easyworship_item: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)


class DirectorDecision(BaseModel):
    """Structured output from AIServiceDirector.decide()."""

    decision: str  # "continue" | "transition" | "deviate"
    confidence: float = 0.0
    reason: str = ""
    service_state: Optional[str] = None  # new ServiceState, if transitioning
    actions: List[DirectorActionSpec] = Field(default_factory=list)
