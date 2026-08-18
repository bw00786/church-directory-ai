"""Production state for LangGraph."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List


@dataclass
class ProductionState:
    """State of the current production/service."""
    
    # Service identification
    service_id: str = ""
    service_date: datetime = field(default_factory=datetime.now)
    
    # ATEM state
    program_input: int = 0
    preview_input: int = 1
    
    # Streaming/Recording
    streaming: bool = False
    recording: bool = False
    
    # AI observation
    active_event: Optional[str] = None
    event_confidence: float = 0.0
    
    # Camera decision
    candidate_camera: Optional[int] = None
    selected_camera: Optional[int] = None
    
    # Action history
    last_action: Optional[str] = None
    last_action_result: Optional[str] = None
    last_action_time: Optional[datetime] = None
    
    # Mode control
    autonomous_mode: bool = False
    
    # Available inputs
    available_inputs: List[dict] = field(default_factory=list)
