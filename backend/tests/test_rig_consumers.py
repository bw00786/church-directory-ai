"""The rig profile drives the role table, camera registration, mixer roles,
camera -> ATEM input maps, cue overrides and order-of-service synonyms."""

import asyncio

import pytest

from app.audio.yamaha_capture import RoleChannel, configured_role_channels, role_channel
from app.cameras.service import CAMERA_ROLES, CameraService, configured_role_shot, register_from_profile
from app.config import settings
from app.director.context_sync import camera_role_for_input
from app.director.models import ActionType, AdvanceTrigger
from app.director.order_of_service import build_script_from_order, parse_order_of_service
from app.director.plan_builder import plan_entry_for_cue
from app.director.script import build_cue_catalog, build_default_service_script, profile_warnings
from app.domain.service_state import ServiceState
from app.rig import profile as rig_mod
from app.rig.profile import RigProfile
from app.vision.verification import camera_to_atem_input

TWO_PTZ = """
church: {name: Grace Community Church}
cameras:
  - {id: 1, name: Front, host: 10.0.0.1, atem_input: 1}
  - {id: 2, name: Rear, host: 10.0.0.2, atem_input: 3, driver: ptzoptics}
  - {id: 3, name: Fake, driver: mock, atem_input: 6}
switcher: {inputs: {front: 1, rear: 3, slides: 4}, slides_input: 4}
mixer:
  roles: {pastor: 1, worship_leader: 3, choir: 6, congregation: 8}
roles:
  pastor: {camera: 1, preset: 1}
  choir: {camera: 2, preset: 2}
  worship_leader: {camera: 2, preset: 1}
cues:
  - id: service_start
    timer_seconds: 120
  - id: first_song
    label: Opening Song
    channels: [worship_leader, congregation]
  - id: anthem
    after: announcements
    label: Choir Anthem
    state: special_music
    speaker_role: choir
    camera_role: choir
    advance: song_end
    channels: [choir]
    cover_with: slides
    actions:
      - {type: ptz_preset, role: choir}
      - {type: atem_program, input: rear, transition: auto}
  - id: sermon
    label: Message
    actions:
      - {type: atem_program, input: front}
      - {type: ptz_preset, role: pastor, preset: 7}
      - {type: note, note: Lapel mic on channel 1.}
  - {id: childrens_message, enabled: false}
  - {id: communion_pastor, enabled: false}
  - id: closing_blessing
    before: service_end
    label: Closing Blessing
    state: benediction
    advance: timer
    timer_seconds: 30
    actions:
      - {type: ptz_preset, camera: 1, preset: 6}
      - {type: slide, op: clear}
heading_synonyms:
  sermon: ["message", "homily"]
  anthem: ["anthem", "choir"]
  "benediction,service_end": ["blessing"]
follower:
  cues: [first_song, closing_praise]
  roles: [worship_leader, congregation]
"""


@pytest.fixture
def two_ptz():
    profile = RigProfile.from_yaml(TWO_PTZ, source="file", path="rig.yaml")
    rig_mod.install(profile)
    yield profile
    rig_mod.install(None)


@pytest.fixture(autouse=True)
def _reset_singleton():
    rig_mod.install(None)
    yield
    rig_mod.install(None)


@pytest.fixture(autouse=True)
def _no_learned_presets(monkeypatch):
    import app.identity.service as identity_mod

    monkeypatch.setattr(identity_mod.identity_service, "best_preset_for_role", lambda role, camera_id: None)


# -- role table ---------------------------------------------------------------------

def test_role_table_from_profile_with_settings_fallback(two_ptz, monkeypatch):
    assert configured_role_shot("choir") == (2, 2)
    assert configured_role_shot("Worship_Leader ") == (2, 1)
    assert configured_role_shot("pastor") == (1, 1)
    monkeypatch.setattr(settings, "camera_role_liturgist_preset", 4)
    assert configured_role_shot("liturgist") == (1, 4)  # omitted by the profile: settings
    assert configured_role_shot("drummer") == (None, None)
    roles = list(CAMERA_ROLES)
    assert roles[:3] == ["pastor", "choir", "worship_leader"]
    assert "liturgist" in CAMERA_ROLES and "wide" in CAMERA_ROLES and "drummer" not in CAMERA_ROLES
    assert len(CAMERA_ROLES) == len(set(roles))


