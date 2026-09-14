from datetime import datetime, timezone
from enum import IntEnum
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Priority(IntEnum):
    SILENT = 0
    BACKGROUND = 1
    ATTENTION = 2
    WARNING = 3
    CRITICAL = 4


class AttentionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: str = Field(max_length=100)
    source: str = Field(default="system", max_length=100)
    resource: str = Field(default="production", max_length=100)
    service_id: str | None = None
    confidence: float = Field(default=1, ge=0, le=1)
    consequence: Literal["low", "medium", "high", "critical"] = "low"
    reversible: bool = True
    operator_required: bool = False
    hardware_failure: bool = False
    state_mismatch: bool = False
    execution_failure: bool = False
    policy_block: bool = False
    critical: bool = False
    routine: bool = False
    resolved: bool = False
    state: str = "active"
    approval_token: str | None = None


class VoiceEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=utcnow)
    input: AttentionInput
    priority: Priority
    message: str
    mode: str
    spoken: bool = False
    spoken_at: datetime | None = None
    acknowledged: bool = False
    acknowledged_at: datetime | None = None
    operator_action: str | None = None
    feedback: str | None = None
    cooldown_suppressed: bool = False
    aggregation_id: str | None = None
    result: str = "observed"
    resolved: bool = False


class VoiceConfig(BaseModel):
    gender: Literal["female"] = "female"
    style: Literal["warm_conversational"] = "warm_conversational"
    language: Literal["en-US"] = "en-US"
    voice_id: str = Field(default="", max_length=150)
    speaking_rate: float = Field(default=0.94, ge=0.75, le=1.2)
    expressiveness: float = Field(default=0.70, ge=0, le=1)
    pitch: Literal["neutral"] = "neutral"
    breathiness: Literal["subtle"] = "subtle"


class ProsodyConfig(BaseModel):
    speaking_rate: float = 0.94
    pitch: str = "0%"
    expressiveness: float = 0.7
    pause_before_ms: int = 0
    pause_after_ms: int = 100
    emotional_style: str = "calm"


class AudioResult(BaseModel):
    data: bytes
    content_type: Literal["audio/wav"] = "audio/wav"


class TTSProvider(Protocol):
    async def synthesize(self, text: str, voice_config: VoiceConfig, prosody: ProsodyConfig) -> AudioResult: ...