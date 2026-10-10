from app.director.transcript_triggers import classify_transcript


def test_call_to_worship_is_detected_from_liturgist_transcript():
    result = classify_transcript(
        "call_to_worship_liturgist", "liturgist", "Please stand for the Call to Worship."
    )
    assert result is not None
    assert result[1] >= 0.9


def test_scripture_end_is_detected_from_liturgist_transcript():
    result = classify_transcript(
        "scripture_reading", "liturgist", "This is the Word of the Lord."
    )
    assert result is not None


def test_untrusted_role_does_not_trigger_service_transition():
    assert classify_transcript("scripture_reading", "congregation", "Amen") is None


def test_opening_prayer_amen_advances_from_prayer_slides():
    assert classify_transcript("call_to_worship_slides", "pastor", "Amen") is not None