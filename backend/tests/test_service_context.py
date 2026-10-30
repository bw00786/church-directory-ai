"""Tests for ServiceContext (authoritative rolling context memory)."""

from datetime import datetime, timedelta, timezone

from app.domain.observations import AudioObservation, VisionObservation
from app.domain.service_context import ServiceContext, TranscriptLine
from app.domain.service_state import ServiceState


def _ctx(mono=None, wall=None) -> ServiceContext:
    ctx = ServiceContext()
    if mono is not None:
        ctx._mono = lambda: mono[0]
        ctx.state_since = mono[0]
    if wall is not None:
        ctx._wall = lambda: wall[0]
    return ctx


def test_record_audio_appends_transcript_only_when_speaking_with_text():
    ctx = ServiceContext()
    ctx.record_audio(AudioObservation(channel=2, speaker_role="liturgist", speaking=True, transcript=""))
    assert len(ctx.transcript) == 0

    ctx.record_audio(
        AudioObservation(channel=2, speaker_role="liturgist", speaking=True, transcript="Let us pray")
    )
    assert len(ctx.transcript) == 1
    assert ctx.transcript[0].text == "Let us pray"


def test_record_action_and_snapshot():
    ctx = ServiceContext()
    ctx.set_state(ServiceState.SCRIPTURE)
    ctx.record_action("PTZ_SELECT_ROLE: camera -> liturgist (ok)")

    snapshot = ctx.snapshot()
    assert snapshot["service_state"] == "scripture"
    assert "PTZ_SELECT_ROLE: camera -> liturgist (ok)" in snapshot["last_actions"]


def test_recent_transcript_text_formats_speaker_and_text():
    ctx = ServiceContext()
    ctx.record_audio(AudioObservation(channel=1, speaker_role="pastor", speaking=True, transcript="Good morning"))
    text = ctx.recent_transcript_text()
    assert "pastor: Good morning" in text


def test_multi_role_transcripts_interleave_by_arrival(monkeypatch):
    """Role-tagged transcript lines from simultaneous roles interleave in order."""
    ctx = ServiceContext()
    base = datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone.utc)
    # Emit in timestamp order across two roles (as the transcriber would).
    ctx.record_audio(AudioObservation(channel=2, speaker_role="liturgist", speaking=True,
                                      transcript="the word of the Lord", timestamp=base))
    ctx.record_audio(AudioObservation(channel=1, speaker_role="pastor", speaking=True,
                                      transcript="thank you", timestamp=base + timedelta(seconds=1)))
    ctx.record_audio(AudioObservation(channel=4, speaker_role="vocalist", speaking=True,
                                      transcript="hallelujah", timestamp=base + timedelta(seconds=2)))

    lines = list(ctx.transcript)
    assert [line.speaker_role for line in lines] == ["liturgist", "pastor", "vocalist"]
    assert [line.text for line in lines] == ["the word of the Lord", "thank you", "hallelujah"]


# -- version / change tracking ------------------------------------------------
def test_every_mutation_bumps_version():
    ctx = ServiceContext()
    assert ctx.version == 0
    seen = [0]

    def bumped():
        assert ctx.version == seen[0] + 1, "mutation did not bump version"
        seen[0] = ctx.version

    ctx.set_state(ServiceState.WELCOME); bumped()
    ctx.set_program(1); bumped()
    ctx.set_preview(2); bumped()
    ctx.set_easyworship_item("Scripture", 3); bumped()
    ctx.record_audio(AudioObservation(channel=1, speaker_role="pastor", speaking=True, transcript="hi")); bumped()
    ctx.set_speaking("liturgist", True); bumped()
    ctx.record_vision(VisionObservation(input="program", frame_health="ok")); bumped()
    ctx.record_action("x"); bumped()
    ctx.set_camera_role("pastor"); bumped()
    ctx.set_plan_cursor(2); bumped()


def test_no_op_mutations_do_not_bump_version():
    ctx = ServiceContext()
    ctx.set_program(1)
    ctx.set_state(ServiceState.WELCOME)
    ctx.set_speaking("pastor", True)
    v = ctx.version
    assert ctx.set_program(1) is False
    assert ctx.set_state(ServiceState.WELCOME) is False
    assert ctx.set_speaking("pastor", True) is False  # still speaking: just refreshes last_spoke_at
    assert ctx.set_preview(None) is False
    assert ctx.version == v


