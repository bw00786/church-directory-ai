"""API-key authentication for the control plane (HTTP and WebSocket).

Pure ASGI middleware so every route (REST and ``/ws/*``) is covered without
touching the individual routers.

Credentials
    ``X-API-Key: <key>`` or ``Authorization: Bearer <key>``; WebSockets may
    also pass ``?token=<key>`` because browsers cannot set headers on them.

Roles
    ``settings.api_admin_key`` -> ``admin``; ``settings.api_key`` ->
    ``operator``. Mutating requests under ``ADMIN_ONLY_PREFIXES`` need admin.
    The resolved role is stored on ``request.state.role``
    (``websocket.state.role`` for sockets).

Unconfigured keys
    With ``api_auth_enabled`` true and both keys empty, the API is allowed in
    "dev mode" only when ``settings.api_host`` is a loopback address (warned
    once); otherwise every non-public request gets 503 so a network-exposed
    deployment can never run open by accident.
"""

from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"  # unauthenticated read (api_allow_unauthenticated_reads)
ROLE_ANONYMOUS = "anonymous"  # public paths

# Mutating requests (anything but GET/HEAD/OPTIONS) under these prefixes need
# the admin key. Keep this list small and deliberate.
ADMIN_ONLY_PREFIXES: tuple[str, ...] = (
    "/director/ai/mode",
    "/api/settings",
)

# Always reachable without a key.
PUBLIC_PATHS: frozenset[str] = frozenset({"/", "/health", "/docs", "/openapi.json", "/redoc"})
PUBLIC_PREFIXES: tuple[str, ...] = ("/health/", "/docs/", "/app/", "/assets/")

# WebSockets that drive hardware are writes; the rest are read-only streams.
WRITE_WEBSOCKET_PREFIXES: tuple[str, ...] = ("/ws/cameras",)

SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

KEYS_NOT_CONFIGURED = "API keys not configured; set API_KEY or bind API_HOST=127.0.0.1"
MISSING_KEY = "Missing API key: send X-API-Key or Authorization: Bearer (WebSockets: ?token=)"
INVALID_KEY = "Invalid API key"
ADMIN_REQUIRED = "Admin API key required for this operation"

_dev_mode_warned = False


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    role: str | None = None
    status: int = 200
    detail: str = ""

    @classmethod
    def allow(cls, role: str) -> "AuthDecision":
        return cls(True, role=role)

    @classmethod
    def deny(cls, status: int, detail: str) -> "AuthDecision":
        return cls(False, status=status, detail=detail)


def is_loopback_host(host: str | None) -> bool:
    """True for 127.0.0.0/8, ::1 and 'localhost'."""
    if not host:
        return False
    host = host.strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def is_admin_only(path: str, method: str) -> bool:
    return method.upper() not in SAFE_METHODS and path.startswith(ADMIN_ONLY_PREFIXES)


def keys_configured() -> bool:
    return bool(settings.api_key) or bool(settings.api_admin_key)


def _matches(presented: str, expected: str) -> bool:
    return bool(expected) and secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def resolve_role(token: str | None) -> str | None:
    """Map a presented key to a role, or None if it matches nothing."""
    if not token:
        return None
    if _matches(token, settings.api_admin_key):
        return ROLE_ADMIN
    if _matches(token, settings.api_key):
        return ROLE_OPERATOR
    return None


def extract_token(headers: list[tuple[bytes, bytes]], query_string: bytes, *, allow_query: bool) -> str | None:
    """Pull the key from X-API-Key, Authorization: Bearer, or (WebSockets) ?token=."""
    bearer: str | None = None
    for name, value in headers:
        if name == b"x-api-key":
            token = value.decode("latin-1").strip()
            if token:
                return token
        elif name == b"authorization" and bearer is None:
            raw = value.decode("latin-1").strip()
            scheme, _, credential = raw.partition(" ")
            if scheme.lower() == "bearer" and credential.strip():
                bearer = credential.strip()
    if bearer:
        return bearer
    if allow_query and query_string:
        values = parse_qs(query_string.decode("latin-1"), keep_blank_values=False).get("token")
        if values and values[0]:
            return values[0]
    return None


