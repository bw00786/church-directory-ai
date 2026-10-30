"""Canonical ``/api/v1`` paths on top of the historical mixed prefixes.

Routers were registered over time under ``/atem``, ``/director``, ``/cameras``
... and ``/api/assistant``, ``/api/memory`` .... Rather than break every
existing client, this pure-ASGI middleware rewrites the canonical form to the
legacy path before routing::

    /api/v1/atem/status        -> /atem/status
    /api/v1/assistant/chat     -> /api/assistant/chat
    /api/v1/health/ready       -> /health/ready
    /api/v1/ws/director        -> /ws/director   (WebSockets too)

New clients should use ``/api/v1/...``; legacy paths keep working unchanged.
Runs *before* authentication so the auth rules keep matching legacy paths.
"""

from __future__ import annotations

CANONICAL_PREFIX = "/api/v1"

# First path segments whose legacy router lives under /api/<segment>.
LEGACY_API_SEGMENTS = frozenset({"assistant", "memory", "identity", "voice", "vision"})


def legacy_path(path: str) -> str:
    """Map a canonical ``/api/v1/...`` path to the legacy route path (identity otherwise)."""
    if path != CANONICAL_PREFIX and not path.startswith(CANONICAL_PREFIX + "/"):
        return path
    rest = path[len(CANONICAL_PREFIX):] or "/"
    segment = rest.split("/", 2)[1] if rest.count("/") >= 1 else ""
    if segment in LEGACY_API_SEGMENTS:
        return "/api" + rest
    return rest


class ApiVersionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            path = scope.get("path", "")
            mapped = legacy_path(path)
            if mapped != path:
                scope = dict(scope)
                scope["path"] = mapped
                raw = scope.get("raw_path")
                if isinstance(raw, (bytes, bytearray)):
                    scope["raw_path"] = mapped.encode("latin-1")
        await self.app(scope, receive, send)
