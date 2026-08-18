"""Streaming control API endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/stream", tags=["Streaming"])


@router.get("/status")
async def stream_status():
    """Get streaming status."""
    return {"streaming": False}
