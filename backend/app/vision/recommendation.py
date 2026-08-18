"""Deterministic camera recommendation engine with guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CameraRecommendation:
    recommended_camera: int | None
    score: float
    reason: str
    triggered: bool


class RecommendationEngine:
    def __init__(self, min_hold_time: float = 5.0, min_score_difference: float = 0.15, camera_cooldown: float = 5.0):
        self.min_hold_time = min_hold_time
        self.min_score_difference = min_score_difference
        self.camera_cooldown = camera_cooldown
        self.last_switch_time = 0.0

    def recommend(self, quality_scores: dict[int, float], current_program: int, reason_hint: str = "Best composition") -> CameraRecommendation:
        if not quality_scores:
            return CameraRecommendation(None, 0.0, "No camera quality data available", False)

        current_score = quality_scores.get(current_program, 0.0)
        candidate = max(quality_scores.items(), key=lambda item: item[1])
        candidate_camera, candidate_score = candidate

        if candidate_camera == current_program:
            return CameraRecommendation(current_program, candidate_score, "Current camera remains strongest candidate", False)

        diff = candidate_score - current_score
        if diff < self.min_score_difference:
            return CameraRecommendation(current_program, current_score, "Insufficient improvement to justify switching", False)

        # deterministic guardrails
        recommendation = CameraRecommendation(
            recommended_camera=candidate_camera,
            score=candidate_score,
            reason=f"{reason_hint}: camera {candidate_camera} provides superior framing and visibility",
            triggered=True,
        )
        return recommendation
