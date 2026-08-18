"""Wall-clock scheduler that auto-starts the service at a configured time.

Runs a background loop that sleeps until the next configured start time on the
configured weekdays, then starts the director (unless it is already running).
Schedule can be reconfigured at runtime via the API.
"""

import asyncio
import contextlib
from datetime import datetime, timedelta
from typing import Optional, Set

from app.config import settings
from app.logging_config import get_logger

from .engine import service_director

logger = get_logger(__name__)

_WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def _parse_days(days: str) -> Set[int]:
    result: Set[int] = set()
    for token in (days or "").split(","):
        token = token.strip().lower()[:3]
        if token in _WEEKDAYS:
            result.add(_WEEKDAYS[token])
    return result or {6}  # default Sunday


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hh, mm = value.split(":")
        return int(hh), int(mm)
    except Exception:
        return 10, 0


class ServiceScheduler:
    """Starts the director at a scheduled local time on selected days."""

    def __init__(self):
        self.enabled = settings.service_auto_start_enabled
        self.time = settings.service_start_time
        self.days = _parse_days(settings.service_start_days)
        self.autonomous = settings.service_autonomous
        self._task: Optional[asyncio.Task] = None

    def next_run(self, now: Optional[datetime] = None) -> Optional[datetime]:
        if not self.enabled or not self.days:
            return None
        now = now or datetime.now()
        hour, minute = _parse_time(self.time)
        for delta in range(0, 8):
            candidate = (now + timedelta(days=delta)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if candidate.weekday() in self.days and candidate > now:
                return candidate
        return None

    def configure(
        self,
        *,
        enabled: Optional[bool] = None,
        time: Optional[str] = None,
        days: Optional[str] = None,
        autonomous: Optional[bool] = None,
    ) -> None:
        if enabled is not None:
            self.enabled = enabled
        if time is not None:
            self.time = time
        if days is not None:
            self.days = _parse_days(days)
        if autonomous is not None:
            self.autonomous = autonomous
        logger.info("Scheduler configured", enabled=self.enabled, time=self.time)

    def info(self) -> dict:
        nxt = self.next_run()
        inverse = {v: k for k, v in _WEEKDAYS.items()}
        return {
            "enabled": self.enabled,
            "time": self.time,
            "days": sorted(inverse[d] for d in self.days),
            "autonomous": self.autonomous,
            "next_run": nxt.isoformat() if nxt else None,
        }

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                nxt = self.next_run()
                if nxt is None:
                    await asyncio.sleep(30)
                    continue
                delay = max(1.0, (nxt - datetime.now()).total_seconds())
                # Cap the sleep so schedule changes are picked up within a minute.
                await asyncio.sleep(min(delay, 60.0))
                now = datetime.now()
                if (
                    self.enabled
                    and now.weekday() in self.days
                    and now.strftime("%H:%M") == self.time
                    and not service_director.status().running
                ):
                    logger.info("Scheduled auto-start firing")
                    await service_director.start(autonomous=self.autonomous)
                    # Avoid retriggering within the same minute.
                    await asyncio.sleep(61)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Scheduler loop error", error=str(e))
                await asyncio.sleep(30)


# Module-level singleton
service_scheduler = ServiceScheduler()
