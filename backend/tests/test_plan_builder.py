"""Cue -> AI-facing plan mapping (app.director.plan_builder)."""

from app.config import settings
from app.director.models import ActionType, Cue, CueAction, ServiceScript
from app.director.plan_builder import (
    CUE_PLAN_DEFAULTS,
    build_plan_from_script,
    easyworship_item_for_cue,
    plan_entry_for_cue,
    state_for_cue,
)
from app.director.script import build_cue_catalog, build_default_service_script
from app.director.slide_text_source import _FOLLOW_STATES
from app.domain.service_state import FOLLOWABLE_STATES, SONG_STATES, ServiceState


def test_every_builtin_cue_has_a_default_entry():
    ids = {cue.id for cue in build_cue_catalog()}
    assert ids == set(CUE_PLAN_DEFAULTS)


def test_lossy_default_mappings_are_gone():
    by_id = {cue.id: cue for cue in build_cue_catalog()}
    assert state_for_cue(by_id["childrens_message"]) == ServiceState.CHILDRENS_MESSAGE
    assert state_for_cue(by_id["community_prayers"]) == ServiceState.PRAYERS
    assert state_for_cue(by_id["lords_prayer"]) == ServiceState.PRAYERS
    assert state_for_cue(by_id["offertory_liturgist"]) == ServiceState.OFFERTORY
    assert state_for_cue(by_id["bumper_video"]) == ServiceState.VIDEO
    # Nothing that is not a welcome is called a welcome any more.
    assert [cid for cid, e in CUE_PLAN_DEFAULTS.items() if e.state == ServiceState.WELCOME] == []


def test_default_table_values_for_key_cues():
    by_id = {cue.id: cue for cue in build_cue_catalog()}
    sermon = plan_entry_for_cue(by_id["sermon"])
    assert (sermon.state, sermon.speaker_role, sermon.camera_role, sermon.easyworship_item, sermon.followable) == (
        ServiceState.SERMON, "pastor", "pastor", None, False,
    )
    song = plan_entry_for_cue(by_id["first_song"])
    assert song.state == ServiceState.CONGREGATIONAL_SONG
    assert song.easyworship_item == "Opening Praise" and song.followable is True
    assert easyworship_item_for_cue(by_id["scripture_reading"]) == "Scripture"
    assert easyworship_item_for_cue(by_id["sermon"]) is None


def test_followable_defaults_match_song_follower_cues_setting():
    follower_cues = {c.strip() for c in settings.song_follower_cues.split(",") if c.strip()}
    followable = {cid for cid, e in CUE_PLAN_DEFAULTS.items() if e.followable}
    assert followable == follower_cues


def test_followable_states_cover_the_slide_followers_states():
    assert set(_FOLLOW_STATES) <= set(FOLLOWABLE_STATES)
    assert ServiceState.PRAYERS in FOLLOWABLE_STATES  # Lord's Prayer is recited from the slides
    assert ServiceState.SPECIAL_MUSIC in SONG_STATES and ServiceState.SPECIAL_MUSIC not in FOLLOWABLE_STATES


def test_easyworship_items_only_on_cues_that_put_an_item_live():
    for cue in build_cue_catalog():
        navigates = any(a.type == ActionType.SLIDE and a.slide_op in ("next_item", "live") for a in cue.actions)
        assert (easyworship_item_for_cue(cue) is not None) == navigates, cue.id


def test_cue_fields_override_defaults():
    base = {cue.id: cue for cue in build_cue_catalog()}["sermon"]
    cue = base.model_copy(update={"state": "special_music", "speaker_role": "vocalist", "camera_role": "choir",
                                  "easyworship_item": "Anthem", "followable": True})
    entry = plan_entry_for_cue(cue)
    assert entry.state == ServiceState.SPECIAL_MUSIC
    assert entry.speaker_role == "vocalist" and entry.camera_role == "choir"
    assert entry.easyworship_item == "Anthem" and entry.followable is True


def test_partial_override_keeps_other_defaults():
    base = {cue.id: cue for cue in build_cue_catalog()}["doxology"]
    cue = base.model_copy(update={"camera_role": "choir"})
    entry = plan_entry_for_cue(cue)
    assert entry.camera_role == "choir"
    assert entry.state == ServiceState.HYMN and entry.speaker_role == "vocalist"
    assert entry.easyworship_item == "Doxology" and entry.followable is True


def test_invalid_state_override_falls_back_to_default():
    base = {cue.id: cue for cue in build_cue_catalog()}["sermon"]
    assert state_for_cue(base.model_copy(update={"state": "not_a_state"})) == ServiceState.SERMON


def test_unknown_cue_infers_from_actions():
    cue = Cue(
        id="baptism",
        name="Baptism",
        actions=[
            CueAction(type=ActionType.PTZ_PRESET, camera_id=1, preset_id=3, role="pastor"),
            CueAction(type=ActionType.SLIDE, slide_op="next_item"),
        ],
    )
    entry = plan_entry_for_cue(cue)
    assert entry.state == ServiceState.WELCOME  # neutral default; rig profile sets Cue.state
    assert entry.camera_role == "pastor" and entry.speaker_role == "pastor"
    assert entry.easyworship_item == "Baptism" and entry.followable is False
    assert state_for_cue(cue.model_copy(update={"state": "communion"})) == ServiceState.COMMUNION


def test_plan_index_equals_cue_index_and_carries_labels():
    script = build_default_service_script()
    plan = build_plan_from_script(script)
    assert [el.id for el in plan.elements] == [c.id for c in script.cues]
    assert [el.label for el in plan.elements] == [c.name for c in script.cues]
    assert plan.index_of_easyworship_item("scripture") == [c.id for c in script.cues].index("scripture_reading")
    assert plan.first_index_of_state(ServiceState.SERMON) == [c.id for c in script.cues].index("sermon")
    assert plan.elements[plan.first_index_of_state(ServiceState.VIDEO)].followable is False


def test_custom_defaults_table_is_pure_input():
    cue = Cue(id="x", name="X")
    table = {"x": CUE_PLAN_DEFAULTS["benediction"]}
    assert plan_entry_for_cue(cue, defaults=table).state == ServiceState.BENEDICTION
    assert plan_entry_for_cue(cue).state == ServiceState.WELCOME
    assert build_plan_from_script(ServiceScript(name="s", cues=[cue])).elements[0].type == ServiceState.WELCOME
