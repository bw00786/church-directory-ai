"""EasyWorship slide-control REST API."""

from fastapi import APIRouter, HTTPException

from app.easyworship.service import ACTIONS, easyworship_service
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/easyworship", tags=["EasyWorship"])


@router.get("/status")
async def status():
    """Connection state and last action."""
    return easyworship_service.status()


@router.post("/action/{name}")
async def action(name: str):
    """Perform a named action (next_slide, prev_slide, next_item, prev_item, clear, logo, black, live)."""
    if name not in ACTIONS:
        raise HTTPException(status_code=400, detail=f"unknown action: {name}")
    ok = await easyworship_service.action(name)
    if not ok:
        raise HTTPException(status_code=502, detail="EasyWorship command failed")
    return {"ok": True, "action": name}


@router.post("/next")
async def next_slide():
    """Advance to the next slide."""
    ok = await easyworship_service.next_slide()
    if not ok:
        raise HTTPException(status_code=502, detail="EasyWorship command failed")
    return {"ok": True}


@router.post("/previous")
async def previous_slide():
    """Go to the previous slide."""
    ok = await easyworship_service.previous_slide()
    if not ok:
        raise HTTPException(status_code=502, detail="EasyWorship command failed")
    return {"ok": True}
