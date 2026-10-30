"""Placeholder-secret detection (pure; the integrator decides what to do).

``placeholder_secret_problems(settings)`` returns human-readable problems for
secrets still at their shipped/vendor defaults. No I/O, no logging.
"""

from __future__ import annotations

from typing import Any

from app.security.auth import is_loopback_host

PLACEHOLDER_POSTGRES_PASSWORD = "changeme"
PLACEHOLDER_SECRET_KEY_MARKERS = ("change", "your-secret")
# Factory credentials PTZOptics ships on its cameras.
PTZOPTICS_DEFAULT_PASSWORDS = frozenset({"smgadmin", "admin"})


def placeholder_secret_problems(settings: Any) -> list[str]:
    """Flag secrets that are empty or still at a well-known placeholder value."""
    problems: list[str] = []

    if settings.postgres_password == PLACEHOLDER_POSTGRES_PASSWORD:
        problems.append("POSTGRES_PASSWORD is the placeholder 'changeme'")

    secret_key = (settings.secret_key or "").lower()
    if not secret_key or any(marker in secret_key for marker in PLACEHOLDER_SECRET_KEY_MARKERS):
        problems.append("SECRET_KEY is empty or still the placeholder from .env.example")

    camera_password = settings.camera_1_password
    if camera_password and camera_password.lower() in PTZOPTICS_DEFAULT_PASSWORDS:
        problems.append("CAMERA_1_PASSWORD is a PTZOptics factory default; change it on the camera and in .env")

    if settings.api_auth_enabled and not (settings.api_key or settings.api_admin_key):
        if not is_loopback_host(settings.api_host):
            problems.append(
                f"API_KEY and API_ADMIN_KEY are empty while API_HOST={settings.api_host} is not loopback"
            )

    return problems
