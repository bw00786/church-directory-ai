"""Default Sunday service script (built for Vernon UMC), shaped by the rig profile.

Encodes the described flow as a cue sheet. The PTZ camera and the EasyWorship
laptop (slides) are ATEM inputs from the rig profile (``cameras[].atem_input``
/ ``switcher.slides_input``; with no rig.yaml, ``ATEM_CAMERA1_INPUT`` /
``ATEM_CAMERA2_INPUT``). The configured vocalist and congregation channels
are used to detect when a song ends.

A per-church ``rig.yaml`` changes the sheet without editing this file: its
``cues`` list modifies built-in cues by id, removes them (``enabled: false``)
or adds new ones (see :func:`apply_profile_cues` and docs/rig-profile.md).
"""

import contextvars
from contextlib import contextmanager
from typing import Iterator, List, Optional

from app.cameras.service import configured_role_shot
from app.config import settings
from app.rig import RigProfile, rig
from app.rig.profile import ActionSpec, CueOverride, synonym_cue_ids

from .models import ActionType, AdvanceTrigger, Cue, CueAction, ServiceScript, Transition

# Mixer channels used for song-end detection (the .env defaults). The cue
# sheet itself reads the live rig profile (``mixer.roles``) in
# :func:`build_cue_catalog`; these remain for callers that import them.
VOCALIST_CHANNEL = settings.mixer_vocalist_channel
CONGREGATION_CHANNEL = settings.mixer_congregation_channel

# Roles whose mixer channels the SONG_END trigger watches by default.
SONG_ROLES = ("vocalist", "congregation")

# The profile a catalog is being built for (``build_cue_catalog(profile)``),
# so ``_preset`` resolves roles against it rather than the active rig. None =
# the active rig (``configured_role_shot``).
_building_for: contextvars.ContextVar[Optional[RigProfile]] = contextvars.ContextVar("rig_building_for", default=None)


@contextmanager
def _building(profile: Optional[RigProfile]) -> Iterator[None]:
    token = _building_for.set(profile)
    try:
        yield
    finally:
        _building_for.reset(token)


def _role_shot(role: str) -> tuple:
    """``(camera, preset)`` for ``role`` from the profile being built (falling
    back to the camera_role_* settings), else from the active rig."""
    profile = _building_for.get()
    if profile is not None:
        camera_id, preset_id = profile.role_shot(role)
        if camera_id is not None and preset_id is not None:
            return camera_id, preset_id
    return configured_role_shot(role)


def _atem(input_id: int, description: str, transition: Transition | None = None) -> CueAction:
    """ATEM program change. ``transition`` overrides the cue's own
    ``transition`` ("cut" | "auto") for this one action; None inherits it."""
    return CueAction(
        type=ActionType.ATEM_PROGRAM, atem_input=input_id, transition=transition, description=description
    )


def _preset(description: str, role: str | None = None, preset_id: int | None = None) -> CueAction:
    """PTZ preset recall.

    With a ``role`` the camera and preset come from the shared role table
    (``CAMERA_ROLE_<ROLE>_CAMERA`` / ``_PRESET`` in app.config, the same lookup
    ``camera_service.move_to_role`` uses), so the AI Director's
    ``PTZ_SELECT_ROLE pastor`` and the cue sheet's pastor shot are one framing.
    The engine still prefers the preset identity recognition has learned for
    the role at run time. An explicit ``preset_id`` is only for shots that are
    not a role, or a deliberate per-cue override of the role's default (then
    the number wins and the role is kept for learning/verification).
    """
    if role is None and preset_id is None:
        raise ValueError("_preset needs a role or an explicit preset_id")
    camera_id = settings.ptz_camera_id
    if role is not None:
        role_camera, role_preset = _role_shot(role)
        if role_camera is None or role_preset is None:
            raise ValueError(f"No camera_role_{role}_* settings or rig profile roles.{role} for role '{role}'")
        camera_id = role_camera
        if preset_id is None:
            preset_id = role_preset
    return CueAction(
        type=ActionType.PTZ_PRESET,
        camera_id=camera_id,
        preset_id=preset_id,
        role=role,
        description=f"Preset {preset_id} — {description}",
    )


