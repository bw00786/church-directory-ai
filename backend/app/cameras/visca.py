"""VISCA-over-IP client for PTZOptics cameras.

Implements the subset of the VISCA protocol used to drive PTZOptics PT-series
(G3) cameras over IP. Supports both a raw-VISCA TCP transport (PTZOptics
"VISCA over IP" TCP, port 1240) and the Sony-style UDP transport with an
8-byte header (sequence-numbered; port 52381 on Sony-compatible firmware).

Movement/zoom/preset commands and pan/tilt/zoom position inquiries are covered.
All access is serialized through an asyncio lock so responses stay matched to
their commands.

Hardening notes
---------------
* ``connected`` is a real liveness flag. EOF, socket errors and repeated
  ACK timeouts mark the transport disconnected; callers (``PTZOpticsDriver``)
  then fall back to HTTP-CGI immediately instead of hammering a dead socket.
* A bounded exponential-backoff reconnect task runs in the background after a
  drop; it never blocks joystick commands.
* UDP replies are matched to the request sequence number; stale or foreign
  datagrams are discarded instead of being mistaken for the current reply.
* ACK (command accepted) and completion (motion finished) have separate
  timeouts. PTZOptics sends the completion of preset / absolute moves only
  when the motion ends, so a long move must not be reported as a failure.
  A command is reported successful once it is ACKed even if completion is
  slow; the orphaned completion is discarded on the next transaction.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Tuple

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


class ViscaError(Exception):
    """Raised when the camera returns a VISCA error or does not respond."""


class ViscaTimeout(ViscaError):
    """Raised when the camera does not answer within the expected window."""


# PTZOptics speed limits (VISCA).
PAN_SPEED_MAX = 0x18   # 24
TILT_SPEED_MAX = 0x14  # 20
ZOOM_SPEED_MAX = 0x07
PRESET_SPEED_MAX = 0x18  # preset recall speed, 1..24

# Zoom position range for direct zoom (wide .. tele).
ZOOM_POS_MIN = 0x0000
ZOOM_POS_MAX = 0x4000

# Approximate mechanical scaling for PT-20X-4K-G3 absolute positioning.
# VISCA position units per degree; adjust if your unit needs calibration.
PAN_UNITS_PER_DEG = 14.4
TILT_UNITS_PER_DEG = 14.4

# Defaults (settings override where present; see ``_setting``).
DEFAULT_ACK_TIMEOUT = 0.5
DEFAULT_COMPLETION_TIMEOUT = 8.0
DEFAULT_CONNECT_TIMEOUT = 2.0
DEFAULT_MAX_CONSECUTIVE_TIMEOUTS = 2
DEFAULT_RECONNECT_INITIAL = 1.0
DEFAULT_RECONNECT_MAX = 30.0

# Sony VISCA-over-IP header payload types.
_UDP_TYPE_COMMAND = b"\x01\x00"
_UDP_TYPE_INQUIRY = b"\x01\x10"
_UDP_TYPE_REPLY = b"\x01\x11"
_UDP_TYPE_CONTROL = b"\x02\x00"
_UDP_TYPE_CONTROL_REPLY = b"\x02\x01"
_UDP_HEADER_LEN = 8


def _setting(name: str, default):
    """Read an optional setting; the default mirrors ``app.config.Settings``."""
    return getattr(settings, name, default)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _split_nibbles_16(value: int) -> bytes:
    """Split a 16-bit value into four low-nibble bytes (VISCA position encoding)."""
    value &= 0xFFFF
    return bytes(
        [
            (value >> 12) & 0x0F,
            (value >> 8) & 0x0F,
            (value >> 4) & 0x0F,
            value & 0x0F,
        ]
    )


def _join_nibbles(data: bytes) -> int:
    """Reassemble low-nibble bytes into an unsigned integer."""
    result = 0
    for b in data:
        result = (result << 4) | (b & 0x0F)
    return result


def _to_signed(value: int, bits: int = 16) -> int:
    if value >= (1 << (bits - 1)):
        value -= 1 << bits
    return value


def _is_completion(packet: bytes) -> bool:
    """``90 5y FF`` -- a bare command completion (no inquiry data)."""
    return len(packet) == 3 and (packet[1] & 0xF0) == 0x50


@dataclass
class ViscaReply:
    """Outcome of one VISCA transaction."""

    acked: bool
    completed: bool
    packet: Optional[bytes] = None

    @property
    def ok(self) -> bool:
        """The camera accepted the command (ACK), whether or not motion has ended."""
        return self.acked or self.completed


class ViscaOverIPClient:
    """Async VISCA-over-IP client for a single PTZOptics camera."""

    def __init__(
        self,
        host: str,
        port: int = 1240,
        *,
        use_udp: bool = False,
        camera_address: int = 1,
        timeout: Optional[float] = None,
        ack_timeout: Optional[float] = None,
        completion_timeout: Optional[float] = None,
        connect_timeout: Optional[float] = None,
        max_consecutive_timeouts: int = DEFAULT_MAX_CONSECUTIVE_TIMEOUTS,
        reconnect_initial: float = DEFAULT_RECONNECT_INITIAL,
        reconnect_max: float = DEFAULT_RECONNECT_MAX,
        preset_recall_speed: Optional[int] = None,
    ):
        self.host = host
        self.port = port
        self.use_udp = use_udp
        self._addr_byte = 0x80 | (camera_address & 0x0F)
        self._lock = asyncio.Lock()

        # ``timeout`` is kept for backwards compatibility: it sets the ACK timeout.
        self.ack_timeout = float(
            ack_timeout
            if ack_timeout is not None
            else (timeout if timeout is not None else _setting("visca_ack_timeout_seconds", DEFAULT_ACK_TIMEOUT))
        )
        self.completion_timeout = float(
            completion_timeout
            if completion_timeout is not None
            else _setting("visca_completion_timeout_seconds", DEFAULT_COMPLETION_TIMEOUT)
        )
        self.connect_timeout = float(connect_timeout if connect_timeout is not None else DEFAULT_CONNECT_TIMEOUT)
        self.max_consecutive_timeouts = max(1, int(max_consecutive_timeouts))
        self.reconnect_initial = max(0.001, float(reconnect_initial))
        self.reconnect_max = max(self.reconnect_initial, float(reconnect_max))
        # 0 disables the CAM_Memory recall-speed command (PTZOptics: 8x 01 06 01 pp FF).
        self.preset_recall_speed = int(
            preset_recall_speed
            if preset_recall_speed is not None
            else _setting("visca_preset_recall_speed", 0)
        )

        # Injectable for tests.
        self._open_connection: Callable[..., Awaitable[Tuple[asyncio.StreamReader, asyncio.StreamWriter]]] = (
            asyncio.open_connection
        )

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._udp_transport: Optional[asyncio.DatagramTransport] = None
        self._udp_queue: Optional[asyncio.Queue] = None
        self._seq = 0
        self._connected = False
        self._closed = False

        # Health / bookkeeping.
        self.last_error: Optional[str] = None
        self.last_error_at: Optional[float] = None
        self.last_ok_at: Optional[float] = None
        self.reconnect_count = 0
        self.connect_attempts = 0
        self.commands_ok = 0
        self.commands_failed = 0
        self._consecutive_timeouts = 0
        self._stale_completions = 0
        self._last_ack_socket: Optional[int] = None
        self._sent_preset_speed: Optional[int] = None

        self._reconnect_task: Optional[asyncio.Task] = None
        self._backoff = self.reconnect_initial
        self._next_retry_at: Optional[float] = None

    # -- connection lifecycle -------------------------------------------------
    @property
    def connected(self) -> bool:
        """True only while the transport is believed to be alive."""
        return self._connected

    @property
    def reconnecting(self) -> bool:
        return self._reconnect_task is not None and not self._reconnect_task.done()

    async def connect(self) -> bool:
        """Open the transport. Returns True on success.

        Does not start the background reconnect loop; call
        :meth:`schedule_reconnect` (or let a transaction failure do it) for that.
        """
        self._closed = False
        self.connect_attempts += 1
        await self._teardown_transport()
        try:
            if self.use_udp:
                await self._open_udp()
            else:
                self._reader, self._writer = await asyncio.wait_for(
                    self._open_connection(self.host, self.port), self.connect_timeout
                )
            self._connected = True
            self._consecutive_timeouts = 0
            self._stale_completions = 0
            self._last_ack_socket = None
            self._sent_preset_speed = None
            self._backoff = self.reconnect_initial
            self._next_retry_at = None
            self.last_ok_at = time.monotonic()
            logger.info("VISCA connected", host=self.host, port=self.port, udp=self.use_udp)
            return True
        except Exception as e:
            self._record_error(f"connect failed: {e}")
            logger.warning("VISCA connect failed", host=self.host, port=self.port, error=str(e))
            await self._teardown_transport()
            return False

    async def _open_udp(self) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        client = self

        class _Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):  # noqa: ANN001
                queue.put_nowait(data)

            def error_received(self, exc):  # noqa: ANN001
                client._record_error(f"udp error: {exc}")

            def connection_lost(self, exc):  # noqa: ANN001
                if exc is not None:
                    client._record_error(f"udp connection lost: {exc}")

        transport, _ = await loop.create_datagram_endpoint(_Proto, remote_addr=(self.host, self.port))
        self._udp_transport = transport
        self._udp_queue = queue

    async def close(self) -> None:
        """Close the transport and stop reconnecting."""
        self._closed = True
        task, self._reconnect_task = self._reconnect_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._teardown_transport()

    async def _teardown_transport(self) -> None:
        writer, transport = self._writer, self._udp_transport
        self._reader = None
        self._writer = None
        self._udp_transport = None
        self._udp_queue = None
        self._connected = False
        try:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1.0)
                except Exception:
                    pass
            if transport is not None:
                transport.close()
        except Exception:
            pass

    def _record_error(self, message: str) -> None:
        self.last_error = message
        self.last_error_at = time.monotonic()

    def _mark_disconnected(self, reason: str) -> None:
        """Flag the transport dead and kick off background reconnection."""
        self._record_error(reason)
        if self._connected:
            logger.warning("VISCA transport lost", host=self.host, port=self.port, reason=reason)
        self._connected = False
        # Drop the transport without awaiting (we may be inside the lock).
        writer, transport = self._writer, self._udp_transport
        self._reader = self._writer = None
        self._udp_transport = self._udp_queue = None
        try:
            if writer is not None:
                writer.close()
            if transport is not None:
                transport.close()
        except Exception:
            pass
        self.schedule_reconnect()

    # -- reconnect with bounded exponential backoff --------------------------
    def schedule_reconnect(self) -> bool:
        """Start the background reconnect loop if it is not already running."""
        if self._closed or self._connected or self.reconnecting:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._reconnect_task = loop.create_task(self._reconnect_loop())
        return True

    async def _reconnect_loop(self) -> None:
        try:
            while not self._closed and not self._connected:
                delay = self._backoff
                self._next_retry_at = time.monotonic() + delay
                await asyncio.sleep(delay)
                if self._closed:
                    return
                if await self.connect():
                    self.reconnect_count += 1
                    logger.info("VISCA reconnected", host=self.host, port=self.port, attempts=self.connect_attempts)
                    return
                self._backoff = min(self._backoff * 2, self.reconnect_max)
        finally:
            self._next_retry_at = None

    # -- low-level transport --------------------------------------------------
    def _wrap_udp(self, payload: bytes, inquiry: bool) -> Tuple[bytes, int]:
        payload_type = _UDP_TYPE_INQUIRY if inquiry else _UDP_TYPE_COMMAND
        length = len(payload).to_bytes(2, "big")
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return payload_type + length + seq.to_bytes(4, "big") + payload, seq

    async def _read_tcp_packet(self, timeout: float) -> Optional[bytes]:
        assert self._reader is not None
        reader = self._reader
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            chunk = await asyncio.wait_for(reader.read(1), remaining)
            if not chunk:
                return None
            buf += chunk
            if chunk == b"\xff":
                return bytes(buf)

    async def _read_udp_packet(self, timeout: float, expected_seq: Optional[int]) -> Optional[bytes]:
        """Return the next VISCA payload whose header sequence matches ``expected_seq``.

        Headerless datagrams (PTZOptics raw-VISCA UDP mode) are accepted as-is.
        Replies carrying another sequence number are stale and discarded.
        """
        assert self._udp_queue is not None
        queue = self._udp_queue
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            data = await asyncio.wait_for(queue.get(), remaining)
            if not data:
                continue
            if data[0] & 0x80:
                # Raw VISCA reply (0x90 ...) without a Sony header.
                return bytes(data)
            if len(data) < _UDP_HEADER_LEN:
                logger.debug("VISCA UDP runt datagram discarded", data=data.hex())
                continue
            ptype = bytes(data[0:2])
            seq = int.from_bytes(data[4:8], "big")
            if ptype == _UDP_TYPE_CONTROL_REPLY or ptype == _UDP_TYPE_CONTROL:
                continue
            if expected_seq is not None and seq != expected_seq:
                logger.debug("VISCA UDP stale reply discarded", expected=expected_seq, got=seq)
                continue
            return bytes(data[_UDP_HEADER_LEN:])

    async def _transact(
        self, payload: bytes, *, inquiry: bool = False, wait_completion: bool = True
    ) -> ViscaReply:
        """Send a VISCA payload and wait for ACK / completion (or inquiry reply).

        Raises :class:`ViscaError` when the camera is unreachable, answers with
        a VISCA error, or never ACKs. A command that was ACKed but whose
        completion did not arrive within ``completion_timeout`` is returned
        with ``acked=True, completed=False`` rather than raised.
        """
        if not self._connected:
            self.schedule_reconnect()
            raise ViscaError("not connected")

        async with self._lock:
            if not self._connected:
                raise ViscaError("not connected")
            expected_seq: Optional[int] = None
            try:
                if self.use_udp:
                    assert self._udp_transport is not None
                    datagram, expected_seq = self._wrap_udp(payload, inquiry)
                    self._udp_transport.sendto(datagram)
                else:
                    assert self._writer is not None
                    self._writer.write(payload)
                    await self._writer.drain()
            except Exception as e:
                self._mark_disconnected(f"send failed: {e}")
                raise ViscaError(f"send failed: {e}") from e

            acked = False
            ack_socket: Optional[int] = None
            phase_timeout = self.ack_timeout
            while True:
                try:
                    packet = await self._read_packet(phase_timeout, expected_seq)
                except asyncio.TimeoutError:
                    if acked:
                        # Camera accepted the command; the motion is just long.
                        self._stale_completions += 1
                        self._record_error("completion timeout (command ACKed)")
                        logger.debug("VISCA completion timed out after ACK", payload=payload.hex())
                        return ViscaReply(acked=True, completed=False)
                    self._consecutive_timeouts += 1
                    self._record_error("ACK timeout")
                    if self._consecutive_timeouts >= self.max_consecutive_timeouts:
                        self._mark_disconnected(
                            f"{self._consecutive_timeouts} consecutive VISCA timeouts"
                        )
                    raise ViscaTimeout("no ACK from camera")
                except (ConnectionError, OSError) as e:
                    self._mark_disconnected(f"read failed: {e}")
                    raise ViscaError(f"read failed: {e}") from e

                if packet is None:
                    self._mark_disconnected("EOF from camera")
                    raise ViscaError("connection closed by camera")
                if len(packet) < 2:
                    continue

                category = packet[1] & 0xF0
                socket_no = packet[1] & 0x0F

                if category == 0x40:  # ACK -- the camera accepted the command
                    acked = True
                    ack_socket = socket_no
                    self._last_ack_socket = socket_no
                    self._consecutive_timeouts = 0
                    self.last_ok_at = time.monotonic()
                    if not wait_completion:
                        self._stale_completions += 1
                        return ViscaReply(acked=True, completed=False)
                    phase_timeout = self.completion_timeout
                    continue

                if category == 0x50:  # completion or inquiry reply
                    if _is_completion(packet) and self._stale_completions > 0 and (
                        not acked or (ack_socket is not None and socket_no != ack_socket) or inquiry
                    ):
                        # Orphaned completion of an earlier long move.
                        self._stale_completions -= 1
                        logger.debug("VISCA stale completion discarded", packet=packet.hex())
                        continue
                    if inquiry and _is_completion(packet):
                        # A bare completion can never answer an inquiry; keep waiting.
                        continue
                    self._consecutive_timeouts = 0
                    self.last_ok_at = time.monotonic()
                    return ViscaReply(acked=True, completed=True, packet=packet)

                if category == 0x60:  # error -- the camera is alive, the command was refused
                    self._consecutive_timeouts = 0
                    self._record_error(f"VISCA error {packet.hex()}")
                    raise ViscaError(f"VISCA error: {packet.hex()}")

                # Unknown category (e.g. network change notification); ignore.
                logger.debug("VISCA unexpected packet ignored", packet=packet.hex())

    async def _read_packet(self, timeout: float, expected_seq: Optional[int]) -> Optional[bytes]:
        if self.use_udp:
            return await self._read_udp_packet(timeout, expected_seq)
        return await self._read_tcp_packet(timeout)

    async def _command(self, body: bytes, *, wait_completion: bool = True) -> bool:
        """Send a command; True once the camera ACKs it (completion may lag)."""
        try:
            reply = await self._transact(
                bytes([self._addr_byte]) + body + b"\xff", wait_completion=wait_completion
            )
        except Exception as e:
            self.commands_failed += 1
            logger.warning("VISCA command failed", error=str(e), body=body.hex())
            return False
        if reply.ok:
            self.commands_ok += 1
            return True
        self.commands_failed += 1
        return False

    # -- commands -------------------------------------------------------------
    async def power_on(self) -> bool:
        return await self._command(b"\x01\x04\x00\x02")

    async def home(self) -> bool:
        return await self._command(b"\x01\x06\x04")

    async def pan_tilt_drive(
        self, pan_dir: int, tilt_dir: int, pan_speed: int = 12, tilt_speed: int = 12
    ) -> bool:
        """Continuous pan/tilt. dir: -1/0/1 for pan (left/none/right), tilt (down/none/up)."""
        vv = _clamp(pan_speed, 1, PAN_SPEED_MAX)
        ww = _clamp(tilt_speed, 1, TILT_SPEED_MAX)
        pan_byte = {-1: 0x01, 0: 0x03, 1: 0x02}.get(pan_dir, 0x03)
        tilt_byte = {-1: 0x02, 0: 0x03, 1: 0x01}.get(tilt_dir, 0x03)
        return await self._command(bytes([0x01, 0x06, 0x01, vv, ww, pan_byte, tilt_byte]))

    async def pan_tilt_stop(self) -> bool:
        return await self.pan_tilt_drive(0, 0)

    async def pan_tilt_absolute(
        self, pan_units: int, tilt_units: int, pan_speed: int = 0x10, tilt_speed: int = 0x10
    ) -> bool:
        vv = _clamp(pan_speed, 1, PAN_SPEED_MAX)
        ww = _clamp(tilt_speed, 1, TILT_SPEED_MAX)
        body = bytes([0x01, 0x06, 0x02, vv, ww]) + _split_nibbles_16(pan_units) + _split_nibbles_16(tilt_units)
        return await self._command(body)

    async def zoom_drive(self, direction: int, speed: int = 4) -> bool:
        """Continuous zoom. direction: -1 wide, 0 stop, 1 tele."""
        if direction == 0:
            return await self._command(b"\x01\x04\x07\x00")
        p = _clamp(speed, 0, ZOOM_SPEED_MAX)
        code = 0x20 | p if direction > 0 else 0x30 | p
        return await self._command(bytes([0x01, 0x04, 0x07, code]))

    async def zoom_absolute(self, position: int) -> bool:
        position = _clamp(position, ZOOM_POS_MIN, ZOOM_POS_MAX)
        body = bytes([0x01, 0x04, 0x47]) + _split_nibbles_16(position)
        return await self._command(body)

    async def set_preset_recall_speed(self, speed: int) -> bool:
        """PTZOptics preset recall speed (``8x 01 06 01 pp FF``, pp = 1..24).

        Optional: only sent when ``preset_recall_speed`` is configured (> 0).
        """
        pp = _clamp(int(speed), 1, PRESET_SPEED_MAX)
        ok = await self._command(bytes([0x01, 0x06, 0x01, pp]))
        if ok:
            self._sent_preset_speed = pp
        return ok

    async def preset_recall(self, preset: int, *, wait_completion: bool = True) -> bool:
        """Recall a preset. True once the camera ACKs the recall.

        PTZOptics only sends the completion when the move finishes, so with
        ``wait_completion`` the call blocks (up to ``completion_timeout``) for
        the motion to end; the result is still True if only the ACK arrived.
        """
        if self.preset_recall_speed > 0 and self._sent_preset_speed != _clamp(
            self.preset_recall_speed, 1, PRESET_SPEED_MAX
        ):
            if not await self.set_preset_recall_speed(self.preset_recall_speed):
                logger.debug("VISCA preset recall speed not accepted; continuing with recall")
        return await self._command(
            bytes([0x01, 0x04, 0x3F, 0x02, preset & 0x7F]), wait_completion=wait_completion
        )

    async def preset_set(self, preset: int) -> bool:
        return await self._command(bytes([0x01, 0x04, 0x3F, 0x01, preset & 0x7F]))

    # -- inquiries ------------------------------------------------------------
    async def get_pan_tilt(self) -> Optional[Tuple[int, int]]:
        """Return (pan_units, tilt_units) signed, or None on failure."""
        try:
            reply = await self._transact(bytes([self._addr_byte, 0x09, 0x06, 0x12, 0xFF]), inquiry=True)
        except Exception:
            return None
        if reply.packet is None:
            return None
        data = reply.packet[2:-1]
        if len(data) < 8:
            return None
        pan = _to_signed(_join_nibbles(data[0:4]))
        tilt = _to_signed(_join_nibbles(data[4:8]))
        return pan, tilt

    async def get_zoom(self) -> Optional[int]:
        """Return raw zoom position 0..0x4000, or None on failure."""
        try:
            reply = await self._transact(bytes([self._addr_byte, 0x09, 0x04, 0x47, 0xFF]), inquiry=True)
        except Exception:
            return None
        if reply.packet is None:
            return None
        data = reply.packet[2:-1]
        if len(data) < 4:
            return None
        return _join_nibbles(data[0:4])

    # -- health ---------------------------------------------------------------
    def health(self) -> dict:
        """Transport health for status endpoints and diagnostics."""
        now = time.monotonic()
        return {
            "connected": self._connected,
            "transport": "udp" if self.use_udp else "tcp",
            "host": self.host,
            "port": self.port,
            "reconnecting": self.reconnecting,
            "next_retry_in": (
                round(max(0.0, self._next_retry_at - now), 2) if self._next_retry_at is not None else None
            ),
            "reconnect_count": self.reconnect_count,
            "connect_attempts": self.connect_attempts,
            "consecutive_timeouts": self._consecutive_timeouts,
            "last_error": self.last_error,
            "last_error_age": round(now - self.last_error_at, 1) if self.last_error_at is not None else None,
            "last_ok_age": round(now - self.last_ok_at, 1) if self.last_ok_at is not None else None,
            "commands_ok": self.commands_ok,
            "commands_failed": self.commands_failed,
            "ack_timeout": self.ack_timeout,
            "completion_timeout": self.completion_timeout,
            "preset_recall_speed": self.preset_recall_speed,
        }

    # -- unit conversion helpers ---------------------------------------------
    @staticmethod
    def pan_deg_to_units(deg: float) -> int:
        return int(round(deg * PAN_UNITS_PER_DEG))

    @staticmethod
    def tilt_deg_to_units(deg: float) -> int:
        return int(round(deg * TILT_UNITS_PER_DEG))

    @staticmethod
    def units_to_pan_deg(units: int) -> float:
        return round(units / PAN_UNITS_PER_DEG, 2)

    @staticmethod
    def units_to_tilt_deg(units: int) -> float:
        return round(units / TILT_UNITS_PER_DEG, 2)

    @staticmethod
    def zoom_pct_to_pos(pct: float) -> int:
        return int(round(_clamp_float(pct, 0.0, 100.0) / 100.0 * ZOOM_POS_MAX))

    @staticmethod
    def pos_to_zoom_pct(pos: int) -> float:
        return round(pos / ZOOM_POS_MAX * 100.0, 1)


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
