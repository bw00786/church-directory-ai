"""SQLAlchemy ORM models."""

# TODO: Implement remaining models (unrelated to identity/memory, tracked separately):
# - Service (service_id, date, service_type)
# - ProductionEvent (timestamp, event_type, payload)
# - CameraAction (timestamp, camera_id, action)
# - AiDecision (timestamp, reasoning, action, result)
# - CameraPreset (name, camera_id, pan, tilt, zoom)
# - AtemEvent (timestamp, event, state)
# - AuditLog (timestamp, actor, action, parameters, result)

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .connection import Base


class Person(Base):
    """A known individual the system can recognize (pastor, liturgist, vocalist, ...)."""

    __tablename__ = "identity_persons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(100), default="unknown")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    appearance_count: Mapped[int] = mapped_column(Integer, default=0)

    face_profiles: Mapped[list["FaceProfile"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )
    voice_profiles: Mapped[list["VoiceProfile"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )


class FaceProfile(Base):
    """An enrolled facial-appearance embedding sample for a person."""

    __tablename__ = "identity_face_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity_persons.id", ondelete="CASCADE"))
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    person: Mapped["Person"] = relationship(back_populates="face_profiles")


class VoiceProfile(Base):
    """An enrolled voice-spectral embedding sample for a person."""

    __tablename__ = "identity_voice_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity_persons.id", ondelete="CASCADE"))
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    person: Mapped["Person"] = relationship(back_populates="voice_profiles")


class IdentityObservation(Base):
    """Log of a face/voice match (or non-match) for audit and later review."""

    __tablename__ = "identity_observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    modality: Mapped[str] = mapped_column(String(20))  # "face" or "voice"
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity_persons.id", ondelete="SET NULL"), nullable=True
    )
    person_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(100))  # e.g. "camera_1", "channel_5"
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class RolePresetStat(Base):
    """Learned co-occurrence: how often a recognized role was on-camera at a
    given PTZ preset, so the director can pick a preset by role instead of a
    hardcoded number (see app.director.engine's role-aware preset resolution).
    """

    __tablename__ = "identity_role_preset_stats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role: Mapped[str] = mapped_column(String(100))
    camera_id: Mapped[int] = mapped_column(Integer)
    preset_id: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ServiceObservation(Base):
    """A single recorded moment of production memory (cue advance, vision
    event, identity match, ...), embedded for later semantic-ish search.
    """

    __tablename__ = "memory_service_observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    service_date: Mapped[str] = mapped_column(String(10))  # "YYYY-MM-DD", buckets by calendar day
    category: Mapped[str] = mapped_column(String(50))  # e.g. "cue", "vision_event", "identity"
    source: Mapped[str] = mapped_column(String(100), default="system")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float))
