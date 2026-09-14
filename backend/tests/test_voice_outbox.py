from unittest.mock import Mock

import pytest

from app.voice.models import AttentionInput, Priority, VoiceEvent
from app.voice.outbox import DurableVoiceAudit


def event():
    return VoiceEvent(input=AttentionInput(event_type="camera_failure"), priority=Priority.WARNING,
                      message="Check the camera", mode="attention_only")


def test_outbox_survives_database_outage_and_process_restart(tmp_path):
    remote = Mock()
    remote.initialize.side_effect = RuntimeError("offline")
    audit = DurableVoiceAudit(remote, str(tmp_path))
    value = event()
    with pytest.raises(RuntimeError):
        audit.save(value)
    assert not audit.remote_available
    assert len(list(tmp_path.glob("*.json"))) == 1
    recovered = Mock()
    replacement = DurableVoiceAudit(recovered, str(tmp_path))
    replacement.flush()
    recovered.save.assert_called_once_with(value)
    assert not list(tmp_path.glob("*.json"))
    assert replacement.remote_available


def test_outbox_keeps_feedback_update_during_backoff(tmp_path):
    remote = Mock()
    remote.initialize.side_effect = RuntimeError("offline")
    audit = DurableVoiceAudit(remote, str(tmp_path))
    value = event()
    with pytest.raises(RuntimeError):
        audit.save(value)
    value.feedback = "too_late"
    audit.save(value)
    remote.initialize.assert_called_once()
    stored = VoiceEvent.model_validate_json(next(tmp_path.glob("*.json")).read_text())
    assert stored.feedback == "too_late"


def test_outbox_capacity_fails_explicitly_without_losing_existing_record(tmp_path):
    remote = Mock()
    remote.initialize.side_effect = RuntimeError("offline")
    audit = DurableVoiceAudit(remote, str(tmp_path), limit=1)
    with pytest.raises(RuntimeError):
        audit.save(event())
    with pytest.raises(RuntimeError, match="outbox full"):
        audit.save(event())
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_outbox_history_remains_readable_offline(tmp_path):
    remote = Mock()
    remote.initialize.side_effect = RuntimeError("offline")
    remote.recent.side_effect = RuntimeError("offline")
    audit = DurableVoiceAudit(remote, str(tmp_path))
    value = event()
    with pytest.raises(RuntimeError):
        audit.save(value)
    assert audit.recent() == [value]