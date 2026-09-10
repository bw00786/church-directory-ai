"""Tests for Phase 2 predictive PTZ + ATEM-preview staging."""

import pytest

from app.config import settings
from app.director.predictive_ptz import PredictivePreview


class _FakeCameras:
    def __init__(self):
        self.preset_calls = []
        self.role_calls = []

    async def move_to_preset(self, camera_id, preset_id):
        self.preset_calls.append((camera_id, preset_id))
        return True

    async def move_to_role(self, role):
        self.role_calls.append(role)
        return True


class _FakeAtem:
    def __init__(self):
        self.preview_calls = []

    async def set_preview(self, input_id):
        self.preview_calls.append(input_id)
        return True


@pytest.fixture
def wiring(monkeypatch):
    cameras = _FakeCameras()
    atem = _FakeAtem()
    import app.cameras.service as cam_mod
    import app.dependencies as deps

    monkeypatch.setattr(cam_mod, "camera_service", cameras)
    monkeypatch.setattr(deps, "get_atem_service_instance", lambda: atem)
    monkeypatch.setattr(settings, "camera_role_pastor_camera", 1)
    monkeypatch.setattr(settings, "camera_role_pastor_preset", 3)
    return cameras, atem


async def test_disabled_does_not_stage(monkeypatch, wiring):
    cameras, atem = wiring
    monkeypatch.setattr(settings, "ptz_predictive_preview", False)
    staged = await PredictivePreview().stage_role("pastor")
    assert staged is False
    assert cameras.preset_calls == []
    assert atem.preview_calls == []


async def test_stage_role_moves_preset_and_sets_preview(monkeypatch, wiring):
    cameras, atem = wiring
    monkeypatch.setattr(settings, "ptz_predictive_preview", True)
    pp = PredictivePreview()
    staged = await pp.stage_role("pastor")
    assert staged is True
    assert cameras.preset_calls == [(1, 3)]
    assert atem.preview_calls  # preview was set to the mapped input
    assert pp.staged_role == "pastor"


async def test_stage_role_is_idempotent(monkeypatch, wiring):
    cameras, atem = wiring
    monkeypatch.setattr(settings, "ptz_predictive_preview", True)
    pp = PredictivePreview()
    await pp.stage_role("pastor")
    await pp.stage_role("pastor")
    assert cameras.preset_calls == [(1, 3)]  # not moved twice


async def test_unknown_role_is_noop(monkeypatch, wiring):
    cameras, atem = wiring
    monkeypatch.setattr(settings, "ptz_predictive_preview", True)
    pp = PredictivePreview()
    assert await pp.stage_role("ghost") is False
    assert cameras.preset_calls == []
