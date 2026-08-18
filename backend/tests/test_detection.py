def test_person_detector_is_importable():
    from app.detection.person import PersonDetector
    assert PersonDetector is not None
