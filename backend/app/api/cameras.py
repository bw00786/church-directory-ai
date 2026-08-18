"""Camera REST API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.cameras.service import CameraService, camera_service
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/cameras", tags=["Cameras"])


async def get_camera_service() -> CameraService:
    """Get camera service (singleton)."""
    return camera_service


@router.get("/")
async def list_cameras(camera_service: CameraService = Depends(get_camera_service)):
    """List all cameras."""
    return {"cameras": []}


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
    except Exception as e:
        logger.error("Error moving camera", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
