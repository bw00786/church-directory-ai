"""EasyWorship slide-control service."""

import asyncio
from typing import Optional

from app.config import settings
from app.logging_config import get_logger

from .driver import EasyWorshipDriver, build_driver
from .slide_verification import SLIDE_CHANGE_ACTIONS, slide_verifier

logger = get_logger(__name__)

# Actions supported by the service and the underlying driver.
ACTIONS = ("next_slide", "prev_slide", "next_item", "prev_item", "clear", "logo", "black", "live")


class EasyWorshipService:
    """Controls EasyWorship slide advancement for the director and operators."""

    def __init__(self, driver: Optional[EasyWorshipDriver] = None):
        self._driver = driver or build_driver()
        self._connected = False
        self._last_action: Optional[str] = None
        # Best-effort item tracking: EasyWorship has no read-back API, so this
        # only reflects actions *this service* has issued (index into the
        # service plan's easyworship_item order). See select_item().
        self._current_item_index = 0
        self._current_item_label: Optional[str] = None

    async def start(self) -> None:
        self._connected = await self._driver.connect()
        logger.info("EasyWorship service started", connected=self._connected)

    @property
    def connected(self) -> bool:
        return self._connected

    def _item_labels(self) -> list[str]:
        from app.domain.service_plan import build_default_service_plan

        return [
            el.easyworship_item
            for el in build_default_service_plan().elements
            if el.easyworship_item
        ]

    async def select_item(self, label: str) -> bool:
        """Advance/rewind to the item matching ``label`` in the service plan's
        EasyWorship order, using only next_item/prev_item presses (EasyWorship
        has no direct "jump to slide" API). Best-effort: assumes the tracked
        current index is accurate, which only holds if all navigation goes
        through this service.
        """
        labels = self._item_labels()
        if label not in labels:
            logger.warning("Unknown EasyWorship item label", label=label)
            return False

        target_index = labels.index(label)
        delta = target_index - self._current_item_index
        step_action = "next_item" if delta > 0 else "prev_item"

        ok = True
        for _ in range(abs(delta)):
            ok = await self.action(step_action) and ok

        self._current_item_index = target_index
        self._current_item_label = label
        return ok

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
            if name == "next_item":
                self._current_item_index += 1
            elif name == "prev_item":
                self._current_item_index = max(0, self._current_item_index - 1)
            if name in SLIDE_CHANGE_ACTIONS and settings.slide_verify_enabled:
                asyncio.create_task(slide_verifier.verify_after_action(name))
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
        return {
            "connected": self._connected,
            "last_action": self._last_action,
            "current_item_index": self._current_item_index,
            "current_item_label": self._current_item_label,
            "slide_verification": slide_verifier.snapshot(),
        }


# Module-level singleton
easyworship_service = EasyWorshipService()

