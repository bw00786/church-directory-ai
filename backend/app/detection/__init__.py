"""Detection package for future vision event detectors."""

from .person import PersonDetector
from .face import FaceDetector
from .pose import PoseDetector
from .speech import SpeechDetector
from .scene import SceneDetector

__all__ = [
    "PersonDetector",
    "FaceDetector",
    "PoseDetector",
    "SpeechDetector",
    "SceneDetector",
]
