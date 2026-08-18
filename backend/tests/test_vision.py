import pytest

from app.vision.manager import VisionManager


@pytest.mark.asyncio
async def test_vision_manager_starts_and_reports_status():
    manager = VisionManager()
    status = manager.get_status()
    assert 'enabled' in status
    assert 'active' in status
    assert isinstance(status['cameras'], int)


@pytest.mark.asyncio
async def test_vision_manager_policy_decision_tracking():
    manager = VisionManager()
    assert manager.get_recommendations() == []
    assert manager.get_policy_decisions() == []
    assert hasattr(manager, 'policy_engine')
    assert hasattr(manager, 'get_policy_decisions')
