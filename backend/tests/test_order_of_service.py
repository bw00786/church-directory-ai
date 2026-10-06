from app.director.order_of_service import build_script_from_order, parse_order_of_service
import pytest

SAMPLE = """Service Date: March 22, 2026

Theme: Purple Theory

Speaker: Pastor Megan

Order of Service

5 Minute Countdown

Opening Praise:

My Lighthouse

Announcements

Children\u2019s Message

Pastor Megan

Call to Worship

This is the day the Lord has made, let us rejoice and be glad in it!

Opening Prayer:

Dear God, we come before you and offer ourselves to you this day in worship. We are in awe of your presence. Amen.

Hymn of Praise:

#158 Come Christians Join to Sing

Scripture:  1 Corinthians 10:31-33

31 So whether you eat or drink or whatever you do, do it all for the glory of God. 32 Do not cause anyone to stumble.

Bumper Video

Sermon:       Worship

Holy Communion

Lord\u2019s Prayer

Prayers for the Community

Offering with Doxology

Closing Praise:

All the People Said Amen

Dismissal
"""


def test_parses_header_fields():
    order = parse_order_of_service(SAMPLE)
    assert order.date == "March 22, 2026"
    assert order.theme == "Purple Theory"
    assert order.speaker == "Pastor Megan"


def test_sample_order_builds_expected_cue_flow():
    script, _ = build_script_from_order(SAMPLE)
    assert [c.id for c in script.cues] == [
        "service_start",
        "first_song",
        "announcements",
        "childrens_message",
        "call_to_worship_liturgist",
        "call_to_worship_slides",
        "hymn_of_praise",
        "scripture_reading",
        "bumper_video",
        "sermon",
        "communion_pastor",
        "communion_congregation",
        "lords_prayer",
        "community_prayers",
        "offertory_liturgist",
        "doxology",
        "closing_praise",
        "benediction",
        "service_end",
    ]
    assert script.name == "Vernon UMC \u2014 Purple Theory \u2014 March 22, 2026"


def test_body_text_is_not_treated_as_headings():
    order = parse_order_of_service(SAMPLE)
    assert len(order.items) == 16


def test_text_without_items_is_rejected():
    with pytest.raises(ValueError):
        build_script_from_order("nothing useful here")
