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

`MemoryRepository.cosine_similarity` scores mismatched-dimension vectors as
0.0 rather than raising, so mixing embeddings from different tiers in the
same table is safe -- older rows just won't surface against a query embedded
by a different tier, until re-recorded.
"""

from __future__ import annotations

import re
import zlib

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


class TextEmbedder:
    """Picks the best available embedding tier per `embedding_provider`."""

    def __init__(self) -> None:
        self._voyage = _VoyageTextEmbedder()
        self._nomic = _NomicTextEmbedder()
        self._hashed = _HashedTextEmbedder()

    def embed(self, text: str, input_type: str = "document") -> np.ndarray:
        provider = settings.embedding_provider

        if provider == "hashed":
            return self._hashed.embed(text, input_type=input_type)
        if provider == "voyage":
            vector = self._embed_or_none(self._voyage, text, input_type)
            return vector if vector is not None else self._hashed.embed(text, input_type=input_type)
        if provider == "nomic":
            vector = self._embed_or_none(self._nomic, text, input_type)
            return vector if vector is not None else self._hashed.embed(text, input_type=input_type)

        # "auto" (default): Voyage (if configured) -> nomic -> hashed.
        if settings.voyage_api_key:
            vector = self._embed_or_none(self._voyage, text, input_type)
            if vector is not None:
                return vector
        vector = self._embed_or_none(self._nomic, text, input_type)
        if vector is not None:
            return vector
        return self._hashed.embed(text, input_type=input_type)

    @staticmethod
    def _embed_or_none(embedder, text: str, input_type: str) -> np.ndarray | None:
        try:
            return embedder.embed(text, input_type=input_type)
        except Exception:
            logger.warning("%s embedding failed; falling back", type(embedder).__name__, exc_info=True)
            return None


text_embedder = TextEmbedder()
