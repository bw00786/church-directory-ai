"""AI Service Director: turns a ServiceContext into a structured decision.

Uses Anthropic Claude (app.agents.llm) — the same client already used
elsewhere in this codebase. Falls back to a "continue, low confidence"
decision (never an executable action) if no API key is configured or the
call/parse fails, so the system degrades safely.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

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
        decision = await self._decide_with_claude(context)
        if decision is not None:
            return decision
        return DirectorDecision(
            decision="continue",
            confidence=0.0,
            reason="AI Director unavailable (no API key or parse failure); no action taken",
        )

    async def _decide_with_claude(self, context: ServiceContext) -> Optional[DirectorDecision]:
        try:
            from app.agents.llm import get_llm

            llm = get_llm()
        except Exception:
            return None

        snapshot = context.snapshot()
        plan_summary = "\n".join(
            f"- {el.id} ({el.type.value}); speaker={el.speaker}; camera={el.camera_role}"
            for el in context.plan.elements
        )
        user = (
            f"Service plan (guide only):\n{plan_summary}\n\n"
            f"Current state: {snapshot['service_state']}\n"
            f"Currently speaking: {snapshot['speaker']} (speaking={snapshot['speaking']})\n"
            f"Current camera role: {snapshot['camera_role']}\n"
            f"Current ATEM program: {snapshot['atem_program']}\n"
            f"Current EasyWorship item: {snapshot['easyworship_item']}\n"
            f"Recent transcript:\n{snapshot['recent_transcript']}\n\n"
            f"Recent actions: {snapshot['last_actions']}\n\n"
            "What should happen next?"
        )

        try:
            response = await llm.ainvoke([("system", _system_prompt()), ("user", user)])
            content = getattr(response, "content", response)
            if isinstance(content, list):
                content = " ".join(str(part) for part in content)
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
