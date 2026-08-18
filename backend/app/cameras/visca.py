"""VISCA-over-IP client for PTZOptics cameras.

Implements the subset of the VISCA protocol used to drive PTZOptics PT-series
(G3) cameras over IP. Supports both a raw-VISCA TCP transport (PTZOptics
"VISCA over IP" TCP) and the Sony-style UDP transport with an 8-byte header.

Movement/zoom/preset commands and pan/tilt/zoom position inquiries are covered.
All access is serialized through an asyncio lock so responses stay matched to
their commands.
"""

import asyncio
from typing import Optional, Tuple

from app.logging_config import get_logger

logger = get_logger(__name__)


class ViscaError(Exception):
    """Raised when the camera returns a VISCA error or does not respond."""


# PTZOptics speed limits (VISCA).
PAN_SPEED_MAX = 0x18   # 24
TILT_SPEED_MAX = 0x14  # 20
ZOOM_SPEED_MAX = 0x07

# Zoom position range for direct zoom (wide .. tele).
ZOOM_POS_MIN = 0x0000
ZOOM_POS_MAX = 0x4000

# Approximate mechanical scaling for PT-20X-4K-G3 absolute positioning.
# VISCA position units per degree; adjust if your unit needs calibration.
PAN_UNITS_PER_DEG = 14.4
TILT_UNITS_PER_DEG = 14.4


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


class ViscaOverIPClient:
    """Async VISCA-over-IP client for a single PTZOptics camera."""

    def __init__(
        self,
        host: str,
        port: int = 1240,
        *,
        use_udp: bool = False,
        camera_address: int = 1,
        timeout: float = 2.0,
    ):
        self.host = host
        self.port = port
        self.use_udp = use_udp
        self.timeout = timeout
        self._addr_byte = 0x80 | (camera_address & 0x0F)
        self._lock = asyncio.Lock()

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._udp_transport: Optional[asyncio.DatagramTransport] = None
        self._udp_queue: Optional[asyncio.Queue] = None
        self._seq = 0

    # -- connection lifecycle -------------------------------------------------
    async def connect(self) -> bool:
        try:
            if self.use_udp:
                loop = asyncio.get_running_loop()
                queue: asyncio.Queue = asyncio.Queue()

                class _Proto(asyncio.DatagramProtocol):
                    def datagram_received(self, data, addr):  # noqa: ANN001
                        queue.put_nowait(data)

                transport, _ = await loop.create_datagram_endpoint(
                    _Proto, remote_addr=(self.host, self.port)
                )
                self._udp_transport = transport
                self._udp_queue = queue
            else:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), self.timeout
                )
            logger.info("VISCA connected", host=self.host, port=self.port, udp=self.use_udp)
            return True
        except Exception as e:
            logger.warning("VISCA connect failed", host=self.host, port=self.port, error=str(e))
            await self.close()
            return False

    async def close(self) -> None:
        try:
            if self._writer is not None:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass
            if self._udp_transport is not None:
                self._udp_transport.close()
        finally:
            self._reader = None
            self._writer = None
            self._udp_transport = None
            self._udp_queue = None

    @property
    def connected(self) -> bool:
        return self._writer is not None or self._udp_transport is not None

    # -- low-level transport --------------------------------------------------
    def _wrap_udp(self, payload: bytes, inquiry: bool) -> bytes:
        payload_type = b"\x01\x10" if inquiry else b"\x01\x00"
        length = len(payload).to_bytes(2, "big")
        seq = self._seq.to_bytes(4, "big")
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return payload_type + length + seq + payload

    async def _read_tcp_packet(self) -> Optional[bytes]:
        assert self._reader is not None
        buf = bytearray()
        while True:
            chunk = await asyncio.wait_for(self._reader.read(1), self.timeout)
            if not chunk:
                return None
            buf += chunk
            if chunk == b"\xff":
                return bytes(buf)

    async def _read_udp_packet(self) -> Optional[bytes]:
        assert self._udp_queue is not None
        data = await asyncio.wait_for(self._udp_queue.get(), self.timeout)
        # Strip the 8-byte Sony header.
        return data[8:] if len(data) > 8 else data

    async def _transact(self, payload: bytes, *, inquiry: bool = False) -> bytes:
        """Send a VISCA payload and return the completion/inquiry response."""
        if not self.connected:
            raise ViscaError("not connected")

        async with self._lock:
            if self.use_udp:
                assert self._udp_transport is not None
                self._udp_transport.sendto(self._wrap_udp(payload, inquiry))
                read = self._read_udp_packet
            else:
                assert self._writer is not None
                self._writer.write(payload)
                await self._writer.drain()
                read = self._read_tcp_packet

            while True:
                packet = await read()
                if packet is None:
                    raise ViscaError("no response")
                if len(packet) < 2:
                    return packet
                category = packet[1] & 0xF0
                if category == 0x40:  # ACK — wait for completion
                    continue
                if category == 0x50:  # completion or inquiry reply
                    return packet
                if category == 0x60:  # error
                    raise ViscaError(f"VISCA error: {packet.hex()}")
                return packet

    async def _command(self, body: bytes) -> bool:
        try:
            await self._transact(bytes([self._addr_byte]) + body + b"\xff")
            return True
        except Exception as e:
            logger.warning("VISCA command failed", error=str(e))
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

    async def preset_recall(self, preset: int) -> bool:
        return await self._command(bytes([0x01, 0x04, 0x3F, 0x02, preset & 0x7F]))

    async def preset_set(self, preset: int) -> bool:
        return await self._command(bytes([0x01, 0x04, 0x3F, 0x01, preset & 0x7F]))

    # -- inquiries ------------------------------------------------------------
    async def get_pan_tilt(self) -> Optional[Tuple[int, int]]:
        """Return (pan_units, tilt_units) signed, or None on failure."""
        try:
            resp = await self._transact(bytes([self._addr_byte, 0x09, 0x06, 0x12, 0xFF]), inquiry=True)
        except Exception:
            return None
        data = resp[2:-1]
        if len(data) < 8:
            return None
        pan = _to_signed(_join_nibbles(data[0:4]))
        tilt = _to_signed(_join_nibbles(data[4:8]))
        return pan, tilt

    async def get_zoom(self) -> Optional[int]:
        """Return raw zoom position 0..0x4000, or None on failure."""
        try:
            resp = await self._transact(bytes([self._addr_byte, 0x09, 0x04, 0x47, 0xFF]), inquiry=True)
        except Exception:
            return None
        data = resp[2:-1]
        if len(data) < 4:
            return None
        return _join_nibbles(data[0:4])

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
