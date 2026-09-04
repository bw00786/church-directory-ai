"""EasyWorship slide-control service."""

import asyncio
from typing import Callable, Optional

from app.config import settings
from app.events.bus import event_bus
from app.logging_config import get_logger

from .driver import EasyWorshipDriver, build_driver
from .slide_verification import SLIDE_CHANGE_ACTIONS, slide_verifier

logger = get_logger(__name__)

# Actions supported by the service and the underlying driver.
ACTIONS = ("next_slide", "prev_slide", "next_item", "prev_item", "clear", "logo", "black", "live")


class EasyWorshipService:
    """Controls EasyWorship slide advancement for the director and operators.

    With the remote-protocol driver every navigation command is confirmed
    against the position EasyWorship reports back (``pres_no``/``slide_no``);
    with keystroke drivers the service falls back to best-effort counting.
    """

    def __init__(self, driver: Optional[EasyWorshipDriver] = None):
        self._driver = driver or build_driver()
        self._connected = False
        self._last_action: Optional[str] = None
        self._last_confirmed: Optional[bool] = None
        # Best-effort item tracking for drivers without read-back: only
        # reflects actions *this service* has issued (index into the service
        # plan's easyworship_item order). See select_item().
        self._current_item_index = 0
        self._current_item_label: Optional[str] = None
        if self._driver.supports_confirmation:
            self._driver.on_state = self._on_remote_state  # type: ignore[attr-defined]

    async def start(self) -> None:
        self._connected = await self._driver.connect()
        logger.info(
            "EasyWorship service started",
            connected=self._connected,
            driver=type(self._driver).__name__,
        )

    async def stop(self) -> None:
        disconnect = getattr(self._driver, "disconnect", None)
        if disconnect is not None:
            await disconnect()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def confirms_actions(self) -> bool:
        return self._driver.supports_confirmation and settings.easyworship_confirm_actions

    # -- remote-protocol state ------------------------------------------------
    def _remote_state(self):
        return getattr(self._driver, "state", None)

    def _on_remote_state(self, state) -> None:
        self._connected = state.paired
        event_bus.publish({"event": "EASYWORSHIP_STATE", "payload": state.as_dict()})

    async def _confirm(self, predicate: Callable, description: str) -> bool:
        """Wait for EasyWorship to report the expected position."""
        if not self.confirms_actions:
            return True
        ok = await self._driver.wait_for(  # type: ignore[attr-defined]
            predicate, settings.easyworship_confirm_timeout_seconds
        )
        self._last_confirmed = ok
        if not ok:
            state = self._remote_state()
            logger.warning(
                "EasyWorship did not confirm action",
                expected=description,
                pres_no=getattr(state, "pres_no", None),
                slide_no=getattr(state, "slide_no", None),
            )
            event_bus.publish(
                {
                    "event": "EASYWORSHIP_UNCONFIRMED",
                    "payload": {"expected": description, "state": state.as_dict() if state else None},
                }
            )
        return ok

    # -- schedule navigation --------------------------------------------------
    def _item_labels(self) -> list[str]:
        from app.domain.service_plan import build_default_service_plan

        return [
            el.easyworship_item
            for el in build_default_service_plan().elements
            if el.easyworship_item
        ]

    async def select_item(self, label: str) -> bool:
        """Go live on the schedule item matching ``label`` in the service plan's
        EasyWorship order.

        Remote protocol: absolute ``gotoSchedule N`` + presentation start,
        confirmed against the reported ``pres_no``. Keystroke drivers: walk
        next_item/prev_item from the tracked index (best-effort; only accurate
        if all navigation goes through this service).
        """
        labels = self._item_labels()
        if label not in labels:
            logger.warning("Unknown EasyWorship item label", label=label)
            return False
        target_index = labels.index(label)

        if self._driver.supports_confirmation:
            number = target_index + 1 + settings.easyworship_schedule_offset
            if not self._connected:
                self._connected = await self._driver.connect()
            ok = await self._driver.goto_schedule(number)  # type: ignore[attr-defined]
            if ok:
                ok = await self._confirm(
                    lambda s, n=number: s.pres_no == n, f"pres_no == {number} ({label})"
                )
            if ok:
                self._last_action = "select_item"
                self._current_item_index = target_index
                self._current_item_label = label
                if settings.slide_verify_enabled:
                    asyncio.create_task(slide_verifier.verify_after_action("next_item"))
            return ok

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

        before = self._remote_state()
        snapshot = (before.pres_no, before.slide_no, before.status_count) if before else None

        ok = await self._driver.send_action(name)
        if ok and snapshot is not None and name in SLIDE_CHANGE_ACTIONS:
            ok = await self._confirm(
                self._navigation_predicate(name, snapshot), f"{name} from {snapshot[:2]}"
            )
        if ok:
            self._last_action = name
            if name == "next_item":
                self._current_item_index += 1
            elif name == "prev_item":
                self._current_item_index = max(0, self._current_item_index - 1)
            if name in SLIDE_CHANGE_ACTIONS and settings.slide_verify_enabled:
                asyncio.create_task(slide_verifier.verify_after_action(name))
        return ok

    @staticmethod
    def _navigation_predicate(name: str, snapshot: tuple) -> Callable:
        pres_no, slide_no, count = snapshot
        if name == "next_slide" and slide_no is not None:
            # Past the last slide EasyWorship may roll into the next item.
            return lambda s: s.slide_no == slide_no + 1 or (
                s.pres_no is not None and pres_no is not None and s.pres_no > pres_no
            )
        if name == "prev_slide" and slide_no is not None:
            return lambda s: s.slide_no == slide_no - 1 or (
                s.pres_no is not None and pres_no is not None and s.pres_no < pres_no
            )
        if name == "next_item" and pres_no is not None:
            return lambda s: s.pres_no == pres_no + 1
        if name == "prev_item" and pres_no is not None:
            return lambda s: s.pres_no == pres_no - 1
        # Position unknown before the command: accept any fresh status.
        return lambda s: s.status_count > count

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

    async def goto_slide(self, number: int) -> bool:
        """Jump to slide ``number`` (1-based) in the live item (remote protocol only)."""
        if not self._driver.supports_confirmation:
            return False
        ok = await self._driver.goto_slide(number)  # type: ignore[attr-defined]
        if ok:
            ok = await self._confirm(lambda s, n=number: s.slide_no == n, f"slide_no == {number}")
        if ok:
            self._last_action = "goto_slide"
            if settings.slide_verify_enabled:
                asyncio.create_task(slide_verifier.verify_after_action("next_slide"))
        return ok

    def status(self) -> dict:
        state = self._remote_state()
        current_index = self._current_item_index
        if state is not None and state.pres_no is not None:
            current_index = state.pres_no - 1 - settings.easyworship_schedule_offset
        return {
            "connected": self._connected,
            "driver": type(self._driver).__name__,
            "confirms_actions": self.confirms_actions,
            "last_action": self._last_action,
            "last_confirmed": self._last_confirmed,
            "current_item_index": current_index,
            "current_item_label": self._current_item_label,
            "remote_state": state.as_dict() if state is not None else None,
            "slide_verification": slide_verifier.snapshot(),
        }


# Module-level singleton
easyworship_service = EasyWorshipService()

