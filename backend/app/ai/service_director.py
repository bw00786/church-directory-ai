"""AI Service Director: turns a ServiceContext into a structured decision.

Uses local Ollama (app.agents.llm) on the *fast* model with a hard per-cycle
deadline (AI_DIRECTOR_DEADLINE_SECONDS). Unavailable inference, a missed
deadline, or invalid output all fall back to a no-action, zero-confidence
"continue" decision that still carries the cycle id, context version and
latency so the runtime can log every cycle.

Retrieval-augmented: each cycle also pulls structured production memory
(app.memory.production_memory: similar past moments filtered by service state
/ speaker, plus a cheap house-style summary of the operator's own behaviour)
and renders it into the prompt as advisory context. None of this bypasses the
policy engine -- it only informs the DirectorDecision, which is gated like
any other.
"""

import asyncio
import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from app.config import settings
from app.domain.service_context import ServiceContext
from app.logging_config import get_logger

from .decision import DirectorDecision
from .prompt_builder import PromptBuild, build_director_prompt, estimate_tokens

logger = get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "service_director.txt"


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _summarize_validation_error(error: ValidationError) -> str:
    parts = []
    for item in error.errors()[:3]:
        loc = ".".join(str(p) for p in item.get("loc", ())) or "decision"
        msg = str(item.get("msg", "")).replace("Value error, ", "")
        parts.append(f"{loc}: {msg}")
    return "; ".join(parts) or "schema violation"


