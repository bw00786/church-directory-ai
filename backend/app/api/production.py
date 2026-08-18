"""Production control API endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/production", tags=["Production"])


@router.get("/")
async def get_production_status():
    """Get production status."""
    return {"status": "ready"}
