"""Yamaha DM3/MGX16 channel -> role mapping.

Wraps the existing listen-only app.mixer.service (per-channel RMS from the
mgx-ai-mixer meter feed) with the channel/role assignments from config, so
the rest of the audio pipeline never hard-codes channel numbers.
"""

from dataclasses import dataclass

from app.config import settings
from app.mixer.service import mixer_service


@dataclass(frozen=True)
class RoleChannel:
    role: str
    channel: int


def configured_role_channels() -> list[RoleChannel]:
    """The channel -> role assignments, from configuration."""
    return [
        RoleChannel("pastor", settings.mixer_pastor_channel),
        RoleChannel("liturgist", settings.mixer_liturgist_channel),
        RoleChannel("vocalist", settings.mixer_vocalist_channel),
        RoleChannel("congregation", settings.mixer_congregation_channel),
    ]


def channel_rms(channel: int) -> float:
    """Current RMS (dB) for a channel, from the mixer meter feed."""
    return mixer_service.channel_rms(channel)
