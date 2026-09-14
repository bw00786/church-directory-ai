"""Service state-machine tests with in-memory WAV synthesis and mocked hardware."""

import asyncio
import io
import wave
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.events.bus import EventBus
from app.voice import service as service_module
from app.voice.config import TTSSettings, VoiceSettings
from app.voice.events import from_bus
from app.voice.models import AttentionInput, AudioResult, Priority, utcnow
from app.voice.service import VoiceService


@pytest.fixture
def rig(monkeypatch):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00\xff\x7f\x00\x80" * 80)
    audio = AudioResult(data=buffer.getvalue())
    clock = [100.0]
    bus = EventBus()
    monkeypatch.setattr(service_module, "event_bus", bus)
    monkeypatch.setattr(service_module, "logger", Mock())
    monkeypatch.setattr(service_module, "TTSSettings", lambda: TTSSettings(_env_file=None, provider="disabled"))
    monkeypatch.setattr(service_module, "build_provider", Mock(side_effect=AssertionError("Unexpected real provider")))
    playback = SimpleNamespace(play=AsyncMock(), stop=Mock(), config=None)
    provider = SimpleNamespace(synthesize=AsyncMock(return_value=audio))
    service = VoiceService(
        config=VoiceSettings(_env_file=None, enabled=True, mode="attention_only",
                             output_device="USB Headphones", output_host_api="Core Audio",
                             routing_verified=True, aggregation_window_seconds=5,
                             cooldown_seconds=10, repeat_interval_seconds=120),
        provider=provider, playback=playback, now=lambda: clock[0],
        audit=SimpleNamespace(initialize=Mock(), save=Mock()),
    )
    return SimpleNamespace(service=service, provider=provider, playback=playback,
                           audio=audio, clock=clock, bus=bus)


def warning(resource="camera-1", **changes):
    return AttentionInput(**{"event_type": "camera_failure", "resource": resource,
                             "hardware_failure": True, "consequence": "high", **changes})


def test_voice_service_cooldown_repeat_interval_boundaries(rig):
    service = rig.service
    first = service.ingest(warning())
    assert first.result == "queued"
    assert service.status()["cooldowns"][service._key(first)] == 10
    for elapsed in (0, 9.999, 10, 119.999):
        rig.clock[0] = 100 + elapsed
        duplicate = service.ingest(warning())
        assert duplicate.result == "cooldown_suppressed"
        assert duplicate.cooldown_suppressed
    rig.clock[0] = 220
    assert service.ingest(warning()).result == "queued"
    assert service.metrics.snapshot()["alerts_suppressed_by_cooldown"] == 4


def test_voice_service_escalation_state_change_and_independent_keys_bypass_dedup(rig):
    service = rig.service
    service.ingest(warning())
    escalated = service.ingest(warning(critical=True))
    assert escalated.priority == Priority.CRITICAL and escalated.result == "queued"
    assert service.queue.items[-1][0] == rig.clock[0]
    assert service.ingest(warning()).cooldown_suppressed
    assert service.ingest(warning(state="disconnected")).result == "queued"
    assert service.ingest(warning(resource="camera-2")).result == "queued"
    assert service.ingest(warning(approval_token="different-approval")).result == "queued"


def test_voice_service_mute_drains_queue_stops_playback_and_blocks_new_critical(rig):
    first = rig.service.ingest(warning())
    rig.service.set_muted(True)
    assert first.result == "muted"
    assert rig.service.queue.items == []
    rig.playback.stop.assert_called_once()
    assert rig.service.ingest(warning(critical=True)).result == "muted"
    assert rig.service.status()["muted"] is True


def test_voice_service_unmute_requeues_recent_unresolved_warnings_observed_while_muted(rig):
    service = rig.service
    service.set_muted(True)
    service.ingest(warning())
    acknowledged = service.ingest(warning("acknowledged"))
    service.acknowledge(acknowledged.id, "acknowledge", None)
    resolved = service.ingest(warning("resolved"))
    resolved.resolved = True
    stale = service.ingest(warning("stale"))
    stale.timestamp = utcnow() - timedelta(seconds=61)
    service.set_muted(False)
    assert [e.input.resource for _, e in service.queue.items] == ["camera-1"]


