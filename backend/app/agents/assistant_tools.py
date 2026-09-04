"""Tools for the AI assistant chatbot: query production history/roster, and
control production subsystems.

Risk-tiered by design: camera switching, PTZ moves/presets, EasyWorship slides,
and service-director actions execute immediately. Streaming, recording, mic
mute, and overwriting a saved PTZ preset are high-risk live-production actions
-- their tools only *register* a pending confirmation (see `pending_actions`)
instead of executing; the actual action runs later via `execute_pending()` once
the operator confirms in the UI.

The Yamaha MGX16 desk itself has no remote-control protocol (faders, preamps,
mutes, pan stay on the console), but the companion mgx-ai-mixer app provides a
software-DSP layer in the USB MAIN return path: per-channel HPF / EQ / comp /
trim / reverb / delay, an autonomous feedback guard, and a mix-quality keeper.
Mixer tools drive that layer; engaging the takeover is confirmation-gated.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional

from langchain_core.tools import tool

from app.cameras.service import camera_service
from app.config import settings
from app.dependencies import get_atem_service_instance
from app.director.engine import service_director
from app.domain.service_context import service_context
from app.easyworship.service import ACTIONS as EASYWORSHIP_ACTIONS, easyworship_service
from app.identity.service import identity_service
from app.logging_config import get_logger
from app.memory.production_memory import memory_manager
from app.mixer.service import mixer_service
from app.vision.manager import vision_manager

logger = get_logger(__name__)

CAMERA_ROLES = ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide")
MIXER_ROLES = ("pastor", "liturgist", "vocalist", "congregation")
_NUDGE_DIRECTIONS = {
    "left": (-1, 0, 0),
    "right": (1, 0, 0),
    "up": (0, 1, 0),
    "down": (0, -1, 0),
    "zoom_in": (0, 0, 1),
    "zoom_out": (0, 0, -1),
}
MAX_NUDGE_SECONDS = 3.0

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
        if action == "camera_save_preset":
            ok = await camera_service.save_preset(args["camera_id"], args["preset_id"])
            return {"ok": ok}
        if action == "mixer_engage_dsp":
            state = await mixer_service.engage_dsp(args["engage"])
            return {"ok": True, "dsp": state}
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


@tool
async def get_camera_state(camera_id: int = 1) -> str:
    """Get a PTZ camera's current state: connected, pan/tilt (degrees), zoom (%), and the last preset it was sent to."""
    try:
        state = await camera_service.get_camera_state(camera_id)
        state["current_preset"] = camera_service.get_current_preset(camera_id)
        return _ok(state)
    except Exception as e:
        return _err(str(e))


@tool
def list_camera_roles() -> str:
    """List the framing roles (pastor, liturgist, vocalist, congregation, choir, wide) and which PTZ camera + preset each maps to. Use camera_move_to_role to frame one."""
    roles = {
        role: {
            "camera_id": getattr(settings, f"camera_role_{role}_camera", None),
            "preset_id": getattr(settings, f"camera_role_{role}_preset", None),
        }
        for role in CAMERA_ROLES
    }
    return _ok(roles)


@tool
def get_easyworship_status() -> str:
    """Get EasyWorship slide-control status: connected, driver in use, last action and whether EasyWorship confirmed it, and (remote protocol) the live schedule item number and slide number."""
    return _ok(easyworship_service.status())


@tool
def list_easyworship_items() -> str:
    """List the EasyWorship schedule item labels from the service plan, in order (use with easyworship_select_item)."""
    labels = easyworship_service._item_labels()
    return _ok({"items": labels, "schedule_offset": settings.easyworship_schedule_offset})


