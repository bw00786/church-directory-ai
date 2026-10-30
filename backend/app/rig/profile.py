"""Per-church rig profile: models, loader and the module singleton.

A ``rig.yaml`` next to ``.env`` describes one church's hardware and liturgy:

- ``cameras``   -- every PTZ camera (driver, host, credentials, ATEM input)
- ``switcher``  -- ATEM input names and which input carries the slides
- ``mixer``     -- mixer channel per audio role (any role names)
- ``presentation`` -- EasyWorship host / schedule offset
- ``roles``     -- PTZ framing role -> (camera, preset); any role names
- ``cues``      -- overrides / additions / removals on the built-in cue sheet
- ``heading_synonyms`` -- extra order-of-service heading regexes per cue id
- ``follower``  -- which cues / transcript roles the slide follower uses
- ``church``    -- name, timezone, service time and days

Every section is optional. Missing sections come from the ``.env`` settings
(``camera_1_*``, ``atem_camera*_input``, ``mixer_*_channel``,
``camera_role_*`` ...), so a profile that only lists ``cues`` still describes
the whole rig, and with no ``rig.yaml`` at all :func:`rig` reproduces today's
behaviour exactly (:meth:`RigProfile.default_from_settings`).

Secrets may be written as environment references (``password:
"${CAMERA_1_PASSWORD}"``); they are resolved when a driver needs them and are
never echoed back by the API (:meth:`RigProfile.masked`).

Validation errors are fatal and name the offending field
(``rig.yaml: cameras[1].atem_input: Input should be a valid integer``).
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.config import settings
from app.domain.service_state import ServiceState

# backend/app/rig/profile.py -> repo root (where .env and rig.example.yaml live).
REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_FILE = REPO_ROOT / "rig.example.yaml"
DEFAULT_PROFILE_FILE = "rig.yaml"

# The built-in church the cue sheet was written for; ``church.name`` default.
DEFAULT_CHURCH_NAME = "Vernon UMC"

# Role names that have camera_role_<role>_* settings in app.config.
SETTINGS_CAMERA_ROLES: tuple[str, ...] = ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide")
# Role names that have mixer_<role>_channel settings in app.config (in the
# order configured_role_channels() has always returned them).
SETTINGS_MIXER_ROLES: tuple[str, ...] = ("pastor", "liturgist", "vocalist", "congregation")

SLIDES_INPUT_NAME = "slides"
MASK = "***"

_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

CueAdvance = Literal["manual", "timer", "song_end", "ai"]
CueTransition = Literal["cut", "auto"]
ActionKind = Literal["atem_program", "ptz_preset", "slide", "note"]


class RigProfileError(ValueError):
    """A rig profile could not be read or failed validation (message names the field)."""


def resolve_env_ref(value: Optional[str]) -> Optional[str]:
    """``"${NAME}"`` -> ``os.environ["NAME"]`` (None when unset); other strings unchanged."""
    if value is None:
        return None
    match = _ENV_REF.match(value.strip())
    if not match:
        return value
    return os.environ.get(match.group(1))


class _Strict(BaseModel):
    """Unknown keys are typos: refuse them so a misspelt field is not silently ignored."""

    model_config = ConfigDict(extra="forbid")


class CameraSpec(_Strict):
    id: int = Field(ge=1)
    name: Optional[str] = None
    driver: Literal["ptzoptics", "mock"] = "ptzoptics"
    host: Optional[str] = None
    port: int = Field(80, ge=1, le=65535)
    username: Optional[str] = None
    password: Optional[str] = None  # literal or "${ENV_VAR}"
    visca_port: int = Field(1240, ge=1, le=65535)
    visca_udp: bool = False
    atem_input: int = Field(ge=1)
    snapshot_url: Optional[str] = None
    rtsp_url: Optional[str] = None

    @model_validator(mode="after")
    def _default_name(self) -> "CameraSpec":
        if not self.name:
            self.name = f"Camera {self.id}"
        return self

    def resolved_username(self) -> Optional[str]:
        return resolve_env_ref(self.username)

    def resolved_password(self) -> Optional[str]:
        return resolve_env_ref(self.password)

    def snapshot(self) -> Optional[str]:
        """Still-frame URL for the vision layer (explicit, else the PTZOptics CGI default)."""
        if self.snapshot_url:
            return self.snapshot_url
        return f"http://{self.host}/snapshot.jpg" if self.host else None


class SwitcherSpec(_Strict):
    driver: Literal["atem", "mock"] = "atem"
    inputs: dict[str, int] = Field(default_factory=dict)  # name -> ATEM input id
    slides_input: int = Field(2, ge=1)

    @field_validator("inputs")
    @classmethod
    def _positive_inputs(cls, value: dict[str, int]) -> dict[str, int]:
        for name, input_id in value.items():
            if int(input_id) < 1:
                raise ValueError(f"inputs.{name}: ATEM input ids start at 1")
        return value


class MixerSpec(_Strict):
    driver: Literal["mgx16_usb", "meter_feed", "mock"] = "meter_feed"
    roles: dict[str, int] = Field(default_factory=dict)  # role -> 1-based channel

    @field_validator("roles")
    @classmethod
    def _unique_channels(cls, value: dict[str, int]) -> dict[str, int]:
        seen: dict[int, str] = {}
        for role, channel in value.items():
            if int(channel) < 1:
                raise ValueError(f"roles.{role}: mixer channels are 1-based")
            if channel in seen:
                raise ValueError(f"roles.{role}: channel {channel} is already assigned to '{seen[channel]}'")
            seen[channel] = role
        return value


class PresentationSpec(_Strict):
    driver: Literal["easyworship", "mock"] = "easyworship"
    host: Optional[str] = None
    schedule_offset: int = Field(0, ge=0)


class RoleShot(_Strict):
    camera: int = Field(ge=1)
    preset: int = Field(ge=0)


class ActionSpec(_Strict):
    """One cue action in profile form (converted by app.director.script)."""

    type: ActionKind
    # atem_program: an ATEM input id, an input name from switcher.inputs, "slides" or "camera:<id>".
    input: Optional[Union[int, str]] = None
    transition: Optional[CueTransition] = None
    # ptz_preset: a role from ``roles`` and/or an explicit camera + preset.
    role: Optional[str] = None
    camera: Optional[int] = None
    preset: Optional[int] = None
    # slide: EasyWorship action name (next_item, next_slide, live, clear, ...).
    op: Optional[str] = None
    # note: advisory text for the operator.
    note: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="after")
    def _required_per_type(self) -> "ActionSpec":
        if self.type == "atem_program" and self.input is None:
            raise ValueError("input: required for an atem_program action")
        if self.type == "ptz_preset" and self.role is None and self.preset is None:
            raise ValueError("role or preset: required for a ptz_preset action")
        if self.type == "slide" and not self.op:
            raise ValueError("op: required for a slide action")
        if self.type == "note" and not self.note:
            raise ValueError("note: required for a note action")
        return self


class CueOverride(_Strict):
    """Changes to one cue, matched by ``id``. A built-in id is modified
    (only the fields given change); ``enabled: false`` removes it; an unknown
    id adds a new cue (then ``label`` and ``actions`` are required)."""

    id: str = Field(min_length=1)
    enabled: bool = True
    label: Optional[str] = None
    description: Optional[str] = None
    # AI-facing description (app.director.plan_builder).
    state: Optional[str] = None
    speaker_role: Optional[str] = None
    camera_role: Optional[str] = None
    easyworship_item: Optional[str] = None
    followable: Optional[bool] = None
    # Engine behaviour.
    transition: Optional[CueTransition] = None
    cover_with: Optional[Union[int, str]] = None
    advance: Optional[CueAdvance] = None
    timer_seconds: Optional[int] = Field(None, ge=1)
    channels: Optional[list[Union[int, str]]] = None  # SONG_END channels: numbers or mixer role names
    ai_enabled: Optional[bool] = None
    exit_hint: Optional[str] = None
    actions: Optional[list[ActionSpec]] = None  # replaces the cue's actions
    # Placement of a new cue in the catalog (default: appended).
    after: Optional[str] = None
    before: Optional[str] = None

    @field_validator("state")
    @classmethod
    def _known_state(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            return ServiceState(value.strip().lower()).value
        except ValueError:
            valid = ", ".join(sorted({s.value for s in ServiceState}))
            raise ValueError(f"unknown service state '{value}' (one of: {valid})")

    @model_validator(mode="after")
    def _placement(self) -> "CueOverride":
        if self.after and self.before:
            raise ValueError("after/before: give one or the other")
        return self


class FollowerSpec(_Strict):
    cues: Optional[list[str]] = None   # cue ids during which the slide follower may advance
    roles: Optional[list[str]] = None  # transcript roles it listens to


class ChurchSpec(_Strict):
    name: str = DEFAULT_CHURCH_NAME
    timezone: Optional[str] = None  # IANA name; None = the PC's local zone
    service_time: str = "10:00"
    service_days: list[str] = Field(default_factory=lambda: ["sun"])

    @field_validator("service_time")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not re.fullmatch(r"\d{1,2}:\d{2}", value.strip()):
            raise ValueError("service_time must be HH:MM")
        return value.strip()


class RigProfile(_Strict):
    version: int = 1
    church: ChurchSpec = Field(default_factory=ChurchSpec)
    cameras: list[CameraSpec] = Field(default_factory=list)
    switcher: SwitcherSpec = Field(default_factory=SwitcherSpec)
    mixer: MixerSpec = Field(default_factory=MixerSpec)
    presentation: PresentationSpec = Field(default_factory=PresentationSpec)
    roles: dict[str, RoleShot] = Field(default_factory=dict)
    cues: list[CueOverride] = Field(default_factory=list)
    # cue id (or "id1,id2" for a heading that expands to several cues) -> regexes
    heading_synonyms: dict[str, list[str]] = Field(default_factory=dict)
    follower: FollowerSpec = Field(default_factory=FollowerSpec)

    # Where this profile came from; not part of the YAML.
    source: Literal["defaults", "file", "memory"] = Field("defaults", exclude=True)
    path: Optional[str] = Field(None, exclude=True)

    # -- validation ----------------------------------------------------------
    @model_validator(mode="after")
    def _cross_checks(self) -> "RigProfile":
        ids: dict[int, int] = {}
        inputs: dict[int, int] = {}
        for index, camera in enumerate(self.cameras):
            if camera.id in ids:
                raise ValueError(f"cameras[{index}].id: duplicate camera id {camera.id}")
            ids[camera.id] = index
            if camera.atem_input in inputs:
                raise ValueError(
                    f"cameras[{index}].atem_input: ATEM input {camera.atem_input} is already used by "
                    f"camera {self.cameras[inputs[camera.atem_input]].id}"
                )
            inputs[camera.atem_input] = index
        if self.switcher.slides_input in inputs:
            raise ValueError(
                f"switcher.slides_input: ATEM input {self.switcher.slides_input} is camera "
                f"{self.cameras[inputs[self.switcher.slides_input]].id}'s input"
            )
        if self.cameras:
            for role, shot in self.roles.items():
                if shot.camera not in ids:
                    known = ", ".join(str(c) for c in sorted(ids))
                    raise ValueError(f"roles.{role}.camera: no camera with id {shot.camera} (cameras: {known})")
        seen_cues: set[str] = set()
        for index, cue in enumerate(self.cues):
            if cue.id in seen_cues:
                raise ValueError(f"cues[{index}].id: duplicate cue override '{cue.id}'")
            seen_cues.add(cue.id)
            if cue.cover_with is not None and isinstance(cue.cover_with, str):
                self.resolve_input(cue.cover_with, where=f"cues[{index}].cover_with")
            for a_index, action in enumerate(cue.actions or []):
                if action.type == "atem_program" and isinstance(action.input, str):
                    self.resolve_input(action.input, where=f"cues[{index}].actions[{a_index}].input")
            for c_index, channel in enumerate(cue.channels or []):
                if isinstance(channel, str) and channel not in self.mixer.roles:
                    raise ValueError(
                        f"cues[{index}].channels[{c_index}]: '{channel}' is not a mixer role "
                        f"(mixer.roles: {', '.join(self.mixer.roles) or 'none'})"
                    )
        for cue_id, patterns in self.heading_synonyms.items():
            if not synonym_cue_ids(cue_id):
                raise ValueError(f"heading_synonyms.{cue_id!r}: the key must be a cue id (or 'id1,id2')")
            for p_index, pattern in enumerate(patterns):
                try:
                    re.compile(pattern, re.IGNORECASE)
                except re.error as exc:
                    raise ValueError(f"heading_synonyms.{cue_id}[{p_index}]: invalid regex: {exc}")
        for role in self.follower.roles or []:
            if role not in self.mixer.roles:
                raise ValueError(
                    f"follower.roles: '{role}' is not a mixer role "
                    f"(mixer.roles: {', '.join(self.mixer.roles) or 'none'})"
                )
        return self

    # -- lookups -------------------------------------------------------------
    def camera(self, camera_id: int) -> Optional[CameraSpec]:
        for camera in self.cameras:
            if camera.id == int(camera_id):
                return camera
        return None

    def camera_ids(self) -> list[int]:
        return [camera.id for camera in self.cameras]

    def atem_input_for_camera(self, camera_id: int) -> Optional[int]:
        camera = self.camera(camera_id)
        return camera.atem_input if camera else None

    def camera_for_atem_input(self, atem_input: int) -> Optional[int]:
        for camera in self.cameras:
            if camera.atem_input == int(atem_input):
                return camera.id
        return None

    def role_shot(self, role: str) -> tuple[Optional[int], Optional[int]]:
        shot = self.roles.get((role or "").strip().lower())
        return (shot.camera, shot.preset) if shot else (None, None)

    def role_names(self) -> tuple[str, ...]:
        return tuple(self.roles)

    def mixer_channel(self, role: str) -> Optional[int]:
        return self.mixer.roles.get((role or "").strip().lower())

    def follower_cue_ids(self) -> set[str]:
        """Cue ids during which the slide follower may advance (profile, else SONG_FOLLOWER_CUES)."""
        if self.follower.cues is not None:
            return {c.strip() for c in self.follower.cues if c and c.strip()}
        return _split_csv(settings.song_follower_cues)

    def follower_roles(self) -> list[str]:
        if self.follower.roles is not None:
            return list(self.follower.roles)
        return sorted(_split_csv(settings.song_follower_roles))

    def resolve_input(self, value: Union[int, str], where: str = "input") -> int:
        """ATEM input id for a number, a ``switcher.inputs`` name, ``"slides"`` or ``"camera:<id>"``."""
        if isinstance(value, bool):
            raise ValueError(f"{where}: expected an ATEM input id or name")
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if text.isdigit():
            return int(text)
        lowered = text.lower()
        if lowered == SLIDES_INPUT_NAME:
            return self.switcher.slides_input
        if lowered.startswith("camera:"):
            try:
                camera_id = int(lowered.split(":", 1)[1])
            except ValueError:
                raise ValueError(f"{where}: '{value}' should be camera:<id>")
            mapped = self.atem_input_for_camera(camera_id)
            if mapped is None:
                raise ValueError(f"{where}: no camera with id {camera_id}")
            return mapped
        for name, input_id in self.switcher.inputs.items():
            if name.lower() == lowered:
                return int(input_id)
        names = ", ".join(sorted(self.switcher.inputs)) or "none"
        raise ValueError(f"{where}: unknown ATEM input '{value}' (switcher.inputs: {names}, or 'slides')")

    def cue_override(self, cue_id: str) -> Optional[CueOverride]:
        for cue in self.cues:
            if cue.id == cue_id:
                return cue
        return None

    # -- construction / serialisation ---------------------------------------
    @classmethod
    def default_from_settings(cls) -> "RigProfile":
        """Today's single-PTZ rig, read from the ``.env`` settings."""
        roles = {}
        for role in SETTINGS_CAMERA_ROLES:
            camera_id = getattr(settings, f"camera_role_{role}_camera", None)
            preset_id = getattr(settings, f"camera_role_{role}_preset", None)
            if camera_id is not None and preset_id is not None:
                roles[role] = RoleShot(camera=int(camera_id), preset=int(preset_id))
        mixer_roles = {
            role: int(getattr(settings, f"mixer_{role}_channel"))
            for role in SETTINGS_MIXER_ROLES
            if getattr(settings, f"mixer_{role}_channel", None) is not None
        }
        ptz_id = int(getattr(settings, "ptz_camera_id", 1) or 1)
        cam1 = int(settings.atem_camera1_input)
        slides = int(settings.atem_camera2_input)
        if settings.mgx_usb_enabled:
            mixer_driver = "mgx16_usb"
        else:
            mixer_driver = "mock" if settings.enable_mock_mixer else "meter_feed"
        profile = cls(
            church=ChurchSpec(
                name=DEFAULT_CHURCH_NAME,
                service_time=settings.service_start_time,
                service_days=sorted(_split_csv(settings.service_start_days)) or ["sun"],
            ),
            cameras=[
                CameraSpec(
                    id=ptz_id,
                    name=settings.camera_1_name,
                    driver="ptzoptics",
                    host=settings.camera_1_host,
                    port=settings.camera_1_port,
                    username=settings.camera_1_username,
                    password=settings.camera_1_password,
                    visca_port=settings.camera_1_visca_port,
                    visca_udp=settings.camera_1_visca_udp,
                    atem_input=cam1,
                )
            ],
            switcher=SwitcherSpec(
                driver="mock" if settings.enable_mock_atem else "atem",
                inputs={f"camera_{ptz_id}": cam1, SLIDES_INPUT_NAME: slides},
                slides_input=slides,
            ),
            mixer=MixerSpec(driver=mixer_driver, roles=mixer_roles),
            presentation=PresentationSpec(
                driver="mock" if settings.enable_mock_easyworship else "easyworship",
                host=settings.easyworship_remote_host,
                schedule_offset=int(settings.easyworship_schedule_offset),
            ),
            roles=roles,
            cues=[],
            heading_synonyms={},
            follower=FollowerSpec(
                cues=sorted(_split_csv(settings.song_follower_cues)),
                roles=[r for r in _split_csv_ordered(settings.song_follower_roles) if r in mixer_roles],
            ),
        )
        profile.source = "defaults"
        return profile

    @classmethod
    def from_data(cls, data: Any, *, source: str = "memory", path: Optional[str] = None) -> "RigProfile":
        """Build a profile from parsed YAML/JSON, filling omitted sections from settings."""
        label = path or "rig profile"
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise RigProfileError(f"{label}: the top level must be a mapping of sections, not {type(data).__name__}")
        base = cls.default_from_settings().model_dump(mode="json")
        merged: dict[str, Any] = dict(base)
        for key, value in data.items():
            if key in ("roles", "heading_synonyms") and isinstance(value, dict) and isinstance(merged.get(key), dict):
                # Profile entries first (iteration order is lookup priority), then
                # the settings roles the profile does not mention.
                merged[key] = {**value, **{k: v for k, v in merged[key].items() if k not in value}}
            elif key in ("church", "switcher", "mixer", "presentation", "follower") and isinstance(value, dict):
                merged[key] = {**merged.get(key, {}), **value}
            else:
                merged[key] = value
        if isinstance(data.get("mixer"), dict) and "roles" in data["mixer"]:
            # The profile replaced the mixer roles: inherited follower roles that
            # no longer exist (e.g. liturgist) are dropped rather than rejected.
            follower = dict(merged.get("follower") or {})
            given = data.get("follower") if isinstance(data.get("follower"), dict) else {}
            if "roles" not in given and follower.get("roles"):
                mixer_roles = data["mixer"]["roles"] if isinstance(data["mixer"]["roles"], dict) else {}
                follower["roles"] = [r for r in follower["roles"] if r in mixer_roles]
                merged["follower"] = follower
        try:
            profile = cls.model_validate(merged)
        except ValidationError as exc:
            raise RigProfileError(format_validation_error(exc, label)) from None
        profile.source = source  # type: ignore[assignment]
        profile.path = path
        return profile

    @classmethod
    def from_yaml(cls, text: str, *, source: str = "memory", path: Optional[str] = None) -> "RigProfile":
        label = path or "rig profile"
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RigProfileError(f"{label}: not valid YAML: {exc}") from None
        return cls.from_data(data, source=source, path=path)

    def to_data(self, *, masked: bool = False) -> dict:
        data = self.model_dump(mode="json", exclude_none=True)
        if masked:
            for camera in data.get("cameras", []):
                if camera.get("password"):
                    camera["password"] = MASK
        return data

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_data(), sort_keys=False, allow_unicode=True, default_flow_style=False)

    def masked(self) -> dict:
        """JSON-safe view for the API: passwords replaced by ``***``."""
        return self.to_data(masked=True)

    def summary(self) -> dict:
        return {
            "church": self.church.name,
            "source": self.source,
            "path": self.path,
            "cameras": [{"id": c.id, "name": c.name, "driver": c.driver, "atem_input": c.atem_input, "host": c.host}
                        for c in self.cameras],
            "slides_input": self.switcher.slides_input,
            "roles": {role: {"camera": s.camera, "preset": s.preset} for role, s in self.roles.items()},
            "mixer_roles": dict(self.mixer.roles),
            "cue_overrides": [c.id for c in self.cues],
            "follower_cues": sorted(self.follower_cue_ids()),
        }


