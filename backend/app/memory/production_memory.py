"""Production memory and service history.

Records observations (cue advances, vision events, identity matches, ...)
tagged by calendar date, embeds them with `TextEmbedder` (see
app.memory.embeddings -- tries Voyage AI, then a local nomic-embed-text-v1.5
model, then a hashed fallback), and supports similarity search across past
services. Storage is Postgres via the existing SQLAlchemy session/engine
(see app.database).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text as sql_text

from app.config import settings
from app.database.connection import get_session
from app.database.repositories import MemoryRepository
from app.logging_config import get_logger

from .embeddings import text_embedder

logger = get_logger(__name__)


class MemoryManager:
    """Orchestrates recording and retrieval of production memory."""

    def retrieval_status(self) -> dict:
        from app.database.memory_vectors import INDEXED_DIMENSIONS

        status = {"backend": "pgvector", "embedding_provider": settings.embedding_provider,
                  "ready": False, "indexed_dimensions": list(INDEXED_DIMENSIONS)}
        try:
            with get_session() as session:
                version = session.execute(sql_text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar()
                rows = session.execute(sql_text("SELECT embedding_space, cardinality(embedding) AS dimensions, count(*) AS records FROM memory_service_observations GROUP BY 1, 2")).mappings().all()
                indexes = session.execute(sql_text("""
                    SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                    WHERE i.indrelid = 'memory_service_observations'::regclass AND i.indisvalid
                """)).scalars().all()
                expected = {f"memory_embedding_hnsw_{d}" for d in INDEXED_DIMENSIONS}
                status.update(pgvector_version=version, spaces=[dict(r) for r in rows],
                              indexes=list(indexes), ready=bool(version and expected.issubset(indexes)))
        except Exception:
            logger.warning("RAG schema check failed; run the memory migration or check PostgreSQL", exc_info=True)
            status["error"] = "PostgreSQL unavailable or RAG schema migration required"
        return status

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
        try:
            embedded = text_embedder.embed_with_metadata(text, input_type="document")
            with get_session() as session:
                observation = MemoryRepository(session).add_observation(
                    service_date=service_date,
                    category=category,
                    text=text,
                    embedding=embedded.vector.tolist(),
                    embedding_space=embedded.space,
                    source=source,
                    occurred_at=occurred_at,
                )
                return _observation_to_dict(observation)
        except Exception:
            logger.exception("Production memory unavailable (database unreachable)")
            return None

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        try:
            embedded = text_embedder.embed_with_metadata(query, input_type="query")
            with get_session() as session:
                results = MemoryRepository(session).search(embedded.vector.tolist(), limit=limit, embedding_space=embedded.space)
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
        "embedding_space": observation.embedding_space,
    }


memory_manager = MemoryManager()
