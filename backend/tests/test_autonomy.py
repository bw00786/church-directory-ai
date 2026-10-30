"""Tests for Phase 4 learning loop, adaptive confidence, evidence fusion."""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.director.action_models import DirectorAction, DirectorActionType
from app.director.autonomy import AdaptiveConfidence, EvidenceFusion, LearningRecorder
from app.domain.observations import VisionObservation


def _camera_action(role="pastor", confidence=0.9):
    return DirectorAction(type=DirectorActionType.PTZ_SELECT_ROLE, target=role, confidence=confidence)


@pytest.fixture
def adaptive_on(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ai_director_adaptive_confidence", True)
    monkeypatch.setattr(settings, "ai_director_adaptive_step", 0.02)
    monkeypatch.setattr(settings, "ai_director_adaptive_min", 0.6)
    monkeypatch.setattr(settings, "ai_director_adaptive_max", 0.98)
    path = tmp_path / "adaptive.json"
    monkeypatch.setitem(settings.__dict__, "ai_director_adaptive_file", str(path))
    return path


# -- AdaptiveConfidence --------------------------------------------------------
def test_adaptive_disabled_returns_base(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ai_director_adaptive_confidence", False)
    monkeypatch.setitem(settings.__dict__, "ai_director_adaptive_file", str(tmp_path / "a.json"))
    ac = AdaptiveConfidence()
    ac.record_feedback("camera_change", "sermon", good=False)
    assert ac.threshold_for("camera_change", 0.85) == 0.85
    assert not (tmp_path / "a.json").exists()


def test_adaptive_rejection_tightens(adaptive_on):
    ac = AdaptiveConfidence()
    ac.record_feedback("camera_change", "sermon", good=False)
    assert ac.threshold_for("camera_change", 0.85, "sermon") == pytest.approx(0.87)


def test_adaptive_is_bounded(monkeypatch, adaptive_on):
    monkeypatch.setattr(settings, "ai_director_adaptive_step", 0.1)
    monkeypatch.setattr(settings, "ai_director_adaptive_max", 0.9)
    ac = AdaptiveConfidence()
    for _ in range(10):
        ac.record_feedback("slide_change", None, good=False)
    assert ac.threshold_for("slide_change", 0.85) == 0.9  # capped at max


def test_adaptive_never_below_policy_base(adaptive_on):
    ac = AdaptiveConfidence()
    for _ in range(20):
        ac.record_feedback("camera_change", "sermon", good=True)
    assert ac.threshold_for("camera_change", 0.85, "sermon") == 0.85
    ac.record_feedback("camera_change", "sermon", good=False)
    ac.record_feedback("camera_change", "sermon", good=False)
    ac.record_feedback("camera_change", "sermon", good=True)
    assert ac.threshold_for("camera_change", 0.85, "sermon") == pytest.approx(0.87)


def test_adaptive_keyed_by_category_and_state(adaptive_on):
    ac = AdaptiveConfidence()
    ac.record_feedback("camera_change", "sermon", good=False)
    assert ac.threshold_for("camera_change", 0.85, "sermon") == pytest.approx(0.87)
    assert ac.threshold_for("camera_change", 0.85, "hymn") == 0.85
    assert ac.threshold_for("slide_change", 0.85, "sermon") == 0.85


def test_adaptive_persists_across_instances(adaptive_on):
    first = AdaptiveConfidence()
    first.record_feedback("atem_transition", "sermon", good=False)
    assert adaptive_on.exists()
    second = AdaptiveConfidence()
    assert second.threshold_for("atem_transition", 0.90, "sermon") == pytest.approx(0.92)
    status = second.status({"atem_transition": 0.90}, "sermon")
    assert status["enabled"] is True
    assert status["deltas"] == {"atem_transition|sermon": pytest.approx(0.02)}
    assert status["feedback"]["atem_transition|sermon"] == {"approved": 0, "rejected": 1}
    assert status["thresholds"]["atem_transition"]["effective"] == pytest.approx(0.92)


def test_adaptive_survives_corrupt_file(adaptive_on):
    adaptive_on.write_text("{not json")
    ac = AdaptiveConfidence()
    assert ac.threshold_for("camera_change", 0.85) == 0.85


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


def test_fusion_ignores_stale_observations(monkeypatch):
    monkeypatch.setattr(settings, "ai_director_evidence_fusion", True)
    monkeypatch.setitem(settings.__dict__, "ai_director_evidence_max_age_seconds", 3.0)
    old = datetime.now(timezone.utc) - timedelta(seconds=10)
    vision = {"pastor": VisionObservation(role="pastor", person_present=False, frame_health="black", timestamp=old)}
    ok, reason = EvidenceFusion().corroborates(_camera_action(), vision)
    assert ok is True and reason is None  # stale evidence is no evidence
    fresh = {"pastor": VisionObservation(role="pastor", person_present=False)}
    assert EvidenceFusion().corroborates(_camera_action(), fresh)[0] is False


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
