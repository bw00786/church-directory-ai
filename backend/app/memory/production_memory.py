"""Production memory and service history.

Records observations (cue advances, vision events, identity matches, ...)
tagged by calendar date, embeds them with the dependency-free `TextEmbedder`,
and supports similarity search across past services. Storage is Postgres via
the existing SQLAlchemy session/engine (see app.database).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.database.connection import get_session
from app.database.repositories import MemoryRepository
from app.logging_config import get_logger

from .embeddings import text_embedder

logger = get_logger(__name__)


class MemoryManager:
    """Orchestrates recording and retrieval of production memory."""

    def record_observation(
        self,
        category: str,
        text: str,
        source: str = "system",
        occurred_at: datetime | None = None,
        service_date: str | None = None,
    ) -> dict[str, Any] | None:
        occurred_at = occurred_at or datetime.utcnow()
        service_date = service_date or occurred_at.date().isoformat()
        embedding = text_embedder.embed(text).tolist()
        try:
            with get_session() as session:
                observation = MemoryRepository(session).add_observation(
                    service_date=service_date,
                    category=category,
                    text=text,
                    embedding=embedding,
                    source=source,
                    occurred_at=occurred_at,
                )
                return _observation_to_dict(observation)
        except Exception:
            logger.exception("Production memory unavailable (database unreachable)")
            return None

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query_embedding = text_embedder.embed(query).tolist()
        try:
            with get_session() as session:
                results = MemoryRepository(session).search(query_embedding, limit=limit)
                return [
                    {**_observation_to_dict(observation), "similarity": similarity}
                    for observation, similarity in results
                ]
        except Exception:
            logger.exception("Production memory search unavailable (database unreachable)")
            return []

    def list_services(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            with get_session() as session:
                return MemoryRepository(session).list_service_dates(limit=limit)
        except Exception:
            logger.exception("Production memory unavailable (database unreachable)")
            return []

    def service_summary(self, service_date: str) -> dict[str, Any]:
        try:
            with get_session() as session:
                observations = MemoryRepository(session).observations_for_date(service_date)
        except Exception:
            logger.exception("Production memory unavailable (database unreachable)")
            return {"service_date": service_date, "observation_count": 0, "by_category": {}, "observations": []}

        by_category: dict[str, int] = {}
        for observation in observations:
            by_category[observation.category] = by_category.get(observation.category, 0) + 1

        return {
            "service_date": service_date,
            "observation_count": len(observations),
            "by_category": by_category,
            "observations": [_observation_to_dict(o) for o in observations],
        }


def _observation_to_dict(observation: Any) -> dict[str, Any]:
    return {
        "id": str(observation.id),
        "occurred_at": observation.occurred_at.isoformat(),
        "service_date": observation.service_date,
        "category": observation.category,
        "source": observation.source,
        "text": observation.text,
    }


memory_manager = MemoryManager()
