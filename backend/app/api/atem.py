"""ATEM REST API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.atem.models import MicMuteRequestModel, TransitionRequestModel
from app.atem.service import AtemService
from app.dependencies import get_atem_service
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/atem", tags=["ATEM"])


@router.get("/status")
async def get_status(atem: AtemService = Depends(get_atem_service)):
    """Get current ATEM state."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        return await atem.get_state()
    except Exception as e:
        logger.error("Error getting ATEM status", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connect")
async def connect(atem: AtemService = Depends(get_atem_service)):
    """Connect to ATEM."""
    try:
        connected = await atem.connect()
        if not connected:
            raise HTTPException(status_code=503, detail="Failed to connect to ATEM")
        return {"ok": True, "message": "Connected to ATEM"}
    except Exception as e:
        logger.error("Error connecting to ATEM", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disconnect")
async def disconnect(atem: AtemService = Depends(get_atem_service)):
    """Disconnect from ATEM."""
    try:
        await atem.disconnect()
        return {"ok": True, "message": "Disconnected from ATEM"}
    except Exception as e:
        logger.error("Error disconnecting from ATEM", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/program")
async def set_program(
    request: TransitionRequestModel,
    atem: AtemService = Depends(get_atem_service),
):
    """Switch program input."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        state = await atem.set_program(request.input_id)
        return {
            "ok": True,
            "message": f"Program switched to input {request.input_id}",
            "state": state,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error setting program", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview")
async def set_preview(
    request: TransitionRequestModel,
    atem: AtemService = Depends(get_atem_service),
):
    """Switch preview input."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        state = await atem.set_preview(request.input_id)
        return {
            "ok": True,
            "message": f"Preview switched to input {request.input_id}",
            "state": state,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error setting preview", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cut")
async def cut(atem: AtemService = Depends(get_atem_service)):
    """Perform CUT transition."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        state = await atem.cut()
        return {
            "ok": True,
            "message": "Cut transition performed",
            "state": state,
        }
    except Exception as e:
        logger.error("Error performing cut", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auto")
async def auto(atem: AtemService = Depends(get_atem_service)):
    """Perform AUTO transition."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        state = await atem.auto()
        return {
            "ok": True,
            "message": "Auto transition performed",
            "state": state,
        }
    except Exception as e:
        logger.error("Error performing auto", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream/start")
async def start_stream(atem: AtemService = Depends(get_atem_service)):
    """Start streaming (go \"on air\")."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        ok = await atem.start_stream()
        return {"ok": ok, "state": await atem.get_state()}
    except Exception as e:
        logger.error("Error starting stream", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream/stop")
async def stop_stream(atem: AtemService = Depends(get_atem_service)):
    """Stop streaming (go \"off air\")."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        ok = await atem.stop_stream()
        return {"ok": ok, "state": await atem.get_state()}
    except Exception as e:
        logger.error("Error stopping stream", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/record/start")
async def start_recording(atem: AtemService = Depends(get_atem_service)):
    """Start recording."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        ok = await atem.start_recording()
        return {"ok": ok, "state": await atem.get_state()}
    except Exception as e:
        logger.error("Error starting recording", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/record/stop")
async def stop_recording(atem: AtemService = Depends(get_atem_service)):
    """Stop recording."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        ok = await atem.stop_recording()
        return {"ok": ok, "state": await atem.get_state()}
    except Exception as e:
        logger.error("Error stopping recording", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mic/{mic_id}/mute")
async def set_mic_muted(
    mic_id: int,
    request: MicMuteRequestModel,
    atem: AtemService = Depends(get_atem_service),
):
    """Mute/unmute a mic channel (e.g. mic_id=1 for Mic 1, 2 for Mic 2)."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        state = await atem.set_mic_muted(mic_id, request.muted)
        return {"ok": True, "state": state}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error setting mic mute", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
