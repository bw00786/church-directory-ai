"""Composition and shot quality analysis."""

from .models import CameraQuality, PersonTrack, ShotClassification


def normalize_score(value: float) -> float:
    return max(0.0, min(1.0, value))


def classify_shot(subject_area_ratio: float) -> ShotClassification:
    if subject_area_ratio >= 0.35:
        return ShotClassification.EXTREME_CLOSE
    if subject_area_ratio >= 0.25:
        return ShotClassification.CLOSE
    if subject_area_ratio >= 0.18:
        return ShotClassification.MEDIUM_CLOSE
    if subject_area_ratio >= 0.12:
        return ShotClassification.MEDIUM
    if subject_area_ratio >= 0.08:
        return ShotClassification.MEDIUM_WIDE
    if subject_area_ratio >= 0.04:
        return ShotClassification.WIDE
    return ShotClassification.EXTREME_WIDE


def composition_metrics(track: PersonTrack, frame_width: int, frame_height: int) -> dict[str, float]:
    x, y, width, height = track.bbox
    subject_area = width * height
    frame_area = frame_width * frame_height
    subject_size = normalize_score(subject_area / max(1.0, frame_area))

    center_x = x + width / 2
    center_y = y + height / 2
    horizontal_center = 1.0 - abs(center_x / frame_width - 0.5) * 2.0
    vertical_center = 1.0 - abs(center_y / frame_height - 0.5) * 2.0
    centering = normalize_score((horizontal_center + vertical_center) / 2.0)

    ideal_headroom = 0.15 * frame_height
    headroom = normalize_score(1.0 - max(0.0, (y - ideal_headroom) / (frame_height * 0.5)))

    visibility = normalize_score(1.0 - max(0.0, (x < 0 or y < 0 or x + width > frame_width or y + height > frame_height)))

    return {
        "subject_size": subject_size,
        "centering": centering,
        "headroom": headroom,
        "visibility": visibility,
    }


def camera_quality_score(metrics: dict[str, float], weights: dict[str, float]) -> float:
    return normalize_score(
        metrics["subject_size"] * weights["size"]
        + metrics["centering"] * weights["centering"]
        + metrics["headroom"] * weights["headroom"]
        + metrics["visibility"] * weights["visibility"]
    )


def score_camera_quality(camera_id: int, tracks: list[PersonTrack], frame_width: int, frame_height: int, weights: dict[str, float]) -> CameraQuality:
    if not tracks:
        return CameraQuality(
            camera_id=camera_id,
            framing_score=0.0,
            subject_visibility=0.0,
            composition_score=0.0,
            stability_score=0.0,
            overall_score=0.0,
            shot=ShotClassification.EXTREME_WIDE,
            subject_count=0,
        )

    best_track = max(tracks, key=lambda track: track.confidence)
    metrics = composition_metrics(best_track, frame_width, frame_height)
    shot = classify_shot(metrics["subject_size"])
    overall = camera_quality_score(metrics, weights)
    stability = normalize_score(1.0 - min(1.0, best_track.velocity / 25.0))

    return CameraQuality(
        camera_id=camera_id,
        framing_score=metrics["subject_size"],
        subject_visibility=metrics["visibility"],
        composition_score=metrics["centering"],
        stability_score=stability,
        overall_score=overall,
        shot=shot,
        subject_count=len(tracks),
    )
