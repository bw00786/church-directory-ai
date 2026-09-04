"""Tests for the EasyWorship 7.3+ remote-protocol driver and the service's
state-confirmed navigation, against a fake EasyWorship TCP server."""

import asyncio
import json
from typing import Optional

import pytest

from app.config import settings
from app.easyworship.keys import parse_key_sequence
from app.easyworship.remote_protocol import EWState, RemoteProtocolDriver
from app.easyworship.service import EasyWorshipService


class FakeEasyWorship:
    """Minimal stand-in for EasyWorship's remote-control server.

    Speaks \\r\\n-delimited JSON, pairs on ``connect``, tracks pres_no/slide_no
    and pushes a ``status`` after every navigation command (unless
    ``push_status`` is False, to simulate a command that never took effect).
    """

    def __init__(self, *, pair: bool = True, push_status: bool = True, slides_per_item: int = 3):
        self.pair = pair
        self.push_status = push_status
        self.slides_per_item = slides_per_item
        self.pres_no = 1
        self.slide_no = 1
        self.logo = False
        self.black = False
        self.clear = False
        self.requestrev = 100
        self.received: list[dict] = []
        self.connections = 0
        self._server: Optional[asyncio.AbstractServer] = None
        self._writers: list[asyncio.StreamWriter] = []

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for w in self._writers:
            w.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def drop_clients(self) -> None:
        for w in self._writers:
            w.close()
            try:
                await w.wait_closed()
            except Exception:
                pass
        self._writers.clear()

    def _status(self) -> dict:
        self.requestrev += 1
        return {
            "action": "status",
            "logo": self.logo,
            "black": self.black,
            "clear": self.clear,
            "rectype": 1,
            "pres_rowid": 1000 + self.pres_no,
            "slide_rowid": 5000 + self.slide_no,
            "pres_no": self.pres_no,
            "slide_no": self.slide_no,
            "schedulerev": "7",
            "liverev": str(self.requestrev),
            "imagehash": "abc",
            "permissions": 1,
            "requestrev": str(self.requestrev),
        }

    async def _send(self, writer: asyncio.StreamWriter, payload: dict) -> None:
        writer.write((json.dumps(payload) + "\r\n").encode("latin-1"))
        await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.append(writer)
        try:
            while True:
                raw = await reader.readuntil(b"\r\n")
                msg = json.loads(raw.decode("latin-1"))
                self.received.append(msg)
                action = msg.get("action", "")
                changed = False
                if action == "connect":
                    if self.pair:
                        await self._send(writer, {"action": "paired", "requestrev": str(self.requestrev)})
                        await self._send(writer, self._status())
                    else:
                        await self._send(writer, {"action": "notPaired", "requestrev": str(self.requestrev)})
                    continue
                if action == "heartbeat":
                    continue
                if action == "nextSlide":
                    if self.slide_no < self.slides_per_item:
                        self.slide_no += 1
                        changed = True
                elif action == "prevSlide":
                    if self.slide_no > 1:
                        self.slide_no -= 1
                        changed = True
                elif action == "nextSchedule":
                    self.pres_no += 1
                elif action == "prevSchedule":
                    self.pres_no = max(1, self.pres_no - 1)
                elif action.startswith("gotoSchedule "):
                    self.pres_no = int(action.split()[1])
                elif action.startswith("gotoSlide "):
                    self.slide_no = int(action.split()[1])
                    changed = True
                elif action == "gotoStartPresentation":
                    self.slide_no = 1
                    changed = True
                elif action == "status":
                    self.logo = bool(msg.get("logo"))
                    self.black = bool(msg.get("black"))
                    self.clear = bool(msg.get("clear"))
                    changed = True
                if changed and self.push_status:
                    await self._send(writer, self._status())
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


def _driver(server: FakeEasyWorship, **kwargs) -> RemoteProtocolDriver:
    return RemoteProtocolDriver(
        "127.0.0.1",
        server.port,
        device_name="pytest",
        uid="00000000-0000-0000-0000-000000000001",
        connect_timeout=2.0,
        pair_timeout=2.0,
        discovery_seconds=0,
        backoff=(0.05,),
        **kwargs,
    )


