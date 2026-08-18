"""Camera REST API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.cameras.service import CameraService, camera_service
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/cameras", tags=["Cameras"])


class MoveRequest(BaseModel):
    """Absolute pan/tilt (degrees) and/or zoom (percent)."""

    pan: Optional[float] = None
    tilt: Optional[float] = None
    zoom: Optional[float] = None


class DriveRequest(BaseModel):
    """Continuous (joystick) movement. Directions are -1, 0, or 1."""

    pan_dir: int = 0
    tilt_dir: int = 0
    zoom_dir: int = 0
    pan_speed: int = 12
    tilt_speed: int = 12
    zoom_speed: int = 4


async def get_camera_service() -> CameraService:
    """Get camera service (singleton)."""
    return camera_service


@router.get("/")
async def list_cameras(camera_service: CameraService = Depends(get_camera_service)):
    """List all registered cameras."""
    return {"cameras": camera_service.list_cameras()}


@router.get("/{camera_id}")
async def get_camera(
    camera_id: int,
    camera_service: CameraService = Depends(get_camera_service),
):
    """Get camera state."""
    try:
        state = await camera_service.get_camera_state(camera_id)
        return state
    except Exception as e:
        logger.error("Error getting camera state", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{camera_id}/move")
async def move_camera(
    camera_id: int,
    request: MoveRequest,
    camera_service: CameraService = Depends(get_camera_service),
):
    """Move camera to an absolute pan/tilt/zoom position."""
    try:
        success = await camera_service.move_camera(
            camera_id, pan=request.pan, tilt=request.tilt, zoom=request.zoom
        )
        if not success:
            raise HTTPException(status_code=500, detail="Failed to move camera")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error moving camera", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{camera_id}/drive")
async def drive_camera(
    camera_id: int,
    request: DriveRequest,
    camera_service: CameraService = Depends(get_camera_service),
):
    """Start continuous (joystick) movement."""
    try:
        success = await camera_service.drive_camera(
            camera_id,
            pan_dir=request.pan_dir,
            tilt_dir=request.tilt_dir,
            zoom_dir=request.zoom_dir,
            pan_speed=request.pan_speed,
            tilt_speed=request.tilt_speed,
            zoom_speed=request.zoom_speed,
        )
        if not success:
            raise HTTPException(status_code=500, detail="Failed to drive camera")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error driving camera", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{camera_id}/stop")
async def stop_camera(
    camera_id: int,
    camera_service: CameraService = Depends(get_camera_service),
):
    """Stop all camera motion."""
    try:
        success = await camera_service.stop_camera(camera_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to stop camera")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error stopping camera", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{camera_id}/preset/{preset_id}")
async def move_to_preset(
    camera_id: int,
    preset_id: int,
    camera_service: CameraService = Depends(get_camera_service),
):
    """Move camera to preset."""
    try:
        success = await camera_service.move_to_preset(camera_id, preset_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to move camera")
        return {"ok": True, "message": f"Moved camera {camera_id} to preset {preset_id}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error moving camera", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{camera_id}/preset/{preset_id}/save")
async def save_preset(
    camera_id: int,
    preset_id: int,
    camera_service: CameraService = Depends(get_camera_service),
):
    """Save the camera's current position as a preset."""
    try:
        success = await camera_service.save_preset(camera_id, preset_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save preset")
        return {"ok": True, "message": f"Saved preset {preset_id} on camera {camera_id}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error saving preset", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