@tool
def get_mixer_status() -> str:
    """Get the Yamaha MGX16 audio picture: meter-feed connection, per-role channel RMS (dB) and activity, who is speaking, recent transcript, the latest mix analysis (per-channel spectral bands, headroom, masking pairs, LUFS) and the software-DSP state (engaged, per-channel processing, feedback guard, mix keeper). Desk faders/preamps/mutes are NOT remotely controllable; only the software-DSP layer is."""
    channels = {}
    for role in MIXER_ROLES:
        channel = getattr(settings, f"mixer_{role}_channel", None)
        if channel is None:
            continue
        channels[role] = {
            "channel": channel,
            "rms_db": round(mixer_service.channel_rms(channel), 1),
            "active": mixer_service.channel_active(channel, settings.speech_active_db),
        }
    perception = None
    try:
        from app.audio.audio_observer import audio_observer

        perception = audio_observer.perception_status()
    except Exception as e:  # noqa: BLE001
        perception = {"error": str(e)}
    context = service_context.snapshot()
    return _ok(
        {
            "connected": mixer_service.connected,
            "mock": mixer_service.mock,
            "control": (
                "desk faders/preamps/mutes/pan are console-only; per-channel HPF/EQ/comp/trim/FX, "
                "feedback guard and mix keeper are controllable via the software-DSP tools when engaged"
            ),
            "channels": channels,
            "all_levels_db": mixer_service.levels(),
            "current_speaker_role": context.get("speaker"),
            "speaking": context.get("speaking"),
            "recent_transcript": context.get("recent_transcript"),
            "analysis": mixer_service.analysis(),
            "dsp": mixer_service.dsp_state(),
            "perception": perception,
        }
    )


def _role_channel(role_or_channel: str) -> Optional[int]:
    """Resolve "pastor"/"vocalist"/... or a channel number string to a channel."""
    key = role_or_channel.strip().lower()
    if key.isdigit():
        return int(key)
    for role in MIXER_ROLES:
        if key == role or key.startswith(role):
            return getattr(settings, f"mixer_{role}_channel", None)
    return None


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
async def atem_set_preview(input_id: int) -> str:
    """Put the given ATEM input on preview (not live) so it can be taken with atem_cut or atem_auto."""
    atem = get_atem_service_instance()
    try:
        state = await atem.set_preview(input_id)
        return _ok({"ok": True, "preview_input": state.preview_input})
    except Exception as e:
        return _err(str(e))


@tool
async def atem_show_source(source: str, transition: str = "cut") -> str:
    """Put a named source live on the ATEM: `source` is "camera" (the PTZ camera) or "slides" (the EasyWorship laptop). `transition` is "cut" (instant) or "auto" (fade via preview)."""
    sources = {"camera": settings.atem_camera1_input, "slides": settings.atem_camera2_input}
    input_id = sources.get(source.lower())
    if input_id is None:
        return _err(f"unknown source '{source}'; use one of {sorted(sources)}")
    atem = get_atem_service_instance()
    try:
        if transition == "auto":
            await atem.set_preview(input_id)
            state = await atem.auto()
        else:
            state = await atem.set_program(input_id)
        return _ok({"ok": True, "source": source, "program_input": state.program_input})
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
async def camera_move_to_role(role: str) -> str:
    """Frame the PTZ camera on a service role: pastor, liturgist, vocalist, congregation, choir, or wide (uses the configured camera + preset for that role)."""
    role = role.lower().strip()
    if role not in CAMERA_ROLES:
        return _err(f"unknown role '{role}'; use one of {list(CAMERA_ROLES)}")
    try:
        ok = await camera_service.move_to_role(role)
        return _ok({"ok": ok, "role": role})
    except Exception as e:
        return _err(str(e))


@tool
async def camera_move_absolute(
    camera_id: int = 1,
    pan: Optional[float] = None,
    tilt: Optional[float] = None,
    zoom: Optional[float] = None,
) -> str:
    """Move a PTZ camera to an absolute position: pan/tilt in degrees, zoom in percent (0-100). Omit any value to leave it unchanged."""
    if pan is None and tilt is None and zoom is None:
        return _err("give at least one of pan, tilt, zoom")
    if zoom is not None and not 0 <= zoom <= 100:
        return _err("zoom must be 0-100")
    try:
        ok = await camera_service.move_camera(camera_id, pan=pan, tilt=tilt, zoom=zoom)
        return _ok({"ok": ok})
    except Exception as e:
        return _err(str(e))


