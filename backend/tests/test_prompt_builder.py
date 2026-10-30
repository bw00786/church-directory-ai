"""AI director prompt rendering: sections, graceful fallbacks, token guard, house rules."""

from datetime import datetime, timezone

import pytest

from app.ai import prompt_builder as pb
from app.config import settings
from app.domain.service_context import ServiceContext

NOW = datetime(2026, 10, 30, 10, 30, tzinfo=timezone.utc)


def _rich_snapshot() -> dict:
    return {
        "service_state": "scripture",
        "seconds_in_state": 95,
        "seconds_since_last_cut": 40,
        "song_phase": None,
        "speaking_roles": {
            "pastor": {"speaking": True, "since": NOW.timestamp() - 3, "last_spoke_at": NOW.timestamp()},
            "liturgist": {"speaking": False, "since": NOW.timestamp() - 4, "last_spoke_at": NOW.timestamp() - 4},
            "vocalist": {"speaking": True, "since": NOW.timestamp() - 10, "last_spoke_at": NOW.timestamp(), "is_singing": True},
        },
        "active_speakers": ["pastor", "vocalist"],
        "camera_role": "liturgist",
        "atem_program": 1,
        "atem_preview": 2,
        "easyworship_item": "Scripture",
        "easyworship_slide": 3,
        "plan_cursor": {
            "index": 3, "total": 7,
            "current": {"label": "Scripture", "state": "scripture", "camera_role": "liturgist", "easyworship_item": "Scripture"},
            "next": {"label": "Song", "state": "congregational_song", "camera_role": "congregation", "easyworship_item": "Song"},
        },
        "vision": {
            "program_health": "ok",
            "cameras": {
                "pastor": {"person_present": True, "person_in_roi": True, "frame_health": "ok"},
                "liturgist": {"person_present": False, "person_in_roi": False, "frame_health": "black"},
            },
            "semantic": {},
        },
        "transcript_recent": [
            {"role": "liturgist", "text": "This is the word of the Lord.", "age_seconds": 6, "confidence": 0.9, "is_singing": False},
            {"role": "pastor", "text": "Thank you Carol.", "age_seconds": 2, "confidence": 0.8, "is_singing": False},
            {"role": "congregation", "text": "mumble", "age_seconds": 3, "confidence": 0.1, "is_singing": False},
            {"role": "vocalist", "text": "amazing grace", "age_seconds": 1, "confidence": 0.7, "is_singing": True},
        ],
        "last_actions": ["PTZ liturgist", "ATEM cut"],
        "previous_decision": {"decision": "continue", "confidence": 0.9, "reason": "reading in progress",
                              "actions": [{"type": "PTZ_SELECT_ROLE", "camera_role": "liturgist"}]},
        "version": 12,
        "updated_at": NOW.isoformat(),
    }


def test_all_sections_render_in_order_with_rich_snapshot():
    build = pb.build_director_prompt(_rich_snapshot(), ServiceContext().plan.elements, now=NOW,
                                     house_rules="- hold shots", history=[], transcript_floor=0.3)
    text = build.text
    positions = [text.index(f"=== {name} ===") for name in pb.SECTION_ORDER]
    assert positions == sorted(positions)
    assert set(build.sections) == set(pb.SECTION_ORDER)

    assert "Time in this state: 95 s" in build.sections["TIMING"]
    assert "Since last cut: 40 s" in build.sections["TIMING"]
    assert "- pastor: SPEAKING for 3 s" in build.sections["WHO IS SPEAKING"]
    assert "- liturgist: silent (last spoke 4 s ago)" in build.sections["WHO IS SPEAKING"]
    assert "- vocalist: SPEAKING for 10 s [singing]" in build.sections["WHO IS SPEAKING"]
    assert "You are at element 4 of 7." in build.sections["PLAN CURSOR"]
    assert "Next: Song; state=congregational_song; camera=congregation; slides=Song" in build.sections["PLAN CURSOR"]
    assert "ATEM preview input: 2" in build.sections["ON AIR"]
    assert "EasyWorship slide: 3" in build.sections["ON AIR"]
    assert "- liturgist: EMPTY (no person); not in ROI; frame black" in build.sections["VISION"]
    assert "[2s ago] pastor (conf 0.80): Thank you Carol." in build.sections["RECENT TRANSCRIPT"]
    assert "[1s ago] vocalist (conf 0.70) [singing]: amazing grace" in build.sections["RECENT TRANSCRIPT"]
    assert "Previous decision: continue (confidence 0.90); actions: PTZ_SELECT_ROLE->liturgist" in build.sections["RECENT ACTIONS + PREVIOUS DECISION"]
    assert "- hold shots" in build.sections["HOUSE RULES"]
    assert "None found" in build.sections["MEMORY"]
    assert text.rstrip().endswith("What should happen next?")


def test_transcript_confidence_floor_drops_lines():
    build = pb.build_director_prompt(_rich_snapshot(), now=NOW, house_rules="x", transcript_floor=0.3)
    transcript = build.sections["RECENT TRANSCRIPT"]
    assert "mumble" not in transcript
    assert "1 line(s) below confidence 0.30 hidden" in transcript
    assert build.dropped_transcript_lines == 1


