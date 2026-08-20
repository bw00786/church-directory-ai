"""Agents module initialization."""

from .prompts import DIRECTOR_SYSTEM_PROMPT
from .state import ProductionState

try:  # langchain-anthropic is optional at import time
    from .llm import build_llm, check_anthropic_connection, get_fast_llm, get_llm
except Exception:  # pragma: no cover - missing optional dependency
    build_llm = None
    get_llm = None
    get_fast_llm = None
    check_anthropic_connection = None

__all__ = [
    "ProductionState",
    "DIRECTOR_SYSTEM_PROMPT",
    "build_llm",
    "get_llm",
    "get_fast_llm",
    "check_anthropic_connection",
]
