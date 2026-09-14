"""Isolated voice HTTP/ASGI WebSocket contracts; no application lifespan or devices."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api import voice as voice_api
from app.events.bus import EventBus
from app.voice import service as service_module
from app.voice.config import TTSSettings, VoiceSettings
from app.voice.models import AttentionInput, Priority, utcnow
from app.voice.service import VoiceService


@pytest.fixture
def rig(monkeypatch):
    # Do not let a developer's .env or exported routing/provider settings leak in.
    config = VoiceSettings.model_validate({
        "enabled": False, "mode": "attention_only", "operator_name": "",
        "output_device": "USB Headphones", "output_host_api": "Core Audio",
        "routing_verified": True, "headset_only": True, "church_pa": False,
        "livestream": False, "recording": False, "cooldown_seconds": 10,
        "repeat_interval_seconds": 120, "aggregation_window_seconds": 5,
        "max_event_age_seconds": 60, "queue_limit": 100, "persona": {},
    })
    monkeypatch.setattr(service_module, "TTSSettings", lambda: TTSSettings.model_validate({
        "provider": "disabled", "piper_model_dir": "data/piper-voices",
        "piper_voice": "en_US-ljspeech-high", "timeout_seconds": 30,
    }))
    monkeypatch.setattr(service_module, "logger", Mock())
    provider_factory = Mock(side_effect=AssertionError("Real TTS provider forbidden"))
    playback_factory = Mock(side_effect=AssertionError("Real playback forbidden"))
    monkeypatch.setattr(service_module, "build_provider", provider_factory)
    monkeypatch.setattr(service_module, "HeadsetPlayback", playback_factory)
    provider = SimpleNamespace(synthesize=AsyncMock())
    playback = SimpleNamespace(play=AsyncMock(), stop=Mock(), config=config)
    audit = SimpleNamespace(recent=Mock(return_value=[]), initialize=Mock(), save=Mock())
    bus = EventBus()
    monkeypatch.setattr(voice_api, "event_bus", bus)
    monkeypatch.setattr(service_module, "event_bus", bus)
    service = VoiceService(config=config, provider=provider, playback=playback,
                           audit=audit, now=lambda: 100.0)
    getter = Mock(return_value=service)
    app = FastAPI()
    app.include_router(voice_api.router)
    app.add_api_websocket_route("/ws/voice", voice_api.voice_socket)
    # Depends captured the original callable; the WebSocket looks it up directly.
    app.dependency_overrides[voice_api.get_voice_service] = lambda: service
    monkeypatch.setattr(voice_api, "get_voice_service", getter)
    yield SimpleNamespace(app=app, service=service, bus=bus, audit=audit,
                          provider=provider, playback=playback, getter=getter)
    assert not bus.subscribers
    assert not service._tasks
    provider.synthesize.assert_not_called()
    playback.play.assert_not_called()
    provider_factory.assert_not_called()
    playback_factory.assert_not_called()
    audit.initialize.assert_not_called()
    audit.save.assert_not_called()


@pytest.fixture
def client(rig):
    with TestClient(rig.app) as client:
        yield client


def warning(rig, resource="camera-1", **changes):
    return rig.service.ingest(AttentionInput(**{
        "event_type": "camera_failure", "resource": resource,
        "hardware_failure": True, "consequence": "high", **changes,
    }))


def get_json(client, path):
    response = client.get(f"/api/voice/{path}")
    assert response.status_code == 200, response.text
    return response.json()


def post_json(client, path, **kwargs):
    response = client.post(f"/api/voice/{path}", **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def test_voice_api_initial_get_endpoints(client, rig):
    status = get_json(client, "status")
    assert status == rig.service.status()
    assert status["enabled"] is False and status["muted"] is False
    assert status["speech_state"] == "idle"
    assert status["current"] is None and status["last_alert"] is None
    assert status["headset"] == {
        "ready": True, "device": "USB Headphones", "verified": True, "error": None,
    }
    assert get_json(client, "config") == rig.service.config.model_dump()
    assert get_json(client, "queue") == {"pending": []}
    assert get_json(client, "events") == {"events": []}
    rig.audit.recent.assert_called_once_with(100)
    assert get_json(client, "metrics") == {
        "operator_acknowledgement_rate": 0,
        "operator_dismiss_rate": 0,
        "average_attention_response_time": 0,
    }
    rig.getter.assert_not_called()  # HTTP really uses the dependency override.


def test_voice_api_config_updates_allowed_fields_and_drains_queue(client, rig):
    post_json(client, "enable")
    event = warning(rig)
    patch = {
        "mode": "testing", "operator_name": "Alex", "cooldown_seconds": 20,
        "repeat_interval_seconds": 180, "aggregation_window_seconds": 1,
        "persona": {"voice_id": "test-voice", "speaking_rate": 1.05, "expressiveness": 0.5},
    }
    result = post_json(client, "config", json=patch)
    for key in patch.keys() - {"persona"}:
        assert result[key] == patch[key]
    assert result["persona"] == rig.service.config.persona.model_dump()
    assert result["persona"]["voice_id"] == "test-voice"
    assert result["persona"]["speaking_rate"] == 1.05
    assert result["persona"]["expressiveness"] == 0.5
    assert result["output_device"] == "USB Headphones"
    assert result["enabled"] is True
    assert event.result == "configuration_changed"
    assert get_json(client, "queue") == {"pending": []}
    assert rig.playback.config is rig.service.config
    assert get_json(client, "config") == result


@pytest.mark.parametrize("patch", [{}, {"operator_name": None, "persona": None}])
def test_voice_api_empty_or_null_config_patch_preserves_values(client, rig, patch):
    original = rig.service.config.model_dump()
    assert post_json(client, "config", json=patch) == original


@pytest.mark.parametrize("patch", [
    {"mode": "automatic"}, {"operator_name": "x" * 61},
    {"cooldown_seconds": 0}, {"cooldown_seconds": 601},
    {"repeat_interval_seconds": 9}, {"repeat_interval_seconds": 3601},
    {"aggregation_window_seconds": -1}, {"aggregation_window_seconds": 11},
    {"cooldown_seconds": "not-a-number"}, {"unknown": True},
    {"persona": {"gender": "male"}}, {"persona": {"speaking_rate": 0.5}},
    {"persona": {"expressiveness": 2}}, {"persona": {"voice_id": "x" * 151}},
    {"persona": "invalid"}, [], "invalid",
])
def test_voice_api_malformed_config_is_422_and_atomic(client, rig, patch):
    original = rig.service.config
    event = warning(rig)
    response = client.post("/api/voice/config", json=patch)
    assert response.status_code == 422
    assert "detail" in response.json()
    assert rig.service.config is original
    assert rig.service.events[event.id] is event
    rig.playback.stop.assert_not_called()


def test_voice_api_invalid_json_is_422(client, rig):
    response = client.post("/api/voice/config", content='{"mode":',
                           headers={"content-type": "application/json"})
    assert response.status_code == 422
    rig.playback.stop.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("enabled", True), ("output_device", "USB Headphones"),
    ("output_host_api", "Core Audio"), ("routing_verified", True),
    ("headset_only", False), ("church_pa", True), ("livestream", True),
    ("recording", True), ("queue_limit", 2), ("max_event_age_seconds", 30),
])
def test_voice_api_routing_and_noneditable_fields_are_forbidden(client, rig, field, value):
    original = rig.service.config.model_dump()
    response = client.post("/api/voice/config", json={field: value})
    assert response.status_code == 422
    assert any(e["loc"] == ["body", field] and e["type"] == "extra_forbidden"
               for e in response.json()["detail"])
    assert rig.service.config.model_dump() == original
    rig.playback.stop.assert_not_called()


@pytest.mark.parametrize("patch", [
    {"cooldown_seconds": 121},
    {"cooldown_seconds": 100, "repeat_interval_seconds": 99},
])
def test_voice_api_cross_field_config_validation_returns_422_without_mutation(client, rig, patch):
    post_json(client, "enable")
    event = warning(rig)
    original = rig.service.config
    rig.playback.stop.reset_mock()
    response = client.post("/api/voice/config", json=patch)
    assert response.status_code == 422
    assert response.json() == {
        "detail": "Invalid voice configuration; check cooldown and repeat intervals",
    }
    assert rig.service.config is original
    assert get_json(client, "queue")["pending"][0]["id"] == event.id
    assert event.result == "queued"
    rig.playback.stop.assert_not_called()


@pytest.mark.parametrize("mode,expected", [
    ("off", "attention_only"), ("attention_only", "attention_only"),
    ("testing", "testing"), ("emergency", "emergency"),
])
def test_voice_api_enable_sets_enabled_and_preserves_active_mode(client, mode, expected):
    post_json(client, "config", json={"mode": mode})
    for _ in range(2):
        status = post_json(client, "enable")
        assert status["enabled"] is True and status["mode"] == expected


def test_voice_api_enable_mute_unmute_disable_lifecycle(client, rig):
    assert post_json(client, "enable")["enabled"] is True
    event = warning(rig)
    assert get_json(client, "queue")["pending"][0]["id"] == event.id
    rig.playback.stop.reset_mock()
    muted = post_json(client, "mute")
    assert muted["muted"] is True and muted["pending"] == []
    assert event.result == "muted"
    rig.playback.stop.assert_called_once()
    assert post_json(client, "mute")["pending"] == []
    unmuted = post_json(client, "unmute")
    assert unmuted["muted"] is False
    assert len(unmuted["pending"]) == 1
    repeated = unmuted["pending"][0]
    assert repeated["id"] != event.id and repeated["input"] == event.input.model_dump()
    for _ in range(2):
        disabled = post_json(client, "disable")
        assert disabled["enabled"] is False and disabled["pending"] == []
    assert rig.service.events[repeated["id"]].result == "configuration_changed"
    assert get_json(client, "status") == disabled


@pytest.mark.parametrize("condition,detail", [
    ("disabled", "Enable and unmute voice before testing"),
    ("off", "Enable and unmute voice before testing"),
    ("muted", "Enable and unmute voice before testing"),
    ("routing", "Physical headset isolation must be verified before testing"),
])
def test_voice_api_test_conflicts_do_not_enqueue(client, rig, condition, detail):
    if condition != "disabled":
        post_json(client, "enable")
    if condition == "off":
        post_json(client, "config", json={"mode": "off"})
    elif condition == "muted":
        post_json(client, "mute")
    elif condition == "routing":
        rig.service.update({"routing_verified": False})
    response = client.post("/api/voice/test")
    assert response.status_code == 409
    assert response.json() == {"detail": detail}
    assert get_json(client, "queue") == {"pending": []}
    assert not rig.service.events


def test_voice_api_test_queues_immediate_alert_and_repeat_bypasses_cooldown(client, rig):
    post_json(client, "enable")
    tested = post_json(client, "test")
    first = tested["pending"][0]
    assert first["input"]["event_type"] == "test"
    assert first["priority"] == Priority.ATTENTION
    assert first["result"] == "queued" and first["spoken"] is False
    assert rig.service.queue.items[0][0] == 100.0
    repeated = post_json(client, "repeat")
    assert len(repeated["pending"]) == 2
    second = repeated["pending"][1]
    assert second["id"] != first["id"] and second["input"] == first["input"]
    assert second["cooldown_suppressed"] is False
    assert rig.service.queue.items[1][0] == 100.0
    assert repeated["metrics"]["repeat_count"] == 1


@pytest.mark.parametrize("reason", ["empty", "acknowledged", "resolved", "stale"])
def test_voice_api_repeat_without_relevant_alert_is_noop(client, rig, reason):
    post_json(client, "enable")
    if reason != "empty":
        event = warning(rig)
        if reason == "acknowledged":
            post_json(client, f"events/{event.id}/ack", json={})
        elif reason == "resolved":
            event.resolved = True
        else:
            event.timestamp = utcnow() - timedelta(seconds=61)
        rig.service.queue.clear()
    count = len(rig.service.events)
    result = post_json(client, "repeat")
    assert result["pending"] == [] and len(rig.service.events) == count
    assert result["metrics"].get("repeat_count", 0) == 0


@pytest.mark.parametrize("operation", ["mute", "disable"])
def test_voice_api_repeat_respects_mute_and_disable(client, rig, operation):
    post_json(client, "enable")
    warning(rig)
    post_json(client, operation)
    assert post_json(client, "repeat")["pending"] == []
    latest = list(rig.service.events.values())[-1]
    assert latest.result == ("muted" if operation == "mute" else "policy_suppressed")


@pytest.mark.parametrize("action", ["acknowledge", "dismiss"])
@pytest.mark.parametrize("feedback", [
    None, "useful", "not_useful", "too_sensitive", "too_late", "correct", "incorrect",
])
def test_voice_api_feedback_is_idempotent_and_never_executes_approval(
    client, rig, monkeypatch, action, feedback,
):
    # Guard the production execution boundary without importing device singletons.
    tools = ModuleType("app.agents.assistant_tools")
    execute = AsyncMock(side_effect=AssertionError("Acknowledgement is not approval"))
    pending = {"approval-token": {"action": "atem_stop_stream", "args": {}}}
    monkeypatch.setattr(tools, "execute_pending", execute, raising=False)
    monkeypatch.setattr(tools, "pending_actions", pending, raising=False)
    monkeypatch.setitem(sys.modules, "app.agents.assistant_tools", tools)
    post_json(client, "enable")
    event = warning(rig, approval_token="approval-token")
    for _ in range(2):
        result = post_json(client, f"events/{event.id}/ack",
                           json={"action": action, "feedback": feedback})
        assert result["id"] == event.id and result["acknowledged"] is True
        assert result["acknowledged_at"] is not None
        assert result["operator_action"] == action and result["feedback"] == feedback
        assert result["input"]["approval_token"] == "approval-token"
    assert get_json(client, "queue") == {"pending": []}
    metrics = get_json(client, "metrics")
    counter = "dismissals" if action == "dismiss" else "acknowledgements"
    assert metrics["responses"] == metrics[counter] == 1
    assert metrics.get("false_interruption_count", 0) == int(
        feedback in ("not_useful", "too_sensitive", "incorrect")
    )
    assert metrics["average_attention_response_time"] >= 0
    assert pending == {"approval-token": {"action": "atem_stop_stream", "args": {}}}
    execute.assert_not_called()


def test_voice_api_ack_defaults_and_unknown_or_persisted_only_id(client, rig):
    event = warning(rig)
    result = post_json(client, f"events/{event.id}/ack", json={})
    assert result["operator_action"] == "acknowledge" and result["feedback"] is None
    persisted = event.model_copy(update={"id": "persisted-only"})
    rig.audit.recent.return_value = [persisted]
    for event_id in ("missing", persisted.id):
        response = client.post(f"/api/voice/events/{event_id}/ack", json={})
        assert response.status_code == 404
        assert response.json() == {"detail": "Alert not in current session"}
    rig.audit.recent.assert_not_called()


@pytest.mark.parametrize("payload", [
    {"action": "execute"}, {"action": "approve"}, {"feedback": "great"},
    {"approval_token": "token"}, {"action": None}, [],
])
def test_voice_api_invalid_feedback_does_not_acknowledge(client, rig, payload):
    post_json(client, "enable")
    event = warning(rig)
    response = client.post(f"/api/voice/events/{event.id}/ack", json=payload)
    assert response.status_code == 422
    assert not event.acknowledged
    assert get_json(client, "queue")["pending"][0]["id"] == event.id
    assert get_json(client, "metrics").get("responses", 0) == 0


def test_voice_api_events_merge_sort_limit_and_prefer_current_session(client, rig):
    event = warning(rig)
    event.timestamp = utcnow() - timedelta(seconds=10)
    persisted = event.model_copy(deep=True)
    persisted.message = "Old persisted version"
    post_json(client, f"events/{event.id}/ack", json={"feedback": "useful"})
    newer = event.model_copy(update={"id": "newer", "timestamp": utcnow()})
    older = event.model_copy(update={
        "id": "older", "timestamp": event.timestamp - timedelta(seconds=10),
    })
    rig.audit.recent.return_value = [older, persisted, newer]
    rig.service.audit_available = True
    events = get_json(client, "events?limit=2")["events"]
    rig.audit.recent.assert_called_once_with(2)
    assert [e["id"] for e in events] == ["newer", event.id]
    assert events[1] == event.model_dump(mode="json")
    assert events[1]["acknowledged"] is True
    assert rig.service.audit_available is True
    assert [e["id"] for e in get_json(client, "events?limit=500")["events"]] == [
        "newer", event.id, "older",
    ]


@pytest.mark.parametrize("limit", ["0", "501", "-1", "abc", "1.5"])
def test_voice_api_invalid_event_limit_is_422(client, rig, limit):
    assert client.get(f"/api/voice/events?limit={limit}").status_code == 422
    rig.audit.recent.assert_not_called()


@pytest.mark.parametrize("failure", ["missing", "database", "timeout"])
def test_voice_api_events_fall_back_to_memory(client, rig, monkeypatch, failure):
    event = warning(rig)
    rig.service.audit_available = failure != "missing"
    if failure == "missing":
        rig.service.audit = None
    elif failure == "database":
        rig.audit.recent.side_effect = RuntimeError("Database unavailable")
    else:
        # Exercise the timeout handler without sleeping or opening a database.
        async def timed_out(awaitable, timeout):
            assert timeout == 2
            awaitable.close()
            raise TimeoutError("Audit query timed out")

        monkeypatch.setattr(voice_api, "asyncio", SimpleNamespace(
            wait_for=timed_out, to_thread=asyncio.to_thread,
        ))
    assert get_json(client, "events?limit=1") == {
        "events": [event.model_dump(mode="json")],
    }
    assert get_json(client, "status")["audit_available"] is False
    if failure == "database":
        rig.audit.recent.assert_called_once_with(1)
    else:
        rig.audit.recent.assert_not_called()


def test_voice_api_metrics_reflect_queue_suppression_and_operator_feedback(client, rig):
    post_json(client, "enable")
    first = warning(rig)
    warning(rig)  # Same resource/state is suppressed by cooldown.
    second = warning(rig, "camera-2")
    post_json(client, f"events/{first.id}/ack", json={"feedback": "useful"})
    post_json(client, f"events/{second.id}/ack",
              json={"action": "dismiss", "feedback": "incorrect"})
    values = get_json(client, "metrics")
    assert values["attention_events"] == 3
    assert values["alerts_suppressed_by_cooldown"] == 1
    assert values["responses"] == 2
    assert values["acknowledgements"] == values["dismissals"] == 1
    assert values["false_interruption_count"] == 1
    assert values["operator_acknowledgement_rate"] == pytest.approx(1 / 3)
    assert values["operator_dismiss_rate"] == pytest.approx(1 / 3)
    assert values["average_attention_response_time"] >= 0
    assert values == get_json(client, "status")["metrics"]


@asynccontextmanager
async def voice_connection(app):
    """Drive the actual ASGI route with bounded reads and deterministic disconnect."""
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    scope = {
        "type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.0"},
        "scheme": "ws", "path": "/ws/voice", "raw_path": b"/ws/voice",
        "query_string": b"", "root_path": "", "headers": [],
        "client": ("testclient", 50000), "server": ("testserver", 80),
        "subprotocols": [], "state": {},
    }
    incoming.put_nowait({"type": "websocket.connect"})
    task = asyncio.create_task(app(scope, incoming.get, outgoing.put))

    async def receive_json():
        message = await asyncio.wait_for(outgoing.get(), 2)
        assert message["type"] == "websocket.send"
        return json.loads(message["text"])

    try:
        accepted = await asyncio.wait_for(outgoing.get(), 2)
        assert accepted["type"] == "websocket.accept"
        yield SimpleNamespace(receive_json=receive_json, incoming=incoming, task=task)
    finally:
        incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        try:
            if not task.cancelled():
                await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_voice_api_websocket_initial_update_mute_queue_ack_and_disconnect(rig, monkeypatch):
    unsubscribe = AsyncMock(wraps=rig.bus.unsubscribe)
    monkeypatch.setattr(rig.bus, "unsubscribe", unsubscribe)
    async with voice_connection(rig.app) as socket, AsyncClient(
        transport=ASGITransport(app=rig.app), base_url="http://testserver",
    ) as client:
        initial = await socket.receive_json()
        assert initial == {"type": "voice_status", "status": rig.service.status()}
        rig.getter.assert_called_once_with()
        assert len(rig.bus.subscribers) == 1
        subscription = rig.bus.subscribers[0]
        assert subscription.maxsize == 100

        response = await client.post("/api/voice/enable")
        assert response.status_code == 200
        update = await socket.receive_json()
        assert update == {"type": "voice_status", "status": response.json()}
        assert update["status"]["enabled"] is True
        response = await client.post("/api/voice/config", json={"operator_name": "Alex"})
        assert response.status_code == 200 and response.json()["operator_name"] == "Alex"
        assert (await socket.receive_json())["type"] == "voice_status"
        event = warning(rig)
        attention = await socket.receive_json()
        assert attention["type"] == "voice_attention"
        assert attention["event"] == event.model_dump(mode="json")
        assert attention["status"]["pending"][0]["id"] == event.id

        response = await client.post("/api/voice/mute")
        assert response.status_code == 200
        muted = await socket.receive_json()
        assert muted == {"type": "voice_status", "status": response.json()}
        assert muted["status"]["muted"] is True and muted["status"]["pending"] == []
        rig.service.publish("voice_queue")
        queue = await socket.receive_json()
        assert queue == {"type": "voice_queue", "status": rig.service.status()}

        response = await client.post(f"/api/voice/events/{event.id}/ack",
                                     json={"feedback": "useful"})
        assert response.status_code == 200
        acknowledged = await socket.receive_json()
        assert acknowledged["type"] == "voice_acknowledgement"
        assert acknowledged["event"] == response.json()
        assert acknowledged["event"]["acknowledged"] is True
        assert acknowledged["event"]["feedback"] == "useful"
        assert acknowledged["status"]["metrics"]["acknowledgements"] == 1
        assert (await client.post("/api/voice/unmute")).status_code == 200
        assert (await socket.receive_json())["status"]["muted"] is False
    assert socket.task.done()
    unsubscribe.assert_awaited_once_with(subscription)
    assert not rig.bus.subscribers


async def test_voice_api_websocket_filters_unrelated_bus_messages_and_ignores_commands(rig):
    async with voice_connection(rig.app) as socket:
        await socket.receive_json()
        for message in (None, "voice_status", [], {"event": "CAMERA_CHANGED"},
                        {"type": "director_status"}, {"type": None}):
            rig.bus.publish(message)
        # This socket is a status stream, not a control/approval channel.
        socket.incoming.put_nowait({"type": "websocket.receive", "text": json.dumps({
            "action": "enable", "approval_token": "not-an-approval",
        })})
        rig.service.publish("voice_queue")
        received = await socket.receive_json()
        assert received == {"type": "voice_queue", "status": rig.service.status()}
        assert rig.service.config.enabled is False
        assert not rig.service.events


@pytest.mark.parametrize("failure", [WebSocketDisconnect, RuntimeError])
async def test_voice_api_websocket_sender_failure_unsubscribes(rig, monkeypatch, failure):
    unsubscribe = AsyncMock(wraps=rig.bus.unsubscribe)
    monkeypatch.setattr(rig.bus, "unsubscribe", unsubscribe)
    receive_cancelled = asyncio.Event()

    async def receive():
        try:
            await asyncio.Event().wait()
        finally:
            receive_cancelled.set()

    socket = SimpleNamespace(accept=AsyncMock(), receive=receive,
                             send_json=AsyncMock(side_effect=failure()))
    await asyncio.wait_for(voice_api.voice_socket(socket), 2)
    socket.accept.assert_awaited_once()
    assert receive_cancelled.is_set()
    unsubscribe.assert_awaited_once()
    assert not rig.bus.subscribers


async def test_voice_api_websocket_cancellation_unsubscribes_idle_connection(rig, monkeypatch):
    unsubscribe = AsyncMock(wraps=rig.bus.unsubscribe)
    monkeypatch.setattr(rig.bus, "unsubscribe", unsubscribe)
    async with voice_connection(rig.app) as socket:
        await socket.receive_json()
        subscription = rig.bus.subscribers[0]
        socket.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await socket.task
        unsubscribe.assert_awaited_once_with(subscription)
        assert not rig.bus.subscribers