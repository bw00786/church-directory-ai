"""Tests for Phase 4 learning loop, adaptive confidence, evidence fusion."""

import pytest

from app.config import settings
from app.director.action_models import DirectorAction, DirectorActionType
from app.director.autonomy import AdaptiveConfidence, EvidenceFusion, LearningRecorder
from app.domain.observations import VisionObservation


def _camera_action(role="pastor", confidence=0.9):
    return DirectorAction(type=DirectorActionType.PTZ_SELECT_ROLE, target=role, confidence=confidence)


# -- AdaptiveConfidence --------------------------------------------------------
def test_adaptive_disabled_returns_base(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_adaptive_confidence", False)
    ac = AdaptiveConfidence()
    ac.record_outcome("camera_change", good=False)
    assert ac.threshold_for("camera_change", 0.85) == 0.85


def test_adaptive_rejection_tightens(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_adaptive_confidence", True)
    monkeypatch.setattr(settings, "ai_director_adaptive_step", 0.02)
    monkeypatch.setattr(settings, "ai_director_adaptive_min", 0.6)
    monkeypatch.setattr(settings, "ai_director_adaptive_max", 0.98)
    ac = AdaptiveConfidence()
    ac.record_outcome("camera_change", good=False)
    assert ac.threshold_for("camera_change", 0.85) == pytest.approx(0.87)


def test_adaptive_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_adaptive_confidence", True)
    monkeypatch.setattr(settings, "ai_director_adaptive_step", 0.1)
    monkeypatch.setattr(settings, "ai_director_adaptive_min", 0.6)
    monkeypatch.setattr(settings, "ai_director_adaptive_max", 0.9)
    ac = AdaptiveConfidence()
    for _ in range(10):
        ac.record_outcome("slide_change", good=False)
    assert ac.threshold_for("slide_change", 0.85) == 0.9  # capped at max


# -- EvidenceFusion ------------------------------------------------------------
def test_fusion_disabled_passes(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_evidence_fusion", False)
    ok, reason = EvidenceFusion().corroborates(_camera_action(), {})
    assert ok and reason is None


def test_fusion_missing_vision_does_not_veto(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_evidence_fusion", True)
    ok, reason = EvidenceFusion().corroborates(_camera_action(), {})
    assert ok is True


def test_fusion_vetoes_when_no_person(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_evidence_fusion", True)
    vision = {"pastor": VisionObservation(role="pastor", person_present=False)}
    ok, reason = EvidenceFusion().corroborates(_camera_action(), vision)
    assert ok is False
    assert "no person" in reason


def test_fusion_vetoes_on_black_frame(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_evidence_fusion", True)
    vision = {"pastor": VisionObservation(role="pastor", person_present=True, frame_health="black")}
    ok, reason = EvidenceFusion().corroborates(_camera_action(), vision)
    assert ok is False
    assert "black" in reason


def test_fusion_passes_with_person(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_evidence_fusion", True)
    vision = {"pastor": VisionObservation(role="pastor", person_present=True, frame_health="ok")}
    ok, reason = EvidenceFusion().corroborates(_camera_action(), vision)
    assert ok is True


def test_fusion_ignores_non_camera_actions(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_evidence_fusion", True)
    action = DirectorAction(type=DirectorActionType.EASYWORSHIP_NEXT, confidence=0.9)
    ok, reason = EvidenceFusion().corroborates(action, {})
    assert ok is True


# -- LearningRecorder ----------------------------------------------------------
async def test_learning_disabled_records_nothing(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_learning_enabled", False)
    recorded = []
    import app.memory.production_memory as pm

    monkeypatch.setattr(pm.memory_manager, "record_observation", lambda **kw: recorded.append(kw))
    await LearningRecorder().record_rejection(_camera_action())
    assert recorded == []


async def test_learning_records_rejection(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_learning_enabled", True)
    recorded = []
    import app.memory.production_memory as pm

    monkeypatch.setattr(pm.memory_manager, "record_observation", lambda **kw: recorded.append(kw))
    await LearningRecorder().record_rejection(_camera_action())
    assert len(recorded) == 1
    assert recorded[0]["category"] == "operator_override"
    assert "PTZ_SELECT_ROLE" in recorded[0]["text"]
