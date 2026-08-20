"""Production memory and retrieval: deterministic bag-of-words embeddings.

No external embedding model or API key is required. Tokens are hashed into a
fixed-size vector (the "hashing trick") with log-term-frequency weighting --
a real, classic information-retrieval technique (TF weighting predates neural
embeddings), not a placeholder. It's far less discriminating than a trained
sentence-embedding model, but it is deterministic, dependency-free, and good
enough for retrieving "what happened in past services" over a modest amount
of church production history.
"""

from __future__ import annotations

import re
import zlib

import numpy as np

_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _bucket(token: str) -> int:
    # crc32 (not Python's str hash) so the mapping is stable across runs/processes.
    return zlib.crc32(token.encode("utf-8")) % _DIM


class TextEmbedder:
    """Deterministic hashed bag-of-words embedding for free-text observations."""

    dimension = _DIM

    def embed(self, text: str) -> np.ndarray:
        vector = np.zeros(_DIM, dtype=float)
        for token in _tokenize(text):
            vector[_bucket(token)] += 1.0
        vector = np.log1p(vector)
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 0 else vector


text_embedder = TextEmbedder()
