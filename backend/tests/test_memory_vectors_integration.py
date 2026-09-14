"""Opt-in pgvector checks against the configured app database.

Run from backend with RUN_PGVECTOR_TESTS=1. The existing connection settings
load backend/.env; credentials are never printed. A separate engine sets a
unique schema search_path and schema translation for ORM DDL. All schema DDL,
migration records, indexes and rows live in ONE uncommitted outer transaction
and are rolled back, including on failure. No DROP/TRUNCATE, production rows,
extension installation, or application startup is needed.
"""

import os
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.database.connection import _engine
from app.database.memory_vectors import INDEXED_DIMENSIONS, search_sql
from app.database.migrations import VERSION, migrate
from app.database.models import ServiceObservation
from app.database.repositories import MemoryRepository
from app.memory.embeddings import EmbeddedText
from scripts.migrate_memory_vectors import backfill


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_PGVECTOR_TESTS") != "1",
                       reason="Set RUN_PGVECTOR_TESTS=1 for rollback-only PostgreSQL tests"),
]


@pytest.fixture(scope="module")
def isolated_database():
    schema = "test_memory_vectors_" + uuid4().hex
    # Read-only prerequisite check: never install pgvector into the real DB.
    with _engine.connect() as probe:
        extension = probe.execute(text("""
            SELECT e.extversion, n.nspname
            FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname = 'vector'
        """)).one_or_none()
    if extension is None:
        pytest.skip("pgvector must already be installed; tests do not install extensions")
    if tuple(map(int, extension.extversion.split("."))) < (0, 8, 0):
        pytest.skip("pgvector >= 0.8.0 is required")

    quote = _engine.dialect.identifier_preparer.quote_identifier
    search_path = f"{quote(schema)},{quote(extension.nspname)}"
    engine = create_engine(
        _engine.url, poolclass=NullPool, echo=False, hide_parameters=True,
        connect_args={"options": f"-c search_path={search_path} -c statement_timeout=15000"},
        execution_options={"schema_translate_map": {None: schema}},
    )
    try:
        with engine.connect() as connection:
            outer = connection.begin()
            try:
                connection.execute(text(f"CREATE SCHEMA {quote(schema)}"))
                assert connection.execute(text("SELECT current_schema()")).scalar_one() == schema

                class RollbackOnlyEngine:
                    # Run the ACTUAL migration, but never commit its transaction.
                    # schema_translate_map prevents create_all(checkfirst=True)
                    # from discovering and skipping tables in the real schema.
                    @contextmanager
                    def begin(self):
                        yield connection

                assert migrate(RollbackOnlyEngine()) == VERSION
                assert migrate(RollbackOnlyEngine()) == VERSION
                assert connection.execute(text("""
                    SELECT n.nspname FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.oid = 'memory_service_observations'::regclass
                """)).scalar_one() == schema
                yield SimpleNamespace(connection=connection, schema=schema, engine=engine)
            finally:
                outer.rollback()
                # Prove that even the schema itself was removed by rollback.
                assert connection.execute(text(
                    "SELECT count(*) FROM pg_namespace WHERE nspname = :schema"
                ), {"schema": schema}).scalar_one() == 0
                connection.rollback()
    finally:
        engine.dispose()


@pytest.fixture
def pg_session(isolated_database):
    # Per-test rollback without ending the fixture's uncommitted schema DDL.
    with Session(bind=isolated_database.connection, expire_on_commit=False,
                 join_transaction_mode="create_savepoint") as session:
        yield session


def add(session, label, vector, space):
    return MemoryRepository(session).add_observation(
        service_date="2026-09-14", category="indexed-rag-test", text=label,
        embedding=vector, embedding_space=space, source="isolated-pytest",
    )


def axis(dimension, first=1.0, second=0.0):
    return [float(first), float(second)] + [0.0] * (dimension - 2)


def test_migration_is_idempotent_and_indexes_are_isolated(isolated_database, pg_session):
    assert pg_session.execute(text(
        "SELECT count(*) FROM app_schema_migrations WHERE version = :version"
    ), {"version": VERSION}).scalar_one() == 1
    definitions = dict(pg_session.execute(text("""
        SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :schema
    """), {"schema": isolated_database.schema}).all())
    for dimension in INDEXED_DIMENSIONS:
        definition = definitions[f"memory_embedding_hnsw_{dimension}"]
        assert "USING hnsw" in definition
        assert "vector_cosine_ops" in definition
        assert f"vector({dimension})" in definition
        assert "embedding_space IS NOT NULL" in definition
        assert f"cardinality(embedding) = {dimension}" in definition


