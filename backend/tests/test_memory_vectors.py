"""Indexed RAG unit tests; no database, model downloads, or provider calls."""

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import text

from app.config import settings
from app.database import memory_vectors, migrations
from app.database.models import ServiceObservation
from app.database.repositories import MemoryRepository
from app.memory import production_memory
from app.memory.embeddings import EmbeddedText, TextEmbedder, _NomicTextEmbedder
from scripts.migrate_memory_vectors import backfill


@pytest.mark.parametrize("dimension", [1, 256, 768, 1024, 1536, 16000])
def test_search_sql_binds_data_and_only_interpolates_valid_dimension(dimension):
    statement = text(memory_vectors.search_sql(dimension))
    assert set(statement.compile().params) == {"query", "space", "limit"}
    sql = str(statement)
    assert f"cardinality(embedding) = {dimension}" in sql
    assert f"CAST(:query AS vector({dimension}))" in sql
    assert "embedding_space IS NOT NULL" in sql
    assert "ORDER BY embedding::vector" in sql
    assert "<=>" in sql


@pytest.mark.parametrize("dimension", [0, -1, 16001, 1.5, None, "256", "256); SELECT 1; --"])
def test_search_sql_rejects_invalid_dimensions(dimension):
    with pytest.raises(ValueError, match="dimension"):
        memory_vectors.search_sql(dimension)


def test_search_sql_rejects_boolean_dimension():
    # bool subclasses int, but vector(True) is not valid PostgreSQL syntax.
    with pytest.raises(ValueError, match="dimension"):
        memory_vectors.search_sql(True)


INVALID_VECTORS = [[], [1.0] * 16001, [float("nan")], [float("inf")],
                   [-float("inf")], [3.5e38], [-3.5e38]]


@pytest.mark.parametrize("vector", INVALID_VECTORS,
                         ids=["empty", "too-wide", "nan", "inf", "negative-inf", "overflow", "negative-overflow"])
def test_invalid_vectors_rejected_before_read_or_write(vector):
    session = Mock()
    with pytest.raises(ValueError):
        memory_vectors.validate_embedding(vector)
    with pytest.raises(ValueError):
        memory_vectors.search(session, vector, "test:space", 10)
    with pytest.raises(ValueError):
        MemoryRepository(session).add_observation("2026-09-14", "test", "test", vector)
    assert session.mock_calls == []


@pytest.mark.parametrize("vector", [[0.0], [0.0] * 256, [1.0] * 16000, [3.4028234e38]])
def test_valid_embedding_dimensions_and_zero_storage(vector):
    assert memory_vectors.validate_embedding(vector) == len(vector)


@pytest.mark.parametrize("vector,limit", [([0.0] * 256, 10), ([1.0], 0), ([1.0], -1)])
def test_zero_query_or_nonpositive_limit_does_not_execute_sql(vector, limit):
    session = Mock()
    assert memory_vectors.search(session, vector, "test:space", limit) == []
    assert session.mock_calls == []


@pytest.mark.parametrize("space", ["", None])
def test_query_requires_embedding_space(space):
    session = Mock()
    with pytest.raises(ValueError, match="space"):
        memory_vectors.search(session, [1.0], space, 10)
    assert session.mock_calls == []


@pytest.mark.parametrize("limit,expected", [(1, 1), (10, 10), (1000, 100)])
def test_search_parameterization_limit_and_database_order(limit, expected):
    session = Mock()
    first, second = SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())
    session.execute.return_value.all.return_value = [
        SimpleNamespace(id=first.id, similarity=0.95),
        SimpleNamespace(id=second.id, similarity=0.25),
    ]
    # ORM fetch order must not replace the similarity ranking from PostgreSQL.
    session.scalars.return_value = [second, first]
    hostile_space = "voyage:' OR TRUE; --:256"
    assert memory_vectors.search(session, [1.0, 0.0], hostile_space, limit) == [
        (first, 0.95), (second, 0.25),
    ]
    setup, query = session.execute.call_args_list
    assert str(setup.args[0]) == "SET LOCAL hnsw.iterative_scan = 'strict_order'"
    assert str(query.args[0]) == memory_vectors.search_sql(2)
    assert hostile_space not in str(query.args[0])
    assert query.args[1] == {"query": "[1.0,0.0]", "space": hostile_space, "limit": expected}
    fetch = session.scalars.call_args.args[0].compile()
    assert list(fetch.params.values()) == [[first.id, second.id]]


def test_empty_sql_result_skips_orm_fetch():
    session = Mock()
    session.execute.return_value.all.return_value = []
    assert memory_vectors.search(session, [1.0], "test:space", 10) == []
    session.scalars.assert_not_called()


