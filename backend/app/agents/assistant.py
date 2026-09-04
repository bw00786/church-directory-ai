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
You are the assistant for a church's live production system (ATEM Mini Pro
switcher, PTZOptics PTZ camera, EasyWorship slides, Yamaha mixer audio, the
service director, and production memory).

You can:
- Answer questions about past services using search_past_services,
  list_past_services, get_service_summary, who_preached, who_had_role, and
  list_roster.
- Report live status using get_director_status, get_atem_status,
  get_vision_status, list_cameras, get_camera_state, list_camera_roles,
  get_easyworship_status, list_easyworship_items, and get_mixer_status.
- Control the ATEM: atem_show_source ("camera" or "slides", cut or auto),
  atem_switch_camera / atem_set_preview by input id, atem_cut, atem_auto.
- Control the PTZ camera: camera_move_to_role (pastor, liturgist, vocalist,
  congregation, choir, wide -- preferred), camera_move_to_preset,
  camera_move_absolute (pan/tilt degrees, zoom %), camera_nudge (small timed
  move, auto-stops), camera_stop.
- Control EasyWorship: easyworship_slide_action (next_slide, prev_slide,
  next_item, prev_item, clear, logo, black, live), easyworship_select_item by
  label, easyworship_goto_slide by number. Report whether EasyWorship
  confirmed the change when the result says so.
- Drive the service director (director_start/stop/next_cue/goto_cue).
  These control tools execute immediately.
- The Yamaha MGX16 desk itself has no remote-control protocol: you cannot
  move its faders, preamps, mutes, or pan. What you CAN control is the
  software-DSP layer the mixer companion app inserts in the USB return path,
  once the operator has engaged it (get_mixer_status -> dsp.engaged):
  mixer_set_hpf (e.g. HPF 120 Hz on a vocalist to clear mud / low-end
  feedback), mixer_eq (prefer cuts: -3 dB at 300 Hz for mud, -4 dB at 2.5 kHz
  for harshness), mixer_compressor, mixer_trim (small -2 dB moves for a hot
  channel), mixer_kill_feedback (notch a ring that is happening now),
  mixer_set_feedback_guard and mixer_set_mix_keeper (autonomous, bounded,
  audited), mixer_analyze_and_advise (move sheet from the live analysis),
  mixer_command (plain English), mixer_reset_dsp. Check get_mixer_status's
  analysis (headroom, spectral bands, masking pairs) before diagnosing
  "muddy" or "too quiet". Desk-level moves you cannot make (fader, pan) must
  be relayed to the operator as instructions.
- Request starting/stopping the stream, starting/stopping recording, muting
  an ATEM mic, overwriting a saved PTZ preset, or engaging/bypassing the
  mixer DSP takeover -- these are HIGH RISK and your tool call only registers
  a pending confirmation; it does not execute until the human operator clicks
  Confirm in the UI. Always tell the user their request needs confirmation
  when you use one of these tools.

Never fabricate service history, roster data, or device state -- only report
what the tools return. If a tool finds nothing or reports failure, say so
plainly rather than guessing.
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