def test_voice_service_resolution_removes_all_duplicates_and_resets_cooldown(rig):
    service = rig.service
    first = service.ingest(warning())
    duplicate = service.ingest(warning())
    resolution = service.ingest(warning(resolved=True))
    assert resolution.result == "resolved"
    assert first.resolved and duplicate.resolved
    assert service.queue.items == [] and not service.cooldowns
    assert service.ingest(warning()).result == "queued"


def test_voice_service_repeat_skips_acknowledged_resolved_and_stale_events(rig):
    service = rig.service
    relevant = service.ingest(warning("relevant"))
    stale = service.ingest(warning("stale"))
    stale.timestamp = utcnow() - timedelta(seconds=61)
    resolved = service.ingest(warning("resolved"))
    resolved.resolved = True
    acknowledged = service.ingest(warning("acknowledged"))
    service.acknowledge(acknowledged.id, "acknowledge", None)
    service.queue.clear()
    service.repeat()
    assert len(service.queue.items) == 1
    due, repeated = service.queue.items[0]
    assert due == rig.clock[0] and repeated.input == relevant.input
    assert repeated.id != relevant.id
    assert service.metrics.snapshot()["repeat_count"] == 1
    service.set_muted(True)
    service.repeat()
    assert not service.queue.items


def test_voice_service_repeat_empty_is_noop(rig):
    assert rig.service.repeat()["pending"] == []
    assert "repeat_count" not in rig.service.metrics.snapshot()


@pytest.mark.parametrize("name", ["UNKNOWN_EVENT", "AI_DECISION", "TRANSCRIPT", "director_status"])
def test_voice_service_suppresses_unknown_and_routine_bus_events(rig, name):
    observation = from_bus({"event": name, "payload": {"critical": True, "message": "Speak this!"}})
    event = rig.service.ingest(observation)
    assert event.result == "policy_suppressed"
    assert not rig.service.queue.items
    rig.provider.synthesize.assert_not_called()


def test_voice_service_update_is_atomic_on_invalid_config_and_drains_on_valid_change(rig):
    service = rig.service
    queued = service.ingest(warning())
    original = service.config
    with pytest.raises(ValidationError):
        service.update({"output_device": "ATEM Mini"})
    assert service.config is original and len(service.queue.items) == 1
    rig.playback.stop.assert_not_called()
    service.update({"mode": "emergency", "queue_limit": 2})
    assert service.config.mode == "emergency"
    assert service.queue.limit == 2
    assert rig.playback.config is service.config
    assert queued.result == "configuration_changed" and not service.queue.items


@pytest.mark.parametrize("changes", [{"enabled": False}, {"mode": "off"}, {"routing_verified": False}])
def test_voice_service_test_requires_enabled_and_verified_routing(rig, changes):
    rig.service.update(changes)
    with pytest.raises(ValueError):
        rig.service.test()
    assert not rig.service.queue.items


def test_voice_service_test_requires_unmuted_and_is_immediate(rig):
    rig.service.set_muted(True)
    with pytest.raises(ValueError):
        rig.service.test()
    rig.service.set_muted(False)
    status = rig.service.test()
    assert status["pending"][0]["input"]["event_type"] == "test"
    assert rig.service.queue.items[0][0] == rig.clock[0]


def test_voice_service_acknowledgement_is_idempotent_and_removes_pending(rig):
    event = rig.service.ingest(warning())
    for _ in range(2):
        result = rig.service.acknowledge(event.id, "dismiss", "not_useful")
        assert result.acknowledged and result.acknowledged_at is not None
    values = rig.service.metrics.snapshot()
    assert values["responses"] == values["dismissals"] == values["false_interruption_count"] == 1
    assert values["operator_dismiss_rate"] == 1
    assert values["average_attention_response_time"] >= 0
    assert not rig.service.queue.items
    with pytest.raises(KeyError):
        rig.service.acknowledge("missing", "acknowledge", None)


def test_voice_service_queue_history_cooldown_and_audit_are_bounded(rig):
    service = rig.service
    service.update({"queue_limit": 2})
    first = service.ingest(warning("0"))
    for i in range(1, 510):
        service.ingest(warning(str(i)))
    assert first.result == "queue_full"
    assert len(service.queue.items) == 2
    assert len(service.events) == len(service.cooldowns) == 500
    assert first.id not in service.events
    assert service._key(first) not in service.cooldowns
    assert service._audit_queue.qsize() == 1000
    assert service.metrics.snapshot()["audit_dropped"] > 0
    assert not service.audit_available


