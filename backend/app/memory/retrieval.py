"""MemoryManager.search delegates to model-isolated pgvector cosine queries.

Dimension-specific HNSW expression indexes accelerate the retained float arrays.
No full-text or hybrid lexical search is implemented.
"""
