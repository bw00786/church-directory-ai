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


@pytest.mark.parametrize("model,expected", [("qwen3.8:latest", "configured"), ("", "not configured")])
def test_health_ready(monkeypatch, model, expected):
    """Readiness reports configuration, without inference or legacy keys."""
    monkeypatch.setattr(settings, "ollama_model", model)
    probe = AsyncMock(side_effect=AssertionError("readiness must not probe Ollama"))
    builder = Mock(side_effect=AssertionError("readiness must not build a model"))
    monkeypatch.setattr(llm_module, "check_ollama_connection", probe)
    monkeypatch.setattr(llm_module, "build_llm", builder)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"api": "ok", "ollama": expected}}
    probe.assert_not_awaited()
    builder.assert_not_called()


@pytest.mark.parametrize("ok", [True, False])
def test_health_ollama_returns_mocked_probe(monkeypatch, ok):
    result = {"ok": ok, "provider": "ollama", "model": "qwen3.8:latest"}
    result.update({"reply": "pong", "capabilities": ["tools", "vision"]} if ok else {"error": "Ollama check failed (ConnectError); check server, model tag and timeout"})
    probe = AsyncMock(return_value=result)
    monkeypatch.setattr(llm_module, "check_ollama_connection", probe)
    response = client.get("/health/ollama")
    assert response.status_code == 200
    assert response.json() == result
    probe.assert_awaited_once_with()


def test_old_anthropic_health_endpoint_is_absent(monkeypatch):
    probe = AsyncMock()
    monkeypatch.setattr(llm_module, "check_ollama_connection", probe)
    assert client.get("/health/anthropic").status_code == 404
    assert "/health/anthropic" not in app.openapi()["paths"]
    assert "/health/ollama" in app.openapi()["paths"]
    probe.assert_not_awaited()


@pytest.mark.parametrize("path", ["/health", "/health/live"])
def test_liveness_never_probes_ollama(monkeypatch, path):
    probe = AsyncMock(side_effect=AssertionError("liveness must not invoke Ollama"))
    monkeypatch.setattr(llm_module, "check_ollama_connection", probe)
    assert client.get(path).status_code == 200
    probe.assert_not_awaited()


def test_root():
    """Test root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    assert "name" in response.json()