def synonym_cue_ids(key: str) -> list[str]:
    """Cue ids named by a ``heading_synonyms`` key (``"sermon"`` or ``"benediction,service_end"``)."""
    return [part.strip() for part in str(key).split(",") if part.strip()]


def format_validation_error(exc: ValidationError, label: str) -> str:
    lines = []
    for error in exc.errors():
        loc = ""
        for part in error.get("loc", ()):
            loc += f"[{part}]" if isinstance(part, int) else (f".{part}" if loc else str(part))
        message = error.get("msg", "invalid value")
        if message.startswith("Value error, "):
            message = message[len("Value error, "):]
        value = error.get("input")
        shown = "" if error.get("type") == "missing" or isinstance(value, (dict, list)) else f" (got {value!r})"
        lines.append(f"{label}: {loc}: {message}{shown}" if loc else f"{label}: {message}{shown}")
    return "\n".join(lines)


def _split_csv(value: Optional[str]) -> set[str]:
    return {part.strip() for part in (value or "").split(",") if part.strip()}


def _split_csv_ordered(value: Optional[str]) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


# -- loading ----------------------------------------------------------------

def profile_path(path: Optional[Union[str, Path]] = None) -> Path:
    """Absolute path of the rig profile. Relative paths are taken from the repo
    root (next to ``.env``), so ``RIG_PROFILE_FILE=rig.yaml`` is the default."""
    raw = path or getattr(settings, "rig_profile_file", None) or DEFAULT_PROFILE_FILE
    candidate = Path(str(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate


def load_rig_profile(path: Optional[Union[str, Path]] = None) -> RigProfile:
    """Read ``path`` (default ``settings.rig_profile_file``). A missing file
    gives the settings-derived defaults; an unreadable or invalid file raises
    :class:`RigProfileError` naming the field."""
    target = profile_path(path)
    if not target.exists():
        return RigProfile.default_from_settings()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise RigProfileError(f"{target}: cannot read rig profile: {exc}") from None
    return RigProfile.from_yaml(text, source="file", path=str(target))


def example_yaml() -> str:
    """The commented ``rig.example.yaml`` shipped at the repo root."""
    return EXAMPLE_FILE.read_text(encoding="utf-8")


_lock = threading.Lock()
_cached: Optional[RigProfile] = None


def rig() -> RigProfile:
    """The active profile. A file-backed profile is cached until :func:`reload`;
    with no ``rig.yaml`` the profile mirrors the live settings (so a
    ``monkeypatch`` of ``camera_role_pastor_preset`` is seen immediately)."""
    global _cached
    with _lock:
        if _cached is None:
            _cached = load_rig_profile()
        current = _cached
    if current.source == "defaults":
        return RigProfile.default_from_settings()
    return current


def reload(path: Optional[Union[str, Path]] = None) -> RigProfile:
    """Re-read the profile from disk (after a PUT, or a settings change)."""
    global _cached
    loaded = load_rig_profile(path)
    with _lock:
        _cached = loaded
    return rig()


def install(profile: Optional[RigProfile]) -> None:
    """Use an in-memory profile (tests); ``None`` drops back to disk/defaults."""
    global _cached
    with _lock:
        if profile is not None and profile.source == "defaults":
            profile.source = "memory"  # type: ignore[assignment]
        _cached = profile