def test_role_table_defaults_unchanged_without_profile():
    assert tuple(CAMERA_ROLES) == ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide")
    assert configured_role_shot("pastor") == (settings.camera_role_pastor_camera, settings.camera_role_pastor_preset)


async def test_move_to_role_uses_profile_camera(two_ptz, monkeypatch):
    svc = CameraService()
    moved = []

    async def fake_move(camera_id, preset_id):
        moved.append((camera_id, preset_id))
        return True

    monkeypatch.setattr(svc, "move_to_preset", fake_move)
    assert await svc.move_to_role("choir") is True
    assert moved == [(2, 2)]


# -- camera registration --------------------------------------------------------------

async def test_register_from_profile_registers_every_camera(two_ptz, monkeypatch):
    svc = CameraService()
    connects = []

    async def fake_connect(camera_id):
        connects.append(camera_id)
        return True

    monkeypatch.setattr(svc, "connect_camera", fake_connect)
    registered = register_from_profile(svc, two_ptz)
    await asyncio.sleep(0)
    assert registered == [1, 2, 3]
    listed = {c["camera_id"]: c for c in svc.list_cameras()}
    assert listed[1]["name"] == "Front" and listed[1]["host"] == "10.0.0.1"
    assert listed[2]["host"] == "10.0.0.2"
    assert listed[3]["host"] == "mock"
    assert sorted(connects) == [1, 2]  # mock cameras are never dialled


def test_register_from_profile_default_matches_camera_1_block(monkeypatch):
    monkeypatch.setattr(settings, "camera_1_host", "192.168.30.31")
    monkeypatch.setattr(settings, "camera_1_username", "admin")
    monkeypatch.setattr(settings, "camera_1_password", "pw")
    monkeypatch.setattr(settings, "camera_1_visca_udp", True)
    svc = CameraService()
    calls = []
    monkeypatch.setattr(svc, "register_camera", lambda *a, **k: calls.append((a, k)))
    assert register_from_profile(svc, connect=False) == [1]
    ((camera_id, host), kwargs) = calls[0]
    assert (camera_id, host) == (1, "192.168.30.31")
    assert kwargs == {
        "port": settings.camera_1_port,
        "username": "admin",
        "password": "pw",
        "name": settings.camera_1_name,
        "visca_port": settings.camera_1_visca_port,
        "visca_udp": True,
    }


def test_register_from_profile_skips_hostless_camera(monkeypatch):
    monkeypatch.setattr(settings, "camera_1_host", None)
    svc = CameraService()
    assert register_from_profile(svc, connect=False) == []
    assert svc.list_cameras() == []


def test_env_ref_credentials_are_resolved_at_registration(monkeypatch):
    monkeypatch.setenv("CAM_PW_TEST", "resolved")
    profile = RigProfile.from_data({"cameras": [{"id": 1, "host": "h", "atem_input": 1, "password": "${CAM_PW_TEST}"}]})
    svc = CameraService()
    register_from_profile(svc, profile, connect=False)
    assert svc._cameras[1]["password"] == "resolved"


# -- mixer roles -----------------------------------------------------------------

def test_mixer_roles_from_profile(two_ptz):
    assert configured_role_channels() == [
        RoleChannel("pastor", 1), RoleChannel("worship_leader", 3), RoleChannel("choir", 6), RoleChannel("congregation", 8),
    ]
    assert role_channel("choir") == 6 and role_channel("liturgist") is None


def test_mixer_roles_default_to_settings(monkeypatch):
    monkeypatch.setattr(settings, "mixer_vocalist_channel", 5)
    assert configured_role_channels() == [
        RoleChannel("pastor", settings.mixer_pastor_channel),
        RoleChannel("liturgist", settings.mixer_liturgist_channel),
        RoleChannel("vocalist", 5),
        RoleChannel("congregation", settings.mixer_congregation_channel),
    ]


# -- camera -> ATEM input maps ---------------------------------------------------------

