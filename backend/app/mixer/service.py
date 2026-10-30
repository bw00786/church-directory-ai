"""Yamaha MGX16 mixer integration.

The MGX16 has no remote-control protocol, so nothing here can move the desk's
own faders, preamps, mutes, pan or routing. What *is* controllable is the
companion ``mgx-ai-mixer`` app's **software-DSP takeover**: the MGX16 USB MAIN
is a bidirectional 22x22 interface, and with channel inputs patched to USB the
computer sits in the signal path running per-channel HPF / parametric EQ /
compressor / trim / reverb / delay plus an autonomous feedback guard and a
mix-quality keeper. This service consumes that app's WebSocket (``meters`` for
per-channel RMS, ``analysis`` for spectral/masking/loudness snapshots, ``dsp``
for the takeover state) and drives its REST API.

Meter staleness: every meter sample is stored with a monotonic timestamp. A
channel whose last sample is older than ``MIXER_METER_STALE_SECONDS`` -- or
any channel while the WebSocket is down -- reads as silence (-90 dBFS) rather
than freezing at its last value, so a speaker who was "active" the instant the
feed dropped does not stay active forever and ``wait_for_song_end`` can still
see silence. One ``PERCEPTION_DEGRADED`` (``reason: "mixer meter feed stale"``)
is published per outage and ``PERCEPTION_RESTORED`` when samples flow again.
"""

import asyncio
import contextlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from app.config import settings
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

# Move actions accepted by mgx-ai-mixer's POST /api/moves/apply.
MOVE_ACTIONS = ("fader", "eq_cut", "eq_boost", "hpf", "pan", "comp", "gain")

SILENCE_DB = -90.0
STALE_REASON = "mixer meter feed stale"


def api_url_from_ws(ws_url: str) -> str:
    """ws://host:port/ws -> http://host:port (mgx-ai-mixer serves both)."""
    parts = urlsplit(ws_url)
    scheme = "https" if parts.scheme == "wss" else "http"
    return urlunsplit((scheme, parts.netloc, "", "", ""))


