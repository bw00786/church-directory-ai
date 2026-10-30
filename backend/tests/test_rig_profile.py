"""Rig profile models and loader (app.rig.profile)."""

import pytest
import yaml

from app.config import settings
from app.rig import profile as rig_mod
from app.rig.profile import (
    EXAMPLE_FILE,
    RigProfile,
    RigProfileError,
    load_rig_profile,
    profile_path,
    resolve_env_ref,
)


@pytest.fixture(autouse=True)
def _fresh_singleton():
    rig_mod.install(None)
    yield
    rig_mod.install(None)


def _file(tmp_path, text, name="rig.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# -- defaults == settings -------------------------------------------------------

def test_default_profile_reproduces_settings(monkeypatch):
    monkeypatch.setattr(settings, "camera_1_host", "10.0.0.9")
    monkeypatch.setattr(settings, "camera_1_name", "Sanctuary PTZ")
    monkeypatch.setattr(settings, "camera_1_password", "s3cret")
    monkeypatch.setattr(settings, "atem_camera1_input", 3)
    monkeypatch.setattr(settings, "atem_camera2_input", 5)
    monkeypatch.setattr(settings, "mixer_vocalist_channel", 7)
    monkeypatch.setattr(settings, "camera_role_choir_preset", 9)

    profile = RigProfile.default_from_settings()
    assert profile.source == "defaults"
    (camera,) = profile.cameras
    assert (camera.id, camera.name, camera.host, camera.password, camera.atem_input) == (
        settings.ptz_camera_id, "Sanctuary PTZ", "10.0.0.9", "s3cret", 3,
    )
    assert (camera.port, camera.visca_port, camera.visca_udp) == (
        settings.camera_1_port, settings.camera_1_visca_port, settings.camera_1_visca_udp,
    )
    assert profile.switcher.slides_input == 5
    assert profile.mixer.roles == {
        "pastor": settings.mixer_pastor_channel,
        "liturgist": settings.mixer_liturgist_channel,
        "vocalist": 7,
        "congregation": settings.mixer_congregation_channel,
    }
    for role in ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide"):
        assert profile.role_shot(role) == (
            getattr(settings, f"camera_role_{role}_camera"), getattr(settings, f"camera_role_{role}_preset"),
        )
    assert profile.role_shot("choir") == (1, 9)
    assert profile.follower_cue_ids() == {c.strip() for c in settings.song_follower_cues.split(",")}
    assert profile.church.name == "Vernon UMC"
    assert profile.church.service_time == settings.service_start_time
    assert profile.cues == [] and profile.heading_synonyms == {}


def test_rig_without_file_mirrors_live_settings(monkeypatch, tmp_path):
    monkeypatch.setitem(settings.__dict__, "rig_profile_file", str(tmp_path / "absent.yaml"))
    assert rig_mod.rig().source == "defaults"
    monkeypatch.setattr(settings, "camera_role_pastor_preset", 11)
    assert rig_mod.rig().role_shot("pastor") == (1, 11)


# -- loading --------------------------------------------------------------------

def test_profile_path_relative_to_repo_root(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "rig_profile_file", "rig.yaml")
    assert profile_path() == rig_mod.REPO_ROOT / "rig.yaml"
    assert profile_path("/tmp/x.yaml").as_posix() == "/tmp/x.yaml"


def test_missing_file_gives_defaults(tmp_path):
    profile = load_rig_profile(tmp_path / "nope.yaml")
    assert profile.source == "defaults" and len(profile.cameras) == 1


def test_partial_file_inherits_omitted_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "camera_role_wide_preset", 8)
    path = _file(tmp_path, """
church: {name: St Mark}
roles:
  choir: {camera: 1, preset: 5}
mixer:
  roles: {pastor: 1, choir: 6}
""")
    profile = load_rig_profile(path)
    assert profile.source == "file" and profile.path == str(path)
    assert profile.church.name == "St Mark"
    assert profile.church.service_time == settings.service_start_time  # inherited scalar
    assert profile.role_shot("choir") == (1, 5)
    assert profile.role_shot("wide") == (1, 8)  # settings role kept
    assert profile.mixer.roles == {"pastor": 1, "choir": 6}  # replaced, not merged
    assert profile.mixer.driver in ("mock", "meter_feed", "mgx16_usb")  # inherited
    assert len(profile.cameras) == 1 and profile.cameras[0].atem_input == settings.atem_camera1_input


def test_env_ref_password_resolves_and_masks(tmp_path, monkeypatch):
    monkeypatch.setenv("CAM2_PW", "hunter2")
    path = _file(tmp_path, """
cameras:
  - {id: 1, host: 10.0.0.1, atem_input: 1, password: "${CAM2_PW}", username: admin}
  - {id: 2, host: 10.0.0.2, atem_input: 3, password: literal}
""")
    profile = load_rig_profile(path)
    assert profile.cameras[0].resolved_password() == "hunter2"
    assert profile.cameras[0].password == "${CAM2_PW}"  # raw ref kept for to_yaml
    assert profile.cameras[1].resolved_password() == "literal"
    masked = profile.masked()
    assert [c["password"] for c in masked["cameras"]] == ["***", "***"]
    assert resolve_env_ref("${NOT_SET_ANYWHERE_XYZ}") is None
    assert resolve_env_ref("plain") == "plain"
    # to_yaml round-trips through the loader unchanged.
    again = RigProfile.from_yaml(profile.to_yaml())
    assert again.to_data() == profile.to_data()


