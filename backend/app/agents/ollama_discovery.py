"""Find Ollama on the EasyWorship PC when DHCP gives it a new address.

Only used when OLLAMA_BASE_URL is empty. Candidates, in order: the last
address that answered, EASYWORSHIP_REMOTE_HOST / the EASYWORSHIP_AGENT_URL
host, the EasyWorship PC found by the remote driver (or mDNS), localhost, then
a sweep of OLLAMA_DISCOVERY_SUBNETS (default: this machine's /24) for port
11434. A candidate counts only if ``GET /api/tags`` answers like Ollama; one
that has OLLAMA_MODEL installed wins over one that doesn't.

A background monitor re-checks every OLLAMA_DISCOVERY_INTERVAL_SECONDS and
right after a failed LLM call, so a lease change heals without a restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

OLLAMA_DEFAULT_PORT = 11434
_SCAN_CONCURRENCY = 64
_SCAN_CONNECT_TIMEOUT = 0.4

Probe = Callable[[str], Awaitable[Optional[List[str]]]]


def host_url(host: str) -> str:
    host = host.strip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{OLLAMA_DEFAULT_PORT}"


def configured_easyworship_host() -> Optional[str]:
    host = settings.easyworship_remote_host or urlsplit(settings.easyworship_agent_url or "").hostname
    return host.strip() if host and host.strip() else None


def has_model(models: List[str], wanted: str) -> bool:
    def norm(tag: str) -> str:
        tag = tag.strip().lower()
        return tag if ":" in tag else f"{tag}:latest"

    return bool(wanted.strip()) and norm(wanted) in {norm(m) for m in models if m}


async def probe_ollama(host: str) -> Optional[List[str]]:
    """Installed model tags if ``host`` runs Ollama, else ``None``."""
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_discovery_timeout_seconds, trust_env=False) as client:
            response = await client.get(host_url(host) + "/api/tags")
        if response.status_code != 200:
            return None
        models = response.json().get("models")
        if not isinstance(models, list):
            return None
        return [str(m.get("name") or m.get("model") or "") for m in models if isinstance(m, dict)]
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


def _primary_ipv4() -> Optional[str]:
    # A UDP "connect" sends nothing; it only picks the outbound interface.
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    return None


def scan_subnets() -> List[str]:
    """/24 prefixes to sweep: OLLAMA_DISCOVERY_SUBNETS, else this machine's private /24."""
    configured = [s.strip() for s in settings.ollama_discovery_subnets.split(",") if s.strip()]
    if configured:
        return configured
    ip = _primary_ipv4()
    if ip and ipaddress.ip_address(ip).is_private and not ipaddress.ip_address(ip).is_loopback:
        return [ip.rsplit(".", 1)[0]]
    return []


async def _port_open(host: str) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, OLLAMA_DEFAULT_PORT), _SCAN_CONNECT_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def scan_for_ollama(subnets: List[str], skip: set) -> List[str]:
    """Hosts in the /24 ``subnets`` with port 11434 open."""
    hosts: List[str] = []
    for subnet in subnets:
        try:
            network = ipaddress.ip_network(f"{subnet}.0/24", strict=False)
        except ValueError:
            logger.warning("Invalid Ollama discovery subnet", subnet=subnet)
            continue
        hosts.extend(str(h) for h in network.hosts() if str(h) not in skip)
    semaphore = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def check(host: str) -> Optional[str]:
        async with semaphore:
            return host if await _port_open(host) else None

    return [h for h in await asyncio.gather(*(check(h) for h in hosts)) if h]


def _easyworship_driver_host() -> Optional[str]:
    with contextlib.suppress(Exception):
        from app.easyworship.service import easyworship_service

        return easyworship_service.remote_host
    return None


async def _discover_easyworship_mdns() -> Optional[str]:
    with contextlib.suppress(Exception):
        from app.easyworship.remote_protocol import discover_easyworship

        found = await discover_easyworship(
            settings.easyworship_remote_discovery_seconds, settings.easyworship_remote_server_name
        )
        return found[0] if found else None
    return None


