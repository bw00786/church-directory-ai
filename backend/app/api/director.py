"""Service director REST API."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.director.ai_director_runtime import ai_director_runtime
from app.director.engine import service_director
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
    return {
        "mode": ai_director_runtime.mode,
        "context": service_context.snapshot(),
        "pending_actions": [a.model_dump(mode="json") for a in ai_director_runtime.pending_actions],
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
