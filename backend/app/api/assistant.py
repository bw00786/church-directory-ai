"""AI assistant chat API: query production history/roster and control subsystems."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents import assistant_tools
from app.agents.assistant import run_assistant
from app.agents.assistant_tools import discard_pending, execute_pending
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/assistant", tags=["Assistant"])


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class PendingAction(BaseModel):
    token: str
    action: str
    description: str


@router.get("/pending", response_model=list[PendingAction])
async def pending():
    """List current approvals oldest first, without execution arguments."""
    return [
        PendingAction(token=token, action=item["action"], description=item["description"])
        for token, item in assistant_tools.pending_actions.items()
    ]


@router.post("/chat")
async def chat(request: ChatRequest):
    """Run one turn of the assistant against the full conversation so far."""
    try:
        return await run_assistant([m.model_dump() for m in request.messages])
    except ImportError:
        logger.exception("Assistant runtime dependency unavailable")
        raise HTTPException(status_code=503, detail="Assistant runtime dependencies are unavailable. Install the backend requirements and restart FastAPI.")
    except TimeoutError:
        logger.warning("Assistant request timed out")
        raise HTTPException(status_code=504, detail="The assistant timed out. Check pending approvals and production status before retrying.")
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Assistant chat failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm/{token}")
async def confirm(token: str):
    """Execute a pending high-risk action (streaming/recording/mic) the assistant proposed."""
    result = await execute_pending(token)
    if not result.get("ok", True) and str(result.get("error", "")).startswith("Unknown"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/cancel/{token}")
async def cancel(token: str):
    """Discard a pending high-risk action without executing it."""
    if not discard_pending(token):
        raise HTTPException(status_code=404, detail="Unknown or already-used confirmation token")
    return {"ok": True}
