"""Shared Ollama clients; models propose results, never bypass execution policy."""

import asyncio
from functools import lru_cache
from typing import Optional
from urllib.parse import urlsplit

import httpx
from langchain_ollama import ChatOllama

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


def build_llm(*, model: Optional[str] = None, temperature: Optional[float] = None,
              max_tokens: Optional[int] = None, json_mode: bool = False) -> ChatOllama:
    url = urlsplit(settings.ollama_base_url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("OLLAMA_BASE_URL must be an HTTP(S) server URL without credentials, query or fragment")
    selected = model if model is not None else settings.ollama_model
    if not selected.strip():
        raise ValueError("Configure a nonempty Ollama model tag")
    tokens = settings.llm_max_tokens if max_tokens is None else max_tokens
    if tokens <= 0:
        raise ValueError("max_tokens must be positive")
    return ChatOllama(
        model=selected, base_url=settings.ollama_base_url.rstrip('/'),
        temperature=settings.llm_temperature if temperature is None else temperature,
        num_predict=tokens, num_ctx=settings.ollama_num_ctx,
        reasoning=settings.ollama_reasoning, keep_alive=settings.ollama_keep_alive,
        format="json" if json_mode else None,
        client_kwargs={"timeout": settings.llm_timeout_seconds, "trust_env": False},
    )


@lru_cache(maxsize=1)
def get_llm() -> ChatOllama:
    return build_llm()


@lru_cache(maxsize=1)
def get_fast_llm() -> ChatOllama:
    return build_llm(model=settings.ollama_fast_model, json_mode=True)


@lru_cache(maxsize=1)
def get_director_llm() -> ChatOllama:
    return build_llm(json_mode=True)


@lru_cache(maxsize=1)
def get_vision_llm() -> ChatOllama:
    return build_llm(model=settings.ollama_vision_model, json_mode=True)


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


async def check_ollama_connection() -> dict:
    try:
        async with asyncio.timeout(settings.llm_timeout_seconds):
            llm = build_llm(max_tokens=32)
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds, trust_env=False) as client:
                response = await client.post(settings.ollama_base_url.rstrip('/') + '/api/show', json={"model": settings.ollama_model})
                response.raise_for_status()
                capabilities = response.json().get("capabilities", [])
            reply = response_text(await invoke_llm(llm, [("user", "Reply with just the word: pong")]))
            if not reply:
                raise ValueError("Ollama returned an empty response")
            return {"ok": True, "provider": "ollama", "model": settings.ollama_model,
                    "capabilities": capabilities, "reply": reply}
    except Exception as exc:
        logger.warning("Ollama connectivity check failed", error_type=type(exc).__name__)
        return {"ok": False, "provider": "ollama", "model": settings.ollama_model,
                "error": f"Ollama check failed ({type(exc).__name__}); check server, model tag and timeout"}
