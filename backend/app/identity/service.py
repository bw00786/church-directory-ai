"""Identity recognition service: ties face/voice recognition to persistent roster memory."""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np

from app.database.connection import get_session
from app.database.repositories import PersonRepository, RolePresetRepository
from app.logging_config import get_logger

from .config import IdentitySettings
from .face import FaceEmbedder
from .liveness import assess_liveness
from .voice import SpeakerDiarizer, assess_enrollment_quality, classify_vocal_activity, extract_features

logger = get_logger(__name__)


class IdentityService:
    """Recognizes known people from face crops and voice audio, and remembers them."""

    def __init__(self) -> None:
        self.settings = IdentitySettings()
        self.face_embedder = FaceEmbedder()
        self.voice_diarizer = SpeakerDiarizer(
            match_threshold=self.settings.voice_match_threshold,
            ttl_seconds=self.settings.unknown_speaker_ttl_seconds,
        )

    # -- Roster management --------------------------------------------------

    def enroll_person(self, name: str, role: str = "unknown", notes: str | None = None) -> dict[str, Any]:
        with get_session() as session:
            person = PersonRepository(session).create_person(name, role, notes)
            return _person_to_dict(person)

    def list_roster(self) -> list[dict[str, Any]]:
        try:
            with get_session() as session:
                return [_person_to_dict(person) for person in PersonRepository(session).list_people()]
        except Exception:
            logger.exception("Roster unavailable (database unreachable)")
            return []

    def delete_person(self, person_id: str) -> bool:
        with get_session() as session:
            return PersonRepository(session).delete_person(uuid.UUID(person_id))

    def recent_observations(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            with get_session() as session:
                return [_observation_to_dict(o) for o in PersonRepository(session).recent_observations(limit)]
        except Exception:
            logger.exception("Identity observations unavailable (database unreachable)")
            return []

    def who_was_seen(self, role: str, service_date: str) -> dict[str, Any] | None:
        """Best-confidence identity match for `role` (e.g. "pastor") on a
        given service date -- the answer to "who preached on <date>?".
        Returns None if nobody matching that role was recognized that day.
        """
        try:
            with get_session() as session:
                observations = PersonRepository(session).observations_by_role_on_date(role, service_date)
        except Exception:
            logger.exception("Identity observations unavailable (database unreachable)")
            return None
        if not observations:
            return None
        best = observations[0]
        return {
            "person_name": best.person_name,
            "role": best.role,
            "confidence": best.confidence,
            "service_date": service_date,
            "sighting_count": len(observations),
        }

    # -- Role -> preset learning (links identity recognition to the cue sheet) --

    def record_role_preset_observation(self, role: str, camera_id: int, preset_id: int) -> None:
        """Record that `role` was recognized while `camera_id` sat at `preset_id`.

        Called from the vision pipeline on every confident face match; over
        time the most-common preset for a role becomes the director's
        role-based preset resolution (see app.director.engine).
        """
        try:
            with get_session() as session:
                RolePresetRepository(session).record(role, camera_id, preset_id)
        except Exception:
            logger.exception("Role/preset learning unavailable (database unreachable)")

    def best_preset_for_role(self, role: str, camera_id: int) -> int | None:
        """The most commonly co-occurring preset for `role` on `camera_id`,
        gated by `min_role_preset_samples`/`min_role_preset_margin` so a
        handful of early (possibly wrong) observations can't steer the
        camera -- returns None until enough consistent data has accumulated.
        """
        try:
            with get_session() as session:
                match = RolePresetRepository(session).best_preset(
                    role,
                    camera_id,
                    min_samples=self.settings.min_role_preset_samples,
                    min_margin=self.settings.min_role_preset_margin,
                )
                return match[0] if match else None
        except Exception:
            logger.exception("Role/preset lookup unavailable (database unreachable)")
            return None

    # -- Face -----------------------------------------------------------------

    def enroll_face(self, person_id: str, frame_bgr: np.ndarray) -> bool:
        bbox = self.face_embedder.largest_face(frame_bgr)
        if bbox is None:
            return False
        embedding = self.face_embedder.embed(frame_bgr, bbox)
        with get_session() as session:
            repo = PersonRepository(session)
            person = repo.get_person(uuid.UUID(person_id))
            if person is None:
                return False
            repo.add_face_embedding(person.id, embedding.tolist())
        return True

    def identify_face(
        self,
        frame_bgr: np.ndarray,
        bbox: list[int],
        source: str = "camera",
        liveness_crop_history: list[np.ndarray] | None = None,
    ) -> dict[str, Any] | None:
        if not self.settings.face_recognition_enabled:
            return None
        try:
            embedding = self.face_embedder.embed(frame_bgr, bbox)
        except Exception:
            logger.exception("Face embedding failed")
            return None

        is_live = True
        liveness_score = 1.0
        if self.settings.liveness_check_enabled and liveness_crop_history is not None:
            current_crop = self.face_embedder.gray_crop(frame_bgr, bbox)
            liveness_score, _breakdown = assess_liveness(current_crop, liveness_crop_history)
            is_live = liveness_score >= self.settings.liveness_score_threshold

        try:
            with get_session() as session:
                repo = PersonRepository(session)
                match = repo.match_face(embedding.tolist(), self.settings.face_match_threshold)
                if match is None:
                    return None
                person, score = match
                detail = None if is_live else f"possible spoof: liveness_score={liveness_score:.2f}"
                if is_live:
                    repo.record_sighting(person)
                repo.log_observation("face", score, source, person=person, detail=detail)
                return {
                    "person_id": str(person.id),
                    "name": person.name,
                    "role": person.role,
                    "confidence": score,
                    "live": is_live,
                    "liveness_score": liveness_score,
                }
        except Exception:
            logger.exception("Face identification unavailable (database unreachable)")
            return None

    # -- Voice ------------------------------------------------------------------

    def enroll_voice(self, person_id: str, samples: np.ndarray, sample_rate: int) -> tuple[bool, str]:
        ok, reason = assess_enrollment_quality(samples, sample_rate)
        if not ok:
            return False, reason

        embedding = extract_features(samples, sample_rate)
        with get_session() as session:
            repo = PersonRepository(session)
            person = repo.get_person(uuid.UUID(person_id))
            if person is None:
                return False, "person not found"
            repo.add_voice_embedding(person.id, embedding.tolist())
        return True, "ok"

    def identify_voice(self, channel: str, samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
        activity, activity_confidence = classify_vocal_activity(
            samples, sample_rate, silence_rms=self.settings.voice_silence_rms
        )

        if activity == "silence":
            return {
                "person_id": None,
                "name": None,
                "role": None,
                "confidence": activity_confidence,
                "activity": activity,
                "is_known": False,
            }

        embedding = extract_features(samples, sample_rate)

        if self.settings.voice_diarization_enabled:
            try:
                with get_session() as session:
                    repo = PersonRepository(session)
                    match = repo.match_voice(embedding.tolist(), self.settings.voice_match_threshold)
                    if match is not None:
                        person, score = match
                        repo.record_sighting(person)
                        repo.log_observation("voice", score, channel, person=person, detail=activity)
                        return {
                            "person_id": str(person.id),
                            "name": person.name,
                            "role": person.role,
                            "confidence": score,
                            "activity": activity,
                            "is_known": True,
                        }
            except Exception:
                logger.exception("Voice identification unavailable (database unreachable)")

        provisional_key, score, is_new = self.voice_diarizer.identify(embedding)
        return {
            "person_id": None,
            "name": provisional_key,
            "role": None,
            "confidence": score,
            "activity": activity,
            "is_known": False,
            "is_new_provisional_speaker": is_new,
        }


def _person_to_dict(person: Any) -> dict[str, Any]:
    return {
        "id": str(person.id),
        "name": person.name,
        "role": person.role,
        "notes": person.notes,
        "appearance_count": person.appearance_count,
        "last_seen_at": person.last_seen_at.isoformat() if person.last_seen_at else None,
        "created_at": person.created_at.isoformat() if person.created_at else None,
    }


def _observation_to_dict(observation: Any) -> dict[str, Any]:
    return {
        "id": str(observation.id),
        "timestamp": observation.timestamp.isoformat(),
        "modality": observation.modality,
        "person_id": str(observation.person_id) if observation.person_id else None,
        "person_name": observation.person_name,
        "role": observation.role,
        "confidence": observation.confidence,
        "source": observation.source,
        "detail": observation.detail,
    }


identity_service = IdentityService()
