"""MemoryEventRecorder: records off the event loop with structured fields."""

import asyncio
import threading

import pytest

from app.events.bus import event_bus
from app.memory import event_recorder as recorder_module
from app.memory.event_recorder import MemoryEventRecorder


@pytest.fixture
def recorded(monkeypatch):
    calls = []

    def _record(**kwargs):
        calls.append({"thread": threading.current_thread().name, **kwargs})
        return {"id": "x"}

    monkeypatch.setattr(recorder_module.memory_manager, "record_observation", _record)
    return calls


async def test_handle_runs_off_the_event_loop_thread(recorded):
    recorder = MemoryEventRecorder()
    await recorder.start()
    try:
        event_bus.publish({"type": "director_action", "data": {"action": "advance", "description": "Advance slide",
                                                               "detail": "to 3", "cue_index": 2}})
        for _ in range(50):
            if recorded:
                break
            await asyncio.sleep(0.01)
    finally:
        await recorder.stop()
    assert len(recorded) == 1
    assert recorded[0]["thread"] != threading.main_thread().name
    assert recorded[0]["category"] == "cue"
    assert recorded[0]["text"] == "Advance slide: to 3"
    assert recorded[0]["source"] == "cue_index_2"


def test_ai_decision_records_state_and_outcome(recorded):
    MemoryEventRecorder()._handle({"event": "AI_DECISION", "payload": {
        "decision": "transition", "confidence": 0.9, "reason": "song ended", "service_state": "sermon"}})
    assert recorded[0]["category"] == "ai_decision"
    assert recorded[0]["service_state"] == "sermon" and recorded[0]["outcome"] == "transition"
    assert "song ended" in recorded[0]["text"]


def test_continue_decisions_and_cue_errors_are_not_recorded(recorded):
    recorder = MemoryEventRecorder()
    recorder._handle({"event": "AI_DECISION", "payload": {"decision": "continue"}})
    recorder._handle({"type": "director_action", "data": {"action": "error"}})
    assert recorded == []


@pytest.mark.parametrize("event,status", [("AI_ACTION_APPROVED", "approved"), ("AI_ACTION_REJECTED", "rejected")])
def test_ai_action_records_structured_target(recorded, event, status):
    MemoryEventRecorder()._handle({"event": event, "payload": {
        "type": "PTZ_SELECT_ROLE", "target": "pastor", "confidence": 0.9, "reason": "speaking",
        "service_state": "sermon", "speaker_role": "pastor"}})
    entry = recorded[0]
    assert entry["category"] == "ai_action" and entry["outcome"] == status
    assert entry["camera_role"] == "pastor" and entry["easyworship_item"] is None
    assert entry["service_state"] == "sermon" and entry["speaker_role"] == "pastor"
    assert entry["metadata"] == {"action_type": "PTZ_SELECT_ROLE"}
    assert f"AI action {status}: PTZ_SELECT_ROLE -> pastor" in entry["text"]


def test_easyworship_action_records_item(recorded):
    MemoryEventRecorder()._handle({"event": "AI_ACTION_APPROVED", "payload": {
        "type": "EASYWORSHIP_SELECT", "target": "Song", "confidence": 0.8}})
    assert recorded[0]["easyworship_item"] == "Song" and recorded[0]["camera_role"] is None