def _extract_json(content: str) -> dict:
    text = str(content or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("no JSON in response")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("response JSON is not an object")
    return data


class AIServiceDirector:
    """Reasoning-only AI Director. Produces DirectorDecision, never executes."""

    def __init__(self) -> None:
        self._last_cycle_id: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_prompt_tokens: Optional[int] = None
        self._last_latency_ms: Optional[int] = None

    @property
    def last_cycle_id(self) -> Optional[str]:
        return self._last_cycle_id

    @property
    def last_error(self) -> Optional[str]:
        """Why the last cycle fell back to "continue" (None after a good cycle)."""
        return self._last_error

    @property
    def last_prompt_tokens(self) -> Optional[int]:
        return self._last_prompt_tokens

    @property
    def last_latency_ms(self) -> Optional[int]:
        return self._last_latency_ms

    def status(self) -> dict:
        from app.agents.llm import director_backoff

        return {
            "last_cycle_id": self._last_cycle_id,
            "last_error": self._last_error,
            "last_prompt_tokens": self._last_prompt_tokens,
            "last_latency_ms": self._last_latency_ms,
            "backoff": director_backoff.status(),
        }

    # ------------------------------------------------------------------

    async def decide(self, context: ServiceContext) -> DirectorDecision:
        from app.director import cycle_log

        started = time.monotonic()
        cycle_id = cycle_log.new_cycle_id()
        self._last_cycle_id = cycle_id
        snapshot = context.snapshot()
        version = snapshot.get("version")
        inputs: dict[str, Any] = {"snapshot": snapshot}

        def fallback(reason: str, *, raw: Optional[str] = None, error: Optional[str] = None) -> DirectorDecision:
            """No-action decision that still carries cycle id / version / latency.
            Only the error *type* goes to the audit log and the reason string:
            exception messages can carry hostnames or key material."""
            self._last_error = reason
            latency = int((time.monotonic() - started) * 1000)
            self._last_latency_ms = latency
            decision = DirectorDecision(decision="continue", confidence=0.0, reason=reason,
                                        context_version=version, latency_ms=latency, cycle_id=cycle_id)
            cycle_log.log_model_cycle(cycle_id, {**inputs, "latency_ms": latency}, raw_response=raw,
                                      decision=None, error=error or "fallback")
            return decision

        try:
            from app.agents.llm import (
                director_backoff,
                director_deadline_seconds,
                get_director_llm,
                invoke_llm,
                response_text,
            )
        except Exception as exc:  # pragma: no cover - import failure is a packaging error
            return fallback(f"inference failed: {type(exc).__name__}: {exc}")

        wait = director_backoff.seconds_until_retry()
        if wait > 0:
            return fallback(
                f"inference skipped: backing off {wait:.1f}s after "
                f"{director_backoff.consecutive_failures} consecutive failure(s) ({director_backoff.last_error})",
                error="backoff",
            )

        try:
            llm = get_director_llm()
        except Exception as exc:
            reason = f"inference failed: {type(exc).__name__} building the Ollama client"
            director_backoff.record_failure(reason)
            return fallback(reason, error=type(exc).__name__)

        history, house_style = await asyncio.gather(
            self._retrieve_history(context, snapshot), self._house_style(snapshot)
        )
        prompt = self._build_prompt(context, snapshot, history, house_style)
        inputs.update(plan=prompt.sections.get("PLAN CURSOR"), history=history, house_style=house_style,
                      prompt_tokens=prompt.token_estimate, prompt_trimmed=prompt.trimmed,
                      prompt_budget=prompt.budget)
        self._last_prompt_tokens = prompt.token_estimate
        logger.debug("AI Director prompt built", cycle_id=cycle_id, tokens=prompt.token_estimate,
                     budget=prompt.budget, trimmed=prompt.trimmed)

        deadline = director_deadline_seconds()
        content: Optional[str] = None
        try:
            # Hard per-cycle deadline around the bounded invoke (whose own
            # LLM_TIMEOUT_SECONDS stays long for the assistant path).
            response = await asyncio.wait_for(
                invoke_llm(llm, [("system", _system_prompt()), ("user", prompt.text)]),
                timeout=deadline,
            )
            content = response_text(response)
            data = _extract_json(content)
            decision = DirectorDecision.model_validate(data)
        except asyncio.TimeoutError:
            reason = f"inference failed: deadline of {deadline:g}s exceeded"
            director_backoff.record_failure(reason)
            return fallback(reason, error="TimeoutError")
        except ValidationError as exc:
            # Ollama answered, so the service is healthy; the *output* was bad.
            director_backoff.record_success()
            reason = f"invalid decision: {_summarize_validation_error(exc)}"
            logger.warning("AI Director returned an invalid decision", cycle_id=cycle_id, detail=reason)
            return fallback(reason, raw=content, error="ValidationError")
        except ValueError as exc:
            director_backoff.record_success()
            reason = f"invalid decision: {exc}"
            logger.warning("AI Director returned unparseable output", cycle_id=cycle_id, detail=str(exc))
            return fallback(reason, raw=content, error=type(exc).__name__)
        except Exception as exc:
            reason = f"inference failed: {type(exc).__name__}"
            director_backoff.record_failure(reason)
            logger.warning("AI Director inference failed", cycle_id=cycle_id, error_type=type(exc).__name__)
            return fallback(reason, raw=content, error=type(exc).__name__)

        director_backoff.record_success()
        latency = int((time.monotonic() - started) * 1000)
        decision.context_version = version
        decision.latency_ms = latency
        decision.cycle_id = cycle_id
        self._last_error = None
        self._last_latency_ms = latency
        cycle_log.log_model_cycle(cycle_id, inputs, raw_response=content,
                                  decision=decision.model_dump(mode="json"))
        return decision

    # ------------------------------------------------------------------

    def _build_prompt(self, context: ServiceContext, snapshot: dict, history, house_style) -> PromptBuild:
        from .prompt_builder import prompt_token_budget

        system_tokens = estimate_tokens(_system_prompt())
        return build_director_prompt(
            snapshot,
            list(getattr(getattr(context, "plan", None), "elements", []) or []),
            context_lines=list(getattr(context, "transcript", []) or []),
            history=history,
            house_style=house_style,
            token_budget=prompt_token_budget(system_tokens),
        )

    async def _retrieve_history(self, context: ServiceContext, snapshot: dict):
        """Structured retrieval (state/speaker-filtered, similarity-ranked),
        advisory only. Never raises; runs off the loop since embedding may call
        a network/model."""
        if not settings.ai_director_use_memory_rag:
            return "None (memory retrieval disabled)."
        state = snapshot.get("service_state")
        speaker = snapshot.get("speaker") if snapshot.get("speaking") else None
        active = snapshot.get("active_speakers") or []
        if not speaker and active:
            speaker = str(active[0])
        transcript = snapshot.get("recent_transcript") or ""
        if isinstance(transcript, list):
            transcript = " ".join(str(getattr(t, "text", None) or (t.get("text") if isinstance(t, dict) else t)) for t in transcript)
        query = " ".join(filter(None, [state, speaker, str(transcript)[-400:]])).strip()
        if not query:
            return "None (no context yet to search on)."
        try:
            from app.memory.production_memory import memory_manager

            results = await asyncio.to_thread(
                memory_manager.retrieve_similar_moments, state, speaker, None,
                settings.ai_director_memory_results, query=query,
            )
        except Exception:
            logger.warning("AI Director memory retrieval failed", exc_info=True)
            return "None (retrieval unavailable)."
        floor = settings.ai_director_memory_min_similarity
        relevant = [r for r in results if r.get("similarity", 0.0) >= floor]
        if not relevant:
            return "None found for this moment."
        return relevant

    async def _house_style(self, snapshot: dict) -> list[str]:
        if not settings.ai_director_use_memory_rag:
            return []
        state = snapshot.get("service_state")
        if not state:
            return []
        try:
            from app.memory.production_memory import memory_manager

            rules = await asyncio.to_thread(
                memory_manager.summarize_house_style, state, snapshot.get("easyworship_item")
            )
            return [str(r) for r in (rules or [])][:6]
        except Exception:
            logger.warning("AI Director house-style summary failed", exc_info=True)
            return []


# Module-level singleton
ai_service_director = AIServiceDirector()
