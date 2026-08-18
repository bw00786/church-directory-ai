"""Camera models and abstractions."""

from typing import List, Optional

from pydantic import BaseModel


class CameraPresetModel(BaseModel):
    """A preset position for a PTZ camera."""
    
    id: int
    name: str
    camera_id: int
    pan: float  # 0-360 degrees
    tilt: float  # -90 to 90 degrees
    zoom: float  # 0-100%
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": 1,
                "name": "Pastor Close",
                "camera_id": 0,
                "pan": 0.0,
                "tilt": 5.0,
                "zoom": 75.0,
            }
        }


class CameraStateModel(BaseModel):
    """Current state of a PTZ camera."""
    
    camera_id: int
    name: str
    connected: bool
    pan: float
    tilt: float
    zoom: float
    
    class Config:
        json_schema_extra = {
            "example": {
                "camera_id": 0,
                "name": "PTZ Camera 1",
                "connected": True,
                "pan": 0.0,
                "tilt": 5.0,
                "zoom": 50.0,
            }
        }


class CameraMoveRequestModel(BaseModel):
    """Request to move a PTZ camera."""
    
    camera_id: int
    pan: Optional[float] = None
    tilt: Optional[float] = None
    zoom: Optional[float] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "camera_id": 0,
                "pan": 10.0,
                "tilt": 5.0,
                "zoom": 75.0,
            }
        }


class PresetSelectRequestModel(BaseModel):
    """Request to move camera to a preset."""
    
    camera_id: int
    preset_id: int
    
    class Config:
        json_schema_extra = {
            "example": {
                "camera_id": 0,
                "preset_id": 1,
            }
        }
