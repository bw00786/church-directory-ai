"""Vision layer startup wiring (WO-VISION-1).

Registers the configured frame-capture inputs (program + per-camera snapshot, or
a directory replay source) and starts the perception loop. Kept out of main.py
so the wiring is testable and the lifespan stays readable. Only invoked when
``VISION_ENABLED`` is true.
"""

from __future__ import annotations

from app.config import settings
from app.logging_config import get_logger
from app.vision.frame_capture import device_provider, dir_provider, frame_capture, snapshot_provider
from app.vision.perception import perception_loop

logger = get_logger(__name__)


def _register_inputs() -> None:
    use_dir = settings.vision_frame_source == "dir" and settings.vision_frame_dir

    # Program output input.
    if use_dir:
        frame_capture.register_input("program", dir_provider(f"{settings.vision_frame_dir}/program"))
    elif settings.vision_program_device is not None:
        frame_capture.register_input("program", device_provider(settings.vision_program_device))

    # Per-camera direct snapshot inputs (all configured roles share camera ids).
    cameras = {
        getattr(settings, f"camera_role_{r}_camera", None)
        for r in ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide")
    }
    for camera_id in sorted(c for c in cameras if c is not None):
        tag = f"camera_{camera_id}"
        if use_dir:
            frame_capture.register_input(tag, dir_provider(f"{settings.vision_frame_dir}/{tag}"))
        elif settings.camera_1_host:
            url = f"http://{settings.camera_1_host}/snapshot.jpg"
            auth = None
            if settings.camera_1_username:
                auth = (settings.camera_1_username, settings.camera_1_password or "")
            frame_capture.register_input(tag, snapshot_provider(url, auth))


async def start_vision_layer() -> None:
    _register_inputs()
    await frame_capture.start()
    try:
        await perception_loop.start()  # validates ROIs; may raise on misconfig
    except Exception:
        logger.exception("Perception loop refused to start (check VISION_ROLE_ROI_*)")
    # FR-5: register vision verdicts with WO-CONF-1 if it has landed.
    try:
        from app.vision.evidence import wire_into_conf

        wire_into_conf()
    except Exception:
        logger.exception("Vision evidence wiring failed")


async def stop_vision_layer() -> None:
    await perception_loop.stop()
    await frame_capture.stop()
