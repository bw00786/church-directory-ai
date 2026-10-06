"""Default Sunday service script for Vernon UMC.

Encodes the described flow as a cue sheet. Camera 1 is the PTZOptics camera;
camera 2 is the EasyWorship laptop (slides). Mixer channel 5 is the vocalist and
channel 8 is the congregation, used to detect when a song ends.
"""

from app.config import settings

from .models import ActionType, AdvanceTrigger, Cue, CueAction, ServiceScript

# Mixer channels used for song-end detection.
VOCALIST_CHANNEL = 5
CONGREGATION_CHANNEL = 8


def _atem(input_id: int, description: str) -> CueAction:
    return CueAction(type=ActionType.ATEM_PROGRAM, atem_input=input_id, description=description)


def _preset(preset_id: int, description: str, role: str | None = None) -> CueAction:
    return CueAction(
        type=ActionType.PTZ_PRESET,
        camera_id=settings.ptz_camera_id,
        preset_id=preset_id,
        role=role,
        description=description,
    )


def _note(note: str) -> CueAction:
    return CueAction(type=ActionType.NOTE, note=note, description=note)


def _slide(op: str, description: str) -> CueAction:
    return CueAction(type=ActionType.SLIDE, slide_op=op, description=description)


def build_cue_catalog() -> list[Cue]:
    """Every known cue, in default service order, built from configured inputs."""
    cam1 = settings.atem_camera1_input   # PTZOptics
    cam2 = settings.atem_camera2_input   # EasyWorship laptop / slides
    song_channels = [VOCALIST_CHANNEL, CONGREGATION_CHANNEL]

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
            description="Vocalist (ch 5) and congregation (ch 8) sing over the slides.",
            actions=[
                _atem(cam2, "Stay on slides for song lyrics"),
                _slide("next_item", "EasyWorship: advance to the first song"),
                _note("Vocalist on channel 5; congregation on channel 8."),
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
                _preset(3, "Preset 3 — pastor at altar rails", role="pastor"),
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
                _preset(6, "Preset 6 — wide angle"),
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
                _preset(4, "Preset 4 — liturgist's podium", role="liturgist"),
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
                _preset(3, "Preset 3 — pastor speaking", role="pastor"),
            ],
            advance=AdvanceTrigger.MANUAL,
        ),
        Cue(
            id="communion_pastor",
            name="Communion — Pastor",
            description="Pastor speaks during the introduction of communion",
            actions=[
                _atem(cam1, "Switch to PTZOptics camera"),
                _preset(2, "Preset 2 — pastor speaking", role="pastor"),
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
                _preset(6, "Preset 6 — congregation participating", role="congregation"),
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
                _preset(3, "Preset 3 — pastor speaking", role="pastor"),
            ],
            advance=AdvanceTrigger.MANUAL,
        ),
        Cue(
            id="offertory_liturgist",
            name="Offertory — Liturgist",
            description="Liturgist at the podium for the offertory.",
            actions=[
                _atem(cam1, "Switch to PTZOptics camera"),
                _preset(4, "Preset 4 — liturgist's podium", role="liturgist"),
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
                _preset(3, "Preset 3 — pastor speaking", role="pastor"),
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


def build_default_service_script() -> ServiceScript:
    """The full Vernon UMC Sunday service cue sheet."""
    return ServiceScript(name="Vernon UMC — Sunday Service", cues=build_cue_catalog())
