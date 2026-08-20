"""Lightweight liveness / anti-spoofing heuristics.

Two independent signals are combined into a liveness score in [0, 1]:

1. Texture ("printed photo / screen replay" detector) -- real skin shows
   more high-frequency micro-texture than a printed photo or a screen
   re-captured by a camera, measured via Laplacian-of-Gaussian variance.
2. Temporal micro-motion ("frozen image" detector) -- a real face captured
   across a short window of frames shows small natural movement (blinks,
   breathing, tiny head motion); a rigidly held photo does not.

Honest limitation: neither signal defeats a *video* replay of the real
person (a phone/tablet playing back real footage would pass both checks).
This raises the bar against the common "hold up a printed photo" attack; it
is a heuristic, not a certified/commercial anti-spoofing system.
"""

from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore

# Empirical scaling constants (not learned): real close-up face crops
# typically produce Laplacian variance > ~100 and frame-to-frame mean
# absolute difference in the ~0.005-0.05 band; flat prints/screens read
# noticeably lower on both.
_TEXTURE_SCALE = 150.0
_MOTION_FLOOR = 0.003
_MOTION_SCALE = 0.04


def texture_score(gray_crop: np.ndarray) -> float:
    """Higher = more natural high-frequency texture; lower = suspiciously
    flat/smooth (printed photo, low-detail screen capture)."""
    if cv2 is None or gray_crop.size == 0:
        return 0.5
    variance = float(cv2.Laplacian(gray_crop, cv2.CV_64F).var())
    return float(np.clip(variance / _TEXTURE_SCALE, 0.0, 1.0))


def motion_score(crop_history: list[np.ndarray]) -> float:
    """Higher = natural micro-motion across recent frames; ~0 = frozen image.

    Returns 0.5 (neutral -- neither flags nor clears) when there isn't yet
    enough history to judge, so a brand-new track isn't wrongly rejected.
    """
    if len(crop_history) < 2:
        return 0.5
    diffs = []
    for a, b in zip(crop_history, crop_history[1:]):
        if a.shape != b.shape:
            continue
        diffs.append(float(np.mean(np.abs(a.astype(float) - b.astype(float))) / 255.0))
    if not diffs:
        return 0.5
    avg_diff = float(np.mean(diffs))
    if avg_diff < _MOTION_FLOOR:
        return 0.0
    return float(np.clip(avg_diff / _MOTION_SCALE, 0.0, 1.0))


def assess_liveness(current_gray_crop: np.ndarray, prior_gray_crops: list[np.ndarray]) -> tuple[float, dict]:
    """Combined liveness score plus a breakdown for logging/debugging."""
    texture = texture_score(current_gray_crop)
    motion = motion_score(prior_gray_crops + [current_gray_crop])
    score = 0.5 * texture + 0.5 * motion
    return score, {"texture_score": texture, "motion_score": motion}
