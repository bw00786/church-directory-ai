"""AI agent API endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/agents", tags=["Agents"])


@router.get("/status")
async def agent_status():
    """Get AI agent status."""
    return {"status": "offline"}
