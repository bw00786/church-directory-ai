"""Tests for the local perception layer (WO-VISION-1 FR-2)."""

import numpy as np
import pytest

from app.domain.observations import VisionObservation
from app.vision.perception import RoiError, analyze, frame_health, parse_roi, roi_for_role


class FakeDetector:
    provider = "yolo"

    def __init__(self, detections):
        self._d = detections

    def detect(self, frame):
        return self._d


def _bright_frame():
    gray = np.tile(np.arange(100, dtype=np.float32), (100, 1))  # mean ~49.5
    return np.stack([gray, gray, gray], axis=2)


def test_parse_roi_valid():
    assert parse_roi("0.3,0.15,0.4,0.75", "X") == (0.3, 0.15, 0.4, 0.75)


@pytest.mark.parametrize("spec", ["0.9,0.1,0.5,0.5", "1.2,0,0.1,0.1", "0,0,0,0.5", "0.1,0.1,0.5"])
def test_parse_roi_out_of_bounds_refuses(spec):
    with pytest.raises(RoiError):
        parse_roi(spec, "X")


def test_roi_for_role_reads_config():
    assert roi_for_role("pastor") == (0.30, 0.15, 0.40, 0.75)


def test_frame_health_black_ok_frozen_noframe():
    assert frame_health(None, None)[0] == "no_frame"
    black = np.zeros((50, 50, 3), dtype=np.float32)
    assert frame_health(black, None)[0] == "black"
    frame = _bright_frame()
    health, h1 = frame_health(frame, None)
    assert health == "ok"
    assert frame_health(frame, h1)[0] == "frozen"


def test_analyze_person_in_roi_and_offset():
    det = FakeDetector([(0.5, 0.5, 0.2, 0.4)])  # centre inside the pastor ROI
    obs, _ = analyze(_bright_frame(), roi_for_role("pastor"), det, None)
    assert isinstance(obs, VisionObservation)
    assert obs.person_present and obs.person_in_roi
    assert abs(obs.subject_dx) < 0.01
    assert obs.offset_magnitude < 0.25


def test_analyze_person_out_of_roi():
    det = FakeDetector([(0.05, 0.05, 0.1, 0.1)])  # top-left, outside ROI
    obs, _ = analyze(_bright_frame(), roi_for_role("pastor"), det, None)
    assert obs.person_present and not obs.person_in_roi


def test_analyze_black_frame_skips_detection():
    det = FakeDetector([(0.5, 0.5, 0.2, 0.4)])
    obs, _ = analyze(np.zeros((50, 50, 3), dtype=np.float32), roi_for_role("pastor"), det, None)
    assert obs.frame_health == "black"
    assert obs.person_present is False


def test_analyze_no_detector_is_health_only():
    class NoneDet:
        provider = "none"

        def detect(self, frame):
            return []

    obs, _ = analyze(_bright_frame(), roi_for_role("pastor"), NoneDet(), None)
    assert obs.frame_health == "ok"
    assert obs.person_present is False