@tool
async def camera_nudge(direction: str, camera_id: int = 1, seconds: float = 0.5, speed: int = 8) -> str:
    """Nudge a PTZ camera a little: `direction` is left, right, up, down, zoom_in, or zoom_out; `seconds` is how long to move (max 3.0); `speed` 1-24 for pan/tilt, 1-7 for zoom. Always stops the camera afterwards."""
    dirs = _NUDGE_DIRECTIONS.get(direction.lower().strip())
    if dirs is None:
        return _err(f"unknown direction '{direction}'; use one of {sorted(_NUDGE_DIRECTIONS)}")
    seconds = max(0.05, min(float(seconds), MAX_NUDGE_SECONDS))
    pan_dir, tilt_dir, zoom_dir = dirs
    try:
        started = await camera_service.drive_camera(
            camera_id,
            pan_dir=pan_dir,
            tilt_dir=tilt_dir,
            zoom_dir=zoom_dir,
            pan_speed=max(1, min(int(speed), 24)),
            tilt_speed=max(1, min(int(speed), 20)),
            zoom_speed=max(1, min(int(speed), 7)),
        )
        try:
            await asyncio.sleep(seconds)
        finally:
            stopped = await camera_service.stop_camera(camera_id)
        return _ok({"ok": bool(started and stopped), "direction": direction, "seconds": seconds})
    except Exception as e:
        with_stop = await camera_service.stop_camera(camera_id)
        return _err(f"{e} (stopped={with_stop})")


@tool
async def camera_stop(camera_id: int = 1) -> str:
    """Immediately stop all PTZ camera motion."""
    try:
        return _ok({"ok": await camera_service.stop_camera(camera_id)})
    except Exception as e:
        return _err(str(e))


@tool
async def easyworship_slide_action(action: str) -> str:
    """Control EasyWorship slides. `action` must be one of: next_slide, prev_slide, next_item, prev_item, clear, logo, black, live. With the remote protocol the result reflects whether EasyWorship confirmed the change."""
    if action not in EASYWORSHIP_ACTIONS:
        return _err(f"unknown action '{action}'; use one of {list(EASYWORSHIP_ACTIONS)}")
    try:
        ok = await easyworship_service.action(action)
        return _ok({"ok": ok, "status": easyworship_service.status()})
    except Exception as e:
        return _err(str(e))


@tool
async def easyworship_select_item(label: str) -> str:
    """Go live on a specific EasyWorship schedule item by its service-plan label (see list_easyworship_items), e.g. "Sermon" or "Scripture". Absolute jump; confirmed against EasyWorship's reported position when using the remote protocol."""
    try:
        ok = await easyworship_service.select_item(label)
        return _ok({"ok": ok, "item": label, "status": easyworship_service.status()})
    except Exception as e:
        return _err(str(e))


@tool
async def easyworship_goto_slide(number: int) -> str:
    """Jump to slide `number` (1-based) within the current live EasyWorship item. Requires the remote-protocol driver."""
    if number < 1:
        return _err("slide number must be >= 1")
    try:
        ok = await easyworship_service.goto_slide(number)
        return _ok({"ok": ok, "slide": number})
    except Exception as e:
        return _err(str(e))


# -- Mixer software-DSP tools (mgx-ai-mixer takeover layer) -------------------


@tool
async def mixer_command(text: str) -> str:
    """Plain-English per-channel audio fix on the MGX16 software-DSP layer, e.g. "HPF at 120 Hz on the pastor", "the vocalist is muddy", "channel 2 has feedback, fix it", "more reverb on the vocals", "make the keys brighter". Requires the DSP takeover to be engaged (see get_mixer_status -> dsp.engaged; use request_mixer_engage_dsp otherwise)."""
    try:
        return _ok(await mixer_service.command(text))
    except Exception as e:
        return _err(str(e))


