"""Discover the ATEM switcher's current IP on the LAN.

The ATEM takes a DHCP address, so it changes each time it's power-cycled.
This resolver sweeps the configured subnets (ping to populate ARP) and
identifies the ATEM by its MAC OUI (Blackmagic = 7c:2e:0d), caching the result
and re-scanning when the cached address stops answering. No .env edit or
backend restart needed between Sundays.
"""

from __future__ import annotations

import asyncio
import ipaddress
import subprocess
from typing import Optional

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


class AtemResolver:
    """Resolves and caches the ATEM IP; re-resolves on failure."""

    def __init__(self) -> None:
        self._resolved: Optional[str] = None
        self._lock = asyncio.Lock()

    def _subnets(self) -> list[str]:
        raw = settings.atem_resolve_subnets.strip()
        if not raw:
            return []
        return [s.strip() for s in raw.split(",") if s.strip()]

    def _matches(self, mac: str) -> bool:
        mac = mac.lower().replace("-", ":")
        configured = (settings.atem_resolve_mac or "").lower().replace("-", ":")
        if configured:
            return mac == configured
        return mac.startswith(settings.atem_resolve_oui.lower())

    def _arp_table(self) -> dict[str, str]:
        try:
            output = subprocess.run(
                ["arp", "-a"], capture_output=True, text=True, timeout=15
            ).stdout
        except Exception:
            return {}
        table: dict[str, str] = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0][0].isdigit() and "-" in parts[1]:
                table[parts[0]] = parts[1].lower().replace("-", ":")
        return table

    async def _ping_sweep(self) -> None:
        subnets = self._subnets()
        if not subnets:
            return

        async def _ping(ip: str) -> None:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-n", "1", "-w", str(settings.atem_resolve_ping_timeout_ms), ip,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

        hosts = []
        for subnet in subnets:
            try:
                base = ipaddress.ip_network(f"{subnet}.0/24", strict=False)
                hosts.extend(str(h) for h in base.hosts())
            except ValueError:
                logger.warning("Invalid ATEM resolve subnet", subnet=subnet)
        # Ping concurrently in bounded batches to populate the ARP cache fast.
        semaphore = asyncio.Semaphore(64)

        async def _guarded(ip: str) -> None:
            async with semaphore:
                await _ping(ip)

        await asyncio.gather(*(_guarded(h) for h in hosts), return_exceptions=True)

    async def _scan(self) -> Optional[str]:
        await self._ping_sweep()
        for ip, mac in self._arp_table().items():
            if self._matches(mac):
                logger.info("ATEM resolver found switcher", ip=ip, mac=mac)
                return ip
        return None

    async def resolve(self, *, force: bool = False) -> Optional[str]:
        """Return the ATEM IP: cached, then configured, then a subnet scan."""
        if not settings.atem_resolve_enabled:
            return self._resolved or settings.atem_ip
        async with self._lock:
            if not force:
                if self._resolved:
                    return self._resolved
                if settings.atem_ip:
                    # Trust the configured IP first; we re-resolve on connect failure.
                    return settings.atem_ip
            found = await self._scan()
            if found:
                self._resolved = found
            elif not force and settings.atem_ip:
                return settings.atem_ip
            return self._resolved


atem_resolver = AtemResolver()
