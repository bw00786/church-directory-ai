"""Activity analysis for multimodal events."""

from typing import List

from .models import PersonTrack


def analyze_activity(tracks: List[PersonTrack]) -> dict[str, float]:
    if not tracks:
        return {
            "congregation_active": 0.0,
            "likely_speaker": 0.0,
            "subject_count": 0,
        }

    active_count = sum(1 for track in tracks if track.active)
    score = min(1.0, active_count / max(1.0, len(tracks)))
    return {
        "congregation_active": normalize_score(score * 0.75),
        "likely_speaker": normalize_score(max(track.confidence for track in tracks) * 0.9),
        "subject_count": active_count,
    }


def normalize_score(value: float) -> float:
    return max(0.0, min(1.0, value))
