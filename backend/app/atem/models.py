"""ATEM state models."""

from datetime import datetime
from typing import List

from pydantic import BaseModel


class AtemInputModel(BaseModel):
    """Model for an ATEM input/camera."""
    
    id: int
    name: str
    short_name: str
    type: str  # "HDMI", "Component", "Composite", etc.
    connected: bool
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": 0,
                "name": "Pastor",
                "short_name": "Pas",
                "type": "HDMI",
                "connected": True,
            }
        }


class AtemAudioChannelModel(BaseModel):
    """A mic/audio input channel on the ATEM."""

    id: int
    name: str
    muted: bool

    class Config:
        json_schema_extra = {"example": {"id": 1, "name": "Mic 1", "muted": False}}


class AtemStateModel(BaseModel):
    """Current state of the ATEM."""
    
    connected: bool
    program_input: int
    preview_input: int
    streaming: bool
    recording: bool
    inputs: List[AtemInputModel]
    audio_channels: List[AtemAudioChannelModel] = []
    transition_in_progress: bool
    timestamp: datetime
    
    class Config:
        json_schema_extra = {
            "example": {
                "connected": True,
                "program_input": 0,
                "preview_input": 1,
                "streaming": False,
                "recording": False,
                "inputs": [
                    {
                        "id": 0,
                        "name": "Pastor",
                        "short_name": "Pas",
                        "type": "HDMI",
                        "connected": True,
                    }
                ],
                "transition_in_progress": False,
                "timestamp": "2026-08-12T10:30:00Z",
            }
        }


class TransitionRequestModel(BaseModel):
    """Request for a transition."""
    
    input_id: int
    
    class Config:
        json_schema_extra = {"example": {"input_id": 2}}


class CameraSelectRequestModel(BaseModel):
    """Request to select a camera."""
    
    input_id: int
    
    class Config:
        json_schema_extra = {"example": {"input_id": 1}}


class MicMuteRequestModel(BaseModel):
    """Request to mute/unmute a mic channel."""

    muted: bool

    class Config:
        json_schema_extra = {"example": {"muted": True}}
