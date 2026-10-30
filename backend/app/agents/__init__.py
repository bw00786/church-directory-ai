"""Agents module initialization."""

try:  # Ollama is optional for non-LLM package imports.
    from .llm import build_llm, check_ollama_connection, get_fast_llm, get_llm
except Exception:  # pragma: no cover - missing optional dependency
    build_llm = None
    get_llm = None
    get_fast_llm = None
    check_ollama_connection = None

__all__ = [
    "build_llm",
    "get_llm",
    "get_fast_llm",
    "check_ollama_connection",
]
