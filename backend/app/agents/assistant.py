"""AI assistant chatbot: answers questions about past services/roster and
controls production subsystems via tool-calling (Claude + LangGraph).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.logging_config import get_logger

from . import assistant_tools
from .llm import build_llm

logger = get_logger(__name__)

ASSISTANT_SYSTEM_PROMPT = """\
You are the assistant for a church's live production system (ATEM switcher,
PTZ cameras, EasyWorship slides, service director, and production memory).

You can:
- Answer questions about past services using search_past_services,
  list_past_services, get_service_summary, who_preached, who_had_role, and
  list_roster.
- Report live status using get_director_status, get_atem_status,
  get_vision_status, and list_cameras.
- Control cameras, transitions, slides, and the service director directly
  (these tools execute immediately).
- Request starting/stopping the stream, starting/stopping recording, or
  muting a mic -- these are HIGH RISK and your tool call only registers a
  pending confirmation; it does not execute until the human operator clicks
  Confirm in the UI. Always tell the user their request needs confirmation
  when you use one of these tools.

Never fabricate service history or roster data -- only report what the tools
return. If a tool finds nothing, say so plainly rather than guessing.
"""

_agent = None


def get_agent():
    """Lazily build the LangGraph tool-calling agent (requires ANTHROPIC_API_KEY)."""
    global _agent
    if _agent is None:
        from langgraph.prebuilt import create_react_agent

        llm = build_llm()
        _agent = create_react_agent(llm, assistant_tools.ALL_TOOLS, prompt=ASSISTANT_SYSTEM_PROMPT)
    return _agent


async def run_assistant(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Run one turn of the assistant.

    Args:
        messages: Full conversation so far, as [{"role": "user"|"assistant", "content": str}, ...].

    Returns:
        {"reply": str, "pending_confirmation": {"token", "description"} | None}
    """
    agent = get_agent()
    lc_messages = [(m["role"], m["content"]) for m in messages]
    result = await agent.ainvoke({"messages": lc_messages})
    out_messages = result["messages"]

    pending_confirmation: Optional[dict[str, Any]] = None
    for msg in out_messages:
        content = getattr(msg, "content", None)
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and "pending_confirmation" in parsed:
            pending_confirmation = {
                "token": parsed["pending_confirmation"],
                "description": parsed.get("description", ""),
            }

    reply = ""
    if out_messages:
        content = getattr(out_messages[-1], "content", "")
        reply = content if isinstance(content, str) else " ".join(str(part) for part in content)

    return {"reply": reply, "pending_confirmation": pending_confirmation}
