"""Rig profile API: read, validate and replace the per-church ``rig.yaml``.

``GET /api/rig`` returns the active profile with camera passwords masked;
``GET /api/rig/example`` the commented ``rig.example.yaml``;
``POST /api/rig/validate`` dry-runs a candidate (YAML text or a JSON
profile) and reports errors/warnings; ``PUT /api/rig`` (admin key) validates,
writes ``settings.rig_profile_file`` atomically, reloads the singleton and
says which consumers picked the change up live and which need a restart.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.logging_config import get_logger
from app.rig import RigProfile, RigProfileError, profile_path, reload, rig
from app.rig.profile import example_yaml

logger = get_logger(__name__)

router = APIRouter(prefix="/api/rig", tags=["Rig profile"])

ROLE_ADMIN = "admin"

# Section -> who reads it and whether that reader re-reads the profile on every
# use (live) or only at startup (restart). Keep in step with docs/rig-profile.md.
LIVE_SECTIONS: dict[str, str] = {
    "roles": "cue sheet, camera_service.move_to_role, AI Director PTZ_SELECT_ROLE, context sync",
    "cues": "cue sheet (next script load / order-of-service upload)",
    "heading_synonyms": "order-of-service parser",
    "follower": "slide follower cue list",
    "church": "script titles",
    "switcher": "camera -> ATEM input map, slides input (context sync, PTZ verification)",
    "presentation": "informational",
}
RESTART_SECTIONS: dict[str, str] = {
    "cameras": "camera registration/connection and vision snapshot inputs happen at startup",
    "mixer": "the USB MAIN capture binds role channels when it starts",
}


class RigDocument(BaseModel):
    """A candidate profile: ``yaml`` text, or the profile as a JSON object."""

    yaml: Optional[str] = None
    profile: Optional[dict[str, Any]] = None


def _require_admin(request: Request) -> None:
    role = getattr(request.state, "role", None)
    if role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin API key required to change the rig profile")


async def _candidate(request: Request, body: Optional[Union[RigDocument, dict[str, Any]]]) -> tuple[RigProfile, str]:
    """Parse the request into a validated profile and the YAML text to persist."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    label = "rig profile"
    if content_type in ("text/yaml", "application/x-yaml", "application/yaml", "text/plain"):
        text = (await request.body()).decode("utf-8")
        profile = RigProfile.from_yaml(text, source="memory", path=label)
        return profile, text
    if body is None:
        raise HTTPException(status_code=422, detail="Send YAML text or a JSON body with 'yaml' or 'profile'")
    if isinstance(body, RigDocument):
        if body.yaml is not None:
            profile = RigProfile.from_yaml(body.yaml, source="memory", path=label)
            return profile, body.yaml
        if body.profile is not None:
            profile = RigProfile.from_data(body.profile, source="memory", path=label)
            return profile, profile.to_yaml()
        raise HTTPException(status_code=422, detail="JSON body needs 'yaml' (text) or 'profile' (object)")
    profile = RigProfile.from_data(body, source="memory", path=label)
    return profile, profile.to_yaml()


async def _parse_body(request: Request) -> Optional[Union[RigDocument, dict[str, Any]]]:
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return None
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(status_code=422, detail="Body is not valid JSON")
    if isinstance(data, dict) and set(data) <= {"yaml", "profile"}:
        return RigDocument(**data)
    if isinstance(data, dict):
        return data
    raise HTTPException(status_code=422, detail="JSON body must be an object")


def _check(profile: RigProfile) -> tuple[list[str], list[str]]:
    """(errors, warnings) from building the cue catalog with ``profile``."""
    from app.director.script import build_cue_catalog, profile_warnings

    try:
        build_cue_catalog(profile)
    except (ValueError, RigProfileError) as exc:
        return [str(exc)], []
    return [], profile_warnings(profile)


def _changed_sections(before: RigProfile, after: RigProfile) -> list[str]:
    old = before.to_data()
    new = after.to_data()
    return [key for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key)]


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@router.get("")
@router.get("/")
async def get_rig():
    """The active profile (camera passwords masked) and where it came from."""
    profile = rig()
    return {
        "profile": profile.masked(),
        "summary": profile.summary(),
        "source": profile.source,
        "path": profile.path or str(profile_path()),
    }


@router.get("/example")
async def get_example():
    """The commented rig.example.yaml shipped with the repo, as text."""
    from fastapi.responses import PlainTextResponse

    try:
        return PlainTextResponse(example_yaml(), media_type="text/yaml; charset=utf-8")
    except OSError:
        raise HTTPException(status_code=404, detail="rig.example.yaml is not installed")


@router.post("/validate")
async def validate_rig(request: Request):
    """Dry run: parse and validate a candidate profile without saving it."""
    try:
        profile, _text = await _candidate(request, await _parse_body(request))
    except RigProfileError as exc:
        return {"ok": False, "errors": str(exc).splitlines(), "warnings": []}
    errors, warnings = _check(profile)
    result = {"ok": not errors, "errors": errors, "warnings": warnings}
    if not errors:
        result["summary"] = profile.summary()
        result["changed_sections"] = _changed_sections(rig(), profile)
    return result


@router.put("")
@router.put("/")
async def put_rig(request: Request):
    """Replace the rig profile (admin only): validate, write atomically, reload."""
    _require_admin(request)
    try:
        profile, text = await _candidate(request, await _parse_body(request))
    except RigProfileError as exc:
        raise HTTPException(status_code=422, detail={"errors": str(exc).splitlines()})
    errors, warnings = _check(profile)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors, "warnings": warnings})

    previous = rig()
    changed = _changed_sections(previous, profile)
    target = profile_path()
    try:
        _write_atomic(target, text if text.endswith("\n") else text + "\n")
        active = reload()
    except RigProfileError as exc:  # pragma: no cover - the same text validated a moment ago
        raise HTTPException(status_code=500, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot write {target}: {exc}")

    applied_live = [s for s in changed if s in LIVE_SECTIONS or s not in RESTART_SECTIONS]
    restart_required = [s for s in changed if s in RESTART_SECTIONS]
    logger.info("Rig profile replaced", path=str(target), changed=changed, restart_required=restart_required)
    return {
        "ok": True,
        "path": str(target),
        "changed_sections": changed,
        "applied_live": {s: LIVE_SECTIONS.get(s, "re-read on next use") for s in applied_live},
        "restart_required": {s: RESTART_SECTIONS[s] for s in restart_required},
        "warnings": warnings,
        "summary": active.summary(),
    }