def test_repository_preserves_array_and_embedding_space():
    session = Mock()
    vector = [0.0, 1.0, -0.5]
    observation = MemoryRepository(session).add_observation(
        "2026-09-14", "cue", "Advance", vector, embedding_space="test:model:3",
    )
    assert observation.embedding == vector
    assert observation.embedding_space == "test:model:3"
    session.add.assert_called_once_with(observation)
    session.flush.assert_called_once_with()


def test_repository_delegates_indexed_search(monkeypatch):
    indexed = Mock(return_value=[])
    monkeypatch.setattr(memory_vectors, "search", indexed)
    session = Mock()
    assert MemoryRepository(session).search([1.0], limit=3, embedding_space="model:1") == []
    indexed.assert_called_once_with(session, [1.0], "model:1", 3)


@pytest.mark.parametrize("role", ["document", "query"])
def test_actual_fallback_space_identity_with_same_dimension_providers(monkeypatch, role):
    monkeypatch.setattr(settings, "embedding_provider", "auto")
    monkeypatch.setattr(settings, "voyage_api_key", "unit-test-not-a-real-key")
    monkeypatch.setattr(settings, "voyage_embedding_model", "unit-voyage")
    monkeypatch.setattr(settings, "nomic_model_name", "unit-nomic")
    monkeypatch.setattr(settings, "nomic_model_revision", "revision-123")
    vector = [1.0] + [0.0] * 255
    voyage_client = Mock()
    voyage_client.embed.return_value = SimpleNamespace(embeddings=[vector])
    nomic_model = Mock()
    nomic_model.encode.return_value = np.array(vector)
    monkeypatch.setattr(_NomicTextEmbedder, "_model", nomic_model)
    embedder = TextEmbedder()
    embedder._voyage._client = voyage_client

    voyage = embedder.embed_with_metadata("Sunday cue", input_type=role)
    nomic_model.encode.assert_not_called()
    voyage_client.embed.side_effect = RuntimeError("offline test")
    nomic = embedder.embed_with_metadata("Sunday cue", input_type=role)
    nomic_model.encode.side_effect = RuntimeError("offline test")
    hashed = embedder.embed_with_metadata("Sunday cue", input_type=role)

    assert voyage.space == "voyage:unit-voyage:256"
    assert nomic.space == "nomic:unit-nomic@revision-123:256"
    assert hashed.space == "hashed:crc32-logtf-v1:256"
    assert {result.vector.size for result in [voyage, nomic, hashed]} == {256}
    assert len({result.space for result in [voyage, nomic, hashed]}) == 3
    voyage_client.embed.assert_called_with(["Sunday cue"], model="unit-voyage", input_type=role)
    prefix = "search_query: " if role == "query" else "search_document: "
    nomic_model.encode.assert_called_with(prefix + "Sunday cue", normalize_embeddings=True)
    assert np.isfinite(hashed.vector).all()
    assert np.linalg.norm(hashed.vector) == pytest.approx(1.0)


@pytest.mark.parametrize("invalid", [[], [[1.0]], [float("nan")], [float("inf")]])
def test_invalid_provider_output_gets_actual_fallback_identity(monkeypatch, invalid):
    monkeypatch.setattr(settings, "embedding_provider", "voyage")
    monkeypatch.setattr(settings, "voyage_api_key", "unit-test-not-a-real-key")
    embedder = TextEmbedder()
    embedder._voyage._client = Mock()
    embedder._voyage._client.embed.return_value = SimpleNamespace(embeddings=[invalid])
    result = embedder.embed_with_metadata("cue advance")
    assert result.space == "hashed:crc32-logtf-v1:256"
    assert result.vector.size == 256


@pytest.fixture
def manager_dependencies(monkeypatch):
    session = Mock()
    repository = Mock()
    embedder = Mock(spec=["embed_with_metadata"])
    embedded = EmbeddedText(np.array([0.25, 0.75]), "actual-fallback:model:2")
    embedder.embed_with_metadata.return_value = embedded
    monkeypatch.setattr(production_memory, "text_embedder", embedder)
    monkeypatch.setattr(production_memory, "get_session", lambda: nullcontext(session))
    factory = Mock(return_value=repository)
    monkeypatch.setattr(production_memory, "MemoryRepository", factory)
    observation = ServiceObservation(
        id=uuid4(), occurred_at=datetime(2026, 9, 14, 10), service_date="2026-09-14",
        category="cue", source="test", text="Advance", embedding=embedded.vector.tolist(),
        embedding_space=embedded.space,
    )
    repository.add_observation.return_value = observation
    repository.search.return_value = [(observation, 0.9)]
    return embedder, repository, factory, session, observation


