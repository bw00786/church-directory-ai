"""Tests for ATEM auto-detect (real bridge vs mock fallback)."""

import pytest

from app.atem.service import AtemService
from app.config import settings


@pytest.mark.asyncio
async def test_auto_detect_falls_back_to_mock_when_bridge_unreachable(monkeypatch):
    """With auto-detect on and the real bridge unreachable, connect() should use mock."""
    monkeypatch.setattr(settings, "atem_auto_detect", True)
    monkeypatch.setattr(settings, "enable_mock_atem", False)

    service = AtemService()
    assert service.auto_detect is True

    async def fake_get(url, timeout=None):
        raise ConnectionError("no bridge listening")

    monkeypatch.setattr(service._client, "get", fake_get)

    connected = await service.connect()

    assert connected is True
    assert service.mock is True


@pytest.mark.asyncio
async def test_auto_detect_uses_real_bridge_when_reachable(monkeypatch):
    """With auto-detect on and the real bridge reachable, connect() should not use mock."""
    monkeypatch.setattr(settings, "atem_auto_detect", True)
    monkeypatch.setattr(settings, "enable_mock_atem", True)

    service = AtemService()

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    async def fake_get(url, timeout=None):
        return FakeResponse()

    async def fake_post(url, json=None):
        return FakeResponse()

    monkeypatch.setattr(service._client, "get", fake_get)
    monkeypatch.setattr(service._client, "post", fake_post)

    connected = await service.connect()

    assert connected is True
    assert service.mock is False


def test_explicit_mock_flag_disables_auto_detect(monkeypatch):
    """AtemService(mock=True) should always force mock regardless of auto-detect."""
    monkeypatch.setattr(settings, "atem_auto_detect", True)

    service = AtemService(mock=True)

    assert service.auto_detect is False
    assert service.mock is True
