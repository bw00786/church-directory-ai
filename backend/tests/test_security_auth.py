"""API-key auth middleware: roles, public paths, dev mode, websockets."""

import pytest
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient

from app.config import settings
from app.security import auth
from app.security.auth import ApiKeyAuthMiddleware, startup_auth_check

OPERATOR_KEY = "operator-key-0123456789abcdef"
ADMIN_KEY = "admin-key-0123456789abcdef-xyz"


def _override_setting(monkeypatch, name, value):
    """Set a settings attribute even before the field exists in config.Settings.

    pydantic rejects setattr for unknown fields, but field values live in the
    model's __dict__, so patch that mapping directly; monkeypatch restores or
    removes the entry on teardown.
    """
    monkeypatch.setitem(settings.__dict__, name, value)


def _configure(monkeypatch, **overrides):
    values = {
        "api_auth_enabled": True,
        "api_key": OPERATOR_KEY,
        "api_admin_key": ADMIN_KEY,
        "api_allow_unauthenticated_reads": False,
        "api_host": "0.0.0.0",
    }
    values.update(overrides)
    for name, value in values.items():
        _override_setting(monkeypatch, name, value)
    monkeypatch.setattr(auth, "_dev_mode_warned", False)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiKeyAuthMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/health/live")
    async def live():
        return {"status": "alive"}

    @app.get("/")
    async def root():
        return {"name": "root"}

    @app.get("/atem/status")
    async def atem_status(request: Request):
        return {"role": request.state.role}

    @app.post("/atem/cut")
    async def atem_cut(request: Request):
        return {"role": request.state.role}

    @app.get("/director/ai/mode")
    async def get_mode(request: Request):
        return {"role": request.state.role}

    @app.post("/director/ai/mode")
    async def set_mode(request: Request):
        return {"role": request.state.role}

    @app.post("/director/ai/pending/0/approve")
    async def approve(request: Request):
        return {"role": request.state.role}

    @app.websocket("/ws/director")
    async def ws_director(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"role": websocket.state.role})
        await websocket.close()

    @app.websocket("/ws/cameras/1/joystick")
    async def ws_joystick(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"role": websocket.state.role})
        await websocket.close()

    return app


@pytest.fixture
def client(monkeypatch):
    _configure(monkeypatch)
    return TestClient(_make_app())


def test_missing_key_is_401(client):
    resp = client.get("/atem/status")
    assert resp.status_code == 401
    assert resp.json() == {"detail": auth.MISSING_KEY}
    assert resp.headers["WWW-Authenticate"] == "ApiKey"


