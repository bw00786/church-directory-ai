"""Per-church rig profile (``rig.yaml``): cameras, switcher inputs, mixer
channels, PTZ role shots, cue-sheet overrides and order-of-service heading
synonyms, so a second church with different hardware and liturgy runs the
director without editing Python. See docs/rig-profile.md."""

from .profile import (
    RigProfile,
    RigProfileError,
    install,
    load_rig_profile,
    profile_path,
    reload,
    rig,
)

__all__ = ["RigProfile", "RigProfileError", "install", "load_rig_profile", "profile_path", "reload", "rig"]
