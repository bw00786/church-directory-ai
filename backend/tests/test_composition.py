from app.vision.composition import classify_shot
from app.vision.models import ShotClassification


def test_classify_shot_for_medium_close():
    shot = classify_shot(0.2)
    assert shot in [ShotClassification.MEDIUM_CLOSE, ShotClassification.MEDIUM]
