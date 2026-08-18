"""Person tracking for stable IDs and motion signals."""

from __future__ import annotations

from typing import List

from .models import DetectorObject, PersonTrack


def iou(box_a: List[int], box_b: List[int]) -> float:
    ax, ay, aw, ah = box_a
    bx, by, bw, bh = box_b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = aw * ah + bw * bh - intersection
    return float(intersection) / union if union > 0 else 0.0


class SimpleTracker:
    def __init__(self, max_age_seconds: float = 2.0, match_threshold: float = 0.3):
        self.max_age_seconds = max_age_seconds
        self.match_threshold = match_threshold
        self.next_id = 1
        self.tracks: list[PersonTrack] = []

    def update(self, detections: list[DetectorObject], timestamp: float, camera_id: int) -> list[PersonTrack]:
        assigned = []
        new_tracks: list[PersonTrack] = []
        existing = list(self.tracks)

        for detection in detections:
            best_track = None
            best_score = 0.0
            for track in existing:
                score = iou(track.bbox, detection.bbox)
                if score > best_score:
                    best_score = score
                    best_track = track
            if best_track and best_score >= self.match_threshold:
                existing.remove(best_track)
                velocity = self._compute_velocity(best_track, detection, timestamp)
                updated = PersonTrack(
                    person_id=best_track.person_id,
                    bbox=detection.bbox,
                    confidence=detection.confidence,
                    first_seen=best_track.first_seen,
                    last_seen=timestamp,
                    velocity=velocity,
                    position=self._estimate_position(detection.bbox, best_track.bbox),
                    camera_id=camera_id,
                    active=True,
                )
                assigned.append(updated)
            else:
                assigned.append(
                    PersonTrack(
                        person_id=self.next_id,
                        bbox=detection.bbox,
                        confidence=detection.confidence,
                        first_seen=timestamp,
                        last_seen=timestamp,
                        velocity=0.0,
                        position="unknown",
                        camera_id=camera_id,
                        active=True,
                    )
                )
                self.next_id += 1

        for track in existing:
            if timestamp - track.last_seen <= self.max_age_seconds:
                track.active = False
                assigned.append(track)

        self.tracks = [track for track in assigned if track.active or timestamp - track.last_seen <= self.max_age_seconds]
        return self.tracks

    def _compute_velocity(self, track: PersonTrack, detection: DetectorObject, timestamp: float) -> float:
        old_center = self._center(track.bbox)
        new_center = self._center(detection.bbox)
        dt = max(0.001, timestamp - track.last_seen)
        dx = new_center[0] - old_center[0]
        dy = new_center[1] - old_center[1]
        return ((dx**2 + dy**2) ** 0.5) / dt

    def _center(self, bbox: List[int]) -> tuple[float, float]:
        x, y, w, h = bbox
        return x + w / 2.0, y + h / 2.0

    def _estimate_position(self, bbox: List[int], previous_bbox: List[int]) -> str:
        current_x = bbox[0] + bbox[2] / 2.0
        previous_x = previous_bbox[0] + previous_bbox[2] / 2.0
        if abs(current_x - previous_x) < 20:
            return "center"
        return "left" if current_x < previous_x else "right"