def test_wrong_key_is_401(client):
    resp = client.get("/atem/status", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == auth.INVALID_KEY


def test_operator_key_header_sets_role(client):
    resp = client.get("/atem/status", headers={"X-API-Key": OPERATOR_KEY})
    assert resp.status_code == 200
    assert resp.json() == {"role": "operator"}


def test_bearer_header_accepted(client):
    resp = client.post("/atem/cut", headers={"Authorization": f"Bearer {OPERATOR_KEY}"})
    assert resp.status_code == 200
    assert resp.json() == {"role": "operator"}


def test_operator_cannot_change_ai_mode_but_can_read_and_approve(client):
    headers = {"X-API-Key": OPERATOR_KEY}
    assert client.post("/director/ai/mode", headers=headers).status_code == 403
    assert client.post("/director/ai/mode", headers=headers).json() == {"detail": auth.ADMIN_REQUIRED}
    assert client.get("/director/ai/mode", headers=headers).status_code == 200
    assert client.post("/director/ai/pending/0/approve", headers=headers).status_code == 200


def test_admin_key_allowed_everywhere(client):
    headers = {"X-API-Key": ADMIN_KEY}
    assert client.post("/director/ai/mode", headers=headers).json() == {"role": "admin"}
    assert client.get("/atem/status", headers=headers).json() == {"role": "admin"}


@pytest.mark.parametrize("path", ["/health", "/health/live", "/", "/docs", "/openapi.json"])
def test_public_paths_need_no_key(client, path):
    assert client.get(path).status_code == 200


def test_options_preflight_passes_without_key(client):
    resp = client.options("/atem/cut", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code != 401


def test_query_token_is_not_accepted_for_http(client):
    assert client.get(f"/atem/status?token={OPERATOR_KEY}").status_code == 401


def test_loopback_without_keys_allows_dev_mode(monkeypatch):
    _configure(monkeypatch, api_key="", api_admin_key="", api_host="127.0.0.1")
    client = TestClient(_make_app())
    assert client.post("/director/ai/mode").json() == {"role": "admin"}
    assert auth._dev_mode_warned is True


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.30.5", "::"])
def test_non_loopback_without_keys_is_503(monkeypatch, host):
    _configure(monkeypatch, api_key="", api_admin_key="", api_host=host)
    client = TestClient(_make_app())
    resp = client.get("/atem/status")
    assert resp.status_code == 503
    assert resp.json() == {"detail": auth.KEYS_NOT_CONFIGURED}
    assert client.get("/health").status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/director"):
            pass


def test_auth_disabled_allows_everything(monkeypatch):
    _configure(monkeypatch, api_auth_enabled=False, api_key="", api_admin_key="")
    client = TestClient(_make_app())
    assert client.post("/director/ai/mode").json() == {"role": "admin"}


def test_unauthenticated_reads_when_allowed(monkeypatch):
    _configure(monkeypatch, api_allow_unauthenticated_reads=True)
    client = TestClient(_make_app())
    assert client.get("/atem/status").json() == {"role": "viewer"}
    assert client.post("/atem/cut").status_code == 401
    with client.websocket_connect("/ws/director") as ws:
        assert ws.receive_json() == {"role": "viewer"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/cameras/1/joystick"):
            pass


def test_websocket_requires_token(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/director"):
            pass
    assert exc.value.code == 1008


def test_websocket_accepts_query_token(client):
    with client.websocket_connect(f"/ws/director?token={OPERATOR_KEY}") as ws:
        assert ws.receive_json() == {"role": "operator"}


def test_websocket_accepts_header(client):
    with client.websocket_connect("/ws/cameras/1/joystick", headers={"X-API-Key": ADMIN_KEY}) as ws:
        assert ws.receive_json() == {"role": "admin"}


def test_websocket_rejects_bad_query_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/director?token=wrong"):
            pass


def test_is_loopback_host():
    assert auth.is_loopback_host("127.0.0.1")
    assert auth.is_loopback_host("::1")
    assert auth.is_loopback_host("localhost")
    assert not auth.is_loopback_host("0.0.0.0")
    assert not auth.is_loopback_host("192.168.1.2")
    assert not auth.is_loopback_host("")


def test_startup_check_clean(monkeypatch):
    _configure(monkeypatch)
    assert startup_auth_check() == []


def test_startup_check_flags_missing_keys_off_loopback(monkeypatch):
    _configure(monkeypatch, api_key="", api_admin_key="")
    assert startup_auth_check() == [auth.KEYS_NOT_CONFIGURED]


def test_startup_check_loopback_dev_mode_is_not_a_problem(monkeypatch):
    _configure(monkeypatch, api_key="", api_admin_key="", api_host="127.0.0.1")
    assert startup_auth_check() == []


def test_startup_check_flags_disabled_auth_on_network(monkeypatch):
    _configure(monkeypatch, api_auth_enabled=False)
    problems = startup_auth_check()
    assert len(problems) == 1 and "API_AUTH_ENABLED=false" in problems[0]


def test_startup_check_flags_weak_and_duplicate_keys(monkeypatch):
    _configure(monkeypatch, api_key="short", api_admin_key="short")
    problems = startup_auth_check()
    assert any("identical" in p for p in problems)
    assert sum("shorter than 16" in p for p in problems) == 2


def test_startup_check_flags_missing_admin_key(monkeypatch):
    _configure(monkeypatch, api_admin_key="")
    problems = startup_auth_check()
    assert len(problems) == 1 and "API_ADMIN_KEY is empty" in problems[0]
