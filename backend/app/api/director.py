"""Service director REST API."""

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.director.ai_director_runtime import ai_director_runtime
from app.director.engine import service_director
from app.director.order_document import (
    MAX_UPLOAD_BYTES,
    DocumentError,
    UnsupportedDocumentType,
    build_script_from_document,
    extract_text,
)
from app.director.order_of_service import build_script_from_order
from app.director.service_history import service_history
from app.director.scheduler import service_scheduler
from app.domain.service_context import service_context
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/director", tags=["Director"])


class StartRequest(BaseModel):
    autonomous: bool = True


class ScheduleRequest(BaseModel):
    enabled: Optional[bool] = None
    time: Optional[str] = None
    days: Optional[str] = None
    autonomous: Optional[bool] = None


class SuggestRequest(BaseModel):
    source: str = "external"
    reason: str
    confidence: float = 1.0
    cue_id: Optional[str] = None


class ObserveRequest(BaseModel):
    text: str


class OrderOfServiceRequest(BaseModel):
    text: str


class AiModeRequest(BaseModel):
    mode: str  # "manual" | "assisted" | "ai_directed"


@router.get("/status")
async def get_status():
    """Current director status (running state and current/next cue)."""
    return service_director.status().model_dump()


@router.get("/script")
async def get_script():
    """The loaded service cue sheet."""
    return service_director.script.model_dump()


@router.post("/script/order")
async def load_order_of_service(request: OrderOfServiceRequest):
    """Replace the cue sheet with one built from the pastor's order of service text."""
    if service_director.status().running:
        raise HTTPException(status_code=409, detail="Stop the service before loading a new script")
    try:
        script, order = build_script_from_order(request.text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    service_director.load_script(script)
    await service_director.broadcast_status()
    saved = service_history.record_order(script, order, "rules", request.text)
    return _script_summary(script, order, "rules", saved)


@router.get("/script/history")
async def list_order_history(limit: int = 50):
    """Previously uploaded orders of service, newest first."""
    return {"orders": service_history.list_orders(limit=max(1, min(limit, 200)))}


@router.get("/script/history/{order_id}")
async def get_order_history(order_id: str):
    """One saved order of service including its original text."""
    try:
        order = service_history.get_order(order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Order not found")
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/script/upload")
async def upload_order_of_service(
    file: UploadFile = File(...),
    parser: str = Form("auto"),
):
    """Replace the cue sheet from an uploaded order-of-service .docx/.txt (rules, then AI fallback)."""
    if parser not in ("auto", "rules", "ai"):
        raise HTTPException(status_code=422, detail="parser must be auto, rules or ai")
    if service_director.status().running:
        raise HTTPException(status_code=409, detail="Stop the service before loading a new script")

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (5 MB maximum)")
    try:
        text = extract_text(file.filename or "", data)
        script, order, source = await build_script_from_document(text, parser)
    except UnsupportedDocumentType as e:
        raise HTTPException(status_code=415, detail=str(e))
    except DocumentError as e:
        raise HTTPException(status_code=422, detail=str(e))

    service_director.load_script(script)
    await service_director.broadcast_status()
    saved = service_history.record_order(script, order, source, text, filename=file.filename)
    return _script_summary(script, order, source, saved)


def _script_summary(script, order, source: str, saved: dict | None = None) -> dict:
    return {
        "script_name": script.name,
        "source": source,
        "date": order.date,
        "theme": order.theme,
        "speaker": order.speaker,
        "items": [{"heading": i.heading, "cue_ids": i.cue_ids} for i in order.items],
        "cues": [{"id": c.id, "name": c.name} for c in script.cues],
        "history_id": saved["id"] if saved else None,
        "plan": service_context.plan.model_dump(),
    }


@router.post("/start")
async def start(request: StartRequest):
    """Start the service from the first cue."""
    status = await service_director.start(autonomous=request.autonomous)
    return status.model_dump()


@router.post("/stop")
async def stop():
    """Stop the service."""
    status = await service_director.stop()
    return status.model_dump()


@router.post("/next")
async def next_cue():
    """Advance to the next cue (manual)."""
    status = await service_director.next()
    return status.model_dump()


@router.post("/goto/{index}")
async def goto(index: int):
    """Jump to a specific cue index."""
    try:
        status = await service_director.goto(index)
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return status.model_dump()


@router.get("/schedule")
async def get_schedule():
    """Current auto-start schedule."""
    return service_scheduler.info()


@router.post("/schedule")
async def set_schedule(request: ScheduleRequest):
    """Update the auto-start schedule."""
    service_scheduler.configure(
        enabled=request.enabled,
        time=request.time,
        days=request.days,
        autonomous=request.autonomous,
    )
    return service_scheduler.info()


@router.post("/suggest")
async def suggest(request: SuggestRequest):
    """Feed an advance suggestion from the LLM/vision layer."""
    return await service_director.request_advance(
        source=request.source,
        reason=request.reason,
        confidence=request.confidence,
        cue_id=request.cue_id,
    )


@router.post("/observe")
async def observe(request: ObserveRequest):
    """Let the AI evaluate an observation and decide whether to advance."""
    from app.agents.director_ai import director_ai

    return await director_ai.observe(request.text)


# -- AI Service Director (reasoning layer above the cue engine) --------------


@router.get("/ai/status")
async def ai_status():
    """Current AI Director mode, service context snapshot, and pending actions."""
    from app.audio.audio_observer import audio_observer
    from app.vision.perception import perception_loop

    return {
        "mode": ai_director_runtime.mode,
        "context": service_context.snapshot(),
        "pending_actions": [a.model_dump(mode="json") for a in ai_director_runtime.pending_actions],
        "perception": audio_observer.perception_status(),
        "vision": perception_loop.status(),
    }


@router.get("/ai/mode")
async def get_ai_mode():
    """Current AI Director operating mode."""
    return {"mode": ai_director_runtime.mode}


@router.post("/ai/mode")
async def set_ai_mode(request: AiModeRequest):
    """Set the AI Director operating mode (manual | assisted | ai_directed)."""
    try:
        ai_director_runtime.set_mode(request.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"mode": ai_director_runtime.mode}


@router.post("/ai/tick")
async def ai_tick():
    """Manually trigger one AI Director decision cycle (mainly for testing)."""
    decision = await ai_director_runtime.tick()
    return decision.model_dump(mode="json")


@router.post("/ai/pending/{index}/approve")
async def approve_pending_action(index: int):
    """Approve and execute a pending (assisted-mode) AI-proposed action."""
    try:
        return await ai_director_runtime.approve_pending(index)
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/ai/pending/{index}/reject")
async def reject_pending_action(index: int):
    """Reject (discard) a pending AI-proposed action."""
    ai_director_runtime.reject_pending(index)
    return {"ok": True}
