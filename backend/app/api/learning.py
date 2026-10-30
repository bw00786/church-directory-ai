"""What the system has learned from the operator, under one prefix.

- ``GET /api/learning/operator-shots``     -- camera cuts/reframes the operator made
  (app.director.operator_shots): recorder status + learned history.
- ``GET /api/learning/operator-advances``  -- slide advances the operator made
  (app.director.operator_learning). The older ``/director/operator-advances``
  route keeps working; this is the same payload.
- ``GET /api/learning/house-style?state=`` -- the learned HOUSE RULES bullets the
  AI director's prompt gets for ``state`` (``summarize_house_style``), plus the
  local shot-history answers the role table and pacing consult.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.domain.service_state import ServiceState

router = APIRouter(prefix="/api/learning", tags=["Learning"])


def _limit(limit: int) -> int:
    return max(1, min(int(limit), 500))


@router.get("/operator-shots")
async def operator_shots(limit: int = Query(50, ge=1, le=500)):
    """What has been learned from the operator's own camera cuts and reframes."""
    from app.director.operator_shots import operator_shot_history, operator_shot_recorder

    return {
        **operator_shot_recorder.status(),
        "history": await asyncio.to_thread(operator_shot_history.summary, _limit(limit)),
    }


@router.get("/operator-advances")
async def operator_advances(limit: int = Query(50, ge=1, le=500)):
    """What has been learned from the operator's own slide advances."""
    from app.director.operator_learning import operator_advance_history, operator_advance_recorder

    return {
        **operator_advance_recorder.status(),
        "history": await asyncio.to_thread(operator_advance_history.summary, _limit(limit)),
    }


@router.get("/house-style")
async def house_style(state: str = Query(..., min_length=1), easyworship_item: Optional[str] = None):
    """The learned house-style bullets the prompt would receive for ``state``."""
    valid = {s.value for s in ServiceState}
    if state not in valid:
        raise HTTPException(status_code=400, detail={"error": "unknown service state", "valid": sorted(valid)})
    from app.director.operator_shots import operator_shot_history
    from app.memory.production_memory import memory_manager

    rules = await asyncio.to_thread(memory_manager.summarize_house_style, state, easyworship_item)
    learned = await asyncio.to_thread(
        lambda: {
            "preferred_shot": operator_shot_history.preferred_shot(state),
            "typical_hold_seconds": operator_shot_history.typical_hold(state),
            "speaker_change_latency_seconds": operator_shot_history.speaker_change_latency(state),
        }
    )
    return {"state": state, "easyworship_item": easyworship_item, "rules": list(rules or []), "learned_shots": learned}