class OllamaLocator:
    """Resolves and caches the Ollama host; re-resolves when it stops answering."""

    def __init__(
        self,
        *,
        probe: Probe = probe_ollama,
        driver_host: Callable[[], Optional[str]] = _easyworship_driver_host,
        mdns: Callable[[], Awaitable[Optional[str]]] = _discover_easyworship_mdns,
        scan: Callable[[List[str], set], Awaitable[List[str]]] = scan_for_ollama,
    ) -> None:
        self._probe = probe
        self._driver_host = driver_host
        self._mdns = mdns
        self._scan = scan
        self._host: Optional[str] = None
        self._source: Optional[str] = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    @staticmethod
    def active() -> bool:
        return settings.ollama_discovery_enabled and not settings.ollama_base_url.strip()

    @property
    def host(self) -> Optional[str]:
        return self._host

    def base_url(self) -> str:
        configured = settings.ollama_base_url.strip()
        if configured:
            return configured.rstrip("/")
        return host_url(self._host or configured_easyworship_host() or "127.0.0.1")

    def _candidates(self) -> List[Tuple[str, str]]:
        found: List[Tuple[str, str]] = []
        if self._host:
            found.append(("cached", self._host))
        if configured_easyworship_host():
            found.append(("easyworship_config", configured_easyworship_host()))
        driver_host = self._driver_host()
        if driver_host:
            found.append(("easyworship_remote", driver_host))
        found.append(("localhost", "127.0.0.1"))
        return found

    async def _search(self) -> Optional[Tuple[str, str]]:
        fallback: Optional[Tuple[str, str]] = None
        tried: set = set()

        async def check(source: str, hosts: List[str]) -> Optional[Tuple[str, str]]:
            nonlocal fallback
            for host in hosts:
                if host in tried:
                    continue
                tried.add(host)
                models = await self._probe(host)
                if models is None:
                    continue
                if has_model(models, settings.ollama_model):
                    return host, source
                fallback = fallback or (host, source)
            return None

        for source, host in self._candidates():
            if hit := await check(source, [host]):
                return hit
        # A stale EASYWORSHIP_REMOTE_HOST shouldn't stop us finding the PC's new address.
        mdns_host = await self._mdns()
        if mdns_host and (hit := await check("easyworship_mdns", [mdns_host])):
            return hit
        if settings.ollama_discovery_scan:
            if hit := await check("scan", await self._scan(scan_subnets(), set(tried))):
                return hit
        return fallback

    async def resolve(self, *, force: bool = False) -> Optional[str]:
        """Return the Ollama host, searching the network when the cached one doesn't answer."""
        if not self.active():
            return None
        async with self._lock:
            if not force and self._host and await self._probe(self._host) is not None:
                return self._host
            found = await self._search()
            if found is None:
                logger.warning("Ollama not found on the network", tried_cached=self._host)
                return self._host
            host, source = found
            if host != self._host:
                logger.info("Ollama discovered", host=host, source=source, previous=self._host)
                self._host, self._source = host, source
                _reset_llm_clients()
            return self._host

    def request_resolve(self) -> None:
        """Wake the monitor now (e.g. after a failed LLM call)."""
        self._wake.set()

    async def start(self) -> None:
        if not self.active() or (self._task is not None and not self._task.done()):
            return
        self._task = asyncio.create_task(self._monitor())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _monitor(self) -> None:
        while True:
            try:
                await self.resolve()
            except Exception:
                logger.exception("Ollama discovery failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), settings.ollama_discovery_interval_seconds)
            self._wake.clear()

    def status(self) -> dict:
        return {
            "enabled": self.active(),
            "host": self._host,
            "source": self._source if self._host else None,
            "base_url": self.base_url(),
        }


def _reset_llm_clients() -> None:
    # Cached ChatOllama clients hold the old base URL.
    from app.agents import llm

    for factory in (llm.get_llm, llm.get_fast_llm, llm.get_director_llm, llm.get_vision_llm):
        factory.cache_clear()


ollama_locator = OllamaLocator()