def test_updated_at_tracks_last_mutation():
    ctx = ServiceContext()
    before = ctx.updated_at
    ctx.set_program(3)
    assert ctx.updated_at >= before
    assert ctx.snapshot()["updated_at"] == ctx.updated_at.isoformat()


# -- speaking roles -----------------------------------------------------------
def test_speaker_derives_from_speaking_roles_not_last_polled_channel():
    wall = [100.0]
    ctx = _ctx(wall=wall)
    ctx.set_speaking("pastor", True)
    wall[0] = 101.0
    ctx.set_speaking("liturgist", True)
    # The observer polls every channel every tick; a later silent poll of the
    # congregation channel must not become "the speaker".
    wall[0] = 102.0
    ctx.record_audio(AudioObservation(channel=8, speaker_role="congregation", speaking=False))

    snap = ctx.snapshot()
    assert snap["active_speakers"] == ["liturgist", "pastor"]  # most recent first
    assert snap["speaker"] == "liturgist"
    assert snap["speaking"] is True
    assert snap["speaking_roles"]["congregation"]["speaking"] is False
    assert snap["speaking_roles"]["pastor"] == {"speaking": True, "since": 100.0, "last_spoke_at": 100.0, "is_singing": None}


def test_speaker_falls_back_to_last_role_that_spoke_when_silent():
    wall = [10.0]
    ctx = _ctx(wall=wall)
    ctx.set_speaking("pastor", True)
    wall[0] = 20.0
    ctx.set_speaking("pastor", False)
    ctx.set_speaking("vocalist", False)
    snap = ctx.snapshot()
    assert snap["speaking"] is False
    assert snap["active_speakers"] == []
    assert snap["speaker"] == "pastor"
    assert ctx.silent_for("pastor") == 0.0
    wall[0] = 25.0
    assert ctx.silent_for("pastor") == 5.0
    assert ctx.speaking_for("pastor") == 0.0


def test_speaking_for_measures_continuous_speech():
    wall = [0.0]
    ctx = _ctx(wall=wall)
    ctx.set_speaking("vocalist", True)
    wall[0] = 9.5
    ctx.set_speaking("vocalist", True)  # repeated poll, same state
    assert ctx.speaking_for("vocalist") == 9.5
    assert ctx.speaking_roles["vocalist"]["since"] == 0.0
    assert ctx.speaking_roles["vocalist"]["last_spoke_at"] == 9.5


def test_vad_observations_update_speaking_but_transcripts_do_not_start_speech():
    ctx = ServiceContext()
    ctx.record_audio(AudioObservation(channel=1, speaker_role="pastor", speaking=True))
    assert ctx.speaking_roles["pastor"]["speaking"] is True
    ctx.record_audio(AudioObservation(channel=1, speaker_role="pastor", speaking=False))
    assert ctx.speaking_roles["pastor"]["speaking"] is False
    # A Whisper segment arrives after the speech ended; it must not flip speaking back on.
    ctx.record_audio(AudioObservation(channel=1, speaker_role="pastor", speaking=True, transcript="amen"))
    assert ctx.speaking_roles["pastor"]["speaking"] is False
    assert ctx.transcript[-1].text == "amen"


# -- transcript lines -----------------------------------------------------------
def test_transcript_line_carries_confidence_timing_and_singing():
    ctx = ServiceContext()
    ctx.record_audio(
        AudioObservation(channel=4, speaker_role="vocalist", speaking=True, transcript="amazing grace", confidence=0.77)
    )
    line = ctx.transcript[-1]
    assert isinstance(line, TranscriptLine)
    assert line.confidence == 0.77
    assert line.t_start is None and line.t_end is None and line.is_singing is False

    assert ctx.annotate_transcript("vocalist", "amazing grace", t_start=1.0, t_end=3.5, is_singing=True)
    assert (line.t_start, line.t_end, line.is_singing) == (1.0, 3.5, True)
    assert ctx.annotate_transcript("pastor", "nope") is False

    recent = ctx.snapshot()["transcript_recent"]
    assert recent[-1]["role"] == "vocalist"
    assert recent[-1]["text"] == "amazing grace"
    assert recent[-1]["confidence"] == 0.77
    assert recent[-1]["is_singing"] is True
    assert recent[-1]["age_seconds"] >= 0


def test_transcript_recent_is_last_eight_lines():
    ctx = ServiceContext()
    for i in range(12):
        ctx.record_audio(AudioObservation(channel=1, speaker_role="pastor", speaking=True, transcript=f"line {i}"))
    recent = ctx.snapshot()["transcript_recent"]
    assert len(recent) == 8
    assert recent[0]["text"] == "line 4" and recent[-1]["text"] == "line 11"


