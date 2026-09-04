"""EasyWorship 7.3+ native Remote Control protocol driver.

EasyWorship exposes the same TCP control channel used by its own Stream Deck
plug-in, the EasyWorship Remote mobile app and the Bitfocus Companion module.
It is enabled in EasyWorship under Edit > Options > Advanced > "Enable Remote
Control" and advertised over mDNS as ``_ezwremote._tcp``.

Wire format (reverse-engineered, see companion-module-softouch-easyworship):

* TCP, one JSON object per line, ``\\r\\n`` delimited, latin-1 encoded.
* Pairing: ``{"action":"connect","uid":<uuid>,"device_name":..,"device_type":8,
  "requestrev":"0"}`` -> EasyWorship answers ``paired`` or ``notPaired`` (the
  operator approves the first pairing via the Remote toolbar button; the same
  ``uid`` auto-pairs afterwards).
* Every inbound message carries ``requestrev``; the latest value is echoed in
  each outbound command. A ``heartbeat`` is sent periodically because
  EasyWorship is silent while idle.
* Navigation commands: ``nextSlide``, ``prevSlide``, ``gotoSlide N``,
  ``nextSchedule``, ``prevSchedule``, ``gotoSchedule N``,
  ``gotoStartPresentation``, ``gotoStartSlide``, ``nextBuild``, ``prevBuild``,
  ``Play``, ``Pause``, ``Toggle``.
* Overlays (logo/black/clear) are set by sending a full ``status`` payload.
* EasyWorship pushes ``status`` messages with the live position (``pres_no``,
  ``slide_no``, row ids, revisions) -- the read-back that keystroke injection
  never had, and what lets the service confirm each commanded change.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from app.config import settings
from app.logging_config import get_logger

from .driver import EasyWorshipDriver

logger = get_logger(__name__)

MDNS_SERVICE_TYPE = "_ezwremote._tcp.local."
DEVICE_TYPE = 8  # what the Companion module identifies itself as
MAX_LINE_BYTES = 1024 * 1024

# Reconnect cadence: fast for the first few minutes (someone is probably
# watching), then slow down but never stop.
_FAST_BACKOFF = (1.0, 1.5, 2.3, 3.4, 5.0)
_FAST_PHASE_SECONDS = 180.0
_SLOW_RETRY_SECONDS = 30.0

# Service action name -> protocol action(s).
_SIMPLE_ACTIONS = {
    "next_slide": ("nextSlide",),
    "prev_slide": ("prevSlide",),
    # Selecting a schedule item does not send it live on its own; chain the
    # presentation-start jump exactly like the official Stream Deck presets do.
    "next_item": ("nextSchedule", "gotoStartPresentation"),
    "prev_item": ("prevSchedule", "gotoStartPresentation"),
    "live": ("gotoStartPresentation",),
    "next_build": ("nextBuild",),
    "prev_build": ("prevBuild",),
    "play": ("Play",),
    "pause": ("Pause",),
    "toggle_play": ("Toggle",),
}
_OVERLAY_ACTIONS = ("logo", "black", "clear")


@dataclass
class EWState:
    """Last known EasyWorship state as reported over the protocol."""

    connected: bool = False
    paired: bool = False
    logo: bool = False
    black: bool = False
    clear: bool = False
    pres_no: Optional[int] = None
    slide_no: Optional[int] = None
    pres_rowid: Optional[int] = None
    slide_rowid: Optional[int] = None
    rectype: Optional[int] = None
    schedulerev: Optional[str] = None
    liverev: Optional[str] = None
    imagehash: Optional[str] = None
    permissions: Optional[int] = None
    requestrev: str = "0"
    updated_at: Optional[float] = None
    status_count: int = 0  # increments on every inbound status message

    def as_dict(self) -> dict:
        return asdict(self)


def _load_or_create_uid() -> str:
    """Stable pairing identity: configured value, else a UUID persisted to disk
    so EasyWorship keeps recognising us across restarts."""
    if settings.easyworship_remote_uid:
        return settings.easyworship_remote_uid
    path = Path(settings.easyworship_remote_uid_file)
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        new_uid = str(uuid.uuid4())
        path.write_text(new_uid, encoding="utf-8")
        return new_uid
    except OSError as e:
        logger.warning("Could not persist EasyWorship pairing uid", path=str(path), error=str(e))
        return str(uuid.uuid4())


async def discover_easyworship(
    timeout: float, server_name: Optional[str] = None
) -> Optional[tuple[str, int, str]]:
    """Find an EasyWorship remote server via mDNS. Returns (host, port, name)
    or None. Requires the optional ``zeroconf`` package."""
    try:
        from zeroconf import IPVersion, ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
    except ImportError:
        logger.info("zeroconf not installed; EasyWorship mDNS discovery unavailable")
        return None

    loop = asyncio.get_running_loop()
    found: asyncio.Future = loop.create_future()
    aiozc = AsyncZeroconf(ip_version=IPVersion.V4Only)

    async def _resolve(name: str) -> None:
        info = AsyncServiceInfo(MDNS_SERVICE_TYPE, name)
        if not await info.async_request(aiozc.zeroconf, 3000):
            return
        addresses = info.parsed_addresses()
        if not addresses or not info.port:
            return
        display = name.replace("." + MDNS_SERVICE_TYPE, "")
        if server_name and display.strip().lower() != server_name.strip().lower():
            logger.info("Ignoring EasyWorship server (name mismatch)", server=display)
            return
        if not found.done():
            found.set_result((addresses[0], int(info.port), display))

    def _on_change(zeroconf, service_type, name, state_change) -> None:  # noqa: ANN001
        if state_change is ServiceStateChange.Added:
            asyncio.ensure_future(_resolve(name))

    browser = AsyncServiceBrowser(aiozc.zeroconf, MDNS_SERVICE_TYPE, handlers=[_on_change])
    try:
        return await asyncio.wait_for(found, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        await browser.async_cancel()
        await aiozc.async_close()


class RemoteProtocolDriver(EasyWorshipDriver):
    """Controls EasyWorship over its native remote-control TCP protocol.

    Beyond the ``EasyWorshipDriver`` contract it exposes the live ``state``,
    absolute jumps (``goto_schedule`` / ``goto_slide``) and ``wait_for`` so the
    service can confirm that a command actually took effect.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        *,
        device_name: Optional[str] = None,
        uid: Optional[str] = None,
        server_name: Optional[str] = None,
        heartbeat_seconds: float = 30.0,
        connect_timeout: float = 5.0,
        pair_timeout: float = 5.0,
        pair_retry_seconds: float = 10.0,
        discovery_seconds: Optional[float] = None,
        backoff: tuple[float, ...] = _FAST_BACKOFF,
        slow_retry_seconds: float = _SLOW_RETRY_SECONDS,
    ):
        self.host = host or settings.easyworship_remote_host
        self.port = port or settings.easyworship_remote_port
        self.server_name = server_name or settings.easyworship_remote_server_name
        self.device_name = (device_name or settings.easyworship_remote_device_name)[:64]
        self._uid = uid
        self._heartbeat_seconds = heartbeat_seconds
        self._connect_timeout = connect_timeout
        self._pair_timeout = pair_timeout
        self._pair_retry_seconds = pair_retry_seconds
        self._discovery_seconds = (
            settings.easyworship_remote_discovery_seconds
            if discovery_seconds is None
            else discovery_seconds
        )
        self._backoff = backoff
        self._slow_retry_seconds = slow_retry_seconds

        self.state = EWState()
        self.on_state: Optional[Callable[[EWState], None]] = None

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._supervisor_task: Optional[asyncio.Task] = None
        self._state_event: asyncio.Event = asyncio.Event()
        self._paired_event: asyncio.Event = asyncio.Event()
        self._closing = False
        self._retry_started_at: Optional[float] = None
        self._retry_attempts = 0
        self.reconnect_count = 0

    # -- identity / addressing -----------------------------------------------
    @property
    def uid(self) -> str:
        if self._uid is None:
            self._uid = _load_or_create_uid()
        return self._uid

    async def _resolve_address(self) -> bool:
        if self.host and self.port:
            return True
        if self._discovery_seconds <= 0:
            return False
        result = await discover_easyworship(self._discovery_seconds, self.server_name)
        if result is None:
            logger.warning("No EasyWorship remote server discovered via mDNS")
            return False
        self.host, self.port, name = result
        logger.info("Discovered EasyWorship server", server=name, host=self.host, port=self.port)
        return True

    # -- lifecycle -----------------------------------------------------------
    async def connect(self) -> bool:
        """Connect and pair once; on failure a background supervisor keeps
        retrying forever. Idempotent while the supervisor is running."""
        self._closing = False
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(self._supervise())
        try:
            await asyncio.wait_for(self._paired_event.wait(), self._connect_timeout + self._pair_timeout)
        except asyncio.TimeoutError:
            pass
        return self.state.paired

    async def disconnect(self) -> None:
        self._closing = True
        for task in (self._supervisor_task, self._heartbeat_task, self._reader_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._supervisor_task = self._heartbeat_task = self._reader_task = None
        await self._close_socket()

    async def is_connected(self) -> bool:
        return self.state.paired

    async def _close_socket(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        was_paired = self.state.paired
        self.state.connected = False
        self.state.paired = False
        self._paired_event.clear()
        if was_paired:
            self.reconnect_count += 1
        self._notify()

    async def _supervise(self) -> None:
        """Connect, pair, and stay connected; reconnect with backoff on loss."""
        while not self._closing:
            ok = await self._open_once()
            if ok:
                self._retry_attempts = 0
                self._retry_started_at = None
                # Block until the reader ends (socket closed / error).
                if self._reader_task is not None:
                    try:
                        await self._reader_task
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
                if self._heartbeat_task is not None:
                    self._heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._heartbeat_task
                    self._heartbeat_task = None
                await self._close_socket()
                if self._closing:
                    return
                logger.warning("EasyWorship remote connection lost; reconnecting")
            await asyncio.sleep(self._next_delay())

    def _next_delay(self) -> float:
        now = time.monotonic()
        if self._retry_started_at is None:
            self._retry_started_at = now
        idx = min(self._retry_attempts, len(self._backoff) - 1)
        self._retry_attempts += 1
        if now - self._retry_started_at > _FAST_PHASE_SECONDS:
            return self._slow_retry_seconds
        return self._backoff[idx]

    async def _open_once(self) -> bool:
        if not await self._resolve_address():
            return False
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self._connect_timeout
            )
        except Exception as e:
            logger.warning("EasyWorship remote connect failed", host=self.host, port=self.port, error=str(e))
            self._reader = self._writer = None
            return False

        self.state.connected = True
        self._notify()
        self._reader_task = asyncio.create_task(self._read_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        await self._send_pair_request()
        try:
            await asyncio.wait_for(self._paired_event.wait(), self._pair_timeout)
        except asyncio.TimeoutError:
            # Still TCP-connected: leave the reader running so a later
            # approval on the EasyWorship machine can pair us.
            logger.warning(
                "EasyWorship remote connected but not paired yet -- approve the pairing "
                "request via the Remote button in EasyWorship",
                device_name=self.device_name,
            )
        return True

    # -- transport -------------------------------------------------------------
    async def _send(self, payload: dict) -> bool:
        if self._writer is None:
            return False
        try:
            self._writer.write((json.dumps(payload) + "\r\n").encode("latin-1", errors="replace"))
            await self._writer.drain()
            return True
        except Exception as e:
            logger.warning("EasyWorship remote send failed", error=str(e))
            return False

    async def _send_command(self, action: str) -> bool:
        return await self._send({"action": action, "requestrev": self.state.requestrev})

    async def _send_pair_request(self) -> bool:
        return await self._send(
            {
                "action": "connect",
                "uid": self.uid,
                "device_name": self.device_name,
                "device_type": DEVICE_TYPE,
                "requestrev": "0",
            }
        )

    async def _heartbeat_loop(self) -> None:
        last_heartbeat = last_pair_retry = time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            if self._writer is None:
                return
            now = time.monotonic()
            if self.state.paired:
                if now - last_heartbeat >= self._heartbeat_seconds:
                    last_heartbeat = now
                    await self._send_command("heartbeat")
            elif now - last_pair_retry >= self._pair_retry_seconds:
                last_pair_retry = now
                await self._send_pair_request()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        reader = self._reader
        try:
            while True:
                try:
                    raw = await reader.readuntil(b"\r\n")
                except asyncio.LimitOverrunError:
                    logger.error("EasyWorship remote sent an oversized line; dropping connection")
                    return
                except asyncio.IncompleteReadError:
                    return
                line = raw.decode("latin-1").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("EasyWorship remote sent non-JSON line", line=line[:120])
                    continue
                if isinstance(message, dict):
                    await self._handle_message(message)
        except (ConnectionError, OSError) as e:
            logger.warning("EasyWorship remote read error", error=str(e))
        except asyncio.CancelledError:
            raise

    async def _handle_message(self, message: dict) -> None:
        action = message.get("action")
        if not isinstance(action, str):
            return
        rev = message.get("requestrev")
        if isinstance(rev, (str, int)):
            self.state.requestrev = str(rev)

        if action == "notPaired":
            if self.state.paired:
                logger.warning("EasyWorship revoked pairing")
            self.state.paired = False
            self._paired_event.clear()
            self._notify()
            return  # never heartbeat an unpaired session (EW rejects it)
        if action == "paired":
            if not self.state.paired:
                logger.info("Paired with EasyWorship", host=self.host, port=self.port)
            self.state.paired = True
            self._paired_event.set()
            self._notify()
        elif action == "status":
            self._apply_status(message)
            self._notify()
        elif action != "heartbeat":
            logger.debug("Unknown EasyWorship remote action", action=action)

        await self._send_command("heartbeat")

    def _apply_status(self, m: dict) -> None:
        s = self.state
        s.logo = m.get("logo") is True
        s.black = m.get("black") is True
        s.clear = m.get("clear") is True
        for key in ("pres_no", "slide_no", "pres_rowid", "slide_rowid", "rectype", "permissions"):
            value = m.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                setattr(s, key, int(value))
            elif isinstance(value, str) and value.lstrip("-").isdigit():
                setattr(s, key, int(value))
        for key in ("schedulerev", "liverev", "imagehash"):
            value = m.get(key)
            if isinstance(value, (str, int)):
                setattr(s, key, str(value))
        s.updated_at = time.time()
        s.status_count += 1

    def _notify(self) -> None:
        event, self._state_event = self._state_event, asyncio.Event()
        event.set()
        if self.on_state is not None:
            try:
                self.on_state(self.state)
            except Exception:
                logger.exception("EasyWorship on_state callback failed")

    # -- state confirmation ----------------------------------------------------
    async def wait_for(self, predicate: Callable[[EWState], bool], timeout: float) -> bool:
        """Wait until ``predicate(state)`` holds or ``timeout`` elapses."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if predicate(self.state):
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            event = self._state_event
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), remaining)

    @property
    def supports_confirmation(self) -> bool:
        return True

    # -- commands --------------------------------------------------------------
    async def send_action(self, action: str) -> bool:
        if not self.state.paired:
            if self.state.connected:
                await self._send_pair_request()
            logger.warning("EasyWorship remote not paired; action dropped", action=action)
            return False
        if action in _OVERLAY_ACTIONS:
            return await self._toggle_overlay(action)
        steps = _SIMPLE_ACTIONS.get(action)
        if not steps:
            logger.warning("No EasyWorship remote mapping for action", action=action)
            return False
        for step in steps:
            if not await self._send_command(step):
                return False
        return True

    async def goto_schedule(self, number: int) -> bool:
        """Select schedule item ``number`` (1-based) and send its first slide live."""
        if number < 1:
            return False
        if not self.state.paired:
            return False
        return await self._send_command(f"gotoSchedule {number}") and await self._send_command(
            "gotoStartPresentation"
        )

    async def goto_slide(self, number: int) -> bool:
        if number < 1 or not self.state.paired:
            return False
        return await self._send_command(f"gotoSlide {number}")

    async def set_overlays(
        self,
        *,
        logo: Optional[bool] = None,
        black: Optional[bool] = None,
        clear: Optional[bool] = None,
    ) -> bool:
        """Set overlay flags explicitly. Logo and black are mutually exclusive in
        EasyWorship; the full current status must be echoed back."""
        if not self.state.paired:
            return False
        s = self.state
        new_logo, new_black, new_clear = s.logo, s.black, s.clear
        if logo is not None:
            new_logo = logo
            if logo:
                new_black = False
        if black is not None:
            new_black = black
            if black:
                new_logo = False
        if clear is not None:
            new_clear = clear
        payload = {
            "action": "status",
            "logo": new_logo,
            "black": new_black,
            "clear": new_clear,
            "rectype": s.rectype,
            "pres_rowid": s.pres_rowid,
            "slide_rowid": s.slide_rowid,
            "pres_no": s.pres_no,
            "slide_no": s.slide_no,
            "schedulerev": s.schedulerev,
            "liverev": s.liverev,
            "imagehash": s.imagehash,
            "permissions": s.permissions,
            "requestrev": s.requestrev,
        }
        return await self._send(payload)

    async def _toggle_overlay(self, which: str) -> bool:
        current = getattr(self.state, which)
        return await self.set_overlays(**{which: not current})
