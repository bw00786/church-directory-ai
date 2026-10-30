"""ATEM event definitions.

Two layers exist:

* :class:`AtemEventType` / :class:`AtemEvent` -- typed REST/WebSocket models.
* ``BUS_EVENTS`` -- the raw messages ``AtemService`` publishes on
  ``app.events.bus.event_bus`` (``{"event": <name>, "payload": {...}}``) when
  a poll of the bridge shows a change. The context-sync layer and the operator
  shot recorder consume these.
"""

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


# Bus events emitted by ``app.atem.service.AtemService`` (see docs/atem.md).
BUS_EVENTS: dict[str, dict[str, Any]] = {
    "PROGRAM_CHANGED": {
        "program_input": "int -- new program input id",
        "previous_input": "int -- program input before the change",
        "preview_input": "int -- preview at the time of the poll",
        "transition_in_progress": "bool",
        "source": "'poll' -- detected by comparing consecutive /status polls",
        "commanded": "bool -- True when this service issued the matching command",
        "mock": "bool",
        "timestamp": "ISO-8601",
    },
    "PREVIEW_CHANGED": {
        "preview_input": "int -- new preview input id",
        "previous_input": "int",
        "program_input": "int",
        "transition_in_progress": "bool",
        "source": "'poll'",
        "commanded": "bool",
        "mock": "bool",
        "timestamp": "ISO-8601",
    },
    "ATEM_CONNECTION_CHANGED": {
        "connected": "bool",
        "previous": "bool",
        "atem_ip": "str",
        "mock": "bool",
        "source": "'connect' | 'poll' | 'monitor' | 'disconnect' | 'error'",
        "timestamp": "ISO-8601",
    },
}


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
