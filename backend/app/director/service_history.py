"""Persist each uploaded order of service as service history (Postgres).

Storage is best-effort like production memory: when the database is down the
upload still loads the cue sheet, and the history entry is simply skipped.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.database.connection import get_session
from app.database.repositories import ServiceOrderRepository
from app.logging_config import get_logger

from .models import ServiceScript
from .order_of_service import ParsedOrder

logger = get_logger(__name__)


class ServiceHistory:
    def record_order(
        self,
        script: ServiceScript,
        order: ParsedOrder,
        source: str,
        raw_text: str,
        filename: str | None = None,
    ) -> dict[str, Any] | None:
        try:
            with get_session() as session:
                row = ServiceOrderRepository(session).add(
                    service_date=order.date,
                    theme=order.theme,
                    speaker=order.speaker,
                    source=source,
                    filename=filename,
                    script_name=script.name,
                    raw_text=raw_text,
                    items=[{"heading": i.heading, "cue_ids": i.cue_ids} for i in order.items],
                    cue_ids=[c.id for c in script.cues],
                )
                return _to_dict(row)
        except Exception:
            logger.exception("Service history unavailable (database unreachable); order not saved")
            return None

    def list_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            with get_session() as session:
                return [_to_dict(row) for row in ServiceOrderRepository(session).list_recent(limit)]
        except Exception:
            logger.exception("Service history unavailable (database unreachable)")
            return []

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        try:
            with get_session() as session:
                row = ServiceOrderRepository(session).get(uuid.UUID(order_id))
                return _to_dict(row, include_text=True) if row else None
        except Exception:
            logger.exception("Service history unavailable (database unreachable)")
            return None


def _to_dict(row: Any, include_text: bool = False) -> dict[str, Any]:
    data = {
        "id": str(row.id),
        "uploaded_at": row.uploaded_at.isoformat(),
        "service_date": row.service_date,
        "theme": row.theme,
        "speaker": row.speaker,
        "source": row.source,
        "filename": row.filename,
        "script_name": row.script_name,
        "items": row.items,
        "cue_ids": row.cue_ids,
    }
    if include_text:
        data["raw_text"] = row.raw_text
    return data


service_history = ServiceHistory()
