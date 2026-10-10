import asyncio

import httpx
import pytest

from app.atem.resolver import atem_resolver
from app.atem.service import AtemService
from app.config import settings


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setattr(settings, "atem_auto_detect", True)
    monkeypatch.setattr(settings, "enable_mock_atem", False)
    monkeypatch.setattr(settings, "atem_ip", "192.168.4.39")
    state = {"connected": False, "available": True, "ip": "192.168.4.39"}
    requests = []

    async def resolve(*, force=False):
        return state["ip"] if force else "192.168.4.39"

    monkeypatch.setattr(atem_resolver, "resolve", resolve)

    def handle(request):
        requests.append(request)
        if not state["available"]:
            raise httpx.ConnectError("bridge offline", request=request)
        if request.url.path == "/status":
            return httpx.Response(200, json={"connected": state["connected"], "inputs": []})
        if request.url.path == "/connect":
            import json

            state["connected"] = json.loads(request.content)["atem_ip"] == state["ip"]
            return httpx.Response(200, json={"ok": state["connected"]})
        if request.url.path == "/disconnect":
            state["connected"] = False
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"Unexpected hardware action: {request.url.path}")

    service = AtemService()
    service._monitor_interval = 0.01
    service._retry_max_seconds = 0.04
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return service, state, requests


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait(), 2)


async def test_power_cycle_reconnects_without_output_commands(bridge):
    service, state, requests = bridge
    try:
        await service.start()
        await until(lambda: state["connected"])
        state["connected"] = False
        await until(lambda: state["connected"])
        assert await service.is_connected()
        assert sum(request.url.path == "/connect" for request in requests) == 2
    finally:
        await service.stop()


async def test_changed_ip_rescans_and_reconnects(bridge):
    service, state, requests = bridge
    try:
        await service.start()
        await until(lambda: state["connected"])
        state.update(connected=False, ip="192.168.4.40")
        await until(lambda: state["connected"])
        assert service._last_ip == "192.168.4.40"
    finally:
        await service.stop()


async def test_bridge_outage_never_falls_back_to_mock_after_real_connection(bridge):
    service, state, requests = bridge
    try:
        await service.start()
        await until(lambda: state["connected"])
        state.update(connected=False, available=False)
        await until(lambda: not service._connected)
        assert not service.mock
        state["available"] = True
        await until(lambda: state["connected"])
    finally:
        await service.stop()


async def test_manual_disconnect_stops_retry_and_connect_resumes_it(bridge):
    service, state, requests = bridge
    try:
        await service.start()
        await until(lambda: state["connected"])
        assert await service.disconnect()
        assert service._monitor_task is None
        assert not service._reconnect_enabled
        assert not await service.is_connected()
        assert await service.connect()
        assert service._monitor_task is not None
    finally:
        await service.stop()


async def test_stop_cancels_monitor_and_closes_client(bridge):
    service, state, requests = bridge
    await service.start()
    task = service._monitor_task
    await service.stop()
    assert task.done()
    assert service._client.is_closed


async def test_status_updates_cached_connected_flag(bridge):
    service, state, requests = bridge
    try:
        assert await service.connect()
        state["connected"] = False
        assert not (await service.get_state()).connected
        assert not await service.is_connected()
    finally:
        await service.stop()