def _note(note: str) -> CueAction:
    return CueAction(type=ActionType.NOTE, note=note, description=note)


def _slide(op: str, description: str) -> CueAction:
    return CueAction(type=ActionType.SLIDE, slide_op=op, description=description)


def song_channels_for(profile: RigProfile) -> List[int]:
    """Mixer channels watched for SONG_END: the profile's vocalist and
    congregation channels, else every mixer role the profile has."""
    channels = [profile.mixer_channel(role) for role in SONG_ROLES]
    chosen = [int(c) for c in channels if c is not None]
    return chosen or [int(c) for c in profile.mixer.roles.values()]


def _channel_of(profile: RigProfile, role: str, fallback: int) -> int:
    channel = profile.mixer_channel(role)
    return int(channel) if channel is not None else int(fallback)


def builtin_cues(profile: Optional[RigProfile] = None) -> list[Cue]:
    """The built-in cue sheet, in default service order, before profile overrides."""
    profile = profile or rig()
    cam1 = profile.atem_input_for_camera(settings.ptz_camera_id)
    if cam1 is None:
        cam1 = settings.atem_camera1_input   # PTZOptics
    cam2 = profile.switcher.slides_input      # EasyWorship laptop / slides
    song_channels = song_channels_for(profile)
    VOCALIST_CHANNEL = _channel_of(profile, "vocalist", settings.mixer_vocalist_channel)  # noqa: N806
    CONGREGATION_CHANNEL = _channel_of(profile, "congregation", settings.mixer_congregation_channel)  # noqa: N806

    cues = [
        Cue(
            id="service_start",
            name="Service Start — Countdown",
            description="10:00 AM. Slides on screen; opening 5-minute countdown.",
            actions=[
                _atem(cam2, "Show EasyWorship slides (countdown)"),
                _slide("live", "EasyWorship: go live on the countdown item"),
                _note("Open Mic 1 on the ATEM (Yamaha MGX16 desk feed)."),
            ],
            advance=AdvanceTrigger.TIMER,
            timer_seconds=settings.service_countdown_seconds,
        ),
        Cue(
            id="first_song",
            name="First Song",
            description=f"Vocalist (ch {VOCALIST_CHANNEL}) and congregation (ch {CONGREGATION_CHANNEL}) sing over the slides.",
            actions=[
                _atem(cam2, "Stay on slides for song lyrics"),
                _slide("next_item", "EasyWorship: advance to the first song"),
                _note(f"Vocalist on channel {VOCALIST_CHANNEL}; congregation on channel {CONGREGATION_CHANNEL}."),
            ],
            advance=AdvanceTrigger.SONG_END,
            channels=song_channels,
        ),
        Cue(
            id="announcements",
            name="Announcements — Pastor",
            description="Pastor (between the altar rails) gives announcements.",
            actions=[
                _atem(cam1, "Switch to PTZOptics camera"),
                _preset("pastor at altar rails", role="pastor"),
            ],
            advance=AdvanceTrigger.MANUAL,
            ai_enabled=True,
            exit_hint="Advance when the pastor finishes the announcements.",
        ),
        Cue(
            id="childrens_message",
            name="Children's Message",
            description="Wide shot for the children's message.",
            actions=[
                _preset("wide angle", role="wide"),
            ],
            advance=AdvanceTrigger.MANUAL,
            ai_enabled=True,
            exit_hint="Advance when the children's message ends and the liturgist moves to the podium.",
        ),
        Cue(
            id="call_to_worship_liturgist",
            name="Call to Worship — Liturgist",
            description="Liturgist at the podium reads the call to worship.",
            actions=[
                _preset("liturgist's podium", role="liturgist"),
            ],
            advance=AdvanceTrigger.MANUAL,
            ai_enabled=True,
            exit_hint="Advance when the liturgist begins reading the Call to Worship (show the slides).",
        ),
        Cue(
            id="call_to_worship_slides",
            name="Call to Worship & Opening Prayer — Slides",
            description="Show the Call to Worship and opening prayer slides.",
            actions=[
                _atem(cam2, "Switch to slides for Call to Worship / opening prayer"),
                _slide("next_item", "EasyWorship: advance to Call to Worship / opening prayer"),
            ],
            advance=AdvanceTrigger.MANUAL,
            ai_enabled=True,
            exit_hint="Advance when the liturgist asks the congregation to stand for the hymn of praise.",
        ),
        Cue(
            id="hymn_of_praise",
            name="Hymn of Praise",
            description="Congregation stands for the hymn of praise (slides).",
            actions=[
                _atem(cam2, "Stay on slides for the hymn of praise"),
                _slide("next_item", "EasyWorship: advance to the hymn of praise"),
            ],
            advance=AdvanceTrigger.SONG_END,
            channels=song_channels,
        ),
        Cue(
            id="scripture_reading",
            name="Scripture Reading",
            description="Liturgist reads scripture; slides remain on screen.",
            actions=[
                _atem(cam2, "Stay on slides for the scripture"),
                _slide("next_item", "EasyWorship: advance to the scripture slides"),
            ],
            advance=AdvanceTrigger.MANUAL,
            ai_enabled=True,
            exit_hint="Advance when the liturgist finishes reading the scripture (bumper video follows).",
        ),
        Cue(
            id="bumper_video",
            name="Bumper Video",
            description="Bumper video plays from EasyWorship before the sermon.",
            actions=[
                _atem(cam2, "Stay on slides for the bumper video"),
                _slide("next_item", "EasyWorship: advance to the bumper video"),
            ],
            advance=AdvanceTrigger.MANUAL,
            ai_enabled=True,
            exit_hint="Advance when the bumper video ends and the pastor begins the sermon.",
        ),
        Cue(
            id="sermon",
            name="Sermon — Pastor",
            description="Pastor speaks.",
            actions=[
                _atem(cam1, "Switch to PTZOptics camera"),
                _preset("pastor speaking", role="pastor"),
            ],
            advance=AdvanceTrigger.MANUAL,
        ),
        Cue(
            id="communion_pastor",
            name="Communion — Pastor",
            description="Pastor speaks during the introduction of communion",
            actions=[
                _atem(cam1, "Switch to PTZOptics camera"),
                # Deliberate per-cue override: the communion table framing is not the
                # pastor role's default preset.
                _preset("pastor at the communion table", role="pastor", preset_id=2),
            ],
            ai_enabled=True,
            advance=AdvanceTrigger.MANUAL,
        ),
        Cue(
            id="communion_congregation",
            name="Communion — Congregation",
            description="Congregation participates in the communion.",
            actions=[
                _atem(cam1, "Switch to PTZOptics camera"),
                _preset("congregation participating", role="congregation"),
            ],
            ai_enabled=True,
            advance=AdvanceTrigger.MANUAL,
        ),
        Cue(
            id="lords_prayer",
            name="Lord's Prayer",
            description="Congregation recites the Lord's Prayer (slides).",
            actions=[
                _atem(cam2, "Switch to slides for the Lord's Prayer"),
                _slide("next_item", "EasyWorship: advance to the Lord's Prayer"),
            ],
            advance=AdvanceTrigger.MANUAL,
            ai_enabled=True,
            exit_hint="Advance when the congregation finishes the Lord's Prayer and the pastor begins the prayers for the community.",
        ),
        Cue(
            id="community_prayers",
            name="Community Prayer — Pastor",
            description="Pastor speaks.",
            actions=[
                _atem(cam1, "Switch to PTZOptics camera"),
                _preset("pastor speaking", role="pastor"),
            ],
            advance=AdvanceTrigger.MANUAL,
        ),
        Cue(
            id="offertory_liturgist",
            name="Offertory — Liturgist",
            description="Liturgist at the podium for the offertory.",
            actions=[
                _atem(cam1, "Switch to PTZOptics camera"),
                _preset("liturgist's podium", role="liturgist"),
            ],
            advance=AdvanceTrigger.MANUAL,
            ai_enabled=True,
            exit_hint="Advance when the liturgist begins the offertory.",
        ),
        Cue(
            id="doxology",
            name="Doxology",
            description="Congregation sings the doxology with the offering (slides).",
            actions=[
                _atem(cam2, "Switch to slides for the doxology"),
                _slide("next_item", "EasyWorship: advance to the doxology"),
            ],
            advance=AdvanceTrigger.SONG_END,
            channels=song_channels,
        ),
        Cue(
            id="closing_praise",
            name="Closing Praise — Congregation",
            description="Congregation sings the closing song.",
            actions=[
                _atem(cam2, "Switch to slides for the closing praise"),
                _slide("next_item", "EasyWorship: advance to the closing praise"),
            ],
            advance=AdvanceTrigger.SONG_END,
            channels=song_channels,
        ),
        Cue(
            id="benediction",
            name="Benediction — Pastor",
            description="Pastor gives the benediction.",
            actions=[
                _atem(cam1, "Switch to PTZOptics camera"),
                _preset("pastor speaking", role="pastor"),
            ],
            advance=AdvanceTrigger.MANUAL,
            ai_enabled=True,
            exit_hint="Advance when the pastor finishes the benediction.",
        ),
        Cue(
            id="service_end",
            name="Service End",
            description="Dismissal: show slides and clear the screen.",
            actions=[
                _atem(cam2, "Switch to slides for dismissal"),
                _slide("clear", "EasyWorship: clear the screen"),
            ],
            advance=AdvanceTrigger.MANUAL,
        ),
    ]

    return cues