class MixerService:
    """Reads per-channel audio levels from the mixer meter feed and controls
    the companion app's software-DSP layer."""

    def __init__(self, mock: Optional[bool] = None, ws_url: Optional[str] = None):
        self.mock = settings.enable_mock_mixer if mock is None else mock
        self.ws_url = ws_url or settings.mixer_ws_url
        self.api_url = (settings.mixer_api_url or api_url_from_ws(self.ws_url)).rstrip("/")
        # channel -> (rms_db, monotonic timestamp of the sample)
        self._levels: Dict[int, Tuple[float, float]] = {}
        self._stale_reported = False  # one PERCEPTION_DEGRADED per outage
        self._analysis: Optional[dict] = None
        self._analysis_at: Optional[float] = None
        self._dsp: Optional[dict] = None
        self._connected = False
        self._task: Optional[asyncio.Task] = None
        self._transport = None  # httpx transport override (tests)
        # Mock-mode DSP state so the assistant/UI flow can be exercised.
        self._mock_dsp: dict = {
            "master_bypass": True,
            "engaged": False,
            "channels": {},
            "autoguard": {"enabled": False, "armed": False, "excluded": [], "catches": []},
            "automix": {"enabled": False, "armed": False, "events": []},
        }

    async def start(self) -> None:
        if self.mock:
            self._connected = True
            logger.info("Mixer service started (mock)")
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._reader_loop())
        logger.info("Mixer service started", ws_url=self.ws_url, api_url=self.api_url)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._connected = False
        self._levels.clear()

    @property
    def connected(self) -> bool:
        return self._connected

    # -- meter freshness ------------------------------------------------------
    @property
    def stale_seconds(self) -> float:
        return float(settings.mixer_meter_stale_seconds)

    def _sample(self, channel: int) -> Optional[Tuple[float, Optional[float]]]:
        """``(rms_db, monotonic_ts)`` for a channel. A bare float (legacy
        callers/tests poking ``_levels`` directly) is treated as untimestamped
        and therefore never ages out."""
        raw = self._levels.get(channel)
        if raw is None:
            return None
        if isinstance(raw, tuple):
            return raw
        return float(raw), None

    def meter_age(self, channel: int) -> Optional[float]:
        """Seconds since the last meter sample for ``channel`` (None if never)."""
        sample = self._sample(channel)
        if sample is None:
            return None
        rms, ts = sample
        return 0.0 if ts is None else max(0.0, time.monotonic() - ts)

    def _fresh(self, channel: int, now: Optional[float] = None) -> bool:
        sample = self._sample(channel)
        if sample is None:
            return False
        if self.mock:
            return True
        if not self._connected:
            return False
        ts = sample[1]
        if ts is None:
            return True
        now = time.monotonic() if now is None else now
        return (now - ts) <= self.stale_seconds

    @property
    def stale(self) -> bool:
        """True when the meter feed cannot be trusted: disconnected, or we have
        samples but the newest one is older than ``mixer_meter_stale_seconds``.
        A connected feed that has not sent any meters yet is *not* stale (no
        data is distinct from old data). Mock mode is never stale."""
        if self.mock:
            return False
        if not self._connected:
            return True
        stamps = [s[1] for s in (self._sample(ch) for ch in list(self._levels)) if s and s[1] is not None]
        if not stamps:
            return False
        return (time.monotonic() - max(stamps)) > self.stale_seconds

    def _check_stale(self) -> None:
        """Lazy outage detection on read paths (the reader loop is parked on
        the socket and cannot notice a silent-but-open feed)."""
        if self.mock or self._stale_reported:
            return
        if self.stale:
            self._mark_degraded()

    def _mark_degraded(self) -> None:
        if self._stale_reported:
            return
        self._stale_reported = True
        logger.warning("Mixer meter feed stale; channels read as silence", stale_seconds=self.stale_seconds)
        event_bus.publish({"event": "PERCEPTION_DEGRADED", "payload": {"reason": STALE_REASON, "channel": None}})

    def _mark_restored(self) -> None:
        if not self._stale_reported:
            return
        self._stale_reported = False
        logger.info("Mixer meter feed restored")
        event_bus.publish({"event": "PERCEPTION_RESTORED", "payload": {"source": "mixer_meters", "channel": None}})

    def levels(self) -> Dict[int, float]:
        """Fresh per-channel RMS only; stale/disconnected channels are omitted."""
        self._check_stale()
        now = time.monotonic()
        out: Dict[int, float] = {}
        for ch in list(self._levels):
            sample = self._sample(ch)
            if sample is not None and self._fresh(ch, now):
                out[ch] = sample[0]
        return out

    def channel_rms(self, channel: int) -> float:
        """Latest RMS for ``channel``; -90.0 (silence) when the sample is older
        than ``mixer_meter_stale_seconds`` or the feed is disconnected."""
        self._check_stale()
        sample = self._sample(channel)
        if sample is None or not self._fresh(channel):
            return SILENCE_DB
        return sample[0]

    def status(self) -> dict:
        now = time.monotonic()
        return {
            "mock": self.mock,
            "connected": self._connected,
            "stale": self.stale,
            "stale_seconds": self.stale_seconds,
            "channels": {
                ch: {
                    "rms_db": sample[0],
                    "age": None if sample[1] is None else round(now - sample[1], 3),
                    "fresh": self._fresh(ch, now),
                }
                for ch, sample in ((ch, self._sample(ch)) for ch in sorted(self._levels))
                if sample is not None
            },
        }

    def channel_active(self, channel: int, threshold_db: Optional[float] = None) -> bool:
        threshold = settings.song_end_silence_db if threshold_db is None else threshold_db
        return self.channel_rms(channel) > threshold

    def analysis(self) -> Optional[dict]:
        """Latest MixSnapshot from the companion app (per-channel spectral
        bands, headroom, crest factor, masking pairs, LUFS), with its age."""
        if self._analysis is None:
            return None
        age = None if self._analysis_at is None else round(time.monotonic() - self._analysis_at, 1)
        return {**self._analysis, "age_seconds": age}

    def dsp_state(self) -> Optional[dict]:
        """Last pushed software-DSP state (engaged, per-channel processing,
        feedback guard, mix keeper)."""
        return self._mock_dsp if self.mock else self._dsp

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
                logger.warning("Mixer feed reconnecting", error=str(e))
            self._connected = False
            # Never serve the last value as live: a channel "speaking" at the
            # instant of the drop must not stay speaking forever.
            self._levels.clear()
            event_bus.publish({"event": "MIXER_CONNECTION_FAILED", "payload": {}})
            self._mark_degraded()
            await asyncio.sleep(2.0)

    def _ingest(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            return
        kind = message.get("type")
        data = message.get("data")
        if kind == "meters":
            now = time.monotonic()
            got_any = False
            for meter in data or []:
                channel = meter.get("channel")
                if channel is not None:
                    self._levels[int(channel)] = (float(meter.get("rms_db", SILENCE_DB)), now)
                    got_any = True
            if got_any and (self._connected or self.mock):
                self._mark_restored()
        elif kind == "analysis" and isinstance(data, dict):
            self._analysis = data
            self._analysis_at = time.monotonic()
        elif kind == "dsp" and isinstance(data, dict):
            self._dsp = data

    # -- software-DSP control (mgx-ai-mixer REST API) --------------------------
    async def _post(self, path: str, body: Optional[dict] = None) -> dict:
        import httpx

        async with httpx.AsyncClient(
            base_url=self.api_url, timeout=10.0, transport=self._transport
        ) as client:
            resp = await client.post(path, json=body if body is not None else {})
            resp.raise_for_status()
            return resp.json()

    async def _get(self, path: str) -> dict:
        import httpx

        async with httpx.AsyncClient(
            base_url=self.api_url, timeout=10.0, transport=self._transport
        ) as client:
            resp = await client.get(path)
            resp.raise_for_status()
            return resp.json()

    async def fetch_dsp_state(self) -> dict:
        if self.mock:
            return self._mock_dsp
        self._dsp = await self._get("/api/dsp")
        return self._dsp

    async def engage_dsp(self, engage: bool) -> dict:
        """Master software-DSP takeover on/off (bypass hands back the untouched
        signal instantly)."""
        if self.mock:
            self._mock_dsp["engaged"] = engage
            self._mock_dsp["master_bypass"] = not engage
            return self._mock_dsp
        self._dsp = await self._post("/api/dsp/engage", {"engage": engage})
        return self._dsp

    async def reset_dsp(self, channel: Optional[int] = None) -> dict:
        if self.mock:
            if channel is None:
                self._mock_dsp["channels"] = {}
            else:
                self._mock_dsp["channels"].pop(str(channel), None)
            return self._mock_dsp
        self._dsp = await self._post("/api/dsp/reset", {"channel": channel})
        return self._dsp

    async def command(self, text: str) -> dict:
        """Natural-language per-channel effect command, e.g. "hpf 120 Hz on the
        vocals", "the keys are muddy", "channel 2 has feedback, fix it"."""
        if self.mock:
            return {"understood": text, "applied": [], "mode": "mock"}
        return await self._post("/api/command", {"text": text})

    async def apply_move(
        self, channel: int, action: str, param: str = "", amount: str = "", reason: str = ""
    ) -> dict:
        if action not in MOVE_ACTIONS:
            raise ValueError(f"unknown move action '{action}'; use one of {list(MOVE_ACTIONS)}")
        move = {
            "channel": channel,
            "action": action,
            "param": param,
            "amount": amount,
            "reason": reason,
            "priority": 3,
        }
        if self.mock:
            self._mock_dsp["channels"].setdefault(str(channel), {})[action] = f"{param} {amount}".strip()
            return {"applied": True, "mode": "mock", "detail": f"{action} {param} {amount}".strip()}
        return await self._post("/api/moves/apply", move)

    async def advise(self, apply: bool = False) -> dict:
        """Ask the companion app's Claude advisor for a prioritized move sheet
        from the current mix analysis; ``apply`` executes it on the DSP."""
        if self.mock:
            return {"summary": "mock advisor: no live audio", "moves": [], "applied": apply}
        return await self._post("/api/advise?apply=true" if apply else "/api/advise", {})

    async def set_feedback_guard(
        self, enabled: Optional[bool] = None, excluded: Optional[List[int]] = None
    ) -> dict:
        body: Dict[str, Any] = {}
        if enabled is not None:
            body["enabled"] = enabled
        if excluded is not None:
            body["excluded"] = excluded
        if self.mock:
            self._mock_dsp["autoguard"].update({k: v for k, v in body.items()})
            self._mock_dsp["autoguard"]["armed"] = bool(
                self._mock_dsp["autoguard"]["enabled"] and self._mock_dsp["engaged"]
            )
            return self._mock_dsp["autoguard"]
        result = await self._post("/api/autoguard", body)
        if self._dsp is not None:
            self._dsp["autoguard"] = result
        return result

    async def set_mix_keeper(self, enabled: bool) -> dict:
        if self.mock:
            self._mock_dsp["automix"]["enabled"] = enabled
            self._mock_dsp["automix"]["armed"] = bool(enabled and self._mock_dsp["engaged"])
            return self._mock_dsp["automix"]
        result = await self._post("/api/automix", {"enabled": enabled})
        if self._dsp is not None:
            self._dsp["automix"] = result
        return result

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
