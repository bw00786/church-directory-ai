"""Tools for the AI assistant chatbot: query production history/roster, and
control production subsystems.

Risk-tiered by design: camera switching, PTZ presets, EasyWorship slides, and
service-director actions execute immediately. Streaming, recording, and mic
mute are high-risk live-production actions -- their tools only *register* a
pending confirmation (see `pending_actions`) instead of executing; the actual
action runs later via `execute_pending()` once the operator confirms in the UI.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.tools import tool

from app.cameras.service import camera_service
from app.dependencies import get_atem_service_instance
from app.director.engine import service_director
from app.easyworship.service import easyworship_service
from app.identity.service import identity_service
from app.logging_config import get_logger
from app.memory.production_memory import memory_manager
from app.vision.manager import vision_manager

logger = get_logger(__name__)

# token -> {"action": str, "args": dict, "description": str}
pending_actions: dict[str, dict[str, Any]] = {}


def _register_pending(action: str, args: dict[str, Any], description: str) -> str:
    token = uuid.uuid4().hex[:8]
    pending_actions[token] = {"action": action, "args": args, "description": description}
    return token


async def execute_pending(token: str) -> dict[str, Any]:
    """Execute a previously-registered pending action by its confirmation token."""
    pending = pending_actions.pop(token, None)
    if pending is None:
        return {"ok": False, "error": "Unknown or already-used confirmation token"}

    action = pending["action"]
    args = pending["args"]
    atem = get_atem_service_instance()
    try:
        if action == "atem_start_stream":
            return {"ok": await atem.start_stream()}
        if action == "atem_stop_stream":
            return {"ok": await atem.stop_stream()}
        if action == "atem_start_recording":
            return {"ok": await atem.start_recording()}
        if action == "atem_stop_recording":
            return {"ok": await atem.stop_recording()}
        if action == "atem_set_mic_muted":
            state = await atem.set_mic_muted(args["mic_id"], args["muted"])
            return {"ok": True, "state": state.model_dump(mode="json")}
        return {"ok": False, "error": f"Unknown action: {action}"}
    except Exception as e:
        logger.warning("Pending assistant action failed", action=action, error=str(e))
        return {"ok": False, "error": str(e)}


def discard_pending(token: str) -> bool:
    return pending_actions.pop(token, None) is not None


def _ok(payload: Any) -> str:
    return json.dumps(payload, default=str)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


# -- Query tools (read-only, execute immediately) ----------------------------


@tool
def search_past_services(query: str, limit: int = 10) -> str:
    """Semantically search past-service production memory (cue actions, vision events, identity matches) for a natural-language query. Returns the most relevant recorded observations with their service date."""
    return _ok(memory_manager.search(query, limit=limit))


@tool
def list_past_services(limit: int = 20) -> str:
    """List past service dates that have recorded production memory, most recent first, with an observation count for each."""
    return _ok(memory_manager.list_services(limit=limit))


@tool
def get_service_summary(service_date: str) -> str:
    """Get a summary of everything recorded for one past service date (format YYYY-MM-DD): observation counts by category and the full list of observations."""
    return _ok(memory_manager.service_summary(service_date))


@tool
def who_preached(service_date: str) -> str:
    """Answer "who preached" on a given past service date (format YYYY-MM-DD), using identity recognition history (the "pastor" role)."""
    result = identity_service.who_was_seen("pastor", service_date)
    if result is None:
        return _ok({"found": False, "service_date": service_date, "message": "No pastor sighting recorded for this date."})
    return _ok({"found": True, **result})


@tool
def who_had_role(role: str, service_date: str) -> str:
    """Answer who held a specific role (e.g. "liturgist", "vocalist", "pastor") on a given past service date (format YYYY-MM-DD), using identity recognition history."""
    result = identity_service.who_was_seen(role, service_date)
    if result is None:
        return _ok({"found": False, "role": role, "service_date": service_date, "message": "No sighting recorded for this role/date."})
    return _ok({"found": True, **result})


@tool
def list_roster() -> str:
    """List all people enrolled in the roster (name, role, last seen, appearance count)."""
    return _ok(identity_service.list_roster())


@tool
def get_director_status() -> str:
    """Get the current service director status: running state, autonomous mode, current/next cue."""
    return _ok(service_director.status().model_dump(mode="json"))


@tool
async def get_atem_status() -> str:
    """Get the current ATEM switcher state: connected, program/preview input, streaming, recording, inputs, mic mute state."""
    atem = get_atem_service_instance()
    try:
        if not await atem.is_connected():
            return _ok({"connected": False})
        state = await atem.get_state()
        return _ok(state.model_dump(mode="json"))
    except Exception as e:
        return _err(str(e))


@tool
def get_vision_status() -> str:
    """Get the current vision subsystem status (active, camera count, fps)."""
    return _ok(vision_manager.get_status())


@tool
def list_cameras() -> str:
    """List registered PTZ cameras and their connection status."""
    return _ok(camera_service.list_cameras())


# -- Control tools: immediate (cameras/presets/slides/director) -------------


@tool
async def atem_switch_camera(input_id: int) -> str:
    """Switch the ATEM program (live) output to the given input id."""
    atem = get_atem_service_instance()
    try:
        state = await atem.set_program(input_id)
        return _ok({"ok": True, "program_input": state.program_input})
    except Exception as e:
        return _err(str(e))


@tool
async def atem_cut() -> str:
    """Perform an immediate CUT transition on the ATEM (preview becomes program instantly)."""
    atem = get_atem_service_instance()
    try:
        await atem.cut()
        return _ok({"ok": True})
    except Exception as e:
        return _err(str(e))


@tool
async def atem_auto() -> str:
    """Perform a timed AUTO transition on the ATEM (preview becomes program with a fade/wipe)."""
    atem = get_atem_service_instance()
    try:
        await atem.auto()
        return _ok({"ok": True})
    except Exception as e:
        return _err(str(e))


@tool
async def camera_move_to_preset(camera_id: int, preset_id: int) -> str:
    """Move a PTZ camera to a saved preset position."""
    try:
        ok = await camera_service.move_to_preset(camera_id, preset_id)
        return _ok({"ok": ok})
    except Exception as e:
        return _err(str(e))


@tool
async def easyworship_slide_action(action: str) -> str:
    """Control EasyWorship slides. `action` must be one of: next_slide, prev_slide, next_item, prev_item, clear, logo, black, live."""
    try:
        ok = await easyworship_service.action(action)
        return _ok({"ok": ok})
    except Exception as e:
        return _err(str(e))


@tool
async def director_next_cue() -> str:
    """Advance the service director to the next cue (like the operator pressing Next)."""
    status = await service_director.next()
    return _ok(status.model_dump(mode="json"))


@tool
async def director_goto_cue(index: int) -> str:
    """Jump the service director to a specific cue index (0-based)."""
    try:
        status = await service_director.goto(index)
        return _ok(status.model_dump(mode="json"))
    except IndexError as e:
        return _err(str(e))


@tool
async def director_start(autonomous: bool = True) -> str:
    """Start the scripted service director from the beginning."""
    status = await service_director.start(autonomous=autonomous)
    return _ok(status.model_dump(mode="json"))


@tool
async def director_stop() -> str:
    """Stop the running service director."""
    status = await service_director.stop()
    return _ok(status.model_dump(mode="json"))


# -- Control tools: require operator confirmation (streaming/recording/mic) -


@tool
def request_start_streaming() -> str:
    """Request to start the live stream ("go on air"). High-risk: this only registers a pending confirmation and does NOT start the stream -- the operator must confirm in the UI first."""
    description = "Start the live stream (go ON AIR)"
    token = _register_pending("atem_start_stream", {}, description)
    return _ok({"pending_confirmation": token, "description": description})


@tool
def request_stop_streaming() -> str:
    """Request to stop the live stream ("go off air"). High-risk: this only registers a pending confirmation and does NOT stop the stream -- the operator must confirm in the UI first."""
    description = "Stop the live stream (go OFF AIR)"
    token = _register_pending("atem_stop_stream", {}, description)
    return _ok({"pending_confirmation": token, "description": description})


@tool
def request_start_recording() -> str:
    """Request to start recording. High-risk: this only registers a pending confirmation and does NOT start recording -- the operator must confirm in the UI first."""
    description = "Start recording"
    token = _register_pending("atem_start_recording", {}, description)
    return _ok({"pending_confirmation": token, "description": description})


@tool
def request_stop_recording() -> str:
    """Request to stop recording. High-risk: this only registers a pending confirmation and does NOT stop recording -- the operator must confirm in the UI first."""
    description = "Stop recording"
    token = _register_pending("atem_stop_recording", {}, description)
    return _ok({"pending_confirmation": token, "description": description})


@tool
def request_mic_mute(mic_id: int, muted: bool) -> str:
    """Request to mute or unmute a mic channel (1 or 2). High-risk: this only registers a pending confirmation and does NOT change the mic -- the operator must confirm in the UI first."""
    verb = "Mute" if muted else "Unmute"
    description = f"{verb} Mic {mic_id}"
    token = _register_pending("atem_set_mic_muted", {"mic_id": mic_id, "muted": muted}, description)
    return _ok({"pending_confirmation": token, "description": description})


QUERY_TOOLS = [
    search_past_services,
    list_past_services,
    get_service_summary,
    who_preached,
    who_had_role,
    list_roster,
    get_director_status,
    get_atem_status,
    get_vision_status,
    list_cameras,
]

CONTROL_TOOLS = [
    atem_switch_camera,
    atem_cut,
    atem_auto,
    camera_move_to_preset,
    easyworship_slide_action,
    director_next_cue,
    director_goto_cue,
    director_start,
    director_stop,
    request_start_streaming,
    request_stop_streaming,
    request_start_recording,
    request_stop_recording,
    request_mic_mute,
]

ALL_TOOLS = QUERY_TOOLS + CONTROL_TOOLS
