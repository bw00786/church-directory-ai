"""AI Service Director: turns a ServiceContext into a structured decision.

Uses local Ollama (app.agents.llm). Unavailable inference or invalid output
falls back to a no-action, low-confidence decision.

Retrieval-augmented: each decision cycle also searches production memory
(app.memory.production_memory, backed by past cue/AI observations) for
similar past moments and includes them as advisory-only history in the
prompt. This never bypasses the policy engine -- it only informs the
reasoning behind the DirectorDecision, which is still gated like any other.
"""

import asyncio
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.config import settings
from app.domain.service_context import ServiceContext
from app.logging_config import get_logger

from .decision import DirectorDecision

logger = get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "service_director.txt"


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


class AIServiceDirector:
    """Reasoning-only AI Director. Produces DirectorDecision, never executes."""

    async def decide(self, context: ServiceContext) -> DirectorDecision:
        decision = await self._decide_with_llm(context)
        if decision is not None:
            return decision
        return DirectorDecision(
            decision="continue",
            confidence=0.0,
            reason="AI Director unavailable (Ollama unavailable or invalid response); no action taken",
        )

    async def _retrieve_history(self, context: ServiceContext, snapshot: dict) -> str:
        """Retrieval-augmented context: similar past-service observations,
        advisory only. Never raises -- a retrieval failure (e.g. no
        database) just means the model reasons without history, same as before
        this feature existed. Runs in a thread since embedding/search may now
        involve a real network call to Voyage AI, and this must never block
        the live director's event loop."""
        if not settings.ai_director_use_memory_rag:
            return "None (memory retrieval disabled)."

        query = f"{snapshot['service_state']} {snapshot['recent_transcript']}".strip()
        if not query:
            return "None (no context yet to search on)."

        try:
            from app.memory.production_memory import memory_manager

            results = await asyncio.to_thread(
                memory_manager.search, query, limit=settings.ai_director_memory_results
            )
        except Exception:
            logger.warning("AI Director memory retrieval failed", exc_info=True)
            return "None (retrieval unavailable)."

        relevant = [r for r in results if r.get("similarity", 0.0) >= settings.ai_director_memory_min_similarity]
        if not relevant:
            return "None found for this moment."

        return "\n".join(
            f"- [{r['service_date']}, {r['category']}] {r['text']} (similarity={r['similarity']:.2f})"
            for r in relevant
        )

    async def _decide_with_llm(self, context: ServiceContext) -> Optional[DirectorDecision]:
        try:
            from app.agents.llm import get_director_llm, invoke_llm, response_text

            llm = get_director_llm()
        except Exception:
            return None

        snapshot = context.snapshot()
        plan_summary = "\n".join(
            f"- {el.id} ({el.type.value}); speaker={el.speaker}; camera={el.camera_role}"
            for el in context.plan.elements
        )
        history = await self._retrieve_history(context, snapshot)
        user = (
            f"Service plan (guide only):\n{plan_summary}\n\n"
            f"Current state: {snapshot['service_state']}\n"
            f"Currently speaking: {snapshot['speaker']} (speaking={snapshot['speaking']})\n"
            f"Current camera role: {snapshot['camera_role']}\n"
            f"Current ATEM program: {snapshot['atem_program']}\n"
            f"Current EasyWorship item: {snapshot['easyworship_item']}\n"
            f"Recent transcript:\n{snapshot['recent_transcript']}\n\n"
            f"Recent actions: {snapshot['last_actions']}\n\n"
            f"Relevant history from past services (advisory only -- may be irrelevant "
            f"or outdated; trust live signals above this over history):\n{history}\n\n"
            "What should happen next?"
        )

        try:
            response = await invoke_llm(llm, [("system", _system_prompt()), ("user", user)])
            content = response_text(response)
            match = re.search(r"\{.*\}", str(content), re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group(0))
            return DirectorDecision.model_validate(data)
        except Exception as e:
            logger.warning("AI Director decision failed", error=str(e))
            return None


# Module-level singleton
ai_service_director = AIServiceDirector()
