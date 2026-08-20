"""Semantic and lexical retrieval.

Implemented as `MemoryManager.search()` in production_memory.py, backed by
`MemoryRepository.search()` (brute-force cosine similarity over hashed
bag-of-words embeddings; see embeddings.py). No pgvector/full-text index yet
-- fine at church-service data volumes, not built to scale to a large corpus.
"""
