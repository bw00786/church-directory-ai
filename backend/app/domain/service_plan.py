"""Sunday service definition — context for the AI Director, not a rigid script.

Unlike app.director.script (the cue engine's hard-wired sequence), this plan
is advisory: the AI Director is given it as *expected* structure but is
expected to recognize and handle unplanned events (e.g. an unscheduled
announcement) rather than blindly following it. See docs/ai-director.md.

When a cue sheet is loaded, ``app.director.plan_builder`` derives one
``ServiceElement`` per cue (same order, same ids) so the cue index, the plan
cursor and the EasyWorship schedule position describe the same thing.
"""

from typing import List, Optional

from pydantic import BaseModel

from .service_state import ServiceState


class ServiceElement(BaseModel):
    """One planned element of the service."""

    id: str
    type: ServiceState
    # Human-readable name (the cue name when derived from a cue sheet).
    label: Optional[str] = None
    speaker: Optional[str] = None  # "pastor" | "liturgist" | "vocalist" | "congregation" | None
    camera_role: Optional[str] = None  # "pastor" | "liturgist" | "congregation" | "choir" | "wide"
    easyworship_item: Optional[str] = None  # label matched against the EW schedule order
    # Slide text is read/sung aloud during this element (line-by-line follower may advance).
    followable: bool = False

    def summary(self) -> dict:
        """Compact dict for prompts / status (see ServiceContext.plan_cursor)."""
        return {
            "id": self.id,
            "label": self.label or self.id,
            "state": self.type.value,
            "speaker": self.speaker,
            "camera_role": self.camera_role,
            "easyworship_item": self.easyworship_item,
            "followable": self.followable,
        }


class ServicePlan(BaseModel):
    name: str
    elements: List[ServiceElement]

    def index_of_easyworship_item(self, label: Optional[str]) -> Optional[int]:
        """Plan index of the element that puts EasyWorship item ``label`` live."""
        if not label:
            return None
        wanted = label.strip().lower()
        for index, element in enumerate(self.elements):
            if element.easyworship_item and element.easyworship_item.strip().lower() == wanted:
                return index
        return None

    def first_index_of_state(self, state: Optional[ServiceState]) -> Optional[int]:
        if state is None:
            return None
        for index, element in enumerate(self.elements):
            if element.type == state:
                return index
        return None


def build_default_service_plan() -> ServicePlan:
    """Default Sunday plan mirroring app.director.script's cue sheet."""
    elements = [
        ServiceElement(id="welcome", type=ServiceState.WELCOME, label="Welcome", camera_role="wide"),
        ServiceElement(
            id="call_to_worship",
            type=ServiceState.CALL_TO_WORSHIP,
            label="Call to Worship",
            speaker="liturgist",
            camera_role="liturgist",
            easyworship_item="Call to Worship",
            followable=True,
        ),
        ServiceElement(
            id="opening_prayer",
            type=ServiceState.OPENING_PRAYER,
            label="Opening Prayer",
            speaker="liturgist",
            camera_role="liturgist",
            easyworship_item="Opening Prayer",
            followable=True,
        ),
        ServiceElement(
            id="scripture",
            type=ServiceState.SCRIPTURE,
            label="Scripture",
            speaker="liturgist",
            camera_role="liturgist",
            easyworship_item="Scripture",
            followable=True,
        ),
        ServiceElement(
            id="congregational_song_1",
            type=ServiceState.CONGREGATIONAL_SONG,
            label="Song",
            speaker="vocalist",
            camera_role="congregation",
            easyworship_item="Song",
            followable=True,
        ),
        ServiceElement(
            id="announcements",
            type=ServiceState.ANNOUNCEMENT,
            label="Announcements",
            speaker="pastor",
            camera_role="pastor",
        ),
        ServiceElement(
            id="sermon",
            type=ServiceState.SERMON,
            label="Sermon",
            speaker="pastor",
            camera_role="pastor",
            easyworship_item="Sermon",
        ),
    ]
    return ServicePlan(name="Sunday Worship", elements=elements)
