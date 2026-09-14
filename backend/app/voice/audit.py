from datetime import datetime

from sqlalchemy import JSON, DateTime, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base, get_session, _engine
from .models import VoiceEvent


class VoiceAuditEvent(Base):
    __tablename__ = "voice_attention_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class VoiceAudit:
    def initialize(self):
        VoiceAuditEvent.__table__.create(bind=_engine, checkfirst=True)

    def save(self, event: VoiceEvent):
        with get_session() as session:
            session.merge(VoiceAuditEvent(id=event.id, timestamp=event.timestamp, data=event.model_dump(mode="json")))

    def recent(self, limit: int = 200):
        with get_session() as session:
            rows = session.scalars(select(VoiceAuditEvent).order_by(VoiceAuditEvent.timestamp.desc()).limit(limit))
            return [VoiceEvent.model_validate(row.data) for row in rows]