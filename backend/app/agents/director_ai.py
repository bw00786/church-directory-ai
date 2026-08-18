"""LLM/vision advance decisions for the service director.

Turns free-form observations (a transcript snippet, a vision scene description,
an operator note) into an advance decision for the *current* cue, then feeds it
back into the director via ``request_advance``. Uses Anthropic Claude when
configured, with a lightweight keyword heuristic as a fallback so the pipeline
works without an API key.
"""

import json
import re
from typing import Optional

from app.director.engine import service_director
from app.logging_config import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You control a live church service video switcher. Given the current cue, "
    "its advance condition, and the latest observation, decide whether the "
    "service should now advance to the next cue. Respond with strict JSON only: "
    '{"advance": true|false, "confidence": 0.0-1.0, "reason": "short reason"}.'
)

# Fallback heuristic cues that a speaker has finished / a transition is due.
_ADVANCE_KEYWORDS = re.compile(
    r"\b(amen|let us pray|please stand|please be seated|finished|concludes?|"
    r"in the name of the father|this ends the reading|thanks be to god|"
    r"the word of the lord)\b",
    re.IGNORECASE,
)


class DirectorAI:
    """Decides cue advances from observations and drives the director."""

    async def decide(self, observation: str, *, exit_hint: str, cue_name: str) -> dict:
        """Return {advance, confidence, reason} for the current observation."""
        decision = await self._decide_with_llm(observation, exit_hint=exit_hint, cue_name=cue_name)
        if decision is not None:
            return decision
        return self._decide_heuristic(observation)

    async def observe(self, observation: str) -> dict:
        """Evaluate an observation against the current cue and act on it."""
        cue = service_director.current_cue()
        if cue is None:
            return {"accepted": False, "reason": "no active cue"}

        decision = await self.decide(
            observation,
            exit_hint=cue.exit_hint or "Advance when this segment is clearly finished.",
            cue_name=cue.name,
        )

        if not decision.get("advance"):
            return {"accepted": False, "decision": decision}

        result = await service_director.request_advance(
            source="director_ai",
            reason=decision.get("reason", "AI decision"),
            confidence=float(decision.get("confidence", 0.0)),
            cue_id=cue.id,
        )
        return {"decision": decision, "result": result}

    async def _decide_with_llm(
        self, observation: str, *, exit_hint: str, cue_name: str
    ) -> Optional[dict]:
        try:
            from app.agents.llm import get_llm

            llm = get_llm()
        except Exception:
            return None  # no API key / package — fall back to heuristic

        user = (
            f"Current cue: {cue_name}\n"
            f"Advance condition: {exit_hint}\n"
            f"Latest observation: {observation}\n"
            "Should we advance now?"
        )
        try:
            response = await llm.ainvoke([("system", _SYSTEM_PROMPT), ("user", user)])
            content = getattr(response, "content", response)
            if isinstance(content, list):
                content = " ".join(str(part) for part in content)
            match = re.search(r"\{.*\}", str(content), re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group(0))
            return {
                "advance": bool(data.get("advance", False)),
                "confidence": float(data.get("confidence", 0.0)),
                "reason": str(data.get("reason", "LLM decision")),
            }
        except Exception as e:
            logger.warning("LLM advance decision failed", error=str(e))
            return None

    def _decide_heuristic(self, observation: str) -> dict:
        if _ADVANCE_KEYWORDS.search(observation or ""):
            return {"advance": True, "confidence": 0.9, "reason": "matched end-of-segment phrase"}
        return {"advance": False, "confidence": 0.0, "reason": "no advance signal"}


# Module-level singleton
director_ai = DirectorAI()
