"""Data models for the scripted Sunday service."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class ActionType(str, Enum):
    """Type of action performed when a cue is entered."""

    ATEM_PROGRAM = "atem_program"   # switch ATEM program to an input
    PTZ_PRESET = "ptz_preset"       # recall a PTZOptics preset
    NOTE = "note"                   # advisory note for the operator (e.g. mixer)


class AdvanceTrigger(str, Enum):
    """How the director advances from a cue to the next one."""

    MANUAL = "manual"             # operator presses Next
    TIMER = "timer"               # after ``timer_seconds``
    SONG_END = "song_end"         # mixer channels go silent (song finished)
    AI = "ai"                     # LLM/vision layer decides (with manual fallback)


class CueAction(BaseModel):
    """A single action executed on cue entry."""

    type: ActionType
    atem_input: Optional[int] = None
    camera_id: Optional[int] = None
    preset_id: Optional[int] = None
    note: Optional[str] = None
    description: str = ""


class Cue(BaseModel):
    """One step of the service."""

    id: str
    name: str
    description: str = ""
    actions: List[CueAction] = []
    advance: AdvanceTrigger = AdvanceTrigger.MANUAL
    timer_seconds: Optional[int] = None
    channels: List[int] = []  # mixer channels watched for SONG_END
    ai_enabled: bool = False  # LLM/vision layer may auto-advance this cue
    exit_hint: Optional[str] = None  # natural-language advance condition for the AI


class ServiceScript(BaseModel):
    """An ordered list of cues for a service."""

    name: str
    cues: List[Cue]


class DirectorStatus(BaseModel):
    """Current director state for API/WebSocket responses."""

    running: bool
    autonomous: bool
    script_name: str
    cue_index: int
    total_cues: int
    current_cue: Optional[Cue] = None
    next_cue: Optional[Cue] = None
    pending_suggestion: Optional[dict] = None
