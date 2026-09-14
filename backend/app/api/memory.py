"""Production memory API: search and browse past-service observations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.dependencies import get_memory_manager

router = APIRouter(prefix="/api/memory", tags=["Memory"])


@router.get("/status")
def retrieval_status(memory_manager=Depends(get_memory_manager)):
    return memory_manager.retrieval_status()


class RecordObservationRequest(BaseModel):
    category: str
    text: str
    source: str = "manual"


@router.post("/observations")
def record_observation(request: RecordObservationRequest, memory_manager=Depends(get_memory_manager)):
    return memory_manager.record_observation(request.category, request.text, request.source)


@router.get("/search")
def search_memory(q: str, limit: int = Query(10, ge=1, le=100), memory_manager=Depends(get_memory_manager)):
    return {"results": memory_manager.search(q, limit=limit)}


@router.get("/services")
def list_services(limit: int = 50, memory_manager=Depends(get_memory_manager)):
    return {"services": memory_manager.list_services(limit=limit)}


@router.get("/services/{service_date}")
def get_service_summary(service_date: str, memory_manager=Depends(get_memory_manager)):
    return memory_manager.service_summary(service_date)
