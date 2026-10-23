"""Ollama auto-discovery on a DHCP network (no real network I/O)."""

import pytest

from app.agents import llm as llm_module
from app.agents import ollama_discovery
from app.agents.ollama_discovery import OllamaLocator, has_model, scan_subnets
from app.config import settings


@pytest.fixture(autouse=True)
def discovery_settings(monkeypatch):
    for name, value in {
        "ollama_base_url": "",
        "ollama_model": "qwen3.8:latest",
        "ollama_discovery_enabled": True,
        "ollama_discovery_scan": True,
        "ollama_discovery_subnets": "192.168.4",
        "easyworship_remote_host": None,
        "easyworship_agent_url": None,
    }.items():
        monkeypatch.setattr(settings, name, value)


def _locator(servers, *, driver_host=None, mdns_host=None, open_ports=()):
    """``servers`` maps host -> installed model tags for hosts running Ollama."""
    calls = {"probe": [], "scan": [], "mdns": 0}

    async def probe(host):
        calls["probe"].append(host)
        return servers.get(host)

    async def mdns():
        calls["mdns"] += 1
        return mdns_host

    async def scan(subnets, skip):
        calls["scan"].append((list(subnets), set(skip)))
        return [h for h in open_ports if h not in skip]

    return OllamaLocator(probe=probe, driver_host=lambda: driver_host, mdns=mdns, scan=scan), calls


def test_has_model_normalizes_latest_tag():
    assert has_model(["qwen3.8:latest"], "qwen3.8")
    assert has_model(["Qwen3.8"], "qwen3.8:latest")
    assert not has_model(["llama3:8b"], "qwen3.8:latest")
    assert not has_model(["qwen3.8:latest"], "")


def test_configured_base_url_disables_discovery(monkeypatch):
    monkeypatch.setattr(settings, "ollama_base_url", "http://10.0.0.5:11434/")
    locator, _ = _locator({})
    assert locator.active() is False
    assert locator.base_url() == "http://10.0.0.5:11434"


async def test_configured_base_url_never_searches(monkeypatch):
    monkeypatch.setattr(settings, "ollama_base_url", "http://10.0.0.5:11434")
    locator, calls = _locator({"127.0.0.1": ["qwen3.8:latest"]})
    assert await locator.resolve() is None
    assert calls["probe"] == []


async def test_uses_easyworship_pc_found_by_remote_driver():
    locator, calls = _locator({"192.168.4.37": ["qwen3.8:latest"]}, driver_host="192.168.4.37")
    assert await locator.resolve() == "192.168.4.37"
    assert locator.base_url() == "http://192.168.4.37:11434"
    assert locator.status()["source"] == "easyworship_remote"
    assert calls["scan"] == [] and calls["mdns"] == 0


async def test_stale_configured_host_falls_back_to_mdns(monkeypatch):
    monkeypatch.setattr(settings, "easyworship_remote_host", "192.168.4.20")
    locator, _ = _locator({"192.168.4.51": ["qwen3.8:latest"]}, mdns_host="192.168.4.51")
    assert await locator.resolve() == "192.168.4.51"
    assert locator.status()["source"] == "easyworship_mdns"


async def test_scan_finds_ollama_and_prefers_host_with_model():
    servers = {"192.168.4.10": ["llama3:8b"], "192.168.4.88": ["qwen3.8:latest"]}
    locator, calls = _locator(servers, open_ports=["192.168.4.10", "192.168.4.88"])
    assert await locator.resolve() == "192.168.4.88"
    assert locator.status()["source"] == "scan"
    subnets, skipped = calls["scan"][0]
    assert subnets == ["192.168.4"] and "127.0.0.1" in skipped


async def test_ollama_without_model_is_used_when_nothing_better():
    locator, _ = _locator({"192.168.4.10": ["llama3:8b"]}, open_ports=["192.168.4.10"])
    assert await locator.resolve() == "192.168.4.10"


async def test_scan_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ollama_discovery_scan", False)
    locator, calls = _locator({"192.168.4.88": ["qwen3.8:latest"]}, open_ports=["192.168.4.88"])
    assert await locator.resolve() is None
    assert calls["scan"] == []


async def test_dhcp_change_rediscovers_and_resets_llm_clients(monkeypatch):
    servers = {"192.168.4.37": ["qwen3.8:latest"]}
    locator, calls = _locator(servers, open_ports=["192.168.4.37"])
    assert await locator.resolve() == "192.168.4.37"

    # Cached host still answers: no new search.
    calls["scan"].clear()
    assert await locator.resolve() == "192.168.4.37"
    assert calls["scan"] == []

    cleared = []
    monkeypatch.setattr(ollama_discovery, "_reset_llm_clients", lambda: cleared.append(True))
    servers.pop("192.168.4.37")
    servers["192.168.4.61"] = ["qwen3.8:latest"]
    locator._scan = lambda subnets, skip: _async(["192.168.4.61"])
    assert await locator.resolve() == "192.168.4.61"
    assert cleared == [True]


async def _async(value):
    return value


async def test_not_found_keeps_last_known_host():
    servers = {"192.168.4.37": ["qwen3.8:latest"]}
    locator, _ = _locator(servers, open_ports=["192.168.4.37"])
    await locator.resolve()
    servers.clear()
    assert await locator.resolve() == "192.168.4.37"


def test_base_url_falls_back_to_easyworship_config_then_localhost(monkeypatch):
    locator, _ = _locator({})
    assert locator.base_url() == "http://127.0.0.1:11434"
    monkeypatch.setattr(settings, "easyworship_agent_url", "http://192.168.4.41:8091")
    assert locator.base_url() == "http://192.168.4.41:11434"


def test_llm_base_url_uses_discovered_host(monkeypatch):
    locator, _ = _locator({})
    locator._host = "192.168.4.99"
    monkeypatch.setattr(llm_module, "ollama_locator", locator)
    assert llm_module.ollama_base_url() == "http://192.168.4.99:11434"


def test_scan_subnets_configured_or_local(monkeypatch):
    monkeypatch.setattr(settings, "ollama_discovery_subnets", "192.168.4, 10.1.2")
    assert scan_subnets() == ["192.168.4", "10.1.2"]
    monkeypatch.setattr(settings, "ollama_discovery_subnets", "")
    monkeypatch.setattr(ollama_discovery, "_primary_ipv4", lambda: "192.168.30.12")
    assert scan_subnets() == ["192.168.30"]
    monkeypatch.setattr(ollama_discovery, "_primary_ipv4", lambda: "8.8.8.8")
    assert scan_subnets() == []


async def test_failed_llm_call_wakes_discovery(monkeypatch):
    import httpx

    locator, _ = _locator({})
    monkeypatch.setattr(llm_module, "ollama_locator", locator)

    class _Down:
        async def ainvoke(self, messages):
            raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        await llm_module.invoke_llm(_Down(), [])
    assert locator._wake.is_set()
