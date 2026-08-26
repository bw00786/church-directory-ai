"""Production memory and retrieval: text embeddings.

Uses Voyage AI's `voyage-4-large` -- Anthropic does not offer its own
embeddings API and officially recommends Voyage AI (see
https://platform.claude.com/docs/en/build-with-claude/embeddings) --
for the highest-quality retrieval available: their best general-purpose,
multilingual model.

Falls back to a deterministic local hashed bag-of-words embedding (the
"hashing trick" + log-term-frequency weighting -- a real classic
information-retrieval technique, not a placeholder) if `VOYAGE_API_KEY` isn't
configured or a call to the Voyage API fails, so production memory keeps
working -- with degraded retrieval quality -- even without network/API
access. `MemoryRepository.cosine_similarity` scores mismatched-dimension
vectors as 0.0 rather than raising, so a mix of hashed (256-dim) and Voyage
(1024-dim) embeddings in the same table is safe; hashed rows simply won't
surface once Voyage is configured, until re-recorded.
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
    """Deterministic hashed bag-of-words embedding -- fallback only."""

    dimension = _HASH_DIM

    def embed(self, text: str, input_type: str = "document") -> np.ndarray:
        vector = np.zeros(_HASH_DIM, dtype=float)
        for token in _tokenize(text):
            vector[_bucket(token)] += 1.0
        vector = np.log1p(vector)
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 0 else vector


class VoyageTextEmbedder:
    """Highest-quality embeddings via Voyage AI, with a safe local fallback."""

    def __init__(self) -> None:
        self._client = None
        self._fallback = _HashedTextEmbedder()

    def _get_client(self):
        if self._client is None:
            import voyageai  # imported lazily so the fallback path has no hard dependency

            self._client = voyageai.Client(api_key=settings.voyage_api_key)
        return self._client

    def embed(self, text: str, input_type: str = "document") -> np.ndarray:
        """input_type: "document" when storing an observation, "query" when
        searching -- Voyage prepends a different instruction prefix for each,
        which measurably improves retrieval quality."""
        if not settings.voyage_api_key:
            return self._fallback.embed(text, input_type=input_type)
        try:
            result = self._get_client().embed(
                [text], model=settings.voyage_embedding_model, input_type=input_type
            )
            return np.array(result.embeddings[0], dtype=float)
        except Exception:
            logger.warning("Voyage embedding failed; falling back to local embedder", exc_info=True)
            return self._fallback.embed(text, input_type=input_type)


text_embedder = VoyageTextEmbedder()
