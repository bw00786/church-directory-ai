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


# -- detection cache per tick (WO-HARDEN) --------------------------------------
class CountingDetector:
    provider = "yolo"

    def __init__(self):
        self.calls = 0
        self.frames = []

    def detect(self, frame):
        self.calls += 1
        self.frames.append(frame)
        return [(0.5, 0.5, 0.2, 0.4)]


async def test_tick_runs_detector_once_per_frame_tag(monkeypatch):
    from app.config import settings
    from app.vision import perception as perc
    from app.vision.perception import PerceptionLoop

    frames = {"program": _bright_frame(), "camera_1": _bright_frame() + 1.0}

    def get_frame(tag):
        f = frames.get(tag)
        return f, {"tag": tag, "available": f is not None}

    monkeypatch.setattr(perc.frame_capture, "get_frame", get_frame)
    recorded = []
    monkeypatch.setattr(perc.service_context, "record_vision", lambda obs: recorded.append(obs))
    # Every role on camera 1 (the default production mapping).
    for role in ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide"):
        monkeypatch.setattr(settings, f"camera_role_{role}_camera", 1)

    loop = PerceptionLoop()
    loop._build_roles()
    assert len(loop._roles) == 6
    det = CountingDetector()
    loop.detector = det

    await loop.tick_once()

    # program + camera_1 = 2 detector runs, not 1 + 6.
    assert det.calls == 2
    assert loop.last_tick_detector_calls == 2
    assert len(recorded) == 7
    roles = {o.role for o in recorded if o.role}
    assert roles == {"pastor", "liturgist", "vocalist", "congregation", "choir", "wide"}
    assert all(o.person_present for o in recorded)
    assert all(o.camera_id == 1 for o in recorded if o.role)
    # Different ROIs still get their own projection of the shared detections.
    by_role = {o.role: o for o in recorded if o.role}
    assert by_role["pastor"].person_in_roi is True
    # pastor ROI centre (0.5, 0.525) vs congregation ROI centre (0.5, 0.55).
    assert by_role["congregation"].subject_dy != by_role["pastor"].subject_dy
    assert loop.status()["last_tick_detector_calls"] == 2


async def test_tick_skips_detector_for_missing_or_black_frames(monkeypatch):
    from app.config import settings
    from app.vision import perception as perc
    from app.vision.perception import PerceptionLoop

    frames = {"program": None, "camera_1": np.zeros((40, 40, 3), dtype=np.float32), "camera_2": _bright_frame()}
    monkeypatch.setattr(perc.frame_capture, "get_frame", lambda tag: (frames.get(tag), {}))
    recorded = []
    monkeypatch.setattr(perc.service_context, "record_vision", lambda obs: recorded.append(obs))
    monkeypatch.setattr(settings, "camera_role_pastor_camera", 1)
    monkeypatch.setattr(settings, "camera_role_wide_camera", 2)
    for role in ("liturgist", "vocalist", "congregation", "choir"):
        monkeypatch.setattr(settings, f"camera_role_{role}_camera", 1)

    loop = PerceptionLoop()
    loop._build_roles()
    det = CountingDetector()
    loop.detector = det
    await loop.tick_once()

    assert det.calls == 1  # only camera_2 had a usable frame
    healths = {(o.input): o.frame_health for o in recorded}
    assert healths["program"] == "no_frame"
    assert healths["camera_1"] == "black"
    assert healths["camera_2"] == "ok"


async def test_tick_detection_runs_off_the_event_loop(monkeypatch):
    import threading

    from app.vision import perception as perc
    from app.vision.perception import PerceptionLoop

    monkeypatch.setattr(perc.frame_capture, "get_frame", lambda tag: (_bright_frame(), {}))
    monkeypatch.setattr(perc.service_context, "record_vision", lambda obs: None)

    seen = []

    class ThreadDetector(CountingDetector):
        def detect(self, frame):
            seen.append(threading.current_thread())
            return super().detect(frame)

    loop = PerceptionLoop()
    loop._roles = []
    loop.detector = ThreadDetector()
    await loop.tick_once()
    assert seen and seen[0] is not threading.current_thread()
    assert seen[0].name.startswith("perception-detector")
