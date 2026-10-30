"""First-run setup wizard API (``/api/setup``).

Reports which required settings are configured, runs bounded network
discovery and connectivity tests, lists audio input devices, and (admin only)
writes an allow-listed set of keys into the repo ``.env`` atomically. Secrets
are never echoed back: the status only says whether a secret is *set*.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import Settings, settings
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/setup", tags=["Setup"])

PLACEHOLDER_SECRETS = {"", "changeme", "your-secret-key-here-change-in-production"}
TEST_COMPONENTS = ("atem", "camera", "easyworship", "ollama", "mixer", "database")

# Keys the wizard may write. Exact names plus a few prefix families.
ALLOWED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "ATEM_IP", "ATEM_RESOLVE_SUBNETS",
        "CAMERA_1_HOST", "CAMERA_1_USERNAME", "CAMERA_1_PASSWORD", "CAMERA_1_VISCA_PORT", "CAMERA_1_VISCA_UDP",
        "EASYWORSHIP_REMOTE_HOST", "OLLAMA_BASE_URL",
        "MGX_USB_DEVICE_NAME", "MGX_USB_ENABLED",
        "SLIDE_VERIFY_DEVICE", "API_HOST",
    }
)
ALLOWED_ENV_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^ENABLE_MOCK_[A-Z0-9_]+$"),
    re.compile(r"^MIXER_[A-Z0-9_]+_CHANNEL$"),
    re.compile(r"^CAMERA_ROLE_[A-Z0-9_]+$"),
)
SECRET_ENV_KEYS: frozenset[str] = frozenset({"CAMERA_1_PASSWORD"})
_ENV_LINE = re.compile(r"^(?P<export>export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<rest>.*)$")
_MAX_VALUE_CHARS = 512


def _setting(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _timeout() -> float:
    return float(_setting("setup_probe_timeout_seconds", 8.0))


def env_file_path() -> Path:
    configured = str(_setting("setup_env_file", "") or "").strip()
    if configured:
        return Path(configured)
    return Path(Settings.Config.env_file)


def is_allowed_env_key(key: str) -> bool:
    return key in ALLOWED_ENV_KEYS or any(p.match(key) for p in ALLOWED_ENV_PATTERNS)


def _is_set(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _loopback(host: Optional[str]) -> bool:
    with contextlib.suppress(Exception):
        from app.security.auth import is_loopback_host

        return is_loopback_host(host)
    return host in ("127.0.0.1", "localhost", "::1")


# -- status ---------------------------------------------------------------------------


def _item(key: str, label: str, *, configured: bool, required: bool, mock: Optional[bool] = None, value: Optional[str] = None, hint: str = "") -> dict:
    return {
        "key": key,
        "label": label,
        "configured": configured,
        "required": required,
        "missing": required and not configured,
        "mock": mock,
        "value": value,
        "hint": hint,
    }


def setup_status() -> dict:
    s = settings
    all_mocked = bool(s.enable_mock_atem and s.enable_mock_mixer and s.enable_mock_easyworship)
    keys_set = _is_set(s.api_key) or _is_set(s.api_admin_key)
    api_loopback = _loopback(s.api_host)

    items = [
        _item(
            "atem", "ATEM switcher",
            configured=_is_set(s.atem_ip) or (s.atem_resolve_enabled and _is_set(s.atem_resolve_subnets)),
            required=not s.enable_mock_atem, mock=s.enable_mock_atem,
            value=s.atem_ip or None, hint="ATEM_IP or ATEM_RESOLVE_SUBNETS (DHCP discovery by MAC)",
        ),
        _item(
            "camera_1", "PTZ camera 1",
            configured=_is_set(s.camera_1_host) and _is_set(s.camera_1_password),
            required=not all_mocked, mock=None,
            value=s.camera_1_host or None,
            hint="CAMERA_1_HOST, CAMERA_1_USERNAME, CAMERA_1_PASSWORD" + ("" if not _is_set(s.camera_1_password) else " (password set)"),
        ),
        _item(
            "easyworship", "EasyWorship",
            configured=_is_set(s.easyworship_remote_host) or s.easyworship_driver in ("auto", "remote"),
            required=not s.enable_mock_easyworship, mock=s.enable_mock_easyworship,
            value=s.easyworship_remote_host or ("mDNS discovery" if s.easyworship_driver in ("auto", "remote") else None),
            hint="EASYWORSHIP_REMOTE_HOST, or leave empty for mDNS discovery",
        ),
        _item(
            "ollama", "Ollama (AI inference)",
            configured=_is_set(s.ollama_base_url) or bool(s.ollama_discovery_enabled),
            required=bool(s.enable_ai_director), mock=None,
            value=s.ollama_base_url or ("auto-discovery" if s.ollama_discovery_enabled else None),
            hint="OLLAMA_BASE_URL, or leave empty for discovery on the EasyWorship PC",
        ),
        _item(
            "mixer_usb", "Mixer audio (USB)",
            configured=bool(s.mgx_usb_enabled and _is_set(s.mgx_usb_device_name)) or (not s.enable_mock_mixer and _is_set(s.mixer_ws_url)),
            required=not s.enable_mock_mixer, mock=s.enable_mock_mixer,
            value=(s.mgx_usb_device_name if s.mgx_usb_enabled else None),
            hint="MGX_USB_ENABLED=true and MGX_USB_DEVICE_NAME (per-channel PCM), else the meter feed",
        ),
        _item(
            "database", "PostgreSQL",
            configured=str(s.postgres_password or "").strip() not in PLACEHOLDER_SECRETS,
            required=not all_mocked, mock=None,
            value=f"{s.postgres_host}:{s.postgres_port}/{s.postgres_database}",
            hint="POSTGRES_PASSWORD (the Windows setup script generates one)",
        ),
        _item(
            "api_keys", "API keys",
            configured=keys_set or not s.api_auth_enabled,
            required=not api_loopback, mock=None,
            value=("operator" if _is_set(s.api_key) else "") + ("+admin" if _is_set(s.api_admin_key) else "") or None,
            hint="API_KEY / API_ADMIN_KEY (required when API_HOST is not loopback)",
        ),
        _item(
            "slides_tap", "Slides capture (camera 2 tap)",
            configured=_is_set(s.slide_verify_device),
            required=bool(s.slide_verify_enabled), mock=None,
            value=s.slide_verify_device or None,
            hint="SLIDE_VERIFY_DEVICE (cv2 device index/name of the EasyWorship output tap)",
        ),
    ]
    missing = [i["key"] for i in items if i["missing"]]
    return {
        "enabled": bool(_setting("setup_wizard_enabled", True)),
        "needs_setup": bool(_setting("setup_wizard_enabled", True)) and bool(missing),
        "missing": missing,
        "all_mocked": all_mocked,
        "mocks": {
            "atem": bool(s.enable_mock_atem),
            "mixer": bool(s.enable_mock_mixer),
            "easyworship": bool(s.enable_mock_easyworship),
        },
        "api_host": s.api_host,
        "api_loopback": api_loopback,
        "keys": {"operator": _is_set(s.api_key), "admin": _is_set(s.api_admin_key), "auth_enabled": bool(s.api_auth_enabled)},
        "env_file": str(env_file_path()),
        "items": items,
    }


@router.get("/status")
async def get_status():
    """Which required settings are configured vs missing (secrets masked)."""
    return setup_status()


# -- discovery --------------------------------------------------------------------------


async def _bounded(coro, timeout: Optional[float] = None):
    return await asyncio.wait_for(coro, timeout or _timeout())


@router.post("/discover/atem")
async def discover_atem():
    """Sweep ATEM_RESOLVE_SUBNETS for a Blackmagic MAC (bounded)."""
    from app.atem.resolver import atem_resolver

    try:
        found = await _bounded(atem_resolver.resolve(force=True), max(_timeout(), 20.0))
    except asyncio.TimeoutError:
        return {"found": None, "detail": "discovery timed out", "subnets": settings.atem_resolve_subnets}
    return {
        "found": found,
        "detail": "ATEM found" if found else "no ATEM answered; check ATEM_RESOLVE_SUBNETS or set ATEM_IP",
        "subnets": settings.atem_resolve_subnets,
        "configured": settings.atem_ip or None,
    }


@router.post("/discover/easyworship")
async def discover_easyworship_route():
    """Find the EasyWorship remote server via mDNS (needs the optional zeroconf package)."""
    try:
        import zeroconf  # noqa: F401
    except ImportError:
        return {"found": None, "detail": "zeroconf not installed; enter EASYWORSHIP_REMOTE_HOST manually"}
    from app.easyworship.remote_protocol import discover_easyworship

    try:
        found = await _bounded(
            discover_easyworship(settings.easyworship_remote_discovery_seconds, settings.easyworship_remote_server_name),
            settings.easyworship_remote_discovery_seconds + 5.0,
        )
    except Exception as exc:  # noqa: BLE001 - includes asyncio.TimeoutError
        return {"found": None, "detail": f"discovery failed: {type(exc).__name__}"}
    if not found:
        return {"found": None, "detail": "no EasyWorship remote server advertised; enable Remote in EasyWorship"}
    host, port, name = found
    return {"found": {"host": host, "port": port, "name": name}, "detail": f"found {name}"}


@router.post("/discover/ollama")
async def discover_ollama():
    """Locate Ollama (configured URL first, else the DHCP discovery path)."""
    from app.agents.ollama_discovery import ollama_locator, probe_ollama

    configured = settings.ollama_base_url.strip()
    try:
        if configured:
            host = urlsplit(configured).hostname or configured
            models = await _bounded(probe_ollama(host))
            return {
                "found": host if models is not None else None,
                "base_url": configured,
                "models": models or [],
                "detail": "configured OLLAMA_BASE_URL answers" if models is not None else "configured OLLAMA_BASE_URL does not answer",
            }
        host = await _bounded(ollama_locator.resolve(force=True), max(_timeout(), 30.0))
    except asyncio.TimeoutError:
        return {"found": None, "detail": "discovery timed out", "base_url": ollama_locator.base_url()}
    info = ollama_locator.status()
    return {
        "found": host,
        "base_url": info.get("base_url"),
        "source": info.get("source"),
        "models": [],
        "detail": "Ollama found" if host else "Ollama not found; set OLLAMA_BASE_URL or start it on the EasyWorship PC",
    }


# -- audio devices ----------------------------------------------------------------------


def list_audio_devices() -> dict:
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001 - optional dependency / missing PortAudio
        return {"devices": [], "note": "sounddevice (PortAudio) is not installed; USB capture is unavailable"}
    try:
        raw = sd.query_devices()
        hostapis = list(sd.query_hostapis()) if hasattr(sd, "query_hostapis") else []
    except Exception as exc:  # noqa: BLE001
        return {"devices": [], "note": f"could not query audio devices: {type(exc).__name__}"}
    wanted = str(settings.mgx_usb_device_name or "").lower()
    devices = []
    for index, dev in enumerate(raw):
        channels = int(dev.get("max_input_channels", 0) or 0)
        if channels <= 0:
            continue
        api = dev.get("hostapi")
        devices.append(
            {
                "index": index,
                "name": dev.get("name", f"device {index}"),
                "max_input_channels": channels,
                "default_samplerate": dev.get("default_samplerate"),
                "hostapi": hostapis[api]["name"] if isinstance(api, int) and api < len(hostapis) else None,
                "matches_configured": bool(wanted) and wanted in str(dev.get("name", "")).lower(),
            }
        )
    return {"devices": devices, "note": None, "configured_name": settings.mgx_usb_device_name}


@router.get("/audio-devices")
async def audio_devices():
    """Audio input devices visible to PortAudio (for MGX_USB_DEVICE_NAME)."""
    return await asyncio.to_thread(list_audio_devices)


# -- connectivity tests -----------------------------------------------------------------


class TestRequest(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    visca_udp: Optional[bool] = None
    device_name: Optional[str] = None


async def _tcp_reachable(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError) as exc:
        return False, f"{host}:{port} unreachable ({type(exc).__name__})"
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True, f"{host}:{port} reachable"


def _health(component: str) -> dict:
    from app.health import registry

    probe = registry.PROBES.get(component)
    return registry._safe(component, probe) if probe else {"status": "off", "detail": "no probe"}


async def _test_atem(_: TestRequest) -> dict:
    from app.dependencies import get_atem_service_instance

    service = get_atem_service_instance()
    connected = await service.connect()
    health = _health("atem")
    return {"ok": bool(connected), "detail": health.get("detail", ""), "mock": bool(getattr(service, "mock", False))}


async def _test_camera(req: TestRequest) -> dict:
    host = req.host or settings.camera_1_host
    if not host:
        return {"ok": False, "detail": "no camera host configured"}
    if req.visca_udp if req.visca_udp is not None else settings.camera_1_visca_udp:
        port = req.port or settings.camera_1_port
        ok, detail = await _tcp_reachable(host, port, _timeout())
        return {"ok": ok, "detail": f"HTTP-CGI {detail} (VISCA over UDP cannot be probed)"}
    port = req.port or settings.camera_1_visca_port
    ok, detail = await _tcp_reachable(host, port, _timeout())
    return {"ok": ok, "detail": f"VISCA {detail}"}


async def _test_easyworship(req: TestRequest) -> dict:
    host = req.host or settings.easyworship_remote_host
    if host:
        port = req.port or settings.easyworship_remote_port or 0
        if port:
            ok, detail = await _tcp_reachable(host, int(port), _timeout())
            return {"ok": ok, "detail": f"EasyWorship remote {detail}"}
    health = _health("easyworship")
    return {"ok": health.get("status") in ("ok", "mock"), "detail": health.get("detail", ""), "mock": health.get("status") == "mock"}


async def _test_ollama(req: TestRequest) -> dict:
    from app.agents.ollama_discovery import has_model, ollama_locator, probe_ollama

    host = req.host or urlsplit(ollama_locator.base_url()).hostname or "127.0.0.1"
    models = await probe_ollama(host)
    if models is None:
        return {"ok": False, "detail": f"Ollama at {host} did not answer /api/tags"}
    present = has_model(models, settings.ollama_model)
    return {
        "ok": present,
        "detail": f"{len(models)} model(s); {settings.ollama_model} {'installed' if present else 'MISSING (ollama pull)'}",
        "models": models,
    }


async def _test_mixer(req: TestRequest) -> dict:
    if settings.mgx_usb_enabled or req.device_name:
        wanted = (req.device_name or settings.mgx_usb_device_name or "").lower()
        listing = await asyncio.to_thread(list_audio_devices)
        matches = [d for d in listing["devices"] if wanted and wanted in d["name"].lower()]
        if listing.get("note") and not listing["devices"]:
            return {"ok": False, "detail": listing["note"]}
        return {
            "ok": bool(matches),
            "detail": f"{len(matches)} input device(s) match '{wanted}'" if wanted else "no device name given",
            "devices": [d["name"] for d in matches],
        }
    health = _health("mixer")
    return {"ok": health.get("status") in ("ok", "mock"), "detail": health.get("detail", ""), "mock": health.get("status") == "mock"}


async def _test_database(_: TestRequest) -> dict:
    from app.health import registry

    result = await asyncio.to_thread(registry._database_probe)
    return {"ok": result.get("status") == "ok", "detail": result.get("detail", "")}


_TESTS = {
    "atem": _test_atem,
    "camera": _test_camera,
    "easyworship": _test_easyworship,
    "ollama": _test_ollama,
    "mixer": _test_mixer,
    "database": _test_database,
}


@router.post("/test/{component}")
async def test_component(component: str, request: Optional[TestRequest] = None):
    """Bounded connectivity test for one component; returns ``{ok, detail}``."""
    probe = _TESTS.get(component)
    if probe is None:
        raise HTTPException(status_code=404, detail=f"unknown component; one of {', '.join(TEST_COMPONENTS)}")
    try:
        result = await _bounded(probe(request or TestRequest()), _timeout() + 2.0)
    except asyncio.TimeoutError:
        result = {"ok": False, "detail": f"{component} test timed out after {_timeout():.0f}s"}
    except Exception as exc:  # noqa: BLE001 - the message must stay secret-free
        logger.warning("setup test failed", component=component, error=type(exc).__name__)
        result = {"ok": False, "detail": f"{component} test failed: {type(exc).__name__}"}
    return {"component": component, **result}


# -- .env writer -------------------------------------------------------------------------


class EnvWriteRequest(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)


def _format_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > _MAX_VALUE_CHARS:
        raise ValueError(f"{key} is longer than {_MAX_VALUE_CHARS} characters")
    if "\n" in text or "\r" in text:
        raise ValueError(f"{key} must be a single line")
    return text


def render_env(existing: str, updates: Dict[str, str]) -> str:
    """Replace ``KEY=value`` lines in place (keeping inline comments and order);
    append keys that are not present yet."""
    pending = dict(updates)
    out: List[str] = []
    for line in existing.splitlines():
        match = _ENV_LINE.match(line.strip())
        if match and match.group("key").upper() in pending:
            key = match.group("key").upper()
            rest = match.group("rest")
            comment = ""
            # Keep a trailing "# comment" when the existing value had one.
            comment_match = re.search(r"\s+#.*$", rest)
            if comment_match and not rest.strip().startswith("#"):
                comment = comment_match.group(0)
            elif rest.strip().startswith("#"):
                comment = "  " + rest.strip()
            out.append(f"{match.group('export') or ''}{key}={pending.pop(key)}{comment}")
        else:
            out.append(line)
    if pending:
        if out and out[-1].strip():
            out.append("")
        out.append("# Added by the setup wizard")
        out.extend(f"{key}={value}" for key, value in pending.items())
    return "\n".join(out) + "\n"


def write_env(updates: Dict[str, str], path: Optional[Path] = None) -> List[str]:
    path = path or env_file_path()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    rendered = render_env(existing, updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        if path.exists():
            with contextlib.suppress(OSError):
                os.chmod(tmp_name, path.stat().st_mode & 0o777)
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return list(updates)


def _role(request: Request) -> Optional[str]:
    return getattr(request.state, "role", None) if hasattr(request, "state") else None


@router.put("/env")
async def put_env(body: EnvWriteRequest, request: Request):
    """Write allow-listed keys to the repo ``.env`` (admin only). A restart applies them."""
    if _role(request) != "admin":
        raise HTTPException(status_code=403, detail="Admin API key required to write .env")
    if not body.values:
        raise HTTPException(status_code=422, detail="no values given")
    updates: Dict[str, str] = {}
    unknown: List[str] = []
    for raw_key, value in body.values.items():
        key = str(raw_key).strip().upper()
        if not is_allowed_env_key(key):
            unknown.append(key)
            continue
        try:
            updates[key] = _format_value(key, value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    if unknown:
        raise HTTPException(status_code=422, detail=f"keys not allowed: {', '.join(sorted(unknown))}")
    try:
        written = await asyncio.to_thread(write_env, updates)
    except OSError as exc:
        logger.error("setup wizard could not write .env", error=type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"could not write .env: {type(exc).__name__}")
    logger.info("setup wizard wrote .env", keys=[k for k in written if k not in SECRET_ENV_KEYS], secrets=len([k for k in written if k in SECRET_ENV_KEYS]))
    return {"written": written, "restart_required": True, "env_file": str(env_file_path())}
