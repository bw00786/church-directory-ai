from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import VoiceConfig


class VoiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOICE_", env_file=".env", env_nested_delimiter="__", extra="ignore")
    enabled: bool = False
    mode: Literal["off", "attention_only", "testing", "emergency"] = "attention_only"
    operator_name: str = Field(default="", max_length=60)
    output_device: str = ""
    output_host_api: str = ""
    routing_verified: bool = False
    headset_only: Literal[True] = True
    church_pa: Literal[False] = False
    livestream: Literal[False] = False
    recording: Literal[False] = False
    cooldown_seconds: float = Field(default=10, ge=1, le=600)
    repeat_interval_seconds: float = Field(default=120, ge=10, le=3600)
    aggregation_window_seconds: float = Field(default=5, ge=0, le=10)
    max_event_age_seconds: float = Field(default=60, ge=5, le=600)
    queue_limit: int = Field(default=100, ge=1, le=500)
    audit_spool_dir: str = "data/voice-outbox"
    persona: VoiceConfig = Field(default_factory=VoiceConfig)

    @field_validator("headset_only", "church_pa", "livestream", "recording", mode="before")
    @classmethod
    def parse_routing_flag(cls, value):
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        return value

    @model_validator(mode="after")
    def safe_device(self):
        blocked = ("yamaha", "mgx", "atem", "blackmagic", "main", "loopback", "virtual", "soundflower", "blackhole", "aggregate", "multi-output", "speaker", "hdmi", "displayport")
        if self.output_device and (self.output_device.lower() == "default" or any(x in self.output_device.lower() for x in blocked)):
            raise ValueError("Voice output must be a dedicated headset, never a program or virtual device")
        if self.repeat_interval_seconds < self.cooldown_seconds:
            raise ValueError("Repeat interval must be at least the cooldown")
        return self


class TTSSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TTS_", env_file=".env", extra="ignore")
    provider: Literal["piper", "disabled"] = "piper"
    piper_model_dir: str = "data/piper-voices"
    piper_voice: str = Field(default="en_US-ljspeech-high", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
    timeout_seconds: float = Field(default=30, ge=1, le=30)


class VoiceUpdate(VoiceSettings):
    model_config = ConfigDict(extra="forbid")