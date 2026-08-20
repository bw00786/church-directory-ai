"""Optional pgvector acceleration for face/voice similarity search.

The core `identity_face_profiles` / `identity_voice_profiles` tables always
store embeddings as plain float arrays (see database/models.py) so the app
works on any Postgres, including this dev machine's native install (which
does not have the pgvector extension's control file installed). When the
`vector` extension *is* available, this module additionally maintains a
companion table with a real `vector` column and uses it for indexed
nearest-neighbour search instead of the brute-force Python loop in
repositories.py -- purely as an accelerator, never a hard dependency.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from .connection import get_session

_AVAILABLE: bool | None = None


def pgvector_available() -> bool:
    """Whether the `vector` extension is installed and enabled (cached)."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE

    try:
        with get_session() as session:
            session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            session.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS identity_face_vectors ("
                    "profile_id UUID PRIMARY KEY, person_id UUID NOT NULL, embedding vector(256))"
                )
            )
            session.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS identity_voice_vectors ("
                    "profile_id UUID PRIMARY KEY, person_id UUID NOT NULL, embedding vector(24))"
                )
            )
        _AVAILABLE = True
    except Exception:
        _AVAILABLE = False
    return _AVAILABLE


def _to_vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in embedding) + "]"


def upsert_face_vector(session: Session, profile_id: uuid.UUID, person_id: uuid.UUID, embedding: list[float]) -> None:
    session.execute(
        text(
            "INSERT INTO identity_face_vectors (profile_id, person_id, embedding) "
            "VALUES (:profile_id, :person_id, :embedding) "
            "ON CONFLICT (profile_id) DO UPDATE SET embedding = EXCLUDED.embedding"
        ),
        {"profile_id": profile_id, "person_id": person_id, "embedding": _to_vector_literal(embedding)},
    )


def upsert_voice_vector(session: Session, profile_id: uuid.UUID, person_id: uuid.UUID, embedding: list[float]) -> None:
    session.execute(
        text(
            "INSERT INTO identity_voice_vectors (profile_id, person_id, embedding) "
            "VALUES (:profile_id, :person_id, :embedding) "
            "ON CONFLICT (profile_id) DO UPDATE SET embedding = EXCLUDED.embedding"
        ),
        {"profile_id": profile_id, "person_id": person_id, "embedding": _to_vector_literal(embedding)},
    )


def nearest_face(session: Session, embedding: list[float]) -> tuple[uuid.UUID, float] | None:
    """Nearest person_id by cosine distance, using the pgvector index. Returns
    (person_id, similarity) where similarity is in [-1, 1], or None if empty."""
    row = session.execute(
        text(
            "SELECT person_id, 1 - (embedding <=> :embedding) AS similarity "
            "FROM identity_face_vectors ORDER BY embedding <=> :embedding LIMIT 1"
        ),
        {"embedding": _to_vector_literal(embedding)},
    ).first()
    return (row.person_id, float(row.similarity)) if row else None


def nearest_voice(session: Session, embedding: list[float]) -> tuple[uuid.UUID, float] | None:
    row = session.execute(
        text(
            "SELECT person_id, 1 - (embedding <=> :embedding) AS similarity "
            "FROM identity_voice_vectors ORDER BY embedding <=> :embedding LIMIT 1"
        ),
        {"embedding": _to_vector_literal(embedding)},
    ).first()
    return (row.person_id, float(row.similarity)) if row else None
