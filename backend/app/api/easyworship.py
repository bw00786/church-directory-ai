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


@router.post("/item/{label}")
async def select_item(label: str):
    """Go live on the service-plan item with this EasyWorship label."""
    ok = await easyworship_service.select_item(label)
    if not ok:
        raise HTTPException(status_code=502, detail="EasyWorship item selection failed")
    return {"ok": True, "item": label}


@router.post("/slide/{number}")
async def goto_slide(number: int):
    """Jump to slide `number` (1-based) in the live item. Remote protocol only."""
    if number < 1:
        raise HTTPException(status_code=400, detail="slide number must be >= 1")
    ok = await easyworship_service.goto_slide(number)
    if not ok:
        raise HTTPException(status_code=502, detail="EasyWorship slide jump failed or unsupported")
    return {"ok": True, "slide": number}