@pytest.mark.parametrize("dimension", [3, *INDEXED_DIMENSIONS])
def test_array_roundtrip_ranked_space_isolation_mixed_dimensions_and_legacy(pg_session, dimension):
    space = "voyage:test-provider:" + str(dimension)
    other_space = "nomic:test-provider:" + str(dimension)
    negative = add(pg_session, "negative", axis(dimension, -1), space)
    orthogonal = add(pg_session, "orthogonal", axis(dimension, 0, 1), space)
    closest = add(pg_session, "closest", axis(dimension), space)
    middle = add(pg_session, "middle", axis(dimension, 0.6, 0.8), space)
    other = add(pg_session, "other provider same dimension", axis(dimension), other_space)
    add(pg_session, "wrong dimension same space", axis(dimension + 1), space)
    add(pg_session, "legacy NULL space", axis(dimension), None)
    add(pg_session, "zero document", [0.0] * dimension, space)
    closest_id = closest.id
    # Force a database reload instead of merely checking the ORM identity map.
    pg_session.expire_all()
    stored = pg_session.get(ServiceObservation, closest_id)
    assert stored.embedding == axis(dimension)
    assert stored.embedding_space == space
    assert pg_session.execute(text(
        "SELECT pg_typeof(embedding)::text FROM memory_service_observations WHERE id = :id"
    ), {"id": closest_id}).scalar_one() == "double precision[]"

    results = MemoryRepository(pg_session).search(axis(dimension), limit=10, embedding_space=space)
    assert [row.id for row, _ in results] == [closest.id, middle.id, orthogonal.id, negative.id]
    assert [score for _, score in results] == pytest.approx([1.0, 0.6, 0.0, -1.0], abs=1e-6)
    other_results = MemoryRepository(pg_session).search(
        axis(dimension), limit=10, embedding_space=other_space,
    )
    assert [row.id for row, _ in other_results] == [other.id]
    assert MemoryRepository(pg_session).search(axis(dimension), embedding_space="unknown:model") == []
    assert MemoryRepository(pg_session).search(axis(dimension), embedding_space="' OR TRUE --") == []


@pytest.mark.parametrize("dimension", INDEXED_DIMENSIONS)
def test_production_bound_query_uses_cosine_hnsw_index(pg_session, isolated_database, dimension):
    space = f"index-plan:model:{dimension}"
    for i in range(128):
        add(pg_session, f"rank {i}", axis(dimension, 1.0, i / 128), space)
    # Distractors exercise both the partial predicate and the provider filter.
    add(pg_session, "wrong space", axis(dimension), "other:model")
    add(pg_session, "wrong dimension", axis(dimension + 1), space)
    add(pg_session, "legacy", axis(dimension), None)
    add(pg_session, "zero", [0.0] * dimension, space)
    # Analyze only the qualified test table, never the production table.
    quote = isolated_database.engine.dialect.identifier_preparer.quote_identifier
    pg_session.execute(text(
        f"ANALYZE {quote(isolated_database.schema)}.memory_service_observations"
    ))
    pg_session.execute(text("SET LOCAL enable_seqscan = off"))
    pg_session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
    vector = axis(dimension)
    params = {"query": "[" + ",".join(str(float(v)) for v in vector) + "]",
              "space": space, "limit": 5}
    # Exactly the production expression/predicate and bind parameters, not a
    # simplified ANN query which might conceal an unusable production index.
    sql = search_sql(dimension)
    plan = pg_session.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql), params).scalar_one()

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    plan_nodes = list(nodes(plan[0]["Plan"]))
    assert any(node.get("Index Name") == f"memory_embedding_hnsw_{dimension}"
               for node in plan_nodes), plan
    assert not any(node["Node Type"] == "Seq Scan" for node in plan_nodes), plan
    sql_rows = pg_session.execute(text(sql), params).all()
    results = MemoryRepository(pg_session).search(vector, limit=5, embedding_space=space)
    assert [row.id for row, _ in results] == [row.id for row in sql_rows]
    assert [row.text for row, _ in results] == [f"rank {i}" for i in range(5)]
    assert [score for _, score in results] == pytest.approx(
        [1 / (1 + (i / 128) ** 2) ** 0.5 for i in range(5)], abs=1e-6,
    )


def test_query_limits_zero_vectors_and_server_limit_cap(pg_session):
    space = "limits:model:256"
    for i in range(105):
        add(pg_session, f"limit {i}", axis(256, 1, i / 105), space)
    repository = MemoryRepository(pg_session)
    assert repository.search(axis(256), limit=0, embedding_space=space) == []
    assert repository.search(axis(256), limit=-1, embedding_space=space) == []
    assert repository.search([0.0] * 256, embedding_space=space) == []
    assert len(repository.search(axis(256), limit=1, embedding_space=space)) == 1
    assert len(repository.search(axis(256), limit=1000, embedding_space=space)) == 100


def test_backfill_only_labels_legacy_rows_and_all_commits_remain_rollback_only(isolated_database):
    # A savepoint bounds even the script's explicit session.commit() calls.
    connection = isolated_database.connection
    savepoint = connection.begin_nested()
    factory = sessionmaker(bind=connection, expire_on_commit=False,
                           join_transaction_mode="create_savepoint")
    try:
        with factory() as session:
            legacy = add(session, "legacy cue", [9.0, 8.0], None)
            labelled = add(session, "already labelled", [1.0, 0.0], "keep:2")
            legacy_id, labelled_id = legacy.id, labelled.id
            session.commit()

        class OfflineEmbedder:
            def __init__(self):
                self.calls = []

            def embed_with_metadata(self, value, input_type):
                self.calls.append((value, input_type))
                return EmbeddedText(np.array(axis(256)), "hashed:crc32-logtf-v1:256")

        embedder = OfflineEmbedder()
        assert backfill(factory=factory, embedder=embedder, batch_size=1) == 1
        assert backfill(factory=factory, embedder=embedder, batch_size=1) == 0
        assert embedder.calls == [("legacy cue", "document")]
        with factory() as session:
            legacy = session.get(ServiceObservation, legacy_id)
            labelled = session.get(ServiceObservation, labelled_id)
            assert legacy.embedding == axis(256)
            assert legacy.embedding_space == "hashed:crc32-logtf-v1:256"
            assert labelled.embedding == [1.0, 0.0]
            assert labelled.embedding_space == "keep:2"
            results = MemoryRepository(session).search(axis(256), embedding_space=legacy.embedding_space)
            assert [row.id for row, _ in results] == [legacy_id]
    finally:
        savepoint.rollback()