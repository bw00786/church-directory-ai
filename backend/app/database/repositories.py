"""Data access repositories."""

# TODO: Implement remaining repositories (unrelated to identity/memory, tracked separately):
# - ServiceRepository
# - EventRepository
# - AuditRepository
# - PresetRepository
# - DecisionRepository

from __future__ import annotations

import uuid
from datetime import date, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import pgvector_support
from .models import (
    FaceProfile,
    IdentityObservation,
    Person,
    RolePresetStat,
    ServiceObservation,
    VoiceProfile,
)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 if either vector has no magnitude."""
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0 or va.shape != vb.shape:
        return 0.0
    return float(np.dot(va, vb) / denom)


class PersonRepository:
    """Roster of known people plus their enrolled face/voice embeddings."""

    def __init__(self, session: Session):
        self.session = session

    def create_person(self, name: str, role: str = "unknown", notes: str | None = None) -> Person:
        person = Person(name=name, role=role, notes=notes)
        self.session.add(person)
        self.session.flush()
        return person

    def delete_person(self, person_id: uuid.UUID) -> bool:
        person = self.session.get(Person, person_id)
        if person is None:
            return False
        self.session.delete(person)
        return True

    def list_people(self) -> list[Person]:
        return list(self.session.scalars(select(Person).order_by(Person.name)))

    def get_person(self, person_id: uuid.UUID) -> Person | None:
        return self.session.get(Person, person_id)

    def add_face_embedding(self, person_id: uuid.UUID, embedding: list[float]) -> FaceProfile:
        profile = FaceProfile(person_id=person_id, embedding=embedding)
        self.session.add(profile)
        self.session.flush()
        if pgvector_support.pgvector_available():
            try:
                pgvector_support.upsert_face_vector(self.session, profile.id, person_id, embedding)
            except Exception:
                pass  # accelerator only; brute-force matching still works
        return profile

    def add_voice_embedding(self, person_id: uuid.UUID, embedding: list[float]) -> VoiceProfile:
        profile = VoiceProfile(person_id=person_id, embedding=embedding)
        self.session.add(profile)
        self.session.flush()
        if pgvector_support.pgvector_available():
            try:
                pgvector_support.upsert_voice_vector(self.session, profile.id, person_id, embedding)
            except Exception:
                pass  # accelerator only; brute-force matching still works
        return profile

    def match_face(self, embedding: list[float], threshold: float) -> tuple[Person, float] | None:
        """Nearest enrolled face profile above `threshold`, or None.

        Uses the pgvector-indexed companion table when the extension is
        available (scales far better than the brute-force fallback below);
        otherwise scores every enrolled profile in Python.
        """
        if pgvector_support.pgvector_available():
            try:
                match = pgvector_support.nearest_face(self.session, embedding)
            except Exception:
                match = None
            if match is not None:
                person_id, score = match
                if score >= threshold:
                    person = self.session.get(Person, person_id)
                    if person is not None:
                        return person, score
                return None

        best_person: Person | None = None
        best_score = 0.0
        for profile in self.session.scalars(select(FaceProfile)):
            score = cosine_similarity(embedding, profile.embedding)
            if score > best_score:
                best_score = score
                best_person = profile.person
        if best_person is not None and best_score >= threshold:
            return best_person, best_score
        return None

    def match_voice(self, embedding: list[float], threshold: float) -> tuple[Person, float] | None:
        """Nearest enrolled voice profile above `threshold`, or None.

        Uses the pgvector-indexed companion table when available; otherwise
        scores every enrolled profile in Python (see `match_face`).
        """
        if pgvector_support.pgvector_available():
            try:
                match = pgvector_support.nearest_voice(self.session, embedding)
            except Exception:
                match = None
            if match is not None:
                person_id, score = match
                if score >= threshold:
                    person = self.session.get(Person, person_id)
                    if person is not None:
                        return person, score
                return None

        best_person: Person | None = None
        best_score = 0.0
        for profile in self.session.scalars(select(VoiceProfile)):
            score = cosine_similarity(embedding, profile.embedding)
            if score > best_score:
                best_score = score
                best_person = profile.person
        if best_person is not None and best_score >= threshold:
            return best_person, best_score
        return None

    def record_sighting(self, person: Person) -> None:
        person.last_seen_at = datetime.utcnow()
        person.appearance_count += 1

    def log_observation(
        self,
        modality: str,
        confidence: float,
        source: str,
        person: Person | None = None,
        detail: str | None = None,
    ) -> IdentityObservation:
        observation = IdentityObservation(
            modality=modality,
            person_id=person.id if person else None,
            person_name=person.name if person else None,
            role=person.role if person else None,
            confidence=confidence,
            source=source,
            detail=detail,
        )
        self.session.add(observation)
        self.session.flush()
        return observation

    def recent_observations(self, limit: int = 50) -> list[IdentityObservation]:
        stmt = select(IdentityObservation).order_by(IdentityObservation.timestamp.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def observations_by_role_on_date(self, role: str, service_date: str) -> list[IdentityObservation]:
        """Identity matches for `role` (e.g. "pastor") on a given calendar day.

        `IdentityObservation` only stores a timestamp (not a service_date
        bucket like `ServiceObservation`), so this filters by the day range
        directly. Ordered by confidence descending so callers can take the
        top match as "who was recognized as {role}" for that service.
        """
        day = date.fromisoformat(service_date)
        start = datetime(day.year, day.month, day.day)
        end = datetime(day.year, day.month, day.day, 23, 59, 59, 999999)
        stmt = (
            select(IdentityObservation)
            .where(
                IdentityObservation.role == role,
                IdentityObservation.timestamp >= start,
                IdentityObservation.timestamp <= end,
            )
            .order_by(IdentityObservation.confidence.desc())
        )
        return list(self.session.scalars(stmt))


class RolePresetRepository:
    """Learned mapping of "role X is usually on-camera at preset Y", built
    from identity face matches co-occurring with the PTZ camera's current
    preset. Lets the cue sheet reference a role instead of a hardcoded preset.
    """

    def __init__(self, session: Session):
        self.session = session

    def record(self, role: str, camera_id: int, preset_id: int) -> None:
        stmt = select(RolePresetStat).where(
            RolePresetStat.role == role,
            RolePresetStat.camera_id == camera_id,
            RolePresetStat.preset_id == preset_id,
        )
        stat = self.session.scalars(stmt).first()
        if stat is None:
            stat = RolePresetStat(role=role, camera_id=camera_id, preset_id=preset_id, count=0)
            self.session.add(stat)
            self.session.flush()  # visible to subsequent lookups in this session (autoflush is off)
        stat.count += 1
        stat.last_seen_at = datetime.utcnow()

    def best_preset(
        self, role: str, camera_id: int, min_samples: int = 1, min_margin: int = 0
    ) -> tuple[int, int] | None:
        """(preset_id, count) with the highest co-occurrence count, or None.

        Requires at least `min_samples` observations and a lead of at least
        `min_margin` over the runner-up preset before trusting the result --
        otherwise returns None so the caller falls back to a configured
        default instead of acting on thin/ambiguous data.
        """
        stmt = (
            select(RolePresetStat)
            .where(RolePresetStat.role == role, RolePresetStat.camera_id == camera_id)
            .order_by(RolePresetStat.count.desc())
            .limit(2)
        )
        stats = list(self.session.scalars(stmt))
        if not stats:
            return None

        top = stats[0]
        if top.count < min_samples:
            return None

        runner_up_count = stats[1].count if len(stats) > 1 else 0
        if top.count - runner_up_count < min_margin:
            return None

        return top.preset_id, top.count


    def all_stats(self) -> list[RolePresetStat]:
        return list(self.session.scalars(select(RolePresetStat).order_by(RolePresetStat.role)))


class MemoryRepository:
    """Production memory: a running log of service observations (cue
    advances, vision events, identity matches, ...) with bag-of-words
    embeddings for lightweight semantic search across past services.
    """

    def __init__(self, session: Session):
        self.session = session

    def add_observation(
        self,
        service_date: str,
        category: str,
        text: str,
        embedding: list[float],
        source: str = "system",
        occurred_at: datetime | None = None,
    ) -> ServiceObservation:
        observation = ServiceObservation(
            service_date=service_date,
            category=category,
            text=text,
            embedding=embedding,
            source=source,
            occurred_at=occurred_at or datetime.utcnow(),
        )
        self.session.add(observation)
        self.session.flush()
        return observation

    def search(self, query_embedding: list[float], limit: int = 10) -> list[tuple[ServiceObservation, float]]:
        """Brute-force cosine-similarity search (fine for a church's service
        history; see RolePresetRepository/pgvector_support for the same
        caveat applied to identity embeddings)."""
        scored = [
            (observation, cosine_similarity(query_embedding, observation.embedding))
            for observation in self.session.scalars(select(ServiceObservation))
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    def list_service_dates(self, limit: int = 50) -> list[dict]:
        # Grouped in Python rather than SQL -- observation volume per service
        # is small, so this avoids a second query path just for counts.
        dates: dict[str, int] = {}
        for observation in self.session.scalars(select(ServiceObservation)):
            dates[observation.service_date] = dates.get(observation.service_date, 0) + 1
        ordered = sorted(dates.items(), key=lambda pair: pair[0], reverse=True)[:limit]
        return [{"service_date": service_date, "observation_count": count} for service_date, count in ordered]

    def observations_for_date(self, service_date: str) -> list[ServiceObservation]:
        stmt = (
            select(ServiceObservation)
            .where(ServiceObservation.service_date == service_date)
            .order_by(ServiceObservation.occurred_at)
        )
        return list(self.session.scalars(stmt))
