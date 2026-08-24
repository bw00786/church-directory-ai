"""Formal service-state machine.

The AI Director (and, as a fallback, simple heuristics) move the service
through these states. Unlike the rigid cue engine, transitions here are not
restricted to a fixed order — the AI is expected to *deviate* from the plan
when the live service does (see docs/ai-director.md, "AI must be able to
deviate").
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
    ANNOUNCEMENT = "announcement"
    PASTOR_INTRODUCTION = "pastor_introduction"
    SERMON = "sermon"
    COMMUNION = "communion"
    CLOSING_HYMN = "closing_hymn"
    BENEDICTION = "benediction"
    POST_SERVICE = "post_service"


# Singing-specific sub-states used by the SingingObserver (see app.audio).
class SongPhase(str, Enum):
    SONG_STARTED = "song_started"
    SONG_CONTINUING = "song_continuing"
    SONG_ENDING = "song_ending"
    SONG_COMPLETE = "song_complete"
