"""Anthropic Claude client factory for the AI director.

Centralizes construction of the LangChain ``ChatAnthropic`` model so every
agent/tool shares one configured, policy-agnostic LLM client. The LLM only
produces recommendations; execution still flows through the policy engine.
"""

from functools import lru_cache
from typing import Optional

from langchain_anthropic import ChatAnthropic

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


def build_llm(
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> ChatAnthropic:
    """Build a configured ``ChatAnthropic`` client.

    Args:
        model: Override the configured model id.
        temperature: Override the configured sampling temperature.
        max_tokens: Override the configured max output tokens.

    Returns:
        A ready-to-use ``ChatAnthropic`` instance.

    Raises:
        ValueError: If no Anthropic API key is configured.
    """
    if not settings.anthropic_api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set; cannot initialize the Claude client"
        )

    kwargs = {
        "model": model or settings.anthropic_model,
        "api_key": settings.anthropic_api_key,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "max_tokens": max_tokens or settings.llm_max_tokens,
        "timeout": settings.llm_timeout_seconds,
    }
    if settings.anthropic_base_url:
        kwargs["base_url"] = settings.anthropic_base_url

    logger.info("Initializing Anthropic Claude client", model=kwargs["model"])
    return ChatAnthropic(**kwargs)


@lru_cache(maxsize=1)
def get_llm() -> ChatAnthropic:
    """Return a process-wide cached Claude client."""
    return build_llm()


@lru_cache(maxsize=1)
def get_fast_llm() -> ChatAnthropic:
    """Return a process-wide cached Claude client using the fast/small model.

    Intended for quick classification tasks (e.g. cue-advance decisions) where
    low latency matters more than deep reasoning.
    """
    return build_llm(model=settings.anthropic_fast_model)


async def check_anthropic_connection() -> dict:
    """Make a minimal real API call to verify the Claude connection works.

    Returns a dict like {"ok": True, "model": "..."} or {"ok": False, "error": "..."}.
    Safe to call even when no API key is configured.
    """
    try:
        llm = build_llm(model=settings.anthropic_fast_model, max_tokens=8)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    try:
        response = await llm.ainvoke([("user", "Reply with just the word: pong")])
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = " ".join(str(part) for part in content)
        return {"ok": True, "model": settings.anthropic_fast_model, "reply": str(content).strip()}
    except Exception as e:
        logger.warning("Anthropic connectivity check failed", error=str(e))
        return {"ok": False, "error": str(e)}
