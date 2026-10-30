"""DirectorDecision schema validation: closed vocabularies, bounded confidence,
per-action confidence fallback, readable rejection of unknown action types."""

import pytest
from pydantic import ValidationError

from app.ai.decision import (
    DirectorActionSpec,
    DirectorDecision,
    director_decision_json_schema,
    known_action_types,
)


def test_valid_decision_normalizes_case_and_fills_action_confidence():
    decision = DirectorDecision.model_validate({
        "decision": "Transition", "confidence": 0.9, "reason": "pastor took over",
        "service_state": "Sermon",
        "actions": [
            {"type": "ptz_select_role", "camera_role": "Pastor"},
            {"type": "ATEM_CUT", "confidence": 0.7, "reason": "cut on pause"},
        ],
    })
    assert decision.decision == "transition"
    assert decision.service_state == "sermon"
    first, second = decision.actions
    assert first.type == "PTZ_SELECT_ROLE" and first.camera_role == "pastor"
    assert first.confidence == 0.9 and first.reason == "pastor took over"  # decision-level fallback
    assert second.confidence == 0.7 and second.reason == "cut on pause"  # per-action values kept


@pytest.mark.parametrize("value", ["proceed", "", "stop", 3])
def test_unknown_decision_kind_rejected(value):
    with pytest.raises(ValidationError, match="decision"):
        DirectorDecision.model_validate({"decision": value, "confidence": 0.5})


@pytest.mark.parametrize("value", [-0.1, 1.01, 7])
def test_out_of_range_confidence_rejected(value):
    with pytest.raises(ValidationError, match="confidence"):
        DirectorDecision.model_validate({"decision": "continue", "confidence": value})
    with pytest.raises(ValidationError, match="confidence"):
        DirectorActionSpec.model_validate({"type": "ATEM_CUT", "confidence": value})


def test_unknown_action_type_rejected_with_clear_message():
    with pytest.raises(ValidationError) as excinfo:
        DirectorDecision.model_validate({
            "decision": "deviate", "confidence": 0.8,
            "actions": [{"type": "LAUNCH_CONFETTI"}],
        })
    message = str(excinfo.value)
    assert "unknown action type 'LAUNCH_CONFETTI'" in message
    assert "ATEM_CUT" in message and "PTZ_SELECT_ROLE" in message


def test_all_director_action_types_accepted():
    for action_type in known_action_types():
        spec = DirectorActionSpec.model_validate({"type": action_type})
        assert spec.type == action_type


@pytest.mark.parametrize("value,expected", [(None, None), ("null", None), ("", None), ("unchanged", None), ("Hymn", "hymn")])
def test_service_state_normalization(value, expected):
    decision = DirectorDecision(decision="continue", confidence=0.1, service_state=value)
    assert decision.service_state == expected


def test_runtime_fields_default_none_and_survive_dump():
    decision = DirectorDecision(decision="continue", confidence=0.0, reason="idle",
                                context_version=4, latency_ms=12, cycle_id="abc")
    dumped = decision.model_dump(mode="json")
    assert dumped["context_version"] == 4 and dumped["latency_ms"] == 12 and dumped["cycle_id"] == "abc"
    assert DirectorDecision().cycle_id is None


def test_json_schema_is_flat_and_matches_the_model():
    schema = director_decision_json_schema()
    assert "$defs" not in str(schema) and "$ref" not in str(schema)
    assert schema["properties"]["decision"]["enum"] == ["continue", "transition", "deviate"]
    assert set(schema["properties"]["actions"]["items"]["properties"]["type"]["enum"]) == set(known_action_types())
    assert schema["properties"]["actions"]["items"]["required"] == ["type", "confidence", "reason"]
