"""Production memory and retrieval: text embeddings.

Three tiers, in quality order, each falling through to the next on missing
config or a runtime error so production memory never stops working:

1. Voyage AI's `voyage-4-large` (`VOYAGE_API_KEY` set) -- highest-quality,
   paid API. Anthropic doesn't offer its own embeddings API and officially
   recommends Voyage AI (see
   https://platform.claude.com/docs/en/build-with-claude/embeddings).
2. `nomic-embed-text-v1.5` (Hugging Face, via sentence-transformers) --
   free, runs locally, no API key or network call required. Competitive
   retrieval quality with Voyage on English text at $0 marginal cost.
3. A deterministic local hashed bag-of-words embedding (the "hashing trick"
   + log-term-frequency weighting -- a real classic information-retrieval
   technique, not a placeholder) -- last resort, no ML dependency at all.

`embedding_provider` config ("auto" | "voyage" | "nomic" | "hashed") selects
the tier; "auto" (default) uses the best one available.

Production-memory embeddings carry the actual model/tier identity. Retrieval
filters by both identity and dimension; incompatible or unlabelled legacy
rows cannot appear in results. Re-embed legacy text with the migration CLI.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

import numpy as np

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_HASH_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _bucket(token: str) -> int:
    # crc32 (not Python's str hash) so the mapping is stable across runs/processes.
    return zlib.crc32(token.encode("utf-8")) % _HASH_DIM


class _HashedTextEmbedder:
    """Deterministic hashed bag-of-words embedding -- last-resort tier, never fails."""

    dimension = _HASH_DIM

    def embed(self, text: str, input_type: str = "document") -> np.ndarray:
        vector = np.zeros(_HASH_DIM, dtype=float)
        for token in _tokenize(text):
            vector[_bucket(token)] += 1.0
        vector = np.log1p(vector)
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 0 else vector


class _VoyageTextEmbedder:
    """Voyage AI embeddings. Raises if no API key or the call fails --
    tiering/fallback is the caller's (TextEmbedder's) responsibility."""

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            import voyageai  # imported lazily so other tiers have no hard dependency on it

            self._client = voyageai.Client(api_key=settings.voyage_api_key)
        return self._client

    def embed(self, text: str, input_type: str = "document") -> np.ndarray:
        """input_type: "document" when storing an observation, "query" when
        searching -- Voyage prepends a different instruction prefix for each,
        which measurably improves retrieval quality."""
        if not settings.voyage_api_key:
            raise RuntimeError("VOYAGE_API_KEY is not set")
        result = self._get_client().embed(
            [text], model=settings.voyage_embedding_model, input_type=input_type
        )
        return np.array(result.embeddings[0], dtype=float)


class _NomicTextEmbedder:
    """Locally-run nomic-embed-text-v1.5 (Hugging Face) via sentence-transformers.

    Free (no API key/network call), competitive retrieval quality. Loads the
    model (~550MB) once per process and caches it at class scope since
    loading is expensive; every subsequent embed() call is a local CPU/GPU
    forward pass. Uses `trust_remote_code=True` (this model ships custom
    modeling code) -- pin NOMIC_MODEL_REVISION to a specific commit in
    production to avoid trusting a moving target.

    Raises if sentence-transformers/the model can't be loaded or inference
    fails -- tiering/fallback is the caller's (TextEmbedder's) responsibility.
    """

    _model = None  # shared across instances: the (large) model loads once per process

    def _get_model(self):
        if _NomicTextEmbedder._model is None:
            from sentence_transformers import SentenceTransformer

            _NomicTextEmbedder._model = SentenceTransformer(
                settings.nomic_model_name,
                trust_remote_code=True,
                revision=settings.nomic_model_revision or None,
            )
        return _NomicTextEmbedder._model

    def embed(self, text: str, input_type: str = "document") -> np.ndarray:
        """input_type: nomic-embed-text-v1.5 requires a task-instruction
        prefix -- "search_document: " when storing, "search_query: " when
        searching -- this is how the model was trained/expects to be used."""
        prefix = "search_query: " if input_type == "query" else "search_document: "
        vector = self._get_model().encode(prefix + text, normalize_embeddings=True)
        return np.asarray(vector, dtype=float)


@dataclass(frozen=True)
class EmbeddedText:
    vector: np.ndarray
    space: str


class TextEmbedder:
    """Picks the best available embedding tier per `embedding_provider`."""

    def __init__(self) -> None:
        self._voyage = _VoyageTextEmbedder()
        self._nomic = _NomicTextEmbedder()
        self._hashed = _HashedTextEmbedder()

    def embed(self, text: str, input_type: str = "document") -> np.ndarray:
        return self.embed_with_metadata(text, input_type).vector

    def embed_with_metadata(self, text: str, input_type: str = "document") -> EmbeddedText:
        provider = settings.embedding_provider
        voyage = (self._voyage, f"voyage:{settings.voyage_embedding_model}")
        nomic = (self._nomic, f"nomic:{settings.nomic_model_name}@{settings.nomic_model_revision or 'unpinned'}")
        tiers = {"hashed": [], "voyage": [voyage], "nomic": [nomic],
                 "auto": ([voyage] if settings.voyage_api_key else []) + [nomic]}
        if provider not in tiers:
            raise ValueError(f"Unknown embedding provider: {provider}")
        for embedder, space in tiers[provider]:
            vector = self._embed_or_none(embedder, text, input_type)
            if vector is not None:
                return EmbeddedText(vector, f"{space}:{vector.size}")
        vector = self._hashed.embed(text, input_type=input_type)
        return EmbeddedText(vector, "hashed:crc32-logtf-v1:256")

    @staticmethod
    def _embed_or_none(embedder, text: str, input_type: str) -> np.ndarray | None:
        try:
            vector = np.asarray(embedder.embed(text, input_type=input_type), dtype=float)
            if vector.ndim != 1 or not vector.size or not np.isfinite(vector).all():
                raise ValueError("Invalid embedding response")
            return vector
        except Exception:
            logger.warning("%s embedding failed; falling back", type(embedder).__name__, exc_info=True)
            return None


text_embedder = TextEmbedder()
