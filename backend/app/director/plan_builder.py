"""Derive the AI Director's advisory ServicePlan from the loaded cue sheet.

The cue engine (app.director) and the AI Director (app.ai) used to hold two
unrelated descriptions of the service. This maps each cue id onto the
ServiceState / speaker / camera role / EasyWorship label the AI expects, so an
uploaded order of service changes what the AI anticipates, not just what the
cue engine executes.
"""

from typing import Optional

from pydantic import BaseModel

from app.domain.service_plan import ServiceElement, ServicePlan
from app.domain.service_state import ServiceState

from .models import ActionType, ServiceScript


class _Mapping(BaseModel):
    type: ServiceState
    speaker: Optional[str] = None
    camera_role: Optional[str] = None
    easyworship_item: Optional[str] = None


# Cue id -> AI-facing description. easyworship_item is set only where the cue
# issues a SLIDE action, so the EW schedule order stays in step.
_CUE_PLAN: dict[str, _Mapping] = {
    "service_start": _Mapping(type=ServiceState.PRE_SERVICE, camera_role="wide", easyworship_item="Countdown"),
    "first_song": _Mapping(type=ServiceState.CONGREGATIONAL_SONG, speaker="vocalist", camera_role="congregation", easyworship_item="Opening Praise"),
    "announcements": _Mapping(type=ServiceState.ANNOUNCEMENT, speaker="pastor", camera_role="pastor"),
    "childrens_message": _Mapping(type=ServiceState.WELCOME, speaker="pastor", camera_role="wide"),
    "call_to_worship_liturgist": _Mapping(type=ServiceState.CALL_TO_WORSHIP, speaker="liturgist", camera_role="liturgist"),
    "call_to_worship_slides": _Mapping(type=ServiceState.OPENING_PRAYER, speaker="liturgist", camera_role="liturgist", easyworship_item="Call to Worship"),
    "hymn_of_praise": _Mapping(type=ServiceState.HYMN, speaker="vocalist", camera_role="congregation", easyworship_item="Hymn of Praise"),
    "scripture_reading": _Mapping(type=ServiceState.SCRIPTURE, speaker="liturgist", camera_role="liturgist", easyworship_item="Scripture"),
    "bumper_video": _Mapping(type=ServiceState.PASTOR_INTRODUCTION, camera_role="wide", easyworship_item="Bumper Video"),
    "sermon": _Mapping(type=ServiceState.SERMON, speaker="pastor", camera_role="pastor"),
    "communion_pastor": _Mapping(type=ServiceState.COMMUNION, speaker="pastor", camera_role="pastor"),
    "communion_congregation": _Mapping(type=ServiceState.COMMUNION, camera_role="congregation"),
    "lords_prayer": _Mapping(type=ServiceState.COMMUNION, camera_role="congregation", easyworship_item="Lord's Prayer"),
    "community_prayers": _Mapping(type=ServiceState.OPENING_PRAYER, speaker="pastor", camera_role="pastor"),
    "offertory_liturgist": _Mapping(type=ServiceState.ANNOUNCEMENT, speaker="liturgist", camera_role="liturgist"),
    "doxology": _Mapping(type=ServiceState.HYMN, speaker="vocalist", camera_role="congregation", easyworship_item="Doxology"),
    "closing_praise": _Mapping(type=ServiceState.CLOSING_HYMN, speaker="vocalist", camera_role="congregation", easyworship_item="Closing Praise"),
    "benediction": _Mapping(type=ServiceState.BENEDICTION, speaker="pastor", camera_role="pastor"),
    "service_end": _Mapping(type=ServiceState.POST_SERVICE, camera_role="wide"),
}


def build_plan_from_script(script: ServiceScript) -> ServicePlan:
    """One ServiceElement per cue, in cue order; unknown cues get a generic element."""
    elements = []
    for cue in script.cues:
        mapping = _CUE_PLAN.get(cue.id)
        if mapping is None:
            has_slide = any(a.type == ActionType.SLIDE for a in cue.actions)
            mapping = _Mapping(type=ServiceState.WELCOME, easyworship_item=cue.name if has_slide else None)
        elements.append(
            ServiceElement(
                id=cue.id,
                type=mapping.type,
                speaker=mapping.speaker,
                camera_role=mapping.camera_role,
                easyworship_item=mapping.easyworship_item,
            )
        )
    return ServicePlan(name=script.name, elements=elements)
