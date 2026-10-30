"""Shared Ollama clients; models propose results, never bypass execution policy."""

import asyncio
import random
import time
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from httpx import TransportError
from langchain_ollama import ChatOllama

from app.agents.ollama_discovery import OLLAMA_DEFAULT_PORT, ollama_locator  # noqa: F401
from app.config import settings
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

# Director-path settings (see docs/ai-director.md). getattr keeps the module
# importable before config.py grows the fields; the defaults match config.
DIRECTOR_DEADLINE_DEFAULT = 3.0
DIRECTOR_MAX_TOKENS_DEFAULT = 320


def director_model() -> str:
    """AI_DIRECTOR_MODEL, else the fast model: the live director loop needs
    sub-deadline answers, not the biggest model."""
    configured = str(getattr(settings, "ai_director_model", "") or "").strip()
    return configured or settings.ollama_fast_model


def director_deadline_seconds() -> float:
    value = getattr(settings, "ai_director_deadline_seconds", DIRECTOR_DEADLINE_DEFAULT)
    try:
        return max(0.05, float(value))
    except (TypeError, ValueError):
        return DIRECTOR_DEADLINE_DEFAULT


def ollama_base_url() -> str:
    """OLLAMA_BASE_URL, else Ollama on the EasyWorship PC (auto-discovered on DHCP networks)."""
    return ollama_locator.base_url()