def test_legacy_snapshot_without_new_keys_still_renders_every_section():
    context = ServiceContext()
    snapshot = {
        "service_state": "sermon", "speaker": "pastor", "speaking": True, "camera_role": "pastor",
        "atem_program": 1, "easyworship_item": "Sermon", "recent_transcript": "pastor: let us pray",
        "last_actions": [], "last_decision": None, "vision": {}, "updated_at": NOW.isoformat(),
    }
    build = pb.build_director_prompt(snapshot, context.plan.elements, now=NOW, house_rules="rules", history="None")
    for name in pb.SECTION_ORDER:
        assert f"=== {name} ===" in build.text
    assert "Time in this state: unknown" in build.sections["TIMING"]
    assert "- pastor: SPEAKING" in build.sections["WHO IS SPEAKING"]
    assert "per-role timing not available" in build.sections["WHO IS SPEAKING"]
    assert "inferred from the service state" in build.sections["PLAN CURSOR"]
    assert "Current: sermon" in build.sections["PLAN CURSOR"]
    assert "ATEM preview input: unknown" in build.sections["ON AIR"]
    assert "No vision data" in build.sections["VISION"]
    assert "[age ?] pastor: let us pray" in build.sections["RECENT TRANSCRIPT"]
    assert "Previous decision: none this service." in build.sections["RECENT ACTIONS + PREVIOUS DECISION"]


def test_transcript_falls_back_to_context_lines_with_ages():
    from app.domain.observations import AudioObservation

    context = ServiceContext()
    context.record_audio(AudioObservation(channel=1, speaker_role="pastor", speaking=True,
                                          transcript="Good morning", timestamp=NOW.replace(minute=29, second=50)))
    snapshot = {"service_state": "welcome", "recent_transcript": "pastor: Good morning"}
    build = pb.build_director_prompt(snapshot, context_lines=context.transcript, now=NOW, house_rules="r")
    assert "[10s ago] pastor: Good morning" in build.sections["RECENT TRANSCRIPT"]


def test_memory_section_renders_structured_tags_and_similarity():
    history = [{"service_date": "2026-08-10", "category": "ai_action", "text": "camera -> pastor",
                "similarity": 0.81, "metadata": {"service_state": "sermon", "outcome": "approved"}}]
    build = pb.build_director_prompt({"service_state": "sermon"}, now=NOW, house_rules="r", history=history)
    assert "- [2026-08-10, ai_action, state=sermon, outcome=approved] camera -> pastor (similarity=0.81)" in build.sections["MEMORY"]
    assert "Live signals win" in build.sections["MEMORY"]


def test_house_style_bullets_append_to_house_rules():
    build = pb.build_director_prompt({"service_state": "sermon"}, now=NOW, house_rules="- base rule",
                                     house_style=["Operator takes wide every 4 min."])
    section = build.sections["HOUSE RULES"]
    assert section.index("- base rule") < section.index("Learned from this operator")
    assert "- Operator takes wide every 4 min." in section


def test_token_estimate_guard_trims_bulk_before_core_sections():
    snapshot = _rich_snapshot()
    history = [{"service_date": "2026-08-10", "category": "cue", "text": "x" * 400, "similarity": 0.5}
               for _ in range(6)]
    full = pb.build_director_prompt(snapshot, now=NOW, house_rules="rule " * 200, history=history,
                                    house_style=["style"] * 4, token_budget=100_000)
    tight = pb.build_director_prompt(snapshot, now=NOW, house_rules="rule " * 200, history=history,
                                     house_style=["style"] * 4, token_budget=full.token_estimate - 400)
    assert tight.token_estimate < full.token_estimate
    assert tight.trimmed and tight.trimmed[0] == "memory"
    # Core decision inputs survive trimming.
    for name in ("TIMING", "WHO IS SPEAKING", "PLAN CURSOR", "ON AIR", "VISION"):
        assert tight.sections[name] == full.sections[name]
    assert "=== RECENT TRANSCRIPT ===" in tight.text


def test_estimate_tokens_is_monotonic_and_budget_respects_num_ctx(monkeypatch):
    assert pb.estimate_tokens("") == 0
    assert pb.estimate_tokens("a" * 35) == 10
    assert pb.estimate_tokens("a" * 70) > pb.estimate_tokens("a" * 35)
    monkeypatch.setattr(settings, "ollama_num_ctx", 4096)
    monkeypatch.setitem(settings.__dict__, "ai_director_max_tokens", 320)
    assert pb.prompt_token_budget(system_prompt_tokens=1000) == 4096 - 1000 - 512


def test_house_rules_file_overrides_defaults_and_reloads_on_edit(tmp_path, monkeypatch):
    rules = tmp_path / "house-rules.md"
    monkeypatch.setitem(settings.__dict__, "ai_director_house_rules_file", str(rules))
    pb._house_rules_cache.update(path=None, mtime=None, text=None)

    defaults = pb.load_house_rules()
    assert "Hold every shot at least 8 seconds" in defaults

    rules.write_text("- Our pastor likes a wide shot during the offering.\n", encoding="utf-8")
    assert "offering" in pb.load_house_rules()

    rules.write_text("", encoding="utf-8")  # empty file falls back to defaults
    import os
    os.utime(rules, (NOW.timestamp() + 10, NOW.timestamp() + 10))
    assert "Hold every shot" in pb.load_house_rules()


def test_default_house_rules_cover_the_required_defaults():
    text = pb.DEFAULT_HOUSE_RULES_PATH.read_text(encoding="utf-8").lower()
    for phrase in ("hold every shot", "cut on pauses", "speaker change", "singing", "prayer", "sermon",
                   "empty", "auto", "cut for speech"):
        assert phrase in text, phrase


@pytest.mark.parametrize("value,expected", [(None, "unknown"), (5, "5 s"), (150, "2.5 min"), (7200, "2.0 h"), (-1, "unknown")])
def test_fmt_seconds(value, expected):
    assert pb._fmt_seconds(value) == expected