# -- rig profile cue overrides ------------------------------------------------

def _action_from_spec(spec: ActionSpec, profile: RigProfile, where: str) -> CueAction:
    if spec.type == "atem_program":
        input_id = profile.resolve_input(spec.input, where=f"{where}.input")
        return _atem(input_id, spec.description or f"Switch ATEM program to input {input_id}", spec.transition)
    if spec.type == "ptz_preset":
        if spec.role is not None:
            try:
                action = _preset(spec.description or spec.role, role=spec.role, preset_id=spec.preset)
            except ValueError:
                raise ValueError(f"{where}.role: unknown camera role '{spec.role}' (define it under roles:)") from None
            if spec.camera is not None:
                action.camera_id = int(spec.camera)
            return action
        camera_id = int(spec.camera) if spec.camera is not None else int(settings.ptz_camera_id)
        description = spec.description or f"preset {spec.preset}"
        return CueAction(
            type=ActionType.PTZ_PRESET,
            camera_id=camera_id,
            preset_id=int(spec.preset),  # type: ignore[arg-type]  # validated by ActionSpec
            description=f"Preset {spec.preset} — {description}",
        )
    if spec.type == "slide":
        return _slide(spec.op, spec.description or f"EasyWorship: {spec.op}")  # type: ignore[arg-type]
    return _note(spec.note)  # type: ignore[arg-type]


