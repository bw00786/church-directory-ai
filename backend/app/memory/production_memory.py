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
from .retrieval import HOUSE_STYLE_CATEGORIES, is_hashed_space, observation_matches_state
from .retrieval import summarize_house_style as _summarize_rows

logger = get_logger(__name__)

# Structured fields accepted by record_observation and stored as JSONB metadata.
STRUCTURED_FIELDS = ("service_state", "speaker_role", "category", "camera_role",
                     "atem_input", "easyworship_item", "outcome")


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
        *,
        service_state: str | None = None,
        speaker_role: str | None = None,
        camera_role: str | None = None,
        atem_input: int | None = None,
        easyworship_item: str | None = None,
        outcome: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Record one moment. The keyword fields are optional structured context
        stored as JSONB so the director can filter retrieval by state/role
        instead of relying on free-text similarity alone."""
        occurred_at = occurred_at or datetime.utcnow()
        service_date = service_date or occurred_at.date().isoformat()
        structured = build_metadata(
            service_state=service_state, speaker_role=speaker_role, category=category,
            camera_role=camera_role, atem_input=atem_input, easyworship_item=easyworship_item,
            outcome=outcome, extra=metadata,
        )
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
                    metadata=structured,
                )
                return _observation_to_dict(observation)
        except Exception:
            logger.exception("Production memory unavailable (database unreachable)")
            return None

    def retrieve_similar_moments(
        self,
        state: str | None,
        speaker_role: str | None = None,
        category: str | None = None,
        limit: int = 5,
        *,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Director-path retrieval: filter by service state / category (and
        speaker role when given) first, then rank by embedding similarity.

        The hashed bag-of-words tier is skipped on purpose: its neighbours are
        near-random for this use and would only add noise to the prompt. If the
        strict filter finds nothing the speaker-role filter is relaxed once.
        """
        filters: dict[str, str] = {}
        if state:
            filters["service_state"] = str(state)
        if category:
            filters["category"] = str(category)
        if speaker_role:
            filters["speaker_role"] = str(speaker_role)
        text = (query or " ".join(filter(None, [state, speaker_role, category]))).strip()
        if not text:
            return []
        try:
            embedded = text_embedder.embed_with_metadata(text, input_type="query")
            if is_hashed_space(embedded.space):
                logger.info("Director memory retrieval skipped: only the hashed embedding tier is available",
                            space=embedded.space)
                return []
            with get_session() as session:
                repository = MemoryRepository(session)
                results = repository.search(embedded.vector.tolist(), limit=limit,
                                            embedding_space=embedded.space, filters=filters or None)
                if not results and "speaker_role" in filters:
                    relaxed = {k: v for k, v in filters.items() if k != "speaker_role"}
                    results = repository.search(embedded.vector.tolist(), limit=limit,
                                                embedding_space=embedded.space, filters=relaxed or None)
                return [{**_observation_to_dict(o), "similarity": similarity} for o, similarity in results]
        except Exception:
            logger.exception("Structured memory retrieval unavailable (database unreachable)")
            return []

    def summarize_house_style(self, state: str | None, easyworship_item: str | None = None,
                              limit: int = 400) -> list[str]:
        """3-6 bullet rules distilled from the operator's own behaviour in
        ``state`` (slide-advance timing, preferred shots, overrides). Cheap
        aggregation over recent rows; no LLM call. Never raises."""
        if not state:
            return []
        try:
            with get_session() as session:
                rows = MemoryRepository(session).recent_observations(HOUSE_STYLE_CATEGORIES, limit=limit)
        except Exception:
            logger.warning("House-style summary unavailable (database unreachable)", exc_info=True)
            return []
        relevant = [row for row in rows if observation_matches_state(row, state, easyworship_item)]
        return _summarize_rows(relevant, state)

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


def build_metadata(*, service_state=None, speaker_role=None, category=None, camera_role=None,
                   atem_input=None, easyworship_item=None, outcome=None,
                   extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """JSON-safe structured metadata; None when nothing structured was given
    (so plain free-text observations keep a NULL column)."""
    values = {
        "service_state": service_state, "speaker_role": speaker_role, "category": category,
        "camera_role": camera_role, "atem_input": atem_input, "easyworship_item": easyworship_item,
        "outcome": outcome,
    }
    structured = {k: v for k, v in values.items() if v not in (None, "")}
    if len(structured) == 1 and "category" in structured and not extra:
        return None  # category alone is already a column
    if extra:
        for key, value in extra.items():
            if value is None or key in structured:
                continue
            structured[str(key)] = value if isinstance(value, (str, int, float, bool)) else str(value)
    if "atem_input" in structured:
        try:
            structured["atem_input"] = int(structured["atem_input"])
        except (TypeError, ValueError):
            structured.pop("atem_input")
    return structured or None


def _observation_to_dict(observation: Any) -> dict[str, Any]:
    meta = getattr(observation, "meta", None)
    return {
        "id": str(observation.id),
        "occurred_at": observation.occurred_at.isoformat(),
        "service_date": observation.service_date,
        "category": observation.category,
        "source": observation.source,
        "text": observation.text,
        "embedding_space": observation.embedding_space,
        "metadata": dict(meta) if isinstance(meta, dict) else {},
    }


memory_manager = MemoryManager()