@pytest.mark.parametrize("critical,approval,expected_tone", [(False, None, False), (True, None, True), (False, "token", True)])
async def test_voice_service_success_decodes_real_wav_and_records_spoken(rig, critical, approval, expected_tone):
    event = rig.service.ingest(warning(critical=critical, approval_token=approval))
    rig.service.queue.clear()
    await rig.service.speak([event])
    rig.provider.synthesize.assert_awaited_once()
    rig.playback.play.assert_awaited_once_with(rig.audio, tone=expected_tone)
    assert event.spoken and event.spoken_at and event.result == "spoken"
    assert rig.service.last_alert is event
    assert rig.service.current is None and rig.service.speech_state == "idle"
    assert rig.service.error is None
    values = rig.service.metrics.snapshot()
    assert values["TTS_requests"] == values["voice_alert_count"] == 1


async def test_voice_service_aggregation_synthesizes_once_and_marks_each_event(rig):
    events = [rig.service.ingest(warning(str(i))) for i in range(3)]
    await rig.service.speak(events)
    rig.provider.synthesize.assert_awaited_once()
    assert "Several production issues" in rig.provider.synthesize.call_args.args[0]
    assert len({e.aggregation_id for e in events}) == 1 and events[0].aggregation_id
    assert all(e.spoken for e in events)
    assert rig.service.metrics.snapshot()["voice_alert_count"] == 3