@pytest.fixture
async def ew():
    server = FakeEasyWorship()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture(autouse=True)
def fast_confirm(monkeypatch):
    monkeypatch.setattr(settings, "easyworship_confirm_actions", True)
    monkeypatch.setattr(settings, "easyworship_confirm_timeout_seconds", 0.5)
    monkeypatch.setattr(settings, "easyworship_schedule_offset", 0)
    monkeypatch.setattr(settings, "slide_verify_enabled", False)


# -- driver ------------------------------------------------------------------

async def test_pairs_and_reads_initial_state(ew):
    driver = _driver(ew)
    try:
        assert await driver.connect() is True
        assert await driver.is_connected() is True
        assert await driver.wait_for(lambda s: s.pres_no == 1 and s.slide_no == 1, 1.0)
        connect_msg = ew.received[0]
        assert connect_msg["action"] == "connect"
        assert connect_msg["device_type"] == 8
        assert connect_msg["uid"] == driver.uid
        assert connect_msg["device_name"] == "pytest"
    finally:
        await driver.disconnect()


async def test_next_slide_sends_protocol_action_and_echoes_requestrev(ew):
    driver = _driver(ew)
    try:
        await driver.connect()
        await driver.wait_for(lambda s: s.slide_no == 1, 1.0)
        rev_before = driver.state.requestrev
        assert await driver.send_action("next_slide")
        assert await driver.wait_for(lambda s: s.slide_no == 2, 1.0)
        sent = [m for m in ew.received if m["action"] == "nextSlide"]
        assert sent and sent[0]["requestrev"] == rev_before
    finally:
        await driver.disconnect()


async def test_next_item_chains_presentation_start(ew):
    driver = _driver(ew)
    try:
        await driver.connect()
        assert await driver.send_action("next_item")
        await asyncio.sleep(0.1)
        actions = [m["action"] for m in ew.received if m["action"] != "heartbeat"]
        assert actions[-2:] == ["nextSchedule", "gotoStartPresentation"]
    finally:
        await driver.disconnect()


async def test_goto_schedule_is_absolute(ew):
    driver = _driver(ew)
    try:
        await driver.connect()
        assert await driver.goto_schedule(4)
        assert await driver.wait_for(lambda s: s.pres_no == 4 and s.slide_no == 1, 1.0)
        assert any(m["action"] == "gotoSchedule 4" for m in ew.received)
    finally:
        await driver.disconnect()


async def test_overlay_toggle_sends_full_status_payload(ew):
    driver = _driver(ew)
    try:
        await driver.connect()
        await driver.wait_for(lambda s: s.status_count >= 1, 1.0)
        assert await driver.send_action("logo")
        assert await driver.wait_for(lambda s: s.logo is True, 1.0)
        payload = [m for m in ew.received if m["action"] == "status"][0]
        for key in ("logo", "black", "clear", "pres_no", "slide_no", "liverev", "requestrev"):
            assert key in payload
        assert payload["logo"] is True and payload["black"] is False

        # Black is mutually exclusive with logo.
        assert await driver.send_action("black")
        assert await driver.wait_for(lambda s: s.black is True and s.logo is False, 1.0)
    finally:
        await driver.disconnect()


async def test_not_paired_drops_commands_and_never_heartbeats():
    server = FakeEasyWorship(pair=False)
    await server.start()
    driver = _driver(server)
    try:
        assert await driver.connect() is False
        assert driver.state.connected is True
        assert driver.state.paired is False
        assert await driver.send_action("next_slide") is False
        await asyncio.sleep(0.05)
        assert not any(m["action"] == "heartbeat" for m in server.received)
        assert not any(m["action"] == "nextSlide" for m in server.received)
    finally:
        await driver.disconnect()
        await server.stop()


async def test_reconnects_after_connection_loss(ew):
    driver = _driver(ew)
    try:
        await driver.connect()
        await ew.drop_clients()
        assert await driver.wait_for(lambda s: not s.connected, 1.0)
        assert await driver.wait_for(lambda s: s.paired, 2.0)
        assert ew.connections == 2
        assert driver.reconnect_count == 1
    finally:
        await driver.disconnect()


