"""Camera failure producers preserve driver results and resource IDs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.cameras import service as cameras
from app.events.bus import EventBus


@pytest.fixture
async def rig(monkeypatch):
    bus = EventBus()
    monkeypatch.setattr(cameras, "event_bus", bus)
    return cameras.CameraService(), await bus.subscribe()


@pytest.mark.parametrize("raises", [False, True])
async def test_camera_connect_failure(rig, monkeypatch, raises):
    service, queue = rig
    service.register_camera(7, "camera.local")
    connect = AsyncMock(return_value=False, side_effect=OSError("private") if raises else None)
    monkeypatch.setattr(cameras, "PTZOpticsDriver", lambda **kwargs: SimpleNamespace(connect=connect))
    if raises:
        with pytest.raises(OSError):
            await service.connect_camera(7)
    else:
        assert not await service.connect_camera(7)
    assert queue.get_nowait() == {"event": "CAMERA_CONNECTION_FAILED", "payload": {"camera_id": 7}}
    assert queue.empty()


@pytest.mark.parametrize("method,args,driver_method,action", [
    ("move_to_preset", (7, 3), "move_to_preset", "camera_move_to_preset"),
    ("move_camera", (7,), "move_absolute", "camera_move_absolute"),
    ("drive_camera", (7,), "drive", "camera_drive"),
    ("stop_camera", (7,), "stop", "camera_stop"),
])
@pytest.mark.parametrize("result", ["false", "exception", "success", "missing"])
async def test_camera_move_outcomes(rig, method, args, driver_method, action, result):
    service, queue = rig
    command = AsyncMock(return_value=result == "success")
    if result == "exception":
        command.side_effect = OSError("private")
    if result != "missing":
        service._drivers[7] = SimpleNamespace(**{driver_method: command})
    if result == "exception":
        with pytest.raises(OSError):
            await getattr(service, method)(*args)
    else:
        assert await getattr(service, method)(*args) is (result == "success")
    if result == "missing":
        assert queue.get_nowait() == {"event": "CAMERA_CONNECTION_FAILED", "payload": {"camera_id": 7}}
    elif result != "success":
        assert queue.get_nowait() == {
            "event": "TOOL_EXECUTION_FAILED", "payload": {"camera_id": 7, "action": action},
        }
    assert queue.empty()