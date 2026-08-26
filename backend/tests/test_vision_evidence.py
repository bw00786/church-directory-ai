"""Tests for vision evidence wiring (WO-VISION-1 FR-5).

Skips gracefully when WO-CONF-1's evidence engine is absent.
"""

from app.domain.observations import VisionObservation
from app.domain.service_context import service_context
from app.vision.evidence import conf_available, vision_evidence_for_role, wire_into_conf


def _reset_vision():
    service_context.vision.clear()


def test_conf_absent_is_graceful():
    # WO-CONF-1 not landed in this environment.
    assert conf_available() is False
    assert wire_into_conf() is False


def test_person_in_roi_corroborates():
    _reset_vision()
    service_context.record_vision(
        VisionObservation(role="pastor", person_present=True, person_in_roi=True, frame_health="ok")
    )
    ev = vision_evidence_for_role("pastor")
    assert ev["corroboration"] == 1.0 and ev["veto"] is False


def test_black_target_vetoes():
    _reset_vision()
    service_context.record_vision(VisionObservation(role="pastor", frame_health="black"))
    ev = vision_evidence_for_role("pastor")
    assert ev["veto"] is True and ev["corroboration"] == 0.0


def test_black_program_vetoes_any_transition():
    _reset_vision()
    service_context.record_vision(VisionObservation(input="program", frame_health="black"))
    ev = vision_evidence_for_role("liturgist")
    assert ev["veto"] is True and ev["reason"] == "black_frame:program"


def test_no_signal_is_neutral():
    _reset_vision()
    ev = vision_evidence_for_role("vocalist")
    assert ev["corroboration"] == 0.0 and ev["veto"] is False
