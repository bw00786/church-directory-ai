import asyncio
import contextlib
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.events.bus import event_bus
from app.voice.models import VoiceConfig
from app.voice.service import VoiceService, get_voice_service

router = APIRouter(prefix="/api/voice", tags=["Voice Attention"])


class ConfigurationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["off", "attention_only", "testing", "emergency"] | None = None
    operator_name: str | None = Field(default=None, max_length=60)
    cooldown_seconds: float | None = Field(default=None, ge=1, le=600)
    repeat_interval_seconds: float | None = Field(default=None, ge=10, le=3600)
    aggregation_window_seconds: float | None = Field(default=None, ge=0, le=10)
    persona: VoiceConfig | None = None


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["acknowledge", "dismiss"] = "acknowledge"
    feedback: Literal["useful", "not_useful", "too_sensitive", "too_late", "correct", "incorrect"] | None = None


@router.get("/status")
async def status(service: VoiceService = Depends(get_voice_service)):
    return service.status()


@router.get("/config")
async def configuration(service: VoiceService = Depends(get_voice_service)):
    return service.config.model_dump()


@router.post("/config")
async def configure(patch: ConfigurationPatch, service: VoiceService = Depends(get_voice_service)):
    try:
        service.update(patch.model_dump(exclude_none=True))
    except ValidationError:
        raise HTTPException(422, "Invalid voice configuration; check cooldown and repeat intervals")
    return service.config.model_dump()


@router.get("/queue")
async def queue(service: VoiceService = Depends(get_voice_service)):
    return {"pending": service.status()["pending"]}


@router.get("/metrics")
async def metrics(service: VoiceService = Depends(get_voice_service)):
    return service.metrics.snapshot()


@router.get("/events")
async def events(limit: int = Query(100, ge=1, le=500), service: VoiceService = Depends(get_voice_service)):
    recent = {}
    if service.audit:
        try:
            persisted = await asyncio.wait_for(asyncio.to_thread(service.audit.recent, limit), 2)
            recent.update({e.id: e for e in persisted})
        except Exception:
            service.audit_available = False
    recent.update(service.events)
    return {"events": [e.model_dump(mode="json") for e in sorted(recent.values(), key=lambda e: e.timestamp, reverse=True)[:limit]]}


@router.post("/events/{event_id}/ack")
async def acknowledge(event_id: str, feedback: Feedback, service: VoiceService = Depends(get_voice_service)):
    try:
        return service.acknowledge(event_id, feedback.action, feedback.feedback)
    except KeyError:
        raise HTTPException(404, "Alert not in current session")


@router.post("/enable")
async def enable(service: VoiceService = Depends(get_voice_service)):
    service.update({"enabled": True, "mode": "attention_only" if service.config.mode == "off" else service.config.mode})
    return service.status()


@router.post("/disable")
async def disable(service: VoiceService = Depends(get_voice_service)):
    service.update({"enabled": False})
    return service.status()


@router.post("/mute")
async def mute(service: VoiceService = Depends(get_voice_service)):
    service.set_muted(True)
    return service.status()


@router.post("/unmute")
async def unmute(service: VoiceService = Depends(get_voice_service)):
    service.set_muted(False)
    return service.status()


@router.post("/test")
async def test(service: VoiceService = Depends(get_voice_service)):
    try:
        return service.test()
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.post("/repeat")
async def repeat(service: VoiceService = Depends(get_voice_service)):
    return service.repeat()


async def voice_socket(websocket: WebSocket):
    await websocket.accept()
    service = get_voice_service()
    queue = await event_bus.subscribe(maxsize=100)

    async def sender():
        await websocket.send_json({"type": "voice_status", "status": service.status()})
        while True:
            message = await queue.get()
            if isinstance(message, dict) and str(message.get("type", "")).startswith("voice_"):
                await websocket.send_json(message)

    async def receiver():
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
        await event_bus.unsubscribe(queue)