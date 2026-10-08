import pytest

from app.atem.resolver import AtemResolver
from app.atem.service import AtemService
from app.config import settings


@pytest.fixture
def resolver(monkeypatch):
    monkeypatch.setattr(settings, "atem_resolve_enabled", True)
    monkeypatch.setattr(settings, "atem_resolve_subnets", "192.168.4")
    monkeypatch.setattr(settings, "atem_resolve_oui", "7c:2e:0d")
    monkeypatch.setattr(settings, "atem_resolve_mac", "")
    return AtemResolver()


def test_prefers_configured_ip_without_scan(resolver, monkeypatch):
    monkeypatch.setattr(settings, "atem_ip", "192.168.4.22")
    scanned = []

    async def fake_scan():
        scanned.append(True)
        return None

    monkeypatch.setattr(resolver, "_scan", fake_scan)
    assert __import__("asyncio").run(resolver.resolve()) == "192.168.4.22"
    assert scanned == []


def test_scans_and_caches_on_force(resolver, monkeypatch):
    monkeypatch.setattr(settings, "atem_ip", "")

    async def fake_scan():
        return "192.168.4.55"

    monkeypatch.setattr(resolver, "_scan", fake_scan)
    import asyncio

    assert asyncio.run(resolver.resolve()) == "192.168.4.55"
    # Cached: a second resolve without force does not rescan.
    async def fail_scan():
        raise AssertionError("should not rescan")

    monkeypatch.setattr(resolver, "_scan", fail_scan)
    assert asyncio.run(resolver.resolve()) == "192.168.4.55"


def test_mac_matching_prefix_and_exact(resolver, monkeypatch):
    assert resolver._matches("7C-2E-0D-AA-BB-CC")
    assert not resolver._matches("f8:b4:6a:aa:bb:cc")
    monkeypatch.setattr(settings, "atem_resolve_mac", "7c:2e:0d:11:22:33")
    assert resolver._matches("7C-2E-0D-11-22-33")
    assert not resolver._matches("7C-2E-0D-AA-BB-CC")


async def test_connect_rescans_when_configured_ip_fails(monkeypatch):
    monkeypatch.setattr(settings, "atem_auto_detect", False)
    monkeypatch.setattr(settings, "enable_mock_atem", False)
    monkeypatch.setattr(settings, "atem_ip", "192.168.4.22")

    service = AtemService()
    service.auto_detect = False

    seen = []

    class FakeResponse:
        def __init__(self, ok):
            self._ok = ok

        def json(self):
            return {"ok": self._ok}

    async def fake_resolve(force=False):
        return "192.168.4.99" if force else "192.168.4.22"

    async def fake_post(url, json=None):
        ip = json["atem_ip"]
        seen.append(ip)
        return FakeResponse(ok=ip == "192.168.4.99")

    async def fake_get_state():
        return None

    monkeypatch.setattr("app.atem.service.atem_resolver.resolve", fake_resolve)
    monkeypatch.setattr(service._client, "post", fake_post)
    monkeypatch.setattr(service, "get_state", fake_get_state)

    assert await service.connect() is True
    assert seen == ["192.168.4.22", "192.168.4.99"]