@pytest.mark.parametrize("failure", ["exception", "empty", "malformed", "timeout", "playback"])
async def test_voice_service_provider_and_playback_failure_paths(rig, failure):
    event = rig.service.ingest(warning())
    if failure == "exception":
        rig.provider.synthesize.side_effect = RuntimeError("Synthetic provider failure")
    elif failure in ("empty", "malformed"):
        rig.provider.synthesize.return_value = AudioResult(data=b"" if failure == "empty" else b"not a WAV")
    elif failure == "timeout":
        # Shorten only this in-memory settings instance; no real network request.
        rig.service.tts_config = rig.service.tts_config.model_copy(update={"timeout_seconds": 0.01})
        cancelled = asyncio.Event()

        async def blocked(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        rig.provider.synthesize.side_effect = blocked
    else:
        rig.playback.play.side_effect = RuntimeError("Synthetic disconnected headset")
    await rig.service.speak([event])
    code = "VOICE_PLAYBACK_FAILURE" if failure == "playback" else "VOICE_TTS_FAILURE"
    assert event.result == rig.service.error == code
    assert not event.spoken and event.spoken_at is None
    assert rig.service.current is None and rig.service.speech_state == "idle"
    assert rig.service.last_alert is None
    counter = "playback_failure_count" if failure == "playback" else "TTS_failure_count"
    assert rig.service.metrics.snapshot()[counter] == 1
    if failure != "playback":
        rig.playback.play.assert_not_awaited()
    if failure == "timeout":
        assert cancelled.is_set()


async def test_voice_service_success_clears_previous_failure(rig):
    event = rig.service.ingest(warning())
    rig.provider.synthesize.side_effect = ValueError("synthetic error")
    await rig.service.speak([event])
    rig.provider.synthesize.side_effect = None
    await rig.service.speak([event])
    assert rig.service.error is None and event.spoken


@pytest.mark.parametrize("reason", ["resolved", "acknowledged", "stale", "muted", "disabled"])
async def test_voice_service_rechecks_eligibility_before_synthesizing(rig, reason):
    event = rig.service.ingest(warning())
    if reason in ("resolved", "acknowledged"):
        setattr(event, reason, True)
    elif reason == "stale":
        event.timestamp = utcnow() - timedelta(seconds=61)
    elif reason == "muted":
        rig.service.set_muted(True)
    else:
        rig.service.update({"enabled": False})
    await rig.service.speak([event])
    rig.provider.synthesize.assert_not_awaited()
    rig.playback.play.assert_not_awaited()


@pytest.mark.parametrize("stage", ["synthesizing", "playing"])
@pytest.mark.parametrize("interruption", ["mute", "critical", "configuration"])
async def test_voice_service_interrupts_inflight_speech(rig, stage, interruption):
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    target = rig.provider.synthesize if stage == "synthesizing" else rig.playback.play
    target.side_effect = blocked
    event = rig.service.ingest(warning())
    rig.service.queue.clear()
    task = asyncio.create_task(rig.service.speak([event]))
    rig.service._speech = task
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert rig.service.speech_state == stage
        if interruption == "mute":
            rig.service.set_muted(True)
        elif interruption == "configuration":
            rig.service.update({"mode": "off"})
        else:
            critical = rig.service.ingest(warning("stream", critical=True))
            assert critical.result == "queued"
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert event.result == "interrupted" and not event.spoken
        assert rig.service.current is None and rig.service.speech_state == "idle"
        rig.playback.stop.assert_called()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_voice_service_equal_priority_does_not_interrupt(rig):
    rig.service.current = rig.service.ingest(warning())
    rig.service.ingest(warning("camera-2"))
    rig.playback.stop.assert_not_called()


async def test_voice_service_mute_during_provider_completion_prevents_playback(rig):
    async def synthesize(*args):
        rig.service.set_muted(True)
        return rig.audio

    rig.provider.synthesize.side_effect = synthesize
    event = rig.service.ingest(warning())
    with pytest.raises(asyncio.CancelledError):
        await rig.service.speak([event])
    assert event.result == "interrupted"
    rig.playback.play.assert_not_awaited()


async def test_voice_service_publishes_observation_and_acknowledgement(rig):
    subscription = await rig.bus.subscribe(maxsize=10)
    event = rig.service.ingest(warning())
    message = await asyncio.wait_for(subscription.get(), 1)
    assert message["type"] == "voice_attention"
    assert message["event"]["id"] == event.id
    assert message["status"]["pending"][0]["id"] == event.id
    rig.service.acknowledge(event.id, "acknowledge", "useful")
    message = await asyncio.wait_for(subscription.get(), 1)
    assert message["type"] == "voice_acknowledgement"
    assert message["event"]["acknowledged"]
    await rig.bus.unsubscribe(subscription)


async def test_voice_service_lifecycle_is_idempotent_and_worker_processes_bus_event(rig, monkeypatch):
    spoken = asyncio.Event()

    async def play(*args, **kwargs):
        spoken.set()

    rig.playback.play.side_effect = play
    service = rig.service
    # Isolate lifecycle from the self-notification feedback regression below.
    # Real publish envelopes are covered separately, without an unbounded listener.
    monkeypatch.setattr(service, "publish", Mock())
    try:
        await service.start()
        tasks = list(service._tasks)
        await service.start()
        assert service._tasks == tasks and len(rig.bus.subscribers) == 1
        rig.bus.publish({"event": "STREAM_FAILURE"})
        await asyncio.wait_for(spoken.wait(), 2)
        await asyncio.wait_for(service._audit_queue.join(), 2)
        assert service.audit_available
        service.audit.initialize.assert_called_once()
        assert service.audit.save.call_count >= 2
        assert service.last_alert.spoken
    finally:
        await service.stop()
    assert not rig.bus.subscribers and not service._tasks
    assert all(t.done() for t in tasks)
    await service.stop()


async def test_voice_service_own_attention_envelope_must_not_reenter_listener(rig):
    subscription = await rig.bus.subscribe()
    rig.service.ingest(warning())
    envelope = subscription.get_nowait()
    assert envelope["type"] == "voice_attention"
    assert isinstance(envelope["event"], dict)
    # A non-None observation here causes _listen -> ingest -> publish -> _listen
    # to run forever without yielding. Keep this regression deliberately bounded.
    assert from_bus(envelope) is None


def test_voice_service_unmute_restores_warning_muted_before_first_playback(rig):
    rig.service.ingest(warning())
    rig.service.set_muted(True)
    assert not rig.service.queue.items
    rig.service.set_muted(False)
    assert len(rig.service.queue.items) == 1


@pytest.mark.parametrize("action", ["acknowledge", "resolve"])
async def test_voice_service_rechecks_event_relevance_after_synthesis(rig, action):
    event = rig.service.ingest(warning())

    async def synthesize(*args):
        if action == "acknowledge":
            rig.service.acknowledge(event.id, "acknowledge", None)
        else:
            rig.service.ingest(warning(resolved=True))
        return rig.audio

    rig.provider.synthesize.side_effect = synthesize
    await rig.service.speak([event])
    rig.playback.play.assert_not_awaited()
    assert not event.spoken