"""LLM/heuristic cue-advance decisions from free-form observations.

Turns an observation (a transcript snippet, a vision scene description, an
operator note) into an advance decision for the *current* cue. The decision is
**not** applied here: it becomes a ``CUE_ADVANCE`` proposal submitted to the AI
Director runtime, so it honours the operating mode (manual = observe only,
assisted = operator approves, ai_directed = policy-gated execution) and the
shared proposal queue exactly like every other AI proposal. The cue engine's
own autonomy/confidence gate still applies when the action finally executes.
"""

import json
import re
from typing import Optional

from app.director.action_models import DirectorAction, DirectorActionType
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
# A keyword match is a weak signal; it must not look as sure as the model.
_HEURISTIC_CONFIDENCE = 0.75

SOURCE = "ai_director"


class DirectorAI:
    """Decides cue advances from observations and proposes them to the runtime."""

    async def decide(self, observation: str, *, exit_hint: str, cue_name: str) -> dict:
        """Return {advance, confidence, reason} for the current observation."""
        decision = await self._decide_with_llm(observation, exit_hint=exit_hint, cue_name=cue_name)
        if decision is not None:
            return {**decision, "heuristic": False}
        return self._decide_heuristic(observation)

    async def observe(self, observation: str) -> dict:
        """Evaluate an observation against the current cue; propose (never apply) an advance."""
        from app.director.ai_director_runtime import ai_director_runtime

        cue = service_director.current_cue()
        if cue is None:
            return {"accepted": False, "reason": "no active cue", "mode": ai_director_runtime.mode}

        decision = await self.decide(
            observation,
            exit_hint=cue.exit_hint or "Advance when this segment is clearly finished.",
            cue_name=cue.name,
        )
        if not decision.get("advance"):
            return {"accepted": False, "decision": decision, "mode": ai_director_runtime.mode}

        action = DirectorAction(
            type=DirectorActionType.CUE_ADVANCE,
            target=cue.id,
            parameters={
                "source": SOURCE,
                "cue_id": cue.id,
                "cue_name": cue.name,
                "heuristic": bool(decision.get("heuristic")),
                "observation": observation[:200],
            },
            confidence=float(decision.get("confidence", 0.0)),
            reason=str(decision.get("reason", "AI decision")),
        )
        outcome = await ai_director_runtime.propose([action], source=SOURCE)
        return {
            "accepted": bool(outcome.get("accepted")),
            "decision": decision,
            "mode": ai_director_runtime.mode,
            "proposals": outcome.get("proposals", []),
            "dropped": outcome.get("dropped", []),
        }

    async def _decide_with_llm(
        self, observation: str, *, exit_hint: str, cue_name: str
    ) -> Optional[dict]:
        try:
            from app.agents.llm import get_fast_llm, invoke_llm, response_text

            llm = get_fast_llm()
        except Exception:
            return None  # Missing local client; retain the heuristic fallback.

        user = (
            f"Current cue: {cue_name}\n"
            f"Advance condition: {exit_hint}\n"
            f"Latest observation: {observation}\n"
            "Should we advance now?"
        )
        try:
            response = await invoke_llm(llm, [("system", _SYSTEM_PROMPT), ("user", user)])
            content = response_text(response)
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
            return {
                "advance": True,
                "confidence": _HEURISTIC_CONFIDENCE,
                "reason": "matched end-of-segment phrase",
                "heuristic": True,
            }
        return {"advance": False, "confidence": 0.0, "reason": "no advance signal", "heuristic": True}


# Module-level singleton
director_ai = DirectorAI()
