"""Service director REST API."""

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.director.ai_director_runtime import ai_director_runtime
from app.director.engine import service_director
from app.director.rehearsal import rehearsal_service
from app.director.review import available_dates, review_for, review_recorder
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
from app.director.slide_text_source import (
    MAX_LABEL_CHARS,
    MAX_TEXT_CHARS,
    follow_active,
    live_item_label,
    live_slide_text_source,
    slide_text_library,
)
from app.director.text_follower import song_follower_service
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


class SlideTextRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=MAX_LABEL_CHARS)
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)


class RehearsalStartRequest(BaseModel):
    """Replay a bundled sample (``sample``) or a file under the recordings directory (``file``)."""

    sample: Optional[str] = None
    file: Optional[str] = None
    speed: float = Field(1.0, gt=0.0, le=50.0)
    mode: str = "ai_directed"  # manual | assisted | ai_directed (private runtime; mock dispatch)


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


@router.get("/cycles")
async def list_cycles(limit: int = 100):
    """Recent AI Director decision cycles: inputs, model reply, decision, and
    action outcomes per cycle (from data/ai_director_cycles.jsonl)."""
    from app.director import cycle_log

    return {"cycles": cycle_log.read_cycles(limit=max(1, min(limit, 1000)))}


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
    """Let the AI evaluate an observation; any advance becomes a CUE_ADVANCE
    proposal in the AI Director's queue and honours the operating mode."""
    from app.agents.director_ai import director_ai

    return await director_ai.observe(request.text)


# -- AI Service Director (reasoning layer above the cue engine) --------------


@router.get("/ai/status")
async def ai_status():
    """Current AI Director mode, service context snapshot, pending proposals
    (with ids, TTL and hold reasons), last cycle timing and adaptive thresholds."""
    from app.audio.audio_observer import audio_observer
    from app.vision.perception import perception_loop

    return {
        **ai_director_runtime.status(),
        "context": service_context.snapshot(),
        "perception": audio_observer.perception_status(),
        "vision": perception_loop.status(),
    }


@router.get("/ai/pending")
async def list_pending_actions():
    """Pending AI proposals awaiting operator approval (or a pacing retry)."""
    return {"pending_actions": ai_director_runtime.pending_proposals()}


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


@router.post("/ai/pause")
async def pause_ai():
    """Pause the AI Director (operator role): drop to manual, remembering the mode to resume."""
    return ai_director_runtime.pause()


@router.post("/ai/resume")
async def resume_ai():
    """Resume the mode that was active before the pause (no-op when not paused)."""
    return ai_director_runtime.resume()


@router.post("/ai/tick")
async def ai_tick():
    """Manually trigger one AI Director decision cycle (mainly for testing)."""
    decision = await ai_director_runtime.tick()
    return decision.model_dump(mode="json")


@router.post("/ai/pending/{ref}/approve")
async def approve_pending_action(ref: str):
    """Approve and execute a pending (assisted-mode) AI proposal by **id**.

    A purely numeric ``ref`` is treated as the legacy queue index (kept for one
    release; index routes resolve index -> id before acting)."""
    try:
        return await ai_director_runtime.approve_pending(ref)
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No pending proposal {ref}")


@router.post("/ai/pending/{ref}/reject")
async def reject_pending_action(ref: str):
    """Reject (discard) a pending AI proposal by id (or legacy index)."""
    try:
        return ai_director_runtime.reject_pending(ref)
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No pending proposal {ref}")


# -- Song / reading follower ---------------------------------------------------


@router.get("/slide-texts")
async def list_slide_texts():
    """Slide texts the follower uses, keyed by EasyWorship item label."""
    return {"items": slide_text_library.items()}


@router.put("/slide-texts")
async def set_slide_text(request: SlideTextRequest):
    """Set the text for one EasyWorship item; separate slides with a blank line."""
    try:
        slides = slide_text_library.set(request.label, request.text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"label": request.label, "slides": slides}


@router.delete("/slide-texts/{label}")
async def delete_slide_text(label: str):
    """Remove an item's text (the follower falls back to OCR of the slides feed)."""
    if not slide_text_library.delete(label):
        raise HTTPException(status_code=404, detail="No text for that item")
    return {"ok": True}


@router.get("/follower")
async def follower_status():
    """What the slide follower is tracking right now."""
    return {
        **song_follower_service.status(),
        "active": follow_active(),
        "live_item": live_item_label(),
        "ocr_text": live_slide_text_source.ocr.text,
    }


@router.get("/operator-advances")
async def operator_advances(limit: int = 50):
    """What has been learned from the operator's own slide advances."""
    from app.director.operator_learning import operator_advance_history, operator_advance_recorder

    return {
        **operator_advance_recorder.status(),
        "history": operator_advance_history.summary(limit=max(1, min(limit, 500))),
    }


# -- Rehearsal (replay a recorded service against mock clients) ------------------


@router.get("/rehearsal/samples")
async def rehearsal_samples():
    """Bundled sample recordings plus files in the recordings directory."""
    from app.director.rehearsal import list_samples, rehearsal_allowed

    allowed, reason = rehearsal_allowed()
    return {"samples": list_samples(), "allowed": allowed, "reason": reason}


@router.get("/rehearsal/status")
async def rehearsal_status():
    """Progress, per-step decisions, queued proposals and mock-executed actions."""
    return rehearsal_service.status()


@router.post("/rehearsal/start")
async def rehearsal_start(request: RehearsalStartRequest):
    """Start replaying a recording through a private AI runtime. Never moves hardware."""
    review_recorder.ensure_started()
    try:
        return await rehearsal_service.start(
            sample=request.sample, file=request.file, speed=request.speed, mode=request.mode
        )
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/rehearsal/stop")
async def rehearsal_stop():
    """Stop the running rehearsal (keeps its decisions for review)."""
    return await rehearsal_service.stop()


# -- Post-service review -----------------------------------------------------------


@router.get("/review/dates")
async def review_dates():
    """Local dates that have cycle-log or review-event data, newest first."""
    review_recorder.ensure_started()
    return {"dates": available_dates()}


@router.get("/review")
async def review(date: Optional[str] = None):
    """One day's AI Director activity: totals plus a compact timeline (default today)."""
    from datetime import date as _date

    review_recorder.ensure_started()
    try:
        day = _date.fromisoformat(date) if date else _date.today()
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    return review_for(day)
