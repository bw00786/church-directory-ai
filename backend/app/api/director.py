"""Service director REST API."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.director.engine import service_director
from app.director.scheduler import service_scheduler
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
