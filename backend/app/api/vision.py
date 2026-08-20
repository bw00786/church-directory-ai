"""Vision API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_vision_manager

router = APIRouter(prefix="/api/vision", tags=["Vision"])


@router.get("/status")
async def get_vision_status(vision_manager=Depends(get_vision_manager)):
    return vision_manager.get_status()


@router.get("/cameras")
async def get_vision_cameras(vision_manager=Depends(get_vision_manager)):
    return {"cameras": vision_manager.get_camera_quality()}


@router.get("/events")
async def get_vision_events(vision_manager=Depends(get_vision_manager)):
    return {"events": vision_manager.get_events()}


@router.get("/observations")
async def get_vision_observations(vision_manager=Depends(get_vision_manager)):
    return {
        "observations": [
            {
                "camera_id": event.camera_id,
                "type": event.event_type,
                "confidence": event.confidence,
                "payload": event.payload,
                "timestamp": event.timestamp,
            }
            for event in vision_manager.events[-50:]
        ]
    }


@router.get("/cameras/{camera_id}")
async def get_camera_detail(camera_id: int, vision_manager=Depends(get_vision_manager)):
    camera_quality = [q for q in vision_manager.get_camera_quality() if q["camera_id"] == camera_id]
    if not camera_quality:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera_quality[0]


@router.get("/cameras/{camera_id}/tracks")
async def get_camera_tracks(camera_id: int, vision_manager=Depends(get_vision_manager)):
    """Live detections (bounding boxes + any identity matches) for the
    frontend's detection overlay -- empty lists when vision isn't running
    or nothing's been detected yet."""
    return vision_manager.get_camera_tracks(camera_id)


@router.get("/recommendations")
async def get_vision_recommendations(vision_manager=Depends(get_vision_manager)):
    return {"recommendations": vision_manager.get_recommendations()}


@router.get("/policy-decisions")
async def get_vision_policy_decisions(vision_manager=Depends(get_vision_manager)):
    return {"policy_decisions": vision_manager.get_policy_decisions()}


@router.post("/start")
async def start_vision(vision_manager=Depends(get_vision_manager)):
    await vision_manager.start()
    return {"status": "started"}


@router.post("/stop")
async def stop_vision(vision_manager=Depends(get_vision_manager)):
    await vision_manager.stop()
    return {"status": "stopped"}
