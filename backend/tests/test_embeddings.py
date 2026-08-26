"""Tests for TextEmbedder: Voyage -> nomic -> hashed tiered embeddings."""

import numpy as np
import pytest

from app.config import settings
from app.memory.embeddings import (
    TextEmbedder,
    _HashedTextEmbedder,
    _NomicTextEmbedder,
    _VoyageTextEmbedder,
)


class _FakeVoyageResult:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _FakeVoyageClient:
    def __init__(self, embeddings=None, error=None):
        self._embeddings = embeddings or [[0.1, 0.2, 0.3]]
        self._error = error
        self.calls = []

    def embed(self, texts, model, input_type):
        self.calls.append({"texts": texts, "model": model, "input_type": input_type})
        if self._error:
            raise self._error
        return _FakeVoyageResult(self._embeddings)


class _FakeNomicModel:
    def __init__(self, vector=None, error=None):
        self._vector = vector if vector is not None else [0.4, 0.5, 0.6]
        self._error = error
        self.calls = []

    def encode(self, text, normalize_embeddings=True):
        self.calls.append(text)
        if self._error:
            raise self._error
        return np.array(self._vector)


@pytest.fixture(autouse=True)
def _reset_provider(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "auto")
    monkeypatch.setattr(settings, "voyage_api_key", "")
    _NomicTextEmbedder._model = None
    yield
    _NomicTextEmbedder._model = None


# -- Voyage tier --------------------------------------------------------------


def test_voyage_embedder_raises_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "voyage_api_key", "")

    with pytest.raises(RuntimeError):
        _VoyageTextEmbedder().embed("some text")


def test_voyage_embedder_uses_configured_client(monkeypatch):
    monkeypatch.setattr(settings, "voyage_api_key", "test-key")
    monkeypatch.setattr(settings, "voyage_embedding_model", "voyage-4-large")

    embedder = _VoyageTextEmbedder()
    fake_client = _FakeVoyageClient(embeddings=[[1.0, 2.0, 3.0]])
    embedder._client = fake_client

    vector = embedder.embed("hello world", input_type="document")

    assert np.array_equal(vector, np.array([1.0, 2.0, 3.0]))
    assert fake_client.calls[0]["input_type"] == "document"
    assert fake_client.calls[0]["model"] == "voyage-4-large"


# -- Nomic tier ---------------------------------------------------------------


def test_nomic_embedder_prefixes_document_and_query(monkeypatch):
    embedder = _NomicTextEmbedder()
    fake_model = _FakeNomicModel()
    _NomicTextEmbedder._model = fake_model

    embedder.embed("a document", input_type="document")
    embedder.embed("a query", input_type="query")

    assert fake_model.calls == ["search_document: a document", "search_query: a query"]


def test_nomic_embedder_raises_on_model_error(monkeypatch):
    embedder = _NomicTextEmbedder()
    _NomicTextEmbedder._model = _FakeNomicModel(error=RuntimeError("model load failed"))

    with pytest.raises(RuntimeError):
        embedder.embed("some text")


# -- TextEmbedder tiering/fallthrough ------------------------------------------


def test_auto_uses_voyage_when_api_key_set(monkeypatch):
    monkeypatch.setattr(settings, "voyage_api_key", "test-key")

    embedder = TextEmbedder()
    embedder._voyage._client = _FakeVoyageClient(embeddings=[[9.0, 9.0]])
    embedder._nomic._model = _FakeNomicModel()  # should not be called

    vector = embedder.embed("hello")

    assert np.array_equal(vector, np.array([9.0, 9.0]))


def test_auto_falls_back_to_nomic_when_no_voyage_key(monkeypatch):
    monkeypatch.setattr(settings, "voyage_api_key", "")

    embedder = TextEmbedder()
    _NomicTextEmbedder._model = _FakeNomicModel(vector=[7.0, 7.0])

    vector = embedder.embed("hello")

    assert np.array_equal(vector, np.array([7.0, 7.0]))


def test_auto_falls_back_to_hashed_when_voyage_and_nomic_both_fail(monkeypatch):
    monkeypatch.setattr(settings, "voyage_api_key", "test-key")

    embedder = TextEmbedder()
    embedder._voyage._client = _FakeVoyageClient(error=RuntimeError("voyage down"))
    _NomicTextEmbedder._model = _FakeNomicModel(error=RuntimeError("nomic down"))

    vector = embedder.embed("hello")

    assert vector.shape == (_HashedTextEmbedder.dimension,)


def test_provider_can_be_forced_to_hashed(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "hashed")
    monkeypatch.setattr(settings, "voyage_api_key", "test-key")

    embedder = TextEmbedder()
    embedder._voyage._client = _FakeVoyageClient()  # should not be called

    vector = embedder.embed("hello")

    assert vector.shape == (_HashedTextEmbedder.dimension,)


def test_provider_can_be_forced_to_nomic(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "nomic")
    monkeypatch.setattr(settings, "voyage_api_key", "test-key")

    embedder = TextEmbedder()
    embedder._voyage._client = _FakeVoyageClient()  # should not be called
    _NomicTextEmbedder._model = _FakeNomicModel(vector=[3.0, 3.0])

    vector = embedder.embed("hello")

    assert np.array_equal(vector, np.array([3.0, 3.0]))
