"""Tests for the Anthropic Claude client factory."""

import pytest

from app.agents import llm as llm_module
from app.config import settings


def test_build_llm_raises_without_api_key(monkeypatch):
    """build_llm should fail fast with a clear error when no key is configured."""
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        llm_module.build_llm()


@pytest.mark.asyncio
async def test_check_anthropic_connection_without_api_key(monkeypatch):
    """The connectivity check should report ok=False instead of raising when unconfigured."""
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    result = await llm_module.check_anthropic_connection()
    assert result["ok"] is False
    assert "ANTHROPIC_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_check_anthropic_connection_success(monkeypatch):
    """A working client should report ok=True with the reply text."""

    class FakeResponse:
        content = "pong"

    class FakeLLM:
        async def ainvoke(self, messages):
            return FakeResponse()

    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(llm_module, "build_llm", lambda **kwargs: FakeLLM())

    result = await llm_module.check_anthropic_connection()
    assert result["ok"] is True
    assert result["reply"] == "pong"