@tool
async def mixer_set_hpf(channel: str, frequency_hz: float = 100.0) -> str:
    """Engage/set the high-pass filter on one channel of the MGX16 software-DSP. `channel` is a role (pastor, liturgist, vocalist, congregation) or a channel number. Typical: 80-120 Hz for a male voice, 120-180 Hz for a female vocalist, to remove mud/rumble and reduce low-frequency feedback."""
    ch = _role_channel(channel)
    if ch is None:
        return _err(f"unknown channel/role '{channel}'")
    if not 20 <= frequency_hz <= 500:
        return _err("frequency_hz must be between 20 and 500")
    try:
        result = await mixer_service.apply_move(ch, "hpf", param=f"{frequency_hz:.0f} Hz", reason="assistant")
        return _ok({"channel": ch, **result})
    except Exception as e:
        return _err(str(e))


@tool
async def mixer_eq(channel: str, frequency_hz: float, gain_db: float) -> str:
    """Parametric EQ move on one channel of the MGX16 software-DSP. `channel` is a role or number; `gain_db` negative = cut (e.g. -3 at 300 Hz for mud, -4 at 2.5 kHz for harshness), positive = boost. Clamped by the DSP to +/-8 dB. Prefer cuts over boosts."""
    ch = _role_channel(channel)
    if ch is None:
        return _err(f"unknown channel/role '{channel}'")
    if not 20 <= frequency_hz <= 20000:
        return _err("frequency_hz must be between 20 and 20000")
    action = "eq_cut" if gain_db < 0 else "eq_boost"
    try:
        result = await mixer_service.apply_move(
            ch, action, param=f"{frequency_hz:.0f} Hz", amount=f"{gain_db:+.1f} dB", reason="assistant"
        )
        return _ok({"channel": ch, **result})
    except Exception as e:
        return _err(str(e))


@tool
async def mixer_compressor(channel: str, threshold_db: float = -18.0, ratio: float = 3.0) -> str:
    """Enable a compressor on one channel of the MGX16 software-DSP (ratio clamped to <= 8:1). Use to even out a speaker's level, e.g. the pastor at -18 dB / 3:1."""
    ch = _role_channel(channel)
    if ch is None:
        return _err(f"unknown channel/role '{channel}'")
    try:
        result = await mixer_service.apply_move(
            ch, "comp", param=f"{threshold_db:.0f} dB", amount=f"ratio {ratio:g}:1", reason="assistant"
        )
        return _ok({"channel": ch, **result})
    except Exception as e:
        return _err(str(e))


@tool
async def mixer_trim(channel: str, delta_db: float) -> str:
    """Digital trim on one channel of the MGX16 software-DSP (clamped -12/+6 dB). This is NOT the desk fader -- it adjusts the level on the USB return path. Use small moves (-2 dB) for a hot channel."""
    ch = _role_channel(channel)
    if ch is None:
        return _err(f"unknown channel/role '{channel}'")
    try:
        result = await mixer_service.apply_move(ch, "gain", amount=f"{delta_db:+.1f} dB", reason="assistant")
        return _ok({"channel": ch, **result})
    except Exception as e:
        return _err(str(e))


@tool
async def mixer_kill_feedback(channel: str) -> str:
    """Detect a feedback ring on a channel RIGHT NOW and drop a surgical notch at its frequency (MGX16 software-DSP). `channel` is a role or number. Only works while the ring is audible; for standing protection use mixer_set_feedback_guard."""
    ch = _role_channel(channel)
    if ch is None:
        return _err(f"unknown channel/role '{channel}'")
    try:
        return _ok(await mixer_service.command(f"channel {ch} has feedback, fix it"))
    except Exception as e:
        return _err(str(e))


@tool
async def mixer_set_feedback_guard(enabled: bool, excluded_channels: Optional[list[int]] = None) -> str:
    """Arm/disarm the autonomous feedback guard on the MGX16 software-DSP: every channel is scanned continuously and a stable narrow ring gets an automatic notch (per-channel cooldown, rate-limited, audited). `excluded_channels` keeps it away from e.g. a keyboard drone. Needs the DSP engaged to actually act."""
    try:
        return _ok(await mixer_service.set_feedback_guard(enabled=enabled, excluded=excluded_channels))
    except Exception as e:
        return _err(str(e))