# -- hardware state ---------------------------------------------------------------
def test_set_program_records_cut_time_and_role():
    mono = [1000.0]
    ctx = _ctx(mono=mono)
    assert ctx.seconds_since_last_cut() is None
    assert ctx.set_program(1, camera_role="pastor") is True
    assert ctx.last_cut_at == 1000.0 and ctx.last_cut_at_wall is not None
    mono[0] = 1012.5
    assert ctx.seconds_since_last_cut() == 12.5
    # Same input, new role: no new cut.
    assert ctx.set_program(1, camera_role="wide") is True
    assert ctx.last_cut_at == 1000.0
    assert ctx.current_camera_role == "wide"
    snap = ctx.snapshot()
    assert snap["atem_program"] == 1 and snap["camera_role"] == "wide"
    assert snap["seconds_since_last_cut"] == 12.5


def test_set_state_resets_state_since_only_on_change():
    mono = [50.0]
    ctx = _ctx(mono=mono)
    mono[0] = 60.0
    assert ctx.set_state(ServiceState.SERMON, source="cue") is True
    assert ctx.state_since == 60.0 and ctx.state_source == "cue"
    mono[0] = 90.0
    assert ctx.set_state(ServiceState.SERMON, source="llm") is False
    assert ctx.state_since == 60.0
    assert ctx.seconds_in_state() == 30.0
    snap = ctx.snapshot()
    assert snap["seconds_in_state"] == 30.0
    assert snap["service_state_source"] == "llm"


def test_set_easyworship_item_tracks_label_slide_and_pres_no():
    ctx = ServiceContext()
    assert ctx.set_easyworship_item("Scripture", 2, 5) is True
    assert ctx.set_easyworship_item("Scripture", 2, 5) is False
    assert ctx.set_easyworship_item("Scripture", 3, 5) is True
    snap = ctx.snapshot()
    assert snap["easyworship_item"] == "Scripture"
    assert snap["easyworship_slide"] == 3
    assert ctx.current_easyworship_pres_no == 5


# -- plan cursor --------------------------------------------------------------------
def test_plan_cursor_from_cue_engine_index():
    ctx = ServiceContext()
    ctx.set_plan_cursor(1)
    cursor = ctx.plan_cursor()
    assert cursor["index"] == 1 and cursor["total"] == len(ctx.plan.elements)
    assert cursor["source"] == "cue"
    assert cursor["current"]["id"] == "call_to_worship"
    assert cursor["current"]["state"] == "call_to_worship"
    assert cursor["current"]["camera_role"] == "liturgist"
    assert cursor["current"]["easyworship_item"] == "Call to Worship"
    assert cursor["current"]["label"] == "Call to Worship"
    assert cursor["next"]["id"] == "opening_prayer"


def test_plan_cursor_falls_back_to_easyworship_item_then_state():
    ctx = ServiceContext()
    ctx.set_easyworship_item("Scripture")
    cursor = ctx.plan_cursor()
    assert cursor["source"] == "easyworship" and cursor["current"]["id"] == "scripture"

    ctx.set_easyworship_item(None)
    ctx.set_state(ServiceState.SERMON)
    cursor = ctx.plan_cursor()
    assert cursor["source"] == "state" and cursor["current"]["id"] == "sermon"
    assert cursor["next"] is None  # sermon is last in the default plan

    ctx.set_state(ServiceState.PRE_SERVICE)
    cursor = ctx.plan_cursor()
    assert cursor["index"] is None and cursor["current"] is None
    assert cursor["next"]["id"] == "welcome"


def test_snapshot_has_every_expected_key():
    expected = {
        # existing keys
        "service_state", "speaker", "speaking", "camera_role", "atem_program", "easyworship_item",
        "recent_transcript", "last_actions", "last_decision", "vision", "updated_at",
        # new keys
        "service_state_source", "speaking_roles", "active_speakers", "seconds_in_state",
        "seconds_since_last_cut", "atem_preview", "easyworship_slide", "plan_cursor",
        "previous_decision", "version", "transcript_recent",
    }
    snap = ServiceContext().snapshot()
    assert expected <= set(snap)
    assert snap["vision"] == {"program_health": None, "cameras": {}, "semantic": {}}
    assert snap["previous_decision"] == snap["last_decision"]
