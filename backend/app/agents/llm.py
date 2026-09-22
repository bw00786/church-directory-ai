"""Shared Anthropic (Claude) clients; models propose results, never bypass execution policy."""

import asyncio
from functools import lru_cache
from typing import Optional

from langchain_anthropic import ChatAnthropic

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


def build_llm(*, model: Optional[str] = None, temperature: Optional[float] = None,
              max_tokens: Optional[int] = None, json_mode: bool = False) -> ChatAnthropic:
    # json_mode is retained only for call-site compatibility: Claude has no
    # JSON toggle, so callers keep prompting for JSON and regex-parse replies.
    if not settings.anthropic_api_key.strip():
        raise ValueError("ANTHROPIC_API_KEY is not configured; set it in backend/.env")
    selected = model if model is not None else settings.anthropic_model
    if not selected.strip():
        raise ValueError("Configure a nonempty Anthropic model name")
    tokens = settings.llm_max_tokens if max_tokens is None else max_tokens
    if tokens <= 0:
        raise ValueError("max_tokens must be positive")
    return ChatAnthropic(
        model=selected, api_key=settings.anthropic_api_key,
        temperature=settings.llm_temperature if temperature is None else temperature,
        max_tokens=tokens, timeout=settings.llm_timeout_seconds,
    )


@lru_cache(maxsize=1)
def get_llm() -> ChatAnthropic:
    return build_llm()


@lru_cache(maxsize=1)
def get_fast_llm() -> ChatAnthropic:
    return build_llm(model=settings.anthropic_fast_model, json_mode=True)


@lru_cache(maxsize=1)
def get_director_llm() -> ChatAnthropic:
    return build_llm(json_mode=True)


@lru_cache(maxsize=1)
def get_vision_llm() -> ChatAnthropic:
    return build_llm(model=settings.anthropic_vision_model, json_mode=True)


def response_text(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(part if isinstance(part, str) else part.get("text", "")
                        for part in content if isinstance(part, str) or
                        isinstance(part, dict) and part.get("type") == "text").strip()
    return ""


async def invoke_llm(llm, messages):
    return await asyncio.wait_for(llm.ainvoke(messages), timeout=settings.llm_timeout_seconds)


async def check_anthropic_connection() -> dict:
    """Minimal real Claude API call; errors are sanitized (type names only)."""
    try:
        async with asyncio.timeout(settings.llm_timeout_seconds):
            llm = build_llm(max_tokens=32)
            reply = response_text(await invoke_llm(llm, [("user", "Reply with just the word: pong")]))
            if not reply:
                raise ValueError("Claude returned an empty response")
            return {"ok": True, "provider": "anthropic", "model": settings.anthropic_model,
                    "reply": reply}
    except Exception as exc:
        logger.warning("Claude connectivity check failed", error_type=type(exc).__name__)
        return {"ok": False, "provider": "anthropic", "model": settings.anthropic_model,
                "error": f"Claude check failed ({type(exc).__name__}); check API key, model name and timeout"}
