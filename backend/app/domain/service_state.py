"""Formal service-state machine.

The AI Director (and, as a fallback, simple heuristics) move the service
through these states. Unlike the rigid cue engine, transitions here are not
restricted to a fixed order — the AI is expected to *deviate* from the plan
when the live service does (see docs/ai-director.md, "AI must be able to
deviate").

The LLM only *proposes* a state; ``app.director.phase.PhaseReconciler``
decides whether the proposal (or a hard signal: cue engine position,
EasyWorship item) actually moves ``ServiceContext.state``.
"""

from enum import Enum


class ServiceState(str, Enum):
    PRE_SERVICE = "pre_service"
    WELCOME = "welcome"
    CALL_TO_WORSHIP = "call_to_worship"
    OPENING_PRAYER = "opening_prayer"
    SCRIPTURE = "scripture"
    HYMN = "hymn"
    CONGREGATIONAL_SONG = "congregational_song"
    SPECIAL_MUSIC = "special_music"          # soloist / choir anthem; congregation listens
    ANNOUNCEMENT = "announcement"
    ANNOUNCEMENTS = "announcement"           # alias of ANNOUNCEMENT (same value)
    CHILDRENS_MESSAGE = "childrens_message"
    PRAYERS = "prayers"                      # Lord's Prayer, prayers of the people, pastoral prayer
    OFFERTORY = "offertory"
    VIDEO = "video"                          # bumper / pre-recorded video from EasyWorship
    PASTOR_INTRODUCTION = "pastor_introduction"
    SERMON = "sermon"
    COMMUNION = "communion"
    CLOSING_HYMN = "closing_hymn"
    BENEDICTION = "benediction"
    POST_SERVICE = "post_service"


# -- state families (shared by the phase reconciler and the slide follower) ----

# Congregation / vocalist singing: lyrics are on the slides.
SONG_STATES: frozenset = frozenset(
    {ServiceState.HYMN, ServiceState.CONGREGATIONAL_SONG, ServiceState.CLOSING_HYMN, ServiceState.SPECIAL_MUSIC}
)

# Pastor speaking at length.
SERMON_STATES: frozenset = frozenset({ServiceState.SERMON, ServiceState.PASTOR_INTRODUCTION})

# States whose slide text is read or sung aloud, so a line-by-line slide
# follower can advance on it. ``app.director.slide_text_source._FOLLOW_STATES``
# should mirror this set.
FOLLOWABLE_STATES: frozenset = frozenset(
    {
        ServiceState.CALL_TO_WORSHIP,
        ServiceState.OPENING_PRAYER,
        ServiceState.SCRIPTURE,
        ServiceState.HYMN,
        ServiceState.CONGREGATIONAL_SONG,
        ServiceState.CLOSING_HYMN,
        ServiceState.PRAYERS,
    }
)


# Singing-specific sub-states used by the SingingObserver (see app.audio).
class SongPhase(str, Enum):
    SONG_STARTED = "song_started"
    SONG_CONTINUING = "song_continuing"
    SONG_ENDING = "song_ending"
    SONG_COMPLETE = "song_complete"
