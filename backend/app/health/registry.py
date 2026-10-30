"""Component health aggregation.

Pulls each subsystem's own status accessor (defensively: a broken accessor is a
``degraded`` component, never an exception) and reduces them to one readiness
verdict. Nothing here triggers inference, hardware I/O or network calls beyond
one cheap ``SELECT 1`` on the database, so it is safe to poll every few seconds.

Each component is reported as::

    {"status": "ok" | "degraded" | "down" | "off" | "mock", "detail": str, ...extra}

``off`` means the feature is disabled by configuration; ``mock`` means a mock
integration is in use. Neither counts against readiness; ``down`` does.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_db_cache: tuple[float, dict] = (float("-inf"), {})
_DB_CACHE_SECONDS = 10.0


def _component(status: str, detail: str = "", **extra: Any) -> dict:
    return {"status": status, "detail": detail, **extra}


def _safe(name: str, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - a failing probe is itself a finding
        logger.warning("health probe failed", component=name, error=str(exc))
        return _component("degraded", f"probe failed: {exc}")


def _atem() -> dict:
    from app.dependencies import get_atem_service_instance

    service = get_atem_service_instance()
    connected = bool(getattr(service, "_connected", False))
    mock = bool(getattr(service, "mock", False))
    if connected:
        return _component("mock" if mock else "ok", "connected", mock=mock)
    return _component("down", "not connected", mock=mock)


def _cameras() -> dict:
    from app.cameras.service import camera_service

    cameras = camera_service.list_cameras()
    if not cameras:
        return _component("off", "no cameras configured", cameras=[])
    connected = [c for c in cameras if c.get("connected")]
    status = "ok" if len(connected) == len(cameras) else ("degraded" if connected else "down")
    return _component(status, f"{len(connected)}/{len(cameras)} connected", cameras=cameras)


def _easyworship() -> dict:
    from app.easyworship.service import easyworship_service

    info = easyworship_service.status()
    driver = info.get("driver", "")
    if "Mock" in driver:
        return _component("mock", driver, **{k: info.get(k) for k in ("current_item_label",)})
    return _component("ok" if info.get("connected") else "down", driver, current_item=info.get("current_item_label"))


def _mixer() -> dict:
    from app.mixer.service import mixer_service

    info = mixer_service.status()
    if info.get("mock"):
        return _component("mock", "mock meters")
    if not info.get("connected"):
        return _component("down", "meter feed disconnected")
    if info.get("stale"):
        return _component("degraded", f"meters stale > {info.get('stale_seconds')}s")
    return _component("ok", "meter feed live")


def _audio() -> dict:
    from app.audio.audio_observer import audio_observer

    info = audio_observer.perception_status()
    if not info.get("usb_enabled"):
        return _component("off", "USB capture disabled (meter feed only)", usb=False)
    if not info.get("usb_active"):
        return _component("degraded", "USB capture not running; meter fallback", usb=False)
    channels = info.get("channels", {})
    fallen = [r for r, c in channels.items() if c.get("source") != "usb"]
    status = "degraded" if fallen else "ok"
    detail = f"{len(channels) - len(fallen)}/{len(channels)} roles on USB"
    return _component(status, detail, usb=True, fallen_back=fallen)


def _ollama() -> dict:
    from app.agents.ollama_discovery import ollama_locator

    info = ollama_locator.status()
    base = info.get("base_url")
    if not settings.enable_ai_director:
        return _component("off", "AI director disabled", base_url=base)
    if info.get("enabled") and not info.get("host"):
        return _component("degraded", "Ollama not discovered yet", base_url=base)
    return _component("ok", f"configured ({info.get('source') or 'static'})", base_url=base)


def _ai_director() -> dict:
    from app.director.ai_director_runtime import ai_director_runtime

    if not settings.enable_ai_director:
        return _component("off", "disabled")
    running = bool(getattr(ai_director_runtime, "_task", None)) or bool(getattr(ai_director_runtime, "running", False))
    degraded = bool(getattr(ai_director_runtime, "degraded", False))
    mode = getattr(ai_director_runtime, "mode", "unknown")
    if not running:
        return _component("down", "runtime not running", mode=mode)
    return _component("degraded" if degraded else "ok", f"mode {mode}", mode=mode)


def _slide_verification() -> dict:
    if not settings.slide_verify_enabled:
        return _component("off", "disabled")
    from app.easyworship.slide_verification import slide_verifier

    snap = slide_verifier.snapshot()
    if snap.get("ocr_available") is False:
        return _component("degraded", snap.get("last_unknown_reason") or "OCR unavailable", **{"consecutive_unknown": snap.get("consecutive_unknown")})
    if snap.get("consecutive_unknown", 0) >= 3:
        return _component("degraded", f"{snap['consecutive_unknown']} unknown verifications in a row")
    return _component("ok", "verifying")


def _voice() -> dict:
    from app.voice.service import get_voice_service

    info = get_voice_service().status()
    if not info.get("enabled"):
        return _component("off", "disabled")
    headset = info.get("headset", {})
    return _component("ok" if headset.get("ready") else "degraded", headset.get("error") or "headset ready")


def _database_probe() -> dict:
    from sqlalchemy import text

    from app.database.connection import _engine

    started = time.monotonic()
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return _component("down", f"unreachable: {type(exc).__name__}", host=settings.postgres_host)
    return _component("ok", f"reachable in {(time.monotonic() - started) * 1000:.0f} ms", host=settings.postgres_host)


def _database() -> dict:
    global _db_cache
    at, cached = _db_cache
    if time.monotonic() - at < _DB_CACHE_SECONDS and cached:
        return cached
    result = _database_probe()
    _db_cache = (time.monotonic(), result)
    return result


PROBES: Dict[str, Callable[[], dict]] = {
    "database": _database,
    "atem": _atem,
    "cameras": _cameras,
    "easyworship": _easyworship,
    "mixer": _mixer,
    "audio": _audio,
    "ollama": _ollama,
    "ai_director": _ai_director,
    "slide_verification": _slide_verification,
    "voice": _voice,
}


def component_health() -> Dict[str, dict]:
    """Status of every component; never raises."""
    return {name: _safe(name, probe) for name, probe in PROBES.items()}


def overall_status(components: Dict[str, dict]) -> str:
    """``ready`` when nothing is down, ``degraded`` when something is degraded, else ``not_ready``."""
    statuses = {c.get("status") for c in components.values()}
    if "down" in statuses:
        return "not_ready"
    if "degraded" in statuses:
        return "degraded"
    return "ready"


async def component_health_async() -> Dict[str, dict]:
    """Run the probes off the event loop (the database probe does real I/O)."""
    return await asyncio.to_thread(component_health)
