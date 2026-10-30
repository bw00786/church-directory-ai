"""VISCA-over-IP transport hardening: disconnect detection and CGI fallback,
bounded reconnect backoff, UDP sequence matching, ACK vs completion timeouts,
and no duplicate CGI preset recall once VISCA has ACKed."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from app.cameras import visca as visca_module
from app.cameras.ptz import PTZOpticsDriver
from app.cameras.visca import ViscaOverIPClient
from app.config import settings

ACK = bytes([0x90, 0x41, 0xFF])
COMPLETION = bytes([0x90, 0x51, 0xFF])
ERROR_NOT_EXECUTABLE = bytes([0x90, 0x60, 0x41, 0xFF])


class FakeCamera:
    """Scripted VISCA camera behind an asyncio.StreamReader + fake StreamWriter.

    ``mode`` selects the reply: ``normal`` (ACK then completion after
    ``completion_delay``), ``silent`` (never answers), ``error`` (VISCA error).
    """

    def __init__(self):
        self.reader = asyncio.StreamReader()
        self.sent: list[bytes] = []
        self.mode = "normal"
        self.completion_delay = 0.0
        self.pan_tilt = (0x0120, 0xFF80)  # pan +288 units, tilt -128 units
        self.closed = False
        self.eof = False
        self._tasks: set[asyncio.Task] = set()

    # -- StreamWriter interface ---------------------------------------------
    def write(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        task = asyncio.get_running_loop().create_task(self._reply(bytes(data)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass

    def drop(self) -> None:
        """Simulate the camera closing the socket (EOF)."""
        self.eof = True
        self.reader.feed_eof()

    def is_closing(self) -> bool:
        return self.closed

    # -- camera behaviour ---------------------------------------------------
    async def _reply(self, payload: bytes) -> None:
        if self.mode == "silent" or self.eof:
            return
        if self.mode == "error":
            self.reader.feed_data(ERROR_NOT_EXECUTABLE)
            return
        if payload[1] == 0x09:  # inquiry
            if payload[2:4] == b"\x06\x12":
                pan, tilt = self.pan_tilt
                data = visca_module._split_nibbles_16(pan) + visca_module._split_nibbles_16(tilt)
            else:
                data = visca_module._split_nibbles_16(0x2000)
            self.reader.feed_data(bytes([0x90, 0x50]) + data + b"\xff")
            return
        self.reader.feed_data(ACK)
        if self.completion_delay:
            await asyncio.sleep(self.completion_delay)
        if not self.eof:
            self.reader.feed_data(COMPLETION)

    def commands(self) -> list[bytes]:
        """Command bodies sent so far (address and terminator stripped)."""
        return [p[1:-1] for p in self.sent]


def make_client(camera: FakeCamera, outcomes=None, **kwargs) -> ViscaOverIPClient:
    """Client wired to ``camera``. ``outcomes`` scripts connect attempts
    (True = succeed, False = fail); afterwards attempts fail."""
    options = dict(ack_timeout=0.05, completion_timeout=0.2, reconnect_initial=0.01, reconnect_max=0.04)
    options.update(kwargs)
    client = ViscaOverIPClient("camera.test", 1240, **options)
    script = iter(outcomes if outcomes is not None else [True])
    client.connect_log: list = []  # type: ignore[attr-defined]

    async def open_connection(host, port):
        ok = next(script, False)
        client.connect_log.append((ok, client._backoff))  # type: ignore[attr-defined]
        if not ok:
            raise ConnectionRefusedError("camera offline")
        return camera.reader, camera

    client._open_connection = open_connection
    return client


def make_driver(client: ViscaOverIPClient) -> tuple[PTZOpticsDriver, AsyncMock]:
    driver = PTZOpticsDriver()
    driver._host = "camera.test"
    driver._client = object()  # non-None: CGI path is "available"
    driver._visca = client
    driver._connected = True
    cgi = AsyncMock(return_value=True)
    driver._cgi = cgi  # type: ignore[method-assign]
    return driver, cgi


async def until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.005)


# -- disconnect detection -> CGI fallback -------------------------------------

async def test_eof_marks_disconnected_and_joystick_falls_back_to_cgi():
    camera = FakeCamera()
    client = make_client(camera, outcomes=[True])  # reconnects keep failing
    assert await client.connect()
    assert client.connected

    driver, cgi = make_driver(client)
    assert await driver.drive(1, 0) is True  # VISCA path while healthy
    cgi.assert_not_called()

    camera.drop()  # the camera dropped the socket
    assert await client.pan_tilt_drive(1, 0) is False
    assert client.connected is False
    assert client.last_error == "EOF from camera"

    # Before the fix ``connected`` was "writer is not None" and this stayed on
    # the dead VISCA socket until restart; now the joystick keeps working.
    assert await driver.drive(1, 0) is True
    cgi.assert_awaited_with("ptzcmd", "right", 12, 12)
    assert await driver.zoom_drive(1, 3) is True
    assert await driver.stop() is True
    assert [c.args[1] for c in cgi.await_args_list] == ["right", "zoomin", "ptzstop", "zoomstop"]
    assert driver.health()["http_cgi"]["fallbacks"] >= 3
    await client.close()


async def test_consecutive_ack_timeouts_mark_disconnected():
    camera = FakeCamera()
    camera.mode = "silent"
    client = make_client(camera, outcomes=[True], max_consecutive_timeouts=2)
    assert await client.connect()

    assert await client.pan_tilt_drive(1, 0) is False
    assert client.connected is True, "one timeout is not yet a dead link"
    assert await client.pan_tilt_drive(1, 0) is False
    assert client.connected is False
    assert client.reconnecting is True
    assert "timeouts" in client.last_error
    assert client.health()["consecutive_timeouts"] == 2
    await client.close()


async def test_send_failure_marks_disconnected():
    camera = FakeCamera()
    client = make_client(camera, outcomes=[True])
    assert await client.connect()

    async def failing_drain():
        raise BrokenPipeError("pipe")

    camera.drain = failing_drain  # type: ignore[method-assign]
    assert await client.pan_tilt_stop() is False
    assert client.connected is False
    await client.close()


# -- reconnect with bounded exponential backoff -------------------------------

async def test_reconnect_backoff_doubles_bounded_and_resets():
    camera = FakeCamera()
    # initial connect, then two failed retries, then success
    client = make_client(camera, outcomes=[True, False, False, True])
    assert await client.connect()
    camera.drop()
    assert await client.pan_tilt_drive(1, 0) is False
    assert not client.connected and client.reconnecting

    await until(lambda: client.connected)
    attempts = client.connect_log  # type: ignore[attr-defined]
    assert [ok for ok, _ in attempts] == [True, False, False, True]
    # Backoff in force at each retry: 0.01 -> 0.02 -> 0.04 (capped at reconnect_max)
    assert [round(b, 3) for _, b in attempts[1:]] == [0.01, 0.02, 0.04]
    assert client.reconnect_count == 1
    assert client.connect_attempts == 4
    assert client._backoff == client.reconnect_initial, "backoff resets after success"
    assert client.reconnecting is False

    # The restored link is usable again (fresh reader after EOF).
    camera.reader = asyncio.StreamReader()
    camera.eof = False
    client._reader = camera.reader
    assert await client.pan_tilt_drive(0, 1) is True
    await client.close()


async def test_backoff_is_capped_and_close_stops_retrying():
    camera = FakeCamera()
    client = make_client(camera, outcomes=[True])  # every retry fails
    assert await client.connect()
    camera.drop()
    await client.get_zoom()
    assert not client.connected
    await until(lambda: client.connect_attempts >= 5)
    assert client._backoff <= client.reconnect_max
    assert client.health()["reconnecting"] is True
    await client.close()
    attempts = client.connect_attempts
    await asyncio.sleep(0.1)
    assert client.connect_attempts == attempts, "no retries after close()"
    assert client.schedule_reconnect() is False


async def test_visca_ready_kicks_reconnect_when_initial_connect_failed():
    camera = FakeCamera()
    client = make_client(camera, outcomes=[False, True])
    assert await client.connect() is False
    driver, cgi = make_driver(client)
    assert driver._visca_ready() is False
    assert client.reconnecting is True
    await until(lambda: client.connected)
    assert driver._visca_ready() is True
    await client.close()


# -- UDP sequence matching ----------------------------------------------------

class FakeDatagramTransport:
    def __init__(self, queue: asyncio.Queue, script):
        self.queue = queue
        self.script = script  # callable(seq, payload) -> list[bytes] datagrams to deliver
        self.sent: list[bytes] = []

    def sendto(self, data: bytes, addr=None) -> None:
        self.sent.append(bytes(data))
        seq = int.from_bytes(data[4:8], "big")
        for datagram in self.script(seq, data[8:]):
            self.queue.put_nowait(datagram)

    def close(self) -> None:
        pass


def udp_reply(seq: int, payload: bytes, ptype: bytes = b"\x01\x11") -> bytes:
    return ptype + len(payload).to_bytes(2, "big") + seq.to_bytes(4, "big") + payload


def make_udp_client(script) -> tuple[ViscaOverIPClient, FakeDatagramTransport]:
    client = ViscaOverIPClient("camera.test", 52381, use_udp=True, ack_timeout=0.05, completion_timeout=0.2)
    queue: asyncio.Queue = asyncio.Queue()
    transport = FakeDatagramTransport(queue, script)
    client._udp_transport = transport
    client._udp_queue = queue
    client._connected = True
    client._seq = 41
    return client, transport


async def test_udp_replies_are_matched_to_request_sequence():
    def script(seq, payload):
        stale = visca_module._split_nibbles_16(0x0001) + visca_module._split_nibbles_16(0x0001)
        good = visca_module._split_nibbles_16(0x0120) + visca_module._split_nibbles_16(0xFF80)
        return [
            udp_reply(seq - 1, bytes([0x90, 0x50]) + stale + b"\xff"),  # late answer to a previous inquiry
            udp_reply(seq, b"\x01", ptype=b"\x02\x01"),  # control reply: ignored
            b"\x01\x11\x00",  # runt: ignored
            udp_reply(seq, bytes([0x90, 0x50]) + good + b"\xff"),
        ]

    client, transport = make_udp_client(script)
    assert await client.get_pan_tilt() == (0x0120, -128)
    assert int.from_bytes(transport.sent[0][4:8], "big") == 41
    assert transport.sent[0][:2] == b"\x01\x10"  # inquiry payload type
    assert client._seq == 42


async def test_udp_command_sequence_and_stale_ack_discarded():
    def script(seq, payload):
        return [
            udp_reply(seq + 7, ACK),  # foreign sequence: must not be taken as our ACK
            udp_reply(seq, ACK),
            udp_reply(seq, COMPLETION),
        ]

    client, transport = make_udp_client(script)
    assert await client.pan_tilt_drive(-1, 1, 10, 10) is True
    assert transport.sent[0][:2] == b"\x01\x00"
    assert transport.sent[0][8:] == bytes([0x81, 0x01, 0x06, 0x01, 10, 10, 0x01, 0x01, 0xFF])


async def test_udp_headerless_raw_visca_reply_is_accepted():
    """PTZOptics raw-UDP mode answers without the Sony header."""
    client, _ = make_udp_client(lambda seq, payload: [ACK, COMPLETION])
    assert await client.zoom_drive(1, 2) is True


async def test_udp_sequence_mismatch_only_is_a_timeout_not_a_disconnect():
    client, _ = make_udp_client(lambda seq, payload: [udp_reply(seq + 1, ACK), udp_reply(seq + 1, COMPLETION)])
    assert await client.pan_tilt_stop() is False
    assert client.connected is True
    assert client.last_error == "ACK timeout"


# -- ACK vs completion timeouts ------------------------------------------------

async def test_ack_timeout_is_short_and_completion_timeout_is_long():
    camera = FakeCamera()
    client = make_client(camera, ack_timeout=0.05, completion_timeout=0.4)
    assert await client.connect()
    assert client.ack_timeout == 0.05 and client.completion_timeout == 0.4

    camera.completion_delay = 0.25  # a long preset move: completion well after the ACK window
    start = time.monotonic()
    assert await client.preset_recall(3) is True
    elapsed = time.monotonic() - start
    assert 0.2 <= elapsed < 0.4, "waited for the real completion, not the ACK timeout"
    assert client._stale_completions == 0
    assert client.connected
    await client.close()


async def test_slow_completion_after_ack_is_success_and_orphan_is_discarded():
    camera = FakeCamera()
    client = make_client(camera, ack_timeout=0.05, completion_timeout=0.1)
    assert await client.connect()

    camera.completion_delay = 0.25  # longer than completion_timeout
    assert await client.preset_recall(5) is True, "ACKed commands are not failures"
    assert client.connected is True
    assert client._stale_completions == 1
    assert client.health()["last_error"] == "completion timeout (command ACKed)"

    await asyncio.sleep(0.3)  # the orphaned completion lands in the buffer
    camera.completion_delay = 0.0
    assert await client.pan_tilt_drive(1, 0) is True
    assert client._stale_completions == 0
    assert await client.get_pan_tilt() == (0x0120, -128), "stale completion never answers an inquiry"
    assert client.commands_failed == 0
    await client.close()


async def test_preset_recall_can_return_on_ack_without_waiting():
    camera = FakeCamera()
    client = make_client(camera, completion_timeout=1.0)
    assert await client.connect()
    camera.completion_delay = 0.5
    start = time.monotonic()
    assert await client.preset_recall(2, wait_completion=False) is True
    assert time.monotonic() - start < 0.2
    assert client._stale_completions == 1
    await client.close()


async def test_visca_error_reply_fails_command_but_keeps_link():
    camera = FakeCamera()
    camera.mode = "error"
    client = make_client(camera)
    assert await client.connect()
    assert await client.preset_recall(9) is False
    assert client.connected is True
    assert "VISCA error" in client.last_error
    assert client.health()["commands_failed"] == 1
    await client.close()


# -- no duplicate CGI preset recall -------------------------------------------

async def test_no_duplicate_poscall_when_visca_acked():
    camera = FakeCamera()
    client = make_client(camera, completion_timeout=0.05)
    assert await client.connect()
    driver, cgi = make_driver(client)

    camera.completion_delay = 0.2  # long move: completion arrives after the window
    assert await driver.move_to_preset(4) is True
    cgi.assert_not_called()
    assert camera.commands()[-1] == bytes([0x01, 0x04, 0x3F, 0x02, 0x04])

    await asyncio.sleep(0.25)
    camera.mode = "error"  # camera refuses: CGI is the right fallback, exactly once
    assert await driver.move_to_preset(6) is True
    cgi.assert_awaited_once_with("ptzcmd", "poscall", 6)

    cgi.reset_mock()
    camera.mode = "normal"
    camera.drop()
    await client.get_zoom()  # detects the drop
    assert not client.connected
    assert await driver.move_to_preset(1) is True
    cgi.assert_awaited_once_with("ptzcmd", "poscall", 1)
    await client.close()


# -- preset recall speed (optional) -------------------------------------------

async def test_preset_recall_speed_is_off_by_default():
    camera = FakeCamera()
    client = make_client(camera)
    assert client.preset_recall_speed == 0
    assert await client.connect()
    assert await client.preset_recall(1)
    assert camera.commands() == [bytes([0x01, 0x04, 0x3F, 0x02, 0x01])]
    await client.close()


async def test_preset_recall_speed_sent_once_when_configured():
    camera = FakeCamera()
    client = make_client(camera, preset_recall_speed=10)
    assert await client.connect()
    assert await client.preset_recall(1)
    assert await client.preset_recall(2)
    assert camera.commands() == [
        bytes([0x01, 0x06, 0x01, 0x0A]),  # PTZOptics CAM_Memory recall speed, once
        bytes([0x01, 0x04, 0x3F, 0x02, 0x01]),
        bytes([0x01, 0x04, 0x3F, 0x02, 0x02]),
    ]
    assert camera.sent[0] == bytes([0x81, 0x01, 0x06, 0x01, 0x0A, 0xFF])
    await client.close()


async def test_preset_recall_speed_is_clamped():
    camera = FakeCamera()
    client = make_client(camera, preset_recall_speed=99)
    assert await client.connect()
    assert await client.preset_recall(1)
    assert camera.commands()[0] == bytes([0x01, 0x06, 0x01, visca_module.PRESET_SPEED_MAX])
    await client.close()


# -- settings & health ----------------------------------------------------------

def test_timeouts_and_recall_speed_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "visca_completion_timeout_seconds", 3.5)
    monkeypatch.setattr(settings, "visca_ack_timeout_seconds", 0.75)
    monkeypatch.setattr(settings, "visca_preset_recall_speed", 12)
    client = ViscaOverIPClient("camera.test")
    assert client.completion_timeout == 3.5
    assert client.ack_timeout == 0.75
    assert client.preset_recall_speed == 12


def test_defaults_without_settings(monkeypatch):
    for name in ("visca_completion_timeout_seconds", "visca_ack_timeout_seconds", "visca_preset_recall_speed"):
        monkeypatch.delattr(type(settings), name, raising=False)
    client = ViscaOverIPClient("camera.test", timeout=1.0)
    assert client.ack_timeout == 1.0  # legacy ``timeout`` maps to the ACK timeout
    assert client.completion_timeout == visca_module.DEFAULT_COMPLETION_TIMEOUT
    assert client.preset_recall_speed == 0


async def test_health_reports_error_and_reconnects():
    camera = FakeCamera()
    client = make_client(camera, outcomes=[True, True])
    health = client.health()
    assert health["connected"] is False and health["transport"] == "tcp"
    assert health["reconnect_count"] == 0 and health["last_error"] is None

    assert await client.connect()
    assert await client.pan_tilt_drive(1, 0)
    camera.drop()
    assert await client.pan_tilt_drive(1, 0) is False
    health = client.health()
    assert health["connected"] is False
    assert health["last_error"] == "EOF from camera"
    assert health["last_error_age"] is not None
    assert health["commands_ok"] == 1 and health["commands_failed"] == 1

    await until(lambda: client.connected)
    health = client.health()
    assert health["reconnect_count"] == 1 and health["reconnecting"] is False
    assert health["ack_timeout"] == 0.05 and health["completion_timeout"] == 0.2
    await client.close()


async def test_driver_status_exposes_control_path_and_health():
    camera = FakeCamera()
    client = make_client(camera, outcomes=[True])
    assert await client.connect()
    driver, cgi = make_driver(client)
    status = await driver.get_status()
    assert status["control_path"] == "visca"
    assert status["pan"] == 20.0 and status["tilt"] == -8.89
    assert status["health"]["visca"]["connected"] is True

    camera.drop()
    await client.get_zoom()
    status = await driver.get_status()
    assert status["control_path"] == "cgi"
    assert status["health"]["visca"]["connected"] is False
    assert status["health"]["http_cgi"]["fallbacks"] >= 1
    await client.close()
