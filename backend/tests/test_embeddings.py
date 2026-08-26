"""Tests for VoyageTextEmbedder: Voyage AI embeddings with safe local fallback."""

import numpy as np
import pytest

from app.config import settings
from app.memory.embeddings import VoyageTextEmbedder, _HashedTextEmbedder


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


def test_embed_uses_hashed_fallback_when_no_api_key(monkeypatch):
    monkeypatch.setattr(settings, "voyage_api_key", "")

    embedder = VoyageTextEmbedder()
    vector = embedder.embed("some text")

    assert vector.shape == (_HashedTextEmbedder.dimension,)


def test_embed_uses_voyage_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "voyage_api_key", "test-key")
    monkeypatch.setattr(settings, "voyage_embedding_model", "voyage-4-large")

    embedder = VoyageTextEmbedder()
    fake_client = _FakeVoyageClient(embeddings=[[1.0, 2.0, 3.0]])
    embedder._client = fake_client

    vector = embedder.embed("hello world", input_type="document")

    assert np.array_equal(vector, np.array([1.0, 2.0, 3.0]))
    assert fake_client.calls[0]["input_type"] == "document"
    assert fake_client.calls[0]["model"] == "voyage-4-large"


def test_embed_falls_back_to_hashed_on_voyage_error(monkeypatch):
    monkeypatch.setattr(settings, "voyage_api_key", "test-key")

    embedder = VoyageTextEmbedder()
    embedder._client = _FakeVoyageClient(error=RuntimeError("API unavailable"))

    vector = embedder.embed("some text")

    assert vector.shape == (_HashedTextEmbedder.dimension,)


def test_query_and_document_input_types_are_passed_through(monkeypatch):
    monkeypatch.setattr(settings, "voyage_api_key", "test-key")

    embedder = VoyageTextEmbedder()
    fake_client = _FakeVoyageClient()
    embedder._client = fake_client

    embedder.embed("a query", input_type="query")
    embedder.embed("a document", input_type="document")

    assert [call["input_type"] for call in fake_client.calls] == ["query", "document"]
