"""Explicit, additive PostgreSQL schema upgrades; never run on a request path."""

from sqlalchemy import text

from .connection import Base
from .memory_vectors import INDEXED_DIMENSIONS

VERSION = "20260914_memory_pgvector_v1"


def migrate(engine):
    from . import models  # noqa: F401
    from app.voice.audit import VoiceAuditEvent  # noqa: F401

    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(text("SELECT pg_advisory_xact_lock(2026091401)"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        version = connection.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one()
        if tuple(int(v) for v in version.split('.')) < (0, 8, 0):
            raise RuntimeError("Memory retrieval requires pgvector 0.8.0 or later")
        Base.metadata.create_all(connection)
        connection.execute(text("ALTER TABLE memory_service_observations ADD COLUMN IF NOT EXISTS embedding_space text"))
        connection.execute(text("CREATE TABLE IF NOT EXISTS app_schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"))
        for dimension in INDEXED_DIMENSIONS:
            connection.execute(text(f"""
                CREATE INDEX IF NOT EXISTS memory_embedding_hnsw_{dimension}
                ON memory_service_observations USING hnsw ((embedding::vector({dimension})) vector_cosine_ops)
                WHERE embedding_space IS NOT NULL AND cardinality(embedding) = {dimension}
            """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS memory_embedding_space_idx ON memory_service_observations (embedding_space)"))
        connection.execute(text("INSERT INTO app_schema_migrations (version) VALUES (:version) ON CONFLICT DO NOTHING"), {"version": VERSION})
    return VERSION