def test_camera_to_atem_input_from_profile(two_ptz):
    assert camera_to_atem_input(1) == 1
    assert camera_to_atem_input(2) == 3  # a real PTZ, not the slides laptop
    assert camera_to_atem_input(3) == 6
    assert camera_to_atem_input(9) is None


def test_camera_to_atem_input_legacy_defaults(monkeypatch):
    monkeypatch.setattr(settings, "atem_camera1_input", 7)
    monkeypatch.setattr(settings, "atem_camera2_input", 8)
    assert camera_to_atem_input(1) == 7
    assert camera_to_atem_input(2) == 8
    assert camera_to_atem_input(3) is None


def test_camera_role_for_input_uses_profile_slides_and_cameras(two_ptz, monkeypatch):
    from app.cameras.service import camera_service

    presets = {}
    monkeypatch.setattr(camera_service, "get_current_preset", lambda camera_id: presets.get(camera_id))
    assert camera_role_for_input(4) == "slides"
    assert camera_role_for_input(2) is None  # not a camera, not the slides
    presets[2] = 2
    assert camera_role_for_input(3) == "choir"
    presets[1] = 1
    assert camera_role_for_input(1) == "pastor"


def test_vision_startup_registers_every_profile_camera(two_ptz, monkeypatch):
    from app.vision import startup

    registered = {}
    monkeypatch.setattr(startup.frame_capture, "register_input", lambda tag, provider: registered.__setitem__(tag, provider))
    monkeypatch.setattr(startup, "snapshot_provider", lambda url, auth: (url, auth))
    monkeypatch.setattr(settings, "vision_frame_source", "snapshot", raising=False)
    monkeypatch.setattr(settings, "vision_program_device", None, raising=False)
    startup._register_inputs()
    assert registered["camera_1"] == ("http://10.0.0.1/snapshot.jpg", None)
    assert registered["camera_2"] == ("http://10.0.0.2/snapshot.jpg", None)
    assert "camera_3" not in registered  # mock camera has no host


# -- cue overrides -------------------------------------------------------------------

def test_cue_overrides_additions_and_disables(two_ptz):
    catalog = build_cue_catalog()
    ids = [c.id for c in catalog]
    assert "childrens_message" not in ids and "communion_pastor" not in ids
    assert ids.index("anthem") == ids.index("announcements") + 1
    assert ids.index("closing_blessing") == ids.index("service_end") - 1
    by_id = {c.id: c for c in catalog}

    assert by_id["service_start"].timer_seconds == 120
    assert by_id["service_start"].actions[0].atem_input == 4  # slides input from the profile

    song = by_id["first_song"]
    assert song.name == "Opening Song" and song.channels == [3, 8]
    assert song.advance == AdvanceTrigger.SONG_END  # untouched field kept

    anthem = by_id["anthem"]
    assert anthem.advance == AdvanceTrigger.SONG_END and anthem.channels == [6] and anthem.cover_with == 4
    assert [(a.type, a.camera_id, a.preset_id, a.role, a.atem_input, a.transition) for a in anthem.actions] == [
        (ActionType.PTZ_PRESET, 2, 2, "choir", None, None),
        (ActionType.ATEM_PROGRAM, None, None, None, 3, "auto"),
    ]
    entry = plan_entry_for_cue(anthem)
    assert (entry.state, entry.speaker_role, entry.camera_role) == (ServiceState.SPECIAL_MUSIC, "choir", "choir")

    sermon = by_id["sermon"]
    assert sermon.name == "Message"
    assert [(a.type, a.atem_input, a.camera_id, a.preset_id, a.role) for a in sermon.actions][:2] == [
        (ActionType.ATEM_PROGRAM, 1, None, None, None),
        (ActionType.PTZ_PRESET, None, 1, 7, "pastor"),  # explicit preset override keeps the role
    ]
    assert sermon.actions[2].type == ActionType.NOTE
    assert plan_entry_for_cue(sermon).state == ServiceState.SERMON  # built-in table still applies

    blessing = by_id["closing_blessing"]
    assert blessing.advance == AdvanceTrigger.TIMER and blessing.timer_seconds == 30
    assert blessing.actions[0].camera_id == 1 and blessing.actions[0].preset_id == 6 and blessing.actions[0].role is None
    assert blessing.actions[1].slide_op == "clear"

    assert build_default_service_script().name == "Grace Community Church — Sunday Service"
    assert profile_warnings(two_ptz) == []