def test_new_memory_manager_records_document_with_actual_metadata(manager_dependencies):
    embedder, repository, factory, session, observation = manager_dependencies
    result = production_memory.MemoryManager().record_observation(
        "cue", "Advance", source="test", occurred_at=observation.occurred_at,
    )
    embedder.embed_with_metadata.assert_called_once_with("Advance", input_type="document")
    factory.assert_called_once_with(session)
    repository.add_observation.assert_called_once_with(
        service_date="2026-09-14", category="cue", text="Advance", embedding=[0.25, 0.75],
        embedding_space="actual-fallback:model:2", source="test", occurred_at=observation.occurred_at,
    )
    assert result["embedding_space"] == "actual-fallback:model:2"
    assert result["id"] == str(observation.id)


def test_new_memory_manager_searches_query_with_actual_metadata(manager_dependencies):
    embedder, repository, factory, session, observation = manager_dependencies
    result = production_memory.MemoryManager().search("Find cue", limit=4)
    embedder.embed_with_metadata.assert_called_once_with("Find cue", input_type="query")
    factory.assert_called_once_with(session)
    repository.search.assert_called_once_with(
        [0.25, 0.75], limit=4, embedding_space="actual-fallback:model:2",
    )
    assert result[0]["similarity"] == 0.9
    assert result[0]["embedding_space"] == observation.embedding_space
    assert result[0]["id"] == str(observation.id)


def test_migration_builds_partial_cosine_hnsw_indexes_and_bound_version():
    connection = Mock()
    connection.execute.return_value.scalar_one.return_value = "0.8.6"
    engine = Mock()
    engine.begin.return_value = nullcontext(connection)
    # Prevent even metadata inspection from making a real connection.
    from unittest.mock import patch
    with patch.object(migrations.Base.metadata, "create_all") as create_all:
        assert migrations.migrate(engine) == migrations.VERSION
    create_all.assert_called_once_with(connection)
    sql = [str(call.args[0]) for call in connection.execute.call_args_list]
    indexes = [statement for statement in sql if "USING hnsw" in statement]
    assert len(indexes) == len(memory_vectors.INDEXED_DIMENSIONS)
    for dimension, statement in zip(memory_vectors.INDEXED_DIMENSIONS, indexes):
        assert f"memory_embedding_hnsw_{dimension}" in statement
        assert f"embedding::vector({dimension})) vector_cosine_ops" in statement
        assert f"embedding_space IS NOT NULL AND cardinality(embedding) = {dimension}" in statement
    assert not any("DROP " in statement or "TRUNCATE " in statement for statement in sql)
    version_call = connection.execute.call_args_list[-1]
    assert ":version" in str(version_call.args[0])
    assert version_call.args[1] == {"version": migrations.VERSION}


def test_migration_rejects_old_pgvector_before_schema_changes():
    connection = Mock()
    connection.execute.return_value.scalar_one.return_value = "0.7.4"
    engine = Mock()
    engine.begin.return_value = nullcontext(connection)
    with pytest.raises(RuntimeError, match="0.8.0"):
        migrations.migrate(engine)
    sql = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert not any("ALTER TABLE" in statement or "CREATE INDEX" in statement for statement in sql)


def test_backfill_reembeds_legacy_text_in_batches_with_document_metadata():
    rows = [SimpleNamespace(text="Cue one", embedding=[9.0], embedding_space=None),
            SimpleNamespace(text="Cue two", embedding=[8.0], embedding_space=None)]
    session = Mock()
    session.scalars.side_effect = [[rows[0]], [rows[1]], []]
    factory = Mock(side_effect=lambda: nullcontext(session))
    embedder = Mock(spec=["embed_with_metadata"])
    embedder.embed_with_metadata.side_effect = [
        EmbeddedText(np.array([1.0, 0.0]), "voyage:test:2"),
        EmbeddedText(np.array([0.0, 1.0]), "nomic:test:2"),
    ]
    assert backfill(factory=factory, embedder=embedder, batch_size=1) == 2
    assert session.commit.call_count == 2
    assert [row.embedding_space for row in rows] == ["voyage:test:2", "nomic:test:2"]
    assert [row.embedding for row in rows] == [[1.0, 0.0], [0.0, 1.0]]
    assert [call.kwargs for call in embedder.embed_with_metadata.call_args_list] == [
        {"input_type": "document"}, {"input_type": "document"},
    ]
    for call in session.scalars.call_args_list:
        compiled = call.args[0].compile()
        assert "embedding_space IS NULL" in str(compiled)
        assert "ORDER BY memory_service_observations.id" in str(compiled)
        assert 1 in compiled.params.values()


def test_backfill_invalid_vector_never_commits():
    row = SimpleNamespace(text="Legacy cue", embedding=[9.0], embedding_space=None)
    session = Mock()
    session.scalars.return_value = [row]
    embedder = Mock()
    embedder.embed_with_metadata.return_value = EmbeddedText(np.array([np.nan]), "bad:1")
    with pytest.raises(ValueError):
        backfill(factory=lambda: nullcontext(session), embedder=embedder)
    session.commit.assert_not_called()
    assert row.embedding == [9.0]
    assert row.embedding_space is None