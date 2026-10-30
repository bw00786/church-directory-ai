"""Cue sheet and camera service share one role -> camera/preset table."""

import pytest

from app.cameras.service import CAMERA_ROLES, CameraService, configured_role_shot, resolve_role_shot
from app.config import settings
from app.director.models import ActionType
from app.director.script import _atem, _preset, build_cue_catalog


def _ptz_actions(cue_id):
    cue = next(c for c in build_cue_catalog() if c.id == cue_id)
    return [a for a in cue.actions if a.type == ActionType.PTZ_PRESET]


@pytest.fixture(autouse=True)
def _no_learned_presets(monkeypatch):
    import app.identity.service as identity_mod

    monkeypatch.setattr(identity_mod.identity_service, "best_preset_for_role", lambda role, camera_id: None)


def test_cue_sheet_pastor_shot_follows_role_config(monkeypatch):
    monkeypatch.setattr(settings, "camera_role_pastor_camera", 1)
    monkeypatch.setattr(settings, "camera_role_pastor_preset", 3)
    for cue_id in ("announcements", "sermon", "community_prayers", "benediction"):
        (action,) = _ptz_actions(cue_id)
        assert action.role == "pastor"
        assert (action.camera_id, action.preset_id) == (1, 3), cue_id
        assert action.description.startswith("Preset 3 ")


async def test_cue_sheet_and_move_to_role_agree(monkeypatch):
    monkeypatch.setattr(settings, "camera_role_pastor_preset", 7)
    moved = []

    svc = CameraService()

    async def fake_move(camera_id, preset_id):
        moved.append((camera_id, preset_id))
        return True

    monkeypatch.setattr(svc, "move_to_preset", fake_move)

    assert await svc.move_to_role("pastor") is True
    (sheet,) = _ptz_actions("sermon")
    assert moved == [(sheet.camera_id, sheet.preset_id)] == [(1, 7)]


def test_every_role_cue_matches_configured_shot_unless_overridden():
    for cue in build_cue_catalog():
        for action in cue.actions:
            if action.type != ActionType.PTZ_PRESET or action.role is None:
                continue
            camera_id, preset_id = configured_role_shot(action.role)
            assert action.camera_id == camera_id, cue.id
            if cue.id == "communion_pastor":
                assert action.preset_id == 2  # deliberate per-cue override
            else:
                assert action.preset_id == preset_id, cue.id


def test_preset_helper_requires_role_or_number():
    with pytest.raises(ValueError):
        _preset("nothing")
    with pytest.raises(ValueError):
        _preset("ghost", role="drummer")
    explicit = _preset("tight shot", preset_id=9)
    assert explicit.role is None and explicit.preset_id == 9 and explicit.camera_id == settings.ptz_camera_id
    override = _preset("table", role="pastor", preset_id=2)
    assert override.role == "pastor" and override.preset_id == 2


def test_atem_helper_carries_transition():
    assert _atem(1, "x").transition is None
    assert _atem(1, "x", transition="auto").transition == "auto"


def test_configured_role_shot_known_and_unknown(monkeypatch):
    monkeypatch.setattr(settings, "camera_role_liturgist_camera", 1)
    monkeypatch.setattr(settings, "camera_role_liturgist_preset", 4)
    assert configured_role_shot("liturgist") == (1, 4)
    assert configured_role_shot("Liturgist ") == (1, 4)
    assert configured_role_shot("drummer") == (None, None)
    assert "pastor" in CAMERA_ROLES


def test_resolve_role_shot_prefers_learned_preset(monkeypatch):
    import app.identity.service as identity_mod

    monkeypatch.setattr(settings, "camera_role_pastor_camera", 1)
    monkeypatch.setattr(settings, "camera_role_pastor_preset", 1)
    monkeypatch.setattr(identity_mod.identity_service, "best_preset_for_role", lambda role, camera_id: 3)
    assert resolve_role_shot("pastor") == (1, 3, "learned")
    monkeypatch.setattr(identity_mod.identity_service, "best_preset_for_role", lambda role, camera_id: None)
    assert resolve_role_shot("pastor") == (1, 1, "configured")
    assert resolve_role_shot("drummer") == (None, None, "unknown")


async def test_move_to_role_uses_learned_preset(monkeypatch):
    import app.identity.service as identity_mod

    monkeypatch.setattr(settings, "camera_role_pastor_camera", 1)
    monkeypatch.setattr(settings, "camera_role_pastor_preset", 1)
    monkeypatch.setattr(identity_mod.identity_service, "best_preset_for_role", lambda role, camera_id: 5)
    svc = CameraService()
    moved = []

    async def fake_move(camera_id, preset_id):
        moved.append((camera_id, preset_id))
        return True

    monkeypatch.setattr(svc, "move_to_preset", fake_move)
    assert await svc.move_to_role("pastor") is True
    assert moved == [(1, 5)]
    assert await svc.move_to_role("drummer") is False


def test_cue_defaults_cut_and_no_cover():
    for cue in build_cue_catalog():
        assert cue.transition == "cut"
        assert cue.cover_with is None
