"""Yamaha MGX16 mixer integration (listen-only).

The MGX16 has no remote-control protocol, so this service never moves faders or
mutes channels. Instead it consumes the meter WebSocket published by the
companion ``mgx-ai-mixer`` app (``{"type": "meters", "data": [ChannelMeter...]}``)
and exposes per-channel RMS levels plus helpers used by the service director to
detect when a song starts and ends.
"""

import asyncio
import contextlib
import json
import time
from typing import Dict, List, Optional

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


class MixerService:
    """Reads per-channel audio levels from the mixer meter feed."""

    def __init__(self, mock: Optional[bool] = None, ws_url: Optional[str] = None):
        self.mock = settings.enable_mock_mixer if mock is None else mock
        self.ws_url = ws_url or settings.mixer_ws_url
        self._levels: Dict[int, float] = {}
        self._connected = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self.mock:
            self._connected = True
            logger.info("Mixer service started (mock)")
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._reader_loop())
        logger.info("Mixer service started", ws_url=self.ws_url)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def levels(self) -> Dict[int, float]:
        return dict(self._levels)

    def channel_rms(self, channel: int) -> float:
        return self._levels.get(channel, -90.0)

    def channel_active(self, channel: int, threshold_db: Optional[float] = None) -> bool:
        threshold = settings.song_end_silence_db if threshold_db is None else threshold_db
        return self.channel_rms(channel) > threshold

    async def _reader_loop(self) -> None:
        """Maintain a WebSocket connection to the mixer meter feed."""
        try:
            import websockets
        except ImportError:
            logger.warning("`websockets` not installed; mixer meters unavailable")
            return

        while True:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._connected = True
                    logger.info("Mixer meter feed connected", ws_url=self.ws_url)
                    async for raw in ws:
                        self._ingest(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._connected = False
                logger.warning("Mixer feed reconnecting", error=str(e))
                await asyncio.sleep(2.0)

    def _ingest(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            return
        if message.get("type") != "meters":
            return
        for meter in message.get("data", []):
            channel = meter.get("channel")
            if channel is not None:
                self._levels[int(channel)] = float(meter.get("rms_db", -90.0))

    async def wait_for_song_end(
        self,
        channels: List[int],
        *,
        threshold_db: Optional[float] = None,
        hold_seconds: Optional[float] = None,
        max_wait_seconds: Optional[float] = None,
    ) -> bool:
        """Wait until the given channels are active, then fall silent.

        Returns True when a song-end (sustained silence) is detected, or False
        if ``max_wait_seconds`` elapses first (caller should fall back to a
        manual advance).
        """
        threshold = settings.song_end_silence_db if threshold_db is None else threshold_db
        hold = settings.song_end_hold_seconds if hold_seconds is None else hold_seconds
        max_wait = settings.song_max_wait_seconds if max_wait_seconds is None else max_wait_seconds

        if self.mock:
            await asyncio.sleep(settings.mock_song_seconds)
            return True

        started = time.monotonic()

        # Phase 1: wait for the song to actually start.
        while time.monotonic() - started < max_wait:
            if any(self.channel_active(ch, threshold) for ch in channels):
                break
            await asyncio.sleep(0.2)

        # Phase 2: wait for sustained silence across all channels.
        silent_since: Optional[float] = None
        while time.monotonic() - started < max_wait:
            all_silent = all(not self.channel_active(ch, threshold) for ch in channels)
            now = time.monotonic()
            if all_silent:
                if silent_since is None:
                    silent_since = now
                elif now - silent_since >= hold:
                    return True
            else:
                silent_since = None
            await asyncio.sleep(0.2)

        logger.warning("Song-end detection timed out", channels=channels)
        return False


# Module-level singleton
mixer_service = MixerService()
