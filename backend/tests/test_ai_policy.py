"""Tests for the AI Director's policy confidence gating (check_ai_decision)."""

import pytest

from app.policy.engine import PolicyEngine


@pytest.fixture
def policy():
    return PolicyEngine(
        min_ai_action_confidence=0.80,
        action_confidence_thresholds={
            "camera_change": 0.85,
            "slide_change": 0.85,
            "atem_transition": 0.90,
        },
    )


def test_camera_change_above_threshold_allowed(policy):
    allowed, reason = policy.check_ai_decision("camera_change", 0.9)
    assert allowed is True
    assert reason is None


def test_camera_change_below_threshold_rejected(policy):
    allowed, reason = policy.check_ai_decision("camera_change", 0.7)
    assert allowed is False
    assert "camera_change" in reason


def test_atem_transition_requires_higher_confidence(policy):
    allowed, _ = policy.check_ai_decision("atem_transition", 0.87)
    assert allowed is False
    allowed, _ = policy.check_ai_decision("atem_transition", 0.95)
    assert allowed is True


def test_unknown_category_falls_back_to_global_threshold(policy):
    allowed, _ = policy.check_ai_decision("unknown_category", 0.75)
    assert allowed is False
    allowed, _ = policy.check_ai_decision("unknown_category", 0.85)
    assert allowed is True


def test_human_actor_bypasses_threshold(policy):
    allowed, reason = policy.check_ai_decision("atem_transition", 0.0, actor="human")
    assert allowed is True
    assert reason is None
