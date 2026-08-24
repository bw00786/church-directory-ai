"""Sunday service definition — context for the AI Director, not a rigid script.

Unlike app.director.script (the cue engine's hard-wired sequence), this plan
is advisory: the AI Director is given it as *expected* structure but is
expected to recognize and handle unplanned events (e.g. an unscheduled
announcement) rather than blindly following it. See docs/ai-director.md.
"""

from typing import List, Optional

from pydantic import BaseModel

from .service_state import ServiceState


class ServiceElement(BaseModel):
    """One planned element of the service."""

    id: str
    type: ServiceState
    speaker: Optional[str] = None  # "pastor" | "liturgist" | None
    camera_role: Optional[str] = None  # "pastor" | "liturgist" | "congregation" | "choir" | "wide"
    easyworship_item: Optional[str] = None  # label matched against the EW schedule order


class ServicePlan(BaseModel):
    name: str
    elements: List[ServiceElement]


def build_default_service_plan() -> ServicePlan:
    """Default Sunday plan mirroring app.director.script's cue sheet."""
    elements = [
        ServiceElement(id="welcome", type=ServiceState.WELCOME, camera_role="wide"),
        ServiceElement(
            id="call_to_worship",
            type=ServiceState.CALL_TO_WORSHIP,
            speaker="liturgist",
            camera_role="liturgist",
            easyworship_item="Call to Worship",
        ),
        ServiceElement(
            id="opening_prayer",
            type=ServiceState.OPENING_PRAYER,
            speaker="liturgist",
            camera_role="liturgist",
            easyworship_item="Opening Prayer",
        ),
        ServiceElement(
            id="scripture",
            type=ServiceState.SCRIPTURE,
            speaker="liturgist",
            camera_role="liturgist",
            easyworship_item="Scripture",
        ),
        ServiceElement(
            id="congregational_song_1",
            type=ServiceState.CONGREGATIONAL_SONG,
            speaker="vocalist",
            camera_role="congregation",
            easyworship_item="Song",
        ),
        ServiceElement(
            id="announcements",
            type=ServiceState.ANNOUNCEMENT,
            speaker="pastor",
            camera_role="pastor",
        ),
        ServiceElement(
            id="sermon",
            type=ServiceState.SERMON,
            speaker="pastor",
            camera_role="pastor",
            easyworship_item="Sermon",
        ),
    ]
    return ServicePlan(name="Sunday Worship", elements=elements)
