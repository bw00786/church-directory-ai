"""EasyWorship slide-control service."""

from typing import Optional

from app.logging_config import get_logger

from .driver import EasyWorshipDriver, build_driver

logger = get_logger(__name__)

# Actions supported by the service and the underlying driver.
ACTIONS = ("next_slide", "prev_slide", "next_item", "prev_item", "clear", "logo", "black", "live")


class EasyWorshipService:
    """Controls EasyWorship slide advancement for the director and operators."""

    def __init__(self, driver: Optional[EasyWorshipDriver] = None):
        self._driver = driver or build_driver()
        self._connected = False
        self._last_action: Optional[str] = None

    async def start(self) -> None:
        self._connected = await self._driver.connect()
        logger.info("EasyWorship service started", connected=self._connected)

    @property
    def connected(self) -> bool:
        return self._connected

    async def action(self, name: str) -> bool:
        """Perform a named action (see ``ACTIONS``)."""
        if name not in ACTIONS:
            logger.warning("Unknown EasyWorship action", action=name)
            return False
        if not self._connected:
            # Try a lazy (re)connect before giving up.
            self._connected = await self._driver.connect()
        ok = await self._driver.send_action(name)
        if ok:
            self._last_action = name
        return ok

    async def next_slide(self) -> bool:
        return await self.action("next_slide")

    async def previous_slide(self) -> bool:
        return await self.action("prev_slide")

    async def next_item(self) -> bool:
        return await self.action("next_item")

    async def previous_item(self) -> bool:
        return await self.action("prev_item")

    async def clear(self) -> bool:
        return await self.action("clear")

    async def go_live(self) -> bool:
        return await self.action("live")

    def status(self) -> dict:
        return {"connected": self._connected, "last_action": self._last_action}


# Module-level singleton
easyworship_service = EasyWorshipService()
