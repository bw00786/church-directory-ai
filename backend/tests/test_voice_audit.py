"""Audit round trips against in-memory SQLite, never the production database."""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.voice import audit as audit_module
from app.voice.models import AttentionInput, Priority, VoiceEvent, utcnow


@pytest.fixture
def audit(monkeypatch):
    engine = create_engine("sqlite:///:memory:")

    @contextmanager
    def session():
        with Session(engine) as value:
            yield value
            value.commit()

    monkeypatch.setattr(audit_module, "_engine", engine)
    monkeypatch.setattr(audit_module, "get_session", session)
    store = audit_module.VoiceAudit()
    store.initialize()
    try:
        yield store
    finally:
        engine.dispose()


def test_voice_audit_initialize_is_idempotent_and_empty_history(audit):
    audit.initialize()
    assert audit.recent() == []


def test_voice_audit_upsert_preserves_spoken_acknowledgement_and_feedback(audit):
    event = VoiceEvent(input=AttentionInput(event_type="camera_failure", hardware_failure=True),
                       priority=Priority.WARNING, message="Check the camera", mode="attention_only")
    audit.save(event)
    event.spoken = True
    event.spoken_at = utcnow()
    event.acknowledged = True
    event.acknowledged_at = utcnow()
    event.operator_action = "acknowledge"
    event.feedback = "useful"
    event.result = "spoken"
    audit.save(event)
    assert audit.recent() == [event]


def test_voice_audit_recent_orders_newest_first_and_honors_limit(audit):
    events = [VoiceEvent(input=AttentionInput(event_type="test"), priority=Priority.ATTENTION,
                         message="Test", mode="testing", timestamp=utcnow() + timedelta(seconds=i))
              for i in range(4)]
    for event in reversed(events):
        audit.save(event)
    assert audit.recent(limit=2) == list(reversed(events[-2:]))
    assert audit.recent(limit=0) == []