"""Derive the AI Director's advisory ServicePlan from the loaded cue sheet.

The cue engine (app.director) and the AI Director (app.ai) used to hold two
unrelated descriptions of the service. This maps each cue onto the
ServiceState / speaker / camera role / EasyWorship label / followability the
AI expects, so an uploaded order of service changes what the AI anticipates,
not just what the cue engine executes.

The mapping is a data table (``CUE_PLAN_DEFAULTS``) keyed by cue id, and every
field can be overridden per cue through the optional ``Cue.state`` /
``speaker_role`` / ``camera_role`` / ``easyworship_item`` / ``followable``
fields (a per-church rig profile populates those). :func:`plan_entry_for_cue`
is pure: cue in, merged description out.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.domain.service_plan import ServiceElement, ServicePlan
from app.domain.service_state import ServiceState

from .models import ActionType, Cue, ServiceScript


class CuePlanEntry(BaseModel):
    """AI-facing description of one cue."""

    state: ServiceState
    speaker_role: Optional[str] = None
    camera_role: Optional[str] = None
    easyworship_item: Optional[str] = None
    followable: bool = False


def _e(
    state: ServiceState,
    speaker: Optional[str] = None,
    camera: Optional[str] = None,
    ew: Optional[str] = None,
    followable: bool = False,
) -> CuePlanEntry:
    return CuePlanEntry(state=state, speaker_role=speaker, camera_role=camera, easyworship_item=ew, followable=followable)


# Built-in cue id -> description (app.director.script's cue sheet).
# easyworship_item is set only where the cue issues a SLIDE action, so the EW
# schedule order stays in step. followable marks cues whose slide text is read
# or sung aloud (mirrors the default SONG_FOLLOWER_CUES).
CUE_PLAN_DEFAULTS: dict[str, CuePlanEntry] = {
    "service_start": _e(ServiceState.PRE_SERVICE, camera="wide", ew="Countdown"),
    "first_song": _e(ServiceState.CONGREGATIONAL_SONG, "vocalist", "congregation", "Opening Praise", True),
    "announcements": _e(ServiceState.ANNOUNCEMENT, "pastor", "pastor"),
    "childrens_message": _e(ServiceState.CHILDRENS_MESSAGE, "pastor", "wide"),
    "call_to_worship_liturgist": _e(ServiceState.CALL_TO_WORSHIP, "liturgist", "liturgist"),
    # The slides cue carries the Call to Worship *and* the opening prayer; the
    # liturgist is already reading when it is entered.
    "call_to_worship_slides": _e(ServiceState.OPENING_PRAYER, "liturgist", "liturgist", "Call to Worship", True),
    "hymn_of_praise": _e(ServiceState.HYMN, "vocalist", "congregation", "Hymn of Praise", True),
    "scripture_reading": _e(ServiceState.SCRIPTURE, "liturgist", "liturgist", "Scripture", True),
    "bumper_video": _e(ServiceState.VIDEO, camera="wide", ew="Bumper Video"),
    "sermon": _e(ServiceState.SERMON, "pastor", "pastor"),
    "communion_pastor": _e(ServiceState.COMMUNION, "pastor", "pastor"),
    "communion_congregation": _e(ServiceState.COMMUNION, camera="congregation"),
    "lords_prayer": _e(ServiceState.PRAYERS, "congregation", "congregation", "Lord's Prayer", True),
    "community_prayers": _e(ServiceState.PRAYERS, "pastor", "pastor"),
    "offertory_liturgist": _e(ServiceState.OFFERTORY, "liturgist", "liturgist"),
    "doxology": _e(ServiceState.HYMN, "vocalist", "congregation", "Doxology", True),
    "closing_praise": _e(ServiceState.CLOSING_HYMN, "vocalist", "congregation", "Closing Praise", True),
    "benediction": _e(ServiceState.BENEDICTION, "pastor", "pastor"),
    "service_end": _e(ServiceState.POST_SERVICE, camera="wide"),
}

# Framing roles that imply who is speaking when a cue only says where to point.
_SPEAKER_FOR_CAMERA_ROLE = {"pastor": "pastor", "liturgist": "liturgist", "vocalist": "vocalist"}


def _has_slide(cue: Cue) -> bool:
    return any(a.type == ActionType.SLIDE for a in cue.actions)


def _ptz_role(cue: Cue) -> Optional[str]:
    for action in cue.actions:
        if action.type == ActionType.PTZ_PRESET and action.role:
            return action.role
    return None


def _generic_entry(cue: Cue) -> CuePlanEntry:
    """Best effort for a cue id the table does not know: infer the camera role
    from its PTZ action, the speaker from that role, the EW label from a SLIDE
    action. The state cannot be inferred -- WELCOME is the neutral default and
    the rig profile should set ``Cue.state``."""
    camera = _ptz_role(cue)
    return CuePlanEntry(
        state=ServiceState.WELCOME,
        speaker_role=_SPEAKER_FOR_CAMERA_ROLE.get(camera or ""),
        camera_role=camera,
        easyworship_item=cue.name if _has_slide(cue) else None,
        followable=False,
    )


def _coerce_state(value) -> Optional[ServiceState]:
    if value is None:
        return None
    if isinstance(value, ServiceState):
        return value
    try:
        return ServiceState(str(value).strip().lower())
    except ValueError:
        return None


def plan_entry_for_cue(cue: Cue, defaults: Optional[dict] = None) -> CuePlanEntry:
    """Merged AI-facing description of ``cue``: table default (or an inferred
    generic entry) with any per-cue overrides applied. Pure."""
    table = CUE_PLAN_DEFAULTS if defaults is None else defaults
    base = table.get(cue.id) or _generic_entry(cue)
    state = _coerce_state(getattr(cue, "state", None)) or base.state
    speaker = getattr(cue, "speaker_role", None)
    camera = getattr(cue, "camera_role", None)
    ew_item = getattr(cue, "easyworship_item", None)
    followable = getattr(cue, "followable", None)
    return CuePlanEntry(
        state=state,
        speaker_role=speaker if speaker is not None else base.speaker_role,
        camera_role=camera if camera is not None else base.camera_role,
        easyworship_item=ew_item if ew_item is not None else base.easyworship_item,
        followable=bool(followable) if followable is not None else base.followable,
    )


def easyworship_item_for_cue(cue: Cue) -> Optional[str]:
    """The EasyWorship schedule item label a cue puts live, if any."""
    return plan_entry_for_cue(cue).easyworship_item


def state_for_cue(cue: Cue) -> ServiceState:
    return plan_entry_for_cue(cue).state


def element_for_cue(cue: Cue) -> ServiceElement:
    entry = plan_entry_for_cue(cue)
    return ServiceElement(
        id=cue.id,
        type=entry.state,
        label=cue.name,
        speaker=entry.speaker_role,
        camera_role=entry.camera_role,
        easyworship_item=entry.easyworship_item,
        followable=entry.followable,
    )


def build_plan_from_script(script: ServiceScript) -> ServicePlan:
    """One ServiceElement per cue, in cue order (so plan index == cue index)."""
    return ServicePlan(name=script.name, elements=[element_for_cue(cue) for cue in script.cues])
