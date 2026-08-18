"""Test policy engine."""

import pytest

from app.policy.engine import PolicyEngine
from app.policy.permissions import Permission


@pytest.fixture
def policy():
    """Create a policy engine."""
    return PolicyEngine(
        autonomous_camera_switching=True,
        autonomous_transitions=True,
        autonomous_stream_start=False,
        autonomous_stream_stop=False,
        autonomous_recording=False,
    )


def test_check_permission_human(policy):
    """Test that humans can always perform actions."""
    assert policy.check_permission(Permission.SWITCH_CAMERA, actor="human") is True
    assert policy.check_permission(Permission.START_STREAM, actor="human") is True


def test_check_permission_ai_allowed(policy):
    """Test AI permissions that are allowed."""
    assert policy.check_permission(Permission.SWITCH_CAMERA, actor="ai") is True
    assert policy.check_permission(Permission.PERFORM_CUT, actor="ai") is True


def test_check_permission_ai_denied(policy):
    """Test AI permissions that are denied."""
    assert policy.check_permission(Permission.START_STREAM, actor="ai") is False
    assert policy.check_permission(Permission.STOP_STREAM, actor="ai") is False


def test_can_action_execute_confidence(policy):
    """Test action execution with confidence threshold."""
    # High confidence
    allowed, reason = policy.can_action_execute(
        "switch_camera",
        actor="ai",
        confidence=0.95,
    )
    assert allowed is True
    
    # Low confidence
    allowed, reason = policy.can_action_execute(
        "switch_camera",
        actor="ai",
        confidence=0.70,
    )
    assert allowed is False
    assert "below threshold" in reason


def test_record_action(policy):
    """Test recording actions."""
    policy.record_action("switch_camera", value=1)
    policy.record_action("switch_camera", value=2)
    
    # Should have history
    assert "switch_camera" in policy._action_history


def test_get_constraints(policy):
    """Test getting action constraints."""
    constraints = policy.get_constraints(Permission.SWITCH_CAMERA)
    
    assert constraints.permitted is True
    assert constraints.min_confidence == 0.85
    assert constraints.max_consecutive == 3