def _resolve_channels(values, profile: RigProfile, where: str) -> List[int]:
    channels: List[int] = []
    for index, value in enumerate(values):
        if isinstance(value, str) and not value.strip().isdigit():
            channel = profile.mixer_channel(value)
            if channel is None:
                raise ValueError(f"{where}[{index}]: '{value}' is not a mixer role")
            channels.append(int(channel))
        else:
            channels.append(int(value))
    return channels


def _apply_override(base: Optional[Cue], override: CueOverride, profile: RigProfile) -> Cue:
    where = f"cues[{override.id}]"
    data = base.model_dump() if base is not None else {"id": override.id, "name": ""}
    if override.label is not None:
        data["name"] = override.label
    if override.description is not None:
        data["description"] = override.description
    for field in ("state", "speaker_role", "camera_role", "easyworship_item", "followable", "transition",
                  "exit_hint", "ai_enabled", "timer_seconds"):
        value = getattr(override, field)
        if value is not None:
            data[field] = value
    if override.advance is not None:
        data["advance"] = AdvanceTrigger(override.advance)
    if override.cover_with is not None:
        data["cover_with"] = profile.resolve_input(override.cover_with, where=f"{where}.cover_with")
    if override.channels is not None:
        data["channels"] = _resolve_channels(override.channels, profile, f"{where}.channels")
    if override.actions is not None:
        data["actions"] = [
            _action_from_spec(spec, profile, f"{where}.actions[{index}]") for index, spec in enumerate(override.actions)
        ]
    if base is None:
        if not data["name"]:
            raise ValueError(f"{where}.label: required for a new cue")
        if not data.get("actions"):
            raise ValueError(f"{where}.actions: required for a new cue")
        if data.get("advance") == AdvanceTrigger.SONG_END and not data.get("channels"):
            data["channels"] = song_channels_for(profile)
        if data.get("advance") == AdvanceTrigger.TIMER and not data.get("timer_seconds"):
            raise ValueError(f"{where}.timer_seconds: required when advance is 'timer'")
    return Cue(**data)


