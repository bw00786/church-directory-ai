"""Vision subsystem package.

Exports are resolved lazily (PEP 562) so importing a light submodule such as
``app.vision.perception`` doesn't pull in the DB-heavy ``manager`` chain.
"""

import importlib

__all__ = [
    "VisionSettings",
    "VisionManager",
    "VisionEventType",
    "AudioObservation",
    "MockAudioProvider",
    "CameraRecommendation",
    "RecommendationEngine",
]

_LAZY = {
    "VisionSettings": ".config",
    "VisionManager": ".manager",
    "VisionEventType": ".models",
    "AudioObservation": ".audio",
    "MockAudioProvider": ".audio",
    "CameraRecommendation": ".recommendation",
    "RecommendationEngine": ".recommendation",
}


def __getattr__(name):
    if name in _LAZY:
        module = importlib.import_module(_LAZY[name], __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