def test_builtin_role_cues_fall_back_to_settings_roles(two_ptz, monkeypatch):
    monkeypatch.setattr(settings, "camera_role_liturgist_camera", 1)
    monkeypatch.setattr(settings, "camera_role_liturgist_preset", 4)
    by_id = {c.id: c for c in build_cue_catalog()}
    (ptz,) = [a for a in by_id["offertory_liturgist"].actions if a.type == ActionType.PTZ_PRESET]
    assert (ptz.camera_id, ptz.preset_id, ptz.role) == (1, 4, "liturgist")


def test_song_end_defaults_to_all_mixer_roles_without_vocalist(two_ptz):
    by_id = {c.id: c for c in build_cue_catalog()}
    assert by_id["doxology"].channels == [8]  # congregation only: no vocalist role in this profile
    no_songs = RigProfile.from_data({"mixer": {"roles": {"choir": 6, "lead": 3}}})
    by_id = {c.id: c for c in build_cue_catalog(no_songs)}
    assert sorted(by_id["doxology"].channels) == [3, 6]


@pytest.mark.parametrize(
    "cue, needle",
    [
        ({"id": "x", "label": "X", "actions": [{"type": "ptz_preset", "role": "drummer"}]}, "cues[x].actions[0].role: unknown camera role 'drummer'"),
        ({"id": "x", "actions": [{"type": "note", "note": "n"}]}, "cues[x].label: required for a new cue"),
        ({"id": "x", "label": "X"}, "cues[x].actions: required for a new cue"),
        ({"id": "x", "label": "X", "advance": "timer", "actions": [{"type": "note", "note": "n"}]}, "cues[x].timer_seconds: required"),
        ({"id": "x", "label": "X", "after": "ghost", "actions": [{"type": "note", "note": "n"}]}, "cues[x].after: unknown cue 'ghost'"),
    ],
)
def test_catalog_errors_name_the_cue(cue, needle):
    profile = RigProfile.from_data({"cues": [cue]})
    with pytest.raises(ValueError) as exc:
        build_cue_catalog(profile)
    assert needle in str(exc.value)


def test_profile_warnings_report_unknown_follower_and_synonym_cues():
    profile = RigProfile.from_data({"follower": {"cues": ["sermon", "ghost"]}, "heading_synonyms": {"phantom": ["x"]}})
    assert profile_warnings(profile) == [
        "follower.cues: 'ghost' is not a cue in the catalog",
        "heading_synonyms.phantom: 'phantom' is not a cue in the catalog",
    ]


def test_default_catalog_unchanged_without_profile():
    ids = [c.id for c in build_cue_catalog()]
    assert ids[:3] == ["service_start", "first_song", "announcements"] and ids[-1] == "service_end" and len(ids) == 19


# -- order of service -----------------------------------------------------------------

def test_heading_synonyms_merge_into_parser(two_ptz):
    text = "Welcome\nOpening Praise\nAnnouncements\nChoir Anthem\nHomily: Grace\nBlessing\n"
    order = parse_order_of_service(text)
    assert [item.cue_ids for item in order.items] == [
        ["first_song"], ["announcements"], ["anthem"], ["sermon"], ["benediction", "service_end"],
    ]
    script, _ = build_script_from_order(text)
    assert [c.id for c in script.cues] == ["first_song", "announcements", "anthem", "sermon", "benediction", "service_end"]
    assert script.name.startswith("Grace Community Church — ")


def test_disabled_cues_are_dropped_from_order_of_service(two_ptz):
    script, _ = build_script_from_order("Children's Message\nSermon\n")
    assert [c.id for c in script.cues] == ["sermon"]


def test_follower_cue_ids_from_profile(two_ptz):
    assert two_ptz.follower_cue_ids() == {"first_song", "closing_praise"}
    assert two_ptz.follower_roles() == ["worship_leader", "congregation"]
    defaults = RigProfile.default_from_settings()
    assert defaults.follower_cue_ids() == {c.strip() for c in settings.song_follower_cues.split(",") if c.strip()}