@tool
async def mixer_set_mix_keeper(enabled: bool) -> str:
    """Arm/disarm the autonomous mix keeper on the MGX16 software-DSP: every ~2 s it checks for hot channels (trim -2 dB), low-end mud on bright sources (engage HPF 100 Hz), and persistent masking (-2 dB on the quieter channel). Deterministic, persistence-gated, bounded. Needs the DSP engaged to actually act."""
    try:
        return _ok(await mixer_service.set_mix_keeper(enabled))
    except Exception as e:
        return _err(str(e))


@tool
async def mixer_analyze_and_advise(apply: bool = False) -> str:
    """Ask the mixer companion's advisor for a prioritized move sheet from the live mix analysis (clipping, mud, masking, over-compression, loudness). With apply=false it only returns suggestions for the operator; apply=true executes them on the software-DSP (requires engaged)."""
    try:
        return _ok(await mixer_service.advise(apply=apply))
    except Exception as e:
        return _err(str(e))


@tool
async def mixer_reset_dsp(channel: Optional[str] = None) -> str:
    """Clear software-DSP processing on one channel (role or number) or, with no channel, on all channels -- returns the desk's untouched signal."""
    ch = None
    if channel is not None:
        ch = _role_channel(channel)
        if ch is None:
            return _err(f"unknown channel/role '{channel}'")
    try:
        return _ok(await mixer_service.reset_dsp(ch))
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


@tool
def request_camera_save_preset(camera_id: int, preset_id: int) -> str:
    """Request to overwrite PTZ preset `preset_id` with the camera's current position. High-risk (presets define the pastor/liturgist/... framings): only registers a pending confirmation; the operator must confirm in the UI first."""
    roles = [
        r
        for r in CAMERA_ROLES
        if getattr(settings, f"camera_role_{r}_camera", None) == camera_id
        and getattr(settings, f"camera_role_{r}_preset", None) == preset_id
    ]
    suffix = f" (used by role: {', '.join(roles)})" if roles else ""
    description = f"Overwrite camera {camera_id} preset {preset_id} with current position{suffix}"
    token = _register_pending(
        "camera_save_preset", {"camera_id": camera_id, "preset_id": preset_id}, description
    )
    return _ok({"pending_confirmation": token, "description": description})


@tool
def request_mixer_engage_dsp(engage: bool = True) -> str:
    """Request to engage (or bypass) the MGX16 software-DSP takeover: the computer is inserted into every USB-patched channel's signal path. High-risk during a live service (it changes what the congregation hears): only registers a pending confirmation; the operator must confirm in the UI first. Bypass returns the untouched desk signal."""
    description = "ENGAGE mixer software-DSP takeover" if engage else "BYPASS mixer software-DSP (return untouched signal)"
    token = _register_pending("mixer_engage_dsp", {"engage": engage}, description)
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
    get_camera_state,
    list_camera_roles,
    get_easyworship_status,
    list_easyworship_items,
    get_mixer_status,
]

CONTROL_TOOLS = [
    atem_switch_camera,
    atem_set_preview,
    atem_show_source,
    atem_cut,
    atem_auto,
    camera_move_to_preset,
    camera_move_to_role,
    camera_move_absolute,
    camera_nudge,
    camera_stop,
    easyworship_slide_action,
    easyworship_select_item,
    easyworship_goto_slide,
    mixer_command,
    mixer_set_hpf,
    mixer_eq,
    mixer_compressor,
    mixer_trim,
    mixer_kill_feedback,
    mixer_set_feedback_guard,
    mixer_set_mix_keeper,
    mixer_analyze_and_advise,
    mixer_reset_dsp,
    director_next_cue,
    director_goto_cue,
    director_start,
    director_stop,
    request_start_streaming,
    request_stop_streaming,
    request_start_recording,
    request_stop_recording,
    request_mic_mute,
    request_camera_save_preset,
    request_mixer_engage_dsp,
]

ALL_TOOLS = QUERY_TOOLS + CONTROL_TOOLS
