import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.versioning import ApiVersionMiddleware, legacy_path


@pytest.mark.parametrize(
    "canonical, legacy",
    [
        ("/api/v1/atem/status", "/atem/status"),
        ("/api/v1/director/ai/status", "/director/ai/status"),
        ("/api/v1/assistant/chat", "/api/assistant/chat"),
        ("/api/v1/memory/search", "/api/memory/search"),
        ("/api/v1/health/ready", "/health/ready"),
        ("/api/v1/ws/director", "/ws/director"),
        ("/api/v1", "/"),
        ("/atem/status", "/atem/status"),
        ("/api/assistant/chat", "/api/assistant/chat"),
        ("/api/v10/x", "/api/v10/x"),
    ],
)
def test_legacy_path(canonical, legacy):
    assert legacy_path(canonical) == legacy


def test_middleware_routes_canonical_to_legacy_handlers():
    app = FastAPI()

    @app.get("/atem/status")
    def atem():
        return {"ok": "atem"}

    @app.get("/api/assistant/ping")
    def assistant():
        return {"ok": "assistant"}

    app.add_middleware(ApiVersionMiddleware)
    client = TestClient(app)
    assert client.get("/api/v1/atem/status").json() == {"ok": "atem"}
    assert client.get("/api/v1/assistant/ping").json() == {"ok": "assistant"}
    assert client.get("/atem/status").json() == {"ok": "atem"}
    assert client.get("/api/v1/nope").status_code == 404