def test_yaml_syntax_error_is_fatal_and_named(tmp_path):
    path = _file(tmp_path, "cameras: [\n  - id: 1")
    with pytest.raises(RigProfileError) as exc:
        load_rig_profile(path)
    assert "not valid YAML" in str(exc.value) and str(path) in str(exc.value)


def test_top_level_must_be_mapping(tmp_path):
    with pytest.raises(RigProfileError, match="top level must be a mapping"):
        load_rig_profile(_file(tmp_path, "- just\n- a list\n"))


@pytest.mark.parametrize(
    "text, needle",
    [
        ("cameras:\n  - {id: 1, atem_input: x}\n", "cameras[0].atem_input"),
        ("cameras:\n  - {id: 1, atem_input: 1}\n  - {id: 1, atem_input: 2}\n", "cameras[1].id: duplicate camera id 1"),
        ("cameras:\n  - {id: 1, atem_input: 1}\n  - {id: 2, atem_input: 1}\n", "cameras[1].atem_input: ATEM input 1 is already used by camera 1"),
        ("cameras:\n  - {id: 1, atem_input: 2}\nswitcher: {slides_input: 2}\n", "switcher.slides_input"),
        ("cameras:\n  - {id: 1, atem_input: 1}\nroles:\n  choir: {camera: 2, preset: 1}\n", "roles.choir.camera: no camera with id 2"),
        ("mixer:\n  roles: {pastor: 1, choir: 1}\n", "channel 1 is already assigned to 'pastor'"),
        ("cues:\n  - {id: sermon, state: preaching}\n", "cues[0].state: unknown service state 'preaching'"),
        ("cues:\n  - {id: sermon, advance: sometime}\n", "cues[0].advance"),
        ("cues:\n  - {id: sermon}\n  - {id: sermon}\n", "cues[1].id: duplicate cue override 'sermon'"),
        ("cues:\n  - {id: x, label: X, actions: [{type: atem_program, input: lobby}]}\n", "cues[0].actions[0].input: unknown ATEM input 'lobby'"),
        ("cues:\n  - {id: x, label: X, channels: [choir]}\n", "cues[0].channels[0]: 'choir' is not a mixer role"),
        ("cues:\n  - {id: x, label: X, actions: [{type: slide}]}\n", "op: required for a slide action"),
        ("heading_synonyms:\n  sermon: ['(unclosed']\n", "heading_synonyms.sermon[0]: invalid regex"),
        ("follower:\n  roles: [drummer]\n", "follower.roles: 'drummer' is not a mixer role"),
        ("camerass: []\n", "camerass"),
        ("cameras:\n  - {id: 1, atem_input: 1, hostname: x}\n", "cameras[0].hostname"),
        ("church: {service_time: noon}\n", "church.service_time: service_time must be HH:MM"),
    ],
)
def test_validation_errors_name_the_field(tmp_path, text, needle):
    with pytest.raises(RigProfileError) as exc:
        load_rig_profile(_file(tmp_path, text))
    assert needle in str(exc.value), str(exc.value)
    assert str(tmp_path / "rig.yaml") in str(exc.value)


def test_resolve_input_names_slides_and_camera_refs(tmp_path):
    profile = load_rig_profile(_file(tmp_path, """
cameras:
  - {id: 1, atem_input: 1}
  - {id: 2, atem_input: 3}
switcher: {inputs: {front: 1, rear: 3, slides: 4}, slides_input: 4}
"""))
    assert profile.resolve_input(7) == 7
    assert profile.resolve_input("rear") == 3
    assert profile.resolve_input("SLIDES") == 4
    assert profile.resolve_input("camera:2") == 3
    assert profile.camera_for_atem_input(3) == 2 and profile.atem_input_for_camera(2) == 3
    with pytest.raises(ValueError, match="no camera with id 9"):
        profile.resolve_input("camera:9")


def test_singleton_reload_and_install(tmp_path, monkeypatch):
    path = _file(tmp_path, "church: {name: Reloaded}\n")
    monkeypatch.setitem(settings.__dict__, "rig_profile_file", str(path))
    assert rig_mod.reload().church.name == "Reloaded"
    assert rig_mod.rig().church.name == "Reloaded"
    path.write_text("church: {name: Again}\n", encoding="utf-8")
    assert rig_mod.rig().church.name == "Reloaded"  # cached until reload
    assert rig_mod.reload().church.name == "Again"
    in_memory = RigProfile.from_data({"church": {"name": "Memory"}})
    rig_mod.install(in_memory)
    assert rig_mod.rig().church.name == "Memory" and rig_mod.rig().source == "memory"


def test_example_file_is_valid_and_documented():
    text = EXAMPLE_FILE.read_text(encoding="utf-8")
    profile = RigProfile.from_yaml(text, source="file", path="rig.example.yaml")
    assert [c.id for c in profile.cameras] == [1, 2]
    assert "choir" in profile.mixer.roles and "liturgist" not in profile.mixer.roles
    assert profile.role_shot("choir") == (2, 2)
    assert "anthem" in {c.id for c in profile.cues}
    assert yaml.safe_load(text)["church"]["name"] == profile.church.name
    # Every section is explained.
    for section in ("church", "cameras", "switcher", "mixer", "presentation", "roles", "cues", "heading_synonyms", "follower"):
        assert f"{section}:" in text