async def test_disconnect_stops_supervisor(ew):
    driver = _driver(ew)
    await driver.connect()
    await driver.disconnect()
    connections = ew.connections
    await asyncio.sleep(0.2)
    assert ew.connections == connections
    assert driver.state.paired is False


# -- service -----------------------------------------------------------------

async def test_service_confirms_next_slide(ew):
    driver = _driver(ew)
    svc = EasyWorshipService(driver=driver)
    try:
        await svc.start()
        await driver.wait_for(lambda s: s.slide_no == 1, 1.0)
        assert await svc.next_slide() is True
        assert svc.status()["last_confirmed"] is True
        assert svc.status()["remote_state"]["slide_no"] == 2
    finally:
        await svc.stop()


async def test_service_reports_failure_when_slide_does_not_change():
    server = FakeEasyWorship(slides_per_item=1)  # already on the last slide
    await server.start()
    driver = _driver(server)
    svc = EasyWorshipService(driver=driver)
    try:
        await svc.start()
        await driver.wait_for(lambda s: s.slide_no == 1, 1.0)
        assert await svc.next_slide() is False
        assert svc.status()["last_confirmed"] is False
    finally:
        await svc.stop()
        await server.stop()


async def test_service_select_item_uses_absolute_jump_and_offset(ew, monkeypatch):
    monkeypatch.setattr(settings, "easyworship_schedule_offset", 1)
    driver = _driver(ew)
    svc = EasyWorshipService(driver=driver)
    try:
        await svc.start()
        labels = svc._item_labels()
        target = labels[2]
        assert await svc.select_item(target) is True
        expected_number = 2 + 1 + 1  # index 2, 1-based, plus countdown offset
        assert any(m["action"] == f"gotoSchedule {expected_number}" for m in ew.received)
        assert not any(m["action"] == "nextSchedule" for m in ew.received)
        status = svc.status()
        assert status["current_item_label"] == target
        assert status["current_item_index"] == 2
    finally:
        await svc.stop()


async def test_service_select_item_fails_when_easyworship_never_confirms(ew):
    driver = _driver(ew)
    svc = EasyWorshipService(driver=driver)
    try:
        await svc.start()
        ew.push_status = False  # command lands but no status ever comes back
        labels = svc._item_labels()
        assert await svc.select_item(labels[3]) is False
        assert svc.status()["current_item_label"] is None
    finally:
        await svc.stop()


async def test_service_publishes_state_events(ew):
    from app.events.bus import event_bus

    queue = await event_bus.subscribe()
    driver = _driver(ew)
    svc = EasyWorshipService(driver=driver)
    try:
        await svc.start()
        await driver.wait_for(lambda s: s.status_count >= 1, 1.0)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert any(e.get("event") == "EASYWORSHIP_STATE" for e in events)
    finally:
        await event_bus.unsubscribe(queue)
        await svc.stop()


async def test_service_confirmation_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "easyworship_confirm_actions", False)
    server = FakeEasyWorship(push_status=False)
    await server.start()
    driver = _driver(server)
    svc = EasyWorshipService(driver=driver)
    try:
        await svc.start()
        assert await svc.next_slide() is True
    finally:
        await svc.stop()
        await server.stop()


# -- keystroke fallback ---------------------------------------------------------

def test_key_sequence_parsing():
    seq = parse_key_sequence("right,pagedown")
    assert [vk for _, vk in seq] == [0x27, 0x22]
    mods, vk = parse_key_sequence("ctrl+b")[0]
    assert mods == [0x11] and vk == ord("B")
    with pytest.raises(ValueError):
        parse_key_sequence(" , ")


def test_default_keys_match_easyworship_7_hotkeys():
    from app.easyworship.driver import key_spec_for

    assert key_spec_for("next_slide") == "down"
    assert key_spec_for("live") == "pagedown"
    assert key_spec_for("next_item") == "right,pagedown"
    assert key_spec_for("black") == "ctrl+b"


def test_state_as_dict_roundtrip():
    s = EWState(paired=True, pres_no=3, slide_no=2)
    d = s.as_dict()
    assert d["paired"] is True and d["pres_no"] == 3 and d["slide_no"] == 2