def _warn_dev_mode_once() -> None:
    global _dev_mode_warned
    if not _dev_mode_warned:
        _dev_mode_warned = True
        logger.warning(
            "API auth: no API_KEY/API_ADMIN_KEY configured; allowing all requests because "
            "API_HOST is loopback (dev mode). Set API_KEY before exposing the API.",
            api_host=settings.api_host,
        )


def _is_read(path: str, method: str, is_websocket: bool) -> bool:
    if is_websocket:
        return not path.startswith(WRITE_WEBSOCKET_PREFIXES)
    return method.upper() in SAFE_METHODS


def authorize(scope: Scope) -> AuthDecision:
    """Decide whether an HTTP/WebSocket scope may proceed and with which role."""
    is_websocket = scope["type"] == "websocket"
    path: str = scope.get("path", "") or "/"
    method: str = "GET" if is_websocket else str(scope.get("method", "GET")).upper()

    if not settings.api_auth_enabled:
        return AuthDecision.allow(ROLE_ADMIN)

    # CORS preflight carries no credentials; public paths need none.
    if method == "OPTIONS" or is_public_path(path):
        return AuthDecision.allow(ROLE_ANONYMOUS)

    if not keys_configured():
        if is_loopback_host(settings.api_host):
            _warn_dev_mode_once()
            return AuthDecision.allow(ROLE_ADMIN)
        return AuthDecision.deny(503, KEYS_NOT_CONFIGURED)

    token = extract_token(scope.get("headers", []), scope.get("query_string", b""), allow_query=is_websocket)
    role = resolve_role(token)
    if role is None:
        if token is None and settings.api_allow_unauthenticated_reads and _is_read(path, method, is_websocket):
            return AuthDecision.allow(ROLE_VIEWER)
        return AuthDecision.deny(401, MISSING_KEY if token is None else INVALID_KEY)

    if is_admin_only(path, method) and role != ROLE_ADMIN:
        return AuthDecision.deny(403, ADMIN_REQUIRED)
    return AuthDecision.allow(role)


class ApiKeyAuthMiddleware:
    """ASGI middleware enforcing :func:`authorize` on HTTP and WebSocket scopes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        decision = authorize(scope)
        if decision.allowed:
            scope.setdefault("state", {})["role"] = decision.role
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Consume the connect event, then reject before accepting (HTTP 403 on the wire).
            message = await receive()
            if message.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": 1008, "reason": decision.detail[:120]})
            return

        headers = {"WWW-Authenticate": "ApiKey"} if decision.status == 401 else None
        response = JSONResponse({"detail": decision.detail}, status_code=decision.status, headers=headers)
        await response(scope, receive, send)


def startup_auth_check() -> list[str]:
    """Human-readable auth configuration problems for the integrator's startup gate."""
    problems: list[str] = []
    loopback = is_loopback_host(settings.api_host)

    if not settings.api_auth_enabled:
        if not loopback:
            problems.append(
                f"API_AUTH_ENABLED=false while API_HOST={settings.api_host} is reachable from the network; "
                "every control endpoint is open"
            )
        return problems

    if not keys_configured():
        if loopback:
            _warn_dev_mode_once()
        else:
            problems.append(KEYS_NOT_CONFIGURED)
        return problems

    if not settings.api_admin_key:
        problems.append(
            "API_ADMIN_KEY is empty: admin-only endpoints "
            f"({', '.join(ADMIN_ONLY_PREFIXES)}) cannot be used; set API_ADMIN_KEY"
        )
    if settings.api_key and settings.api_admin_key and _matches(settings.api_key, settings.api_admin_key):
        problems.append("API_KEY and API_ADMIN_KEY are identical; the operator key would grant admin")
    for name, value in (("API_KEY", settings.api_key), ("API_ADMIN_KEY", settings.api_admin_key)):
        if value and len(value) < 16:
            problems.append(
                f"{name} is shorter than 16 characters; generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
    return problems
