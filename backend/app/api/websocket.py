"""WebSocket endpoints for real-time updates."""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.cameras.service import camera_service
from app.config import settings
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/vision")
async def websocket_vision(websocket: WebSocket):
    """WebSocket endpoint for vision event updates."""
    await websocket.accept()
    queue = await event_bus.subscribe()
    try:
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    finally:
        await event_bus.unsubscribe(queue)


@router.websocket("/ws/director")
async def websocket_director(websocket: WebSocket):
    """Stream service-director cue changes and action events."""
    from app.director.engine import service_director

    await websocket.accept()
    # Send the current state immediately so late subscribers are in sync.
    await websocket.send_json({"type": "director", "data": service_director.status().model_dump()})
    queue = await event_bus.subscribe()
    try:
        while True:
            message = await queue.get()
            if isinstance(message, dict) and message.get("type", "").startswith("director"):
                await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    finally:
        await event_bus.unsubscribe(queue)


def _clamp_dir(value) -> int:
    """Coerce a direction to -1, 0, or 1."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    return max(-1, min(1, value))


@router.websocket("/ws/cameras/{camera_id}/joystick")
async def websocket_joystick(websocket: WebSocket, camera_id: int):
    """Press-and-hold PTZ joystick control.

    Continuous VISCA/CGI moves persist until an explicit stop, so this endpoint
    enforces a dead-man switch: while the camera is moving, a new message (a
    ``drive`` or ``keepalive``) must arrive within ``camera_joystick_hold_timeout``
    seconds or the camera is stopped automatically. The camera is also stopped
    when the socket closes.

    Client -> server messages (JSON):
        {"action": "drive", "pan": -1|0|1, "tilt": -1|0|1, "zoom": -1|0|1,
         "pan_speed": 1-24, "tilt_speed": 1-20, "zoom_speed": 0-7}
        {"action": "keepalive"}
        {"action": "stop"}
        {"action": "preset", "preset_id": 1}
        {"action": "move", "pan": <deg>, "tilt": <deg>, "zoom": <pct>}
    """
    await websocket.accept()
    hold_timeout = settings.camera_joystick_hold_timeout
    moving = False

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive_json(), timeout=hold_timeout if moving else None
                )
            except asyncio.TimeoutError:
                # Dead-man switch: no keepalive within the window while moving.
                await camera_service.stop_camera(camera_id)
                moving = False
                await websocket.send_json({"type": "timeout_stop"})
                continue

            action = message.get("action")

            if action == "drive":
                pan = _clamp_dir(message.get("pan", 0))
                tilt = _clamp_dir(message.get("tilt", 0))
                zoom = _clamp_dir(message.get("zoom", 0))
                ok = await camera_service.drive_camera(
                    camera_id,
                    pan_dir=pan,
                    tilt_dir=tilt,
                    zoom_dir=zoom,
                    pan_speed=int(message.get("pan_speed", 12)),
                    tilt_speed=int(message.get("tilt_speed", 12)),
                    zoom_speed=int(message.get("zoom_speed", 4)),
                )
                moving = ok and (pan != 0 or tilt != 0 or zoom != 0)
                await websocket.send_json({"ok": ok, "action": "drive", "moving": moving})

            elif action in ("keepalive", "ping"):
                await websocket.send_json({"ok": True, "action": "keepalive", "moving": moving})

            elif action == "stop":
                ok = await camera_service.stop_camera(camera_id)
                moving = False
                await websocket.send_json({"ok": ok, "action": "stop"})

            elif action == "preset":
                ok = await camera_service.move_to_preset(camera_id, int(message.get("preset_id", 0)))
                await websocket.send_json({"ok": ok, "action": "preset"})

            elif action == "move":
                ok = await camera_service.move_camera(
                    camera_id,
                    pan=message.get("pan"),
                    tilt=message.get("tilt"),
                    zoom=message.get("zoom"),
                )
                await websocket.send_json({"ok": ok, "action": "move"})

            else:
                await websocket.send_json({"ok": False, "error": f"unknown action: {action}"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Joystick websocket error", camera_id=camera_id, error=str(e))
    finally:
        if moving:
            try:
                await camera_service.stop_camera(camera_id)
            except Exception:
                pass
