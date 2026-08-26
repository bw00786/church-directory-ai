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


def test_multi_role_transcripts_interleave_by_arrival(monkeypatch):
    """Role-tagged transcript lines from simultaneous roles interleave in order."""
    from datetime import datetime, timedelta, timezone

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
