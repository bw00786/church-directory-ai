"""Server-side cosine search with pgvector HNSW expression indexes."""

import math

from sqlalchemy import select, text

from .models import ServiceObservation

INDEXED_DIMENSIONS = (256, 768, 1024, 1536)


def validate_embedding(vector: list[float]) -> int:
    if not vector or len(vector) > 16000 or not all(math.isfinite(v) and abs(v) <= 3.4028234e38 for v in vector):
        raise ValueError("Embedding must contain 1..16000 finite float32-compatible values")
    return len(vector)


# Structured pre-filters for the AI director's retrieval. Keys are allow-listed
# and map to fixed SQL; values are always bound parameters.
FILTER_SQL = {
    "category": "category = :category",
    "service_state": "metadata->>'service_state' = :service_state",
    "speaker_role": "metadata->>'speaker_role' = :speaker_role",
    "camera_role": "metadata->>'camera_role' = :camera_role",
}


def _filter_clauses(filters: dict | None) -> str:
    if not filters:
        return ""
    unknown = set(filters) - set(FILTER_SQL)
    if unknown:
        raise ValueError(f"Unsupported retrieval filters: {sorted(unknown)}")
    return "".join(f"\n          AND {FILTER_SQL[key]}" for key in sorted(filters))


def search_sql(dimension: int, filters: dict | None = None) -> str:
    if type(dimension) is not int or not 1 <= dimension <= 16000:
        raise ValueError("Invalid vector dimension")
    # Only the validated integer and allow-listed filter clauses are interpolated;
    # query data, model identity and filter values are bound.
    return f"""
        SELECT id, 1 - (embedding::vector({dimension}) <=> CAST(:query AS vector({dimension}))) AS similarity
        FROM memory_service_observations
        WHERE embedding_space = :space AND embedding_space IS NOT NULL
          AND cardinality(embedding) = {dimension}
          AND vector_norm(embedding::vector) > 0{_filter_clauses(filters)}
        ORDER BY embedding::vector({dimension}) <=> CAST(:query AS vector({dimension}))
        LIMIT :limit
    """


def search(session, vector: list[float], space: str, limit: int, filters: dict | None = None):
    dimension = validate_embedding(vector)
    if not space:
        raise ValueError("Embedding space is required for RAG retrieval")
    if not any(vector) or limit <= 0:
        return []
    literal = "[" + ",".join(str(float(v)) for v in vector) + "]"
    params = {"query": literal, "space": space, "limit": min(limit, 100)}
    for key, value in (filters or {}).items():
        params[key] = str(value)
    session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
    rows = session.execute(text(search_sql(dimension, filters)), params).all()
    if not rows:
        return []
    observations = {o.id: o for o in session.scalars(select(ServiceObservation).where(ServiceObservation.id.in_([r.id for r in rows])))}
    return [(observations[r.id], float(r.similarity)) for r in rows if r.id in observations]