def apply_profile_cues(cues: List[Cue], profile: RigProfile) -> List[Cue]:
    """``profile.cues`` applied on top of ``cues``: built-in ids are modified
    in place (only the given fields change) or removed (``enabled: false``);
    unknown ids become new cues placed ``after``/``before`` another cue or at
    the end. Pure; raises ValueError naming the offending field."""
    by_id = {cue.id: cue for cue in cues}
    order = [cue.id for cue in cues]
    known = set(order) | {override.id for override in profile.cues}
    for override in profile.cues:
        if override.id in by_id:
            if not override.enabled:
                del by_id[override.id]
                order.remove(override.id)
                continue
            by_id[override.id] = _apply_override(by_id[override.id], override, profile)
            continue
        if not override.enabled:
            continue
        by_id[override.id] = _apply_override(None, override, profile)
        anchor = override.after or override.before
        if anchor and anchor not in known:
            raise ValueError(f"cues[{override.id}].{'after' if override.after else 'before'}: unknown cue '{anchor}'")
        if anchor in order:
            index = order.index(anchor) + (1 if override.after else 0)
            order.insert(index, override.id)
        else:
            order.append(override.id)
    return [by_id[cue_id] for cue_id in order]


def build_cue_catalog(profile: Optional[RigProfile] = None) -> list[Cue]:
    """Every known cue, in default service order: the built-in sheet built
    from the rig profile's inputs/channels, with the profile's ``cues``
    overrides applied."""
    profile = profile or rig()
    with _building(profile):
        return apply_profile_cues(builtin_cues(profile), profile)


def profile_warnings(profile: RigProfile) -> List[str]:
    """Non-fatal profile problems found against the built cue catalog (the
    catalog build itself raises ValueError for fatal ones)."""
    catalog = {cue.id for cue in build_cue_catalog(profile)}
    warnings: List[str] = []
    for cue_id in sorted(profile.follower_cue_ids() - catalog):
        warnings.append(f"follower.cues: '{cue_id}' is not a cue in the catalog")
    for key in profile.heading_synonyms:
        for cue_id in synonym_cue_ids(key):
            if cue_id not in catalog:
                warnings.append(f"heading_synonyms.{key}: '{cue_id}' is not a cue in the catalog")
    for override in profile.cues:
        if override.enabled and override.id not in catalog:  # pragma: no cover - defensive
            warnings.append(f"cues[{override.id}]: not in the catalog after overrides")
    for role, shot in profile.roles.items():
        if profile.cameras and profile.camera(shot.camera) is None:  # pragma: no cover - validated earlier
            warnings.append(f"roles.{role}: camera {shot.camera} is not in cameras")
    return warnings


def build_default_service_script(profile: Optional[RigProfile] = None) -> ServiceScript:
    """The full Sunday service cue sheet for the configured church."""
    profile = profile or rig()
    return ServiceScript(name=f"{profile.church.name} — Sunday Service", cues=build_cue_catalog(profile))
