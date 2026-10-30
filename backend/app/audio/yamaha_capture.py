"""Yamaha DM3/MGX16 channel -> role mapping.

Wraps the existing listen-only app.mixer.service (per-channel RMS from the
mgx-ai-mixer meter feed) with the channel/role assignments from the rig
profile (``mixer.roles`` in rig.yaml; with no profile, the
``mixer_<role>_channel`` settings), so the rest of the audio pipeline never
hard-codes channel numbers or a fixed set of roles.
"""

from dataclasses import dataclass

from app.mixer.service import mixer_service
from app.rig import rig


@dataclass(frozen=True)
class RoleChannel:
    role: str
    channel: int


def configured_role_channels() -> list[RoleChannel]:
    """The role -> channel assignments, from the rig profile (any role names,
    in profile order; the default profile lists pastor, liturgist, vocalist,
    congregation from the settings)."""
    return [RoleChannel(role, int(channel)) for role, channel in rig().mixer.roles.items()]


def role_channel(role: str) -> int | None:
    """Mixer channel for ``role`` or None when the profile has no such role."""
    return rig().mixer_channel(role)


def channel_rms(channel: int) -> float:
    """Current RMS (dB) for a channel, from the mixer meter feed."""
    return mixer_service.channel_rms(channel)
