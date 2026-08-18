"""Permission models for policy engine."""

from dataclasses import dataclass
from enum import Enum


class Permission(str, Enum):
    """Available permissions."""
    
    # Camera control
    SWITCH_CAMERA = "switch_camera"
    PREVIEW_CAMERA = "preview_camera"
    
    # Transitions
    PERFORM_CUT = "perform_cut"
    PERFORM_AUTO = "perform_auto"
    
    # Streaming
    START_STREAM = "start_stream"
    STOP_STREAM = "stop_stream"
    
    # Recording
    START_RECORDING = "start_recording"
    STOP_RECORDING = "stop_recording"
    
    # Configuration
    MODIFY_PRESETS = "modify_presets"
    MODIFY_SETTINGS = "modify_settings"


@dataclass
class ActionConstraints:
    """Constraints on an action."""
    
    # Minimum time between repeated actions (seconds)
    min_interval: float = 0.0
    
    # Maximum number of consecutive identical actions
    max_consecutive: int = 999
    
    # Minimum confidence required for AI to perform action
    min_confidence: float = 0.0
    
    # Whether this action is currently permitted
    permitted: bool = True
