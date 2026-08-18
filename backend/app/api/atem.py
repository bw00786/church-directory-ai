"""ATEM REST API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

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
    input_id: int,
    atem: AtemService = Depends(get_atem_service),
):
    """Switch program input."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        state = await atem.set_program(input_id)
        return {
            "ok": True,
            "message": f"Program switched to input {input_id}",
            "state": state,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error setting program", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview")
async def set_preview(
    input_id: int,
    atem: AtemService = Depends(get_atem_service),
):
    """Switch preview input."""
    try:
        if not await atem.is_connected():
            raise HTTPException(status_code=503, detail="ATEM not connected")
        state = await atem.set_preview(input_id)
        return {
            "ok": True,
            "message": f"Preview switched to input {input_id}",
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
