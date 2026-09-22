"""Test FastAPI endpoints."""

import pytest
from unittest.mock import AsyncMock, Mock
from fastapi.testclient import TestClient

from app.agents import llm as llm_module
from app.config import settings
from app.main import app

client = TestClient(app)


def test_health():
    """Test health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


@pytest.mark.parametrize("key,expected", [("test-key", "configured"), ("", "not configured")])
def test_health_ready(monkeypatch, key, expected):
    """Readiness reports configuration, without inference or legacy keys."""
    monkeypatch.setattr(settings, "anthropic_api_key", key)
    probe = AsyncMock(side_effect=AssertionError("readiness must not probe Anthropic"))
    builder = Mock(side_effect=AssertionError("readiness must not build a model"))
    monkeypatch.setattr(llm_module, "check_anthropic_connection", probe)
    monkeypatch.setattr(llm_module, "build_llm", builder)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"api": "ok", "anthropic": expected}}
    probe.assert_not_awaited()
    builder.assert_not_called()


@pytest.mark.parametrize("ok", [True, False])
def test_health_anthropic_returns_mocked_probe(monkeypatch, ok):
    result = {"ok": ok, "provider": "anthropic", "model": "claude-sonnet-5"}
    result.update({"reply": "pong"} if ok else {"error": "Claude check failed (AuthenticationError); check API key, model name and timeout"})
    probe = AsyncMock(return_value=result)
    monkeypatch.setattr(llm_module, "check_anthropic_connection", probe)
    response = client.get("/health/anthropic")
    assert response.status_code == 200
    assert response.json() == result
    probe.assert_awaited_once_with()


def test_old_ollama_health_endpoint_is_absent(monkeypatch):
    probe = AsyncMock()
    monkeypatch.setattr(llm_module, "check_anthropic_connection", probe)
    assert client.get("/health/ollama").status_code == 404
    assert "/health/ollama" not in app.openapi()["paths"]
    assert "/health/anthropic" in app.openapi()["paths"]
    probe.assert_not_awaited()


@pytest.mark.parametrize("path", ["/health", "/health/live"])
def test_liveness_never_probes_anthropic(monkeypatch, path):
    probe = AsyncMock(side_effect=AssertionError("liveness must not invoke Anthropic"))
    monkeypatch.setattr(llm_module, "check_anthropic_connection", probe)
    assert client.get(path).status_code == 200
    probe.assert_not_awaited()


def test_root():
    """Test root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    assert "name" in response.json()
