"""Production memory API: search and browse past-service observations."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_memory_manager

router = APIRouter(prefix="/api/memory", tags=["Memory"])


class RecordObservationRequest(BaseModel):
    category: str
    text: str
    source: str = "manual"


@router.post("/observations")
async def record_observation(request: RecordObservationRequest, memory_manager=Depends(get_memory_manager)):
    return memory_manager.record_observation(request.category, request.text, request.source)


@router.get("/search")
async def search_memory(q: str, limit: int = 10, memory_manager=Depends(get_memory_manager)):
    return {"results": memory_manager.search(q, limit=limit)}


@router.get("/services")
async def list_services(limit: int = 50, memory_manager=Depends(get_memory_manager)):
    return {"services": memory_manager.list_services(limit=limit)}


@router.get("/services/{service_date}")
async def get_service_summary(service_date: str, memory_manager=Depends(get_memory_manager)):
    return memory_manager.service_summary(service_date)
