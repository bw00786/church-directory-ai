"""ATEM event definitions."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class AtemEventType(str, Enum):
    """Types of ATEM events."""
    
    CONNECTED = "atem_connected"
    DISCONNECTED = "atem_disconnected"
    PROGRAM_CHANGED = "program_changed"
    PREVIEW_CHANGED = "preview_changed"
    TRANSITION_STARTED = "transition_started"
    TRANSITION_COMPLETED = "transition_completed"
    STREAM_STARTED = "stream_started"
    STREAM_STOPPED = "stream_stopped"
    RECORDING_STARTED = "recording_started"
    RECORDING_STOPPED = "recording_stopped"
    INPUT_CONNECTED = "input_connected"
    INPUT_DISCONNECTED = "input_disconnected"


class AtemEvent(BaseModel):
    """An ATEM state change event."""
    
    type: AtemEventType
    timestamp: datetime
    payload: dict[str, Any] = {}
    
    class Config:
        json_schema_extra = {
            "example": {
                "type": "program_changed",
                "timestamp": "2026-08-12T10:30:00Z",
                "payload": {"program_input": 1, "previous_input": 0},
            }
        }
