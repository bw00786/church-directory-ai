"""Tests for ServiceContext (authoritative rolling context memory)."""

from app.domain.observations import AudioObservation
from app.domain.service_context import ServiceContext
from app.domain.service_state import ServiceState


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