def build_llm(*, model: Optional[str] = None, temperature: Optional[float] = None,
              max_tokens: Optional[int] = None, json_mode: bool = False,
              json_schema: Optional[dict] = None) -> ChatOllama:
    """json_schema (Ollama structured outputs) takes precedence over json_mode."""
    base_url = ollama_base_url()
    url = urlsplit(base_url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("OLLAMA_BASE_URL must be an HTTP(S) server URL without credentials, query or fragment")
    selected = model if model is not None else settings.ollama_model
    if not selected.strip():
        raise ValueError("Configure a nonempty Ollama model tag")
    tokens = settings.llm_max_tokens if max_tokens is None else max_tokens
    if tokens <= 0:
        raise ValueError("max_tokens must be positive")
    return ChatOllama(
        model=selected, base_url=base_url,
        temperature=settings.llm_temperature if temperature is None else temperature,
        num_predict=tokens, num_ctx=settings.ollama_num_ctx,
        reasoning=settings.ollama_reasoning, keep_alive=settings.ollama_keep_alive,
        format=json_schema if json_schema else ("json" if json_mode else None),
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
    """Fast model, small output budget, schema-constrained JSON. The hard
    per-cycle deadline is applied by invoke_llm(timeout=...), not here: the
    httpx timeout stays the long LLM_TIMEOUT_SECONDS so a slow first token
    after model load is not misreported as a transport error."""
    from app.ai.decision import director_decision_json_schema

    max_tokens = int(getattr(settings, "ai_director_max_tokens", DIRECTOR_MAX_TOKENS_DEFAULT) or DIRECTOR_MAX_TOKENS_DEFAULT)
    return build_llm(model=director_model(), json_mode=True,
                     json_schema=director_decision_json_schema(),
                     max_tokens=min(max_tokens, settings.llm_max_tokens))


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


async def invoke_llm(llm, messages, *, timeout: Optional[float] = None):
    """Bounded inference. ``timeout`` overrides LLM_TIMEOUT_SECONDS (the
    director passes its per-cycle deadline; the assistant keeps the default)."""
    limit = settings.llm_timeout_seconds if timeout is None else timeout
    try:
        return await asyncio.wait_for(llm.ainvoke(messages), timeout=limit)
    except (TransportError, ConnectionError, asyncio.TimeoutError):
        # The EasyWorship PC may have a new DHCP address.
        ollama_locator.request_resolve()
        raise


class InferenceBackoff:
    """Exponential backoff with jitter for a hot inference loop.

    After a failure the caller should skip inference until ``retry_after``;
    consecutive failures double the wait (``base`` .. ``cap`` seconds). One
    ``<prefix>_DEGRADED`` event is published per outage and one
    ``<prefix>_RESTORED`` when a call succeeds again. Pure bookkeeping: it
    never calls Ollama and never blocks.
    """

    def __init__(self, *, prefix: str = "AI_DIRECTOR", base: float = 1.0, cap: float = 30.0,
                 jitter: float = 0.25, clock=time.monotonic, rng=random.random) -> None:
        self.prefix = prefix
        self.base = base
        self.cap = cap
        self.jitter = jitter
        self._clock = clock
        self._rng = rng
        self.consecutive_failures = 0
        self.retry_after: Optional[float] = None
        self.last_error: Optional[str] = None
        self.degraded = False

    def seconds_until_retry(self) -> float:
        """0.0 when inference may run now; otherwise the remaining wait."""
        if self.retry_after is None:
            return 0.0
        return max(0.0, self.retry_after - self._clock())

    def next_delay(self, failures: int) -> float:
        delay = min(self.cap, self.base * (2 ** max(0, failures - 1)))
        return min(self.cap, delay * (1.0 + self.jitter * self._rng()))

    def record_failure(self, reason: str) -> float:
        self.consecutive_failures += 1
        self.last_error = reason
        delay = self.next_delay(self.consecutive_failures)
        self.retry_after = self._clock() + delay
        if not self.degraded:
            self.degraded = True
            self._publish("DEGRADED", {"reason": reason,
                                       "consecutive_failures": self.consecutive_failures,
                                       "next_retry_seconds": round(delay, 2)})
        logger.warning("Inference failed; backing off", component=self.prefix, reason=reason,
                       consecutive_failures=self.consecutive_failures, next_retry_seconds=round(delay, 2))
        return delay

    def record_success(self) -> None:
        was_degraded = self.degraded
        failures = self.consecutive_failures
        self.consecutive_failures = 0
        self.retry_after = None
        self.degraded = False
        self.last_error = None
        if was_degraded:
            self._publish("RESTORED", {"failures_during_outage": failures})
            logger.info("Inference restored", component=self.prefix, failures_during_outage=failures)

    def status(self) -> dict[str, Any]:
        return {"degraded": self.degraded, "consecutive_failures": self.consecutive_failures,
                "last_error": self.last_error, "seconds_until_retry": round(self.seconds_until_retry(), 2)}

    def _publish(self, suffix: str, payload: dict[str, Any]) -> None:
        try:
            event_bus.publish({"event": f"{self.prefix}_{suffix}", "payload": payload})
        except Exception:  # pragma: no cover - the bus never raises today
            logger.warning("Backoff event publish failed", exc_info=True)


# Director-path only; the assistant/chat path and /health/ollama are unaffected.
director_backoff = InferenceBackoff(prefix="AI_DIRECTOR")


async def check_ollama_connection() -> dict:
    try:
        async with asyncio.timeout(settings.llm_timeout_seconds):
            llm = build_llm(max_tokens=32)
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds, trust_env=False) as client:
                response = await client.post(ollama_base_url() + '/api/show', json={"model": settings.ollama_model})
                response.raise_for_status()
                capabilities = response.json().get("capabilities", [])
            reply = response_text(await invoke_llm(llm, [("user", "Reply with just the word: pong")]))
            if not reply:
                raise ValueError("Ollama returned an empty response")
            return {"ok": True, "provider": "ollama", "model": settings.ollama_model, "base_url": ollama_base_url(),
                    "capabilities": capabilities, "reply": reply, "discovery": ollama_locator.status()}
    except Exception as exc:
        logger.warning("Ollama connectivity check failed", error_type=type(exc).__name__)
        ollama_locator.request_resolve()
        return {"ok": False, "provider": "ollama", "model": settings.ollama_model, "base_url": ollama_base_url(),
                "discovery": ollama_locator.status(),
                "error": f"Ollama check failed ({type(exc).__name__}); check server, model tag and timeout"}
