"""Source-arbitrating audio observer (WO-MGX-USB-1).

Preferred perception source is the MGX16 **USB MAIN** PCM stream: real
per-channel waveform -> Silero VAD -> role-attributed Whisper ASR. The
listen-only **meter feed** (RMS-only energy VAD, no ASR) is the degraded
fallback. Both may run; USB wins per channel while healthy.

Degradation ladder: a channel with no USB frame within ``MGX_USB_STALL_SECONDS``
falls back to the meter feed and emits ``PERCEPTION_DEGRADED``; when USB frames
resume it switches back and emits ``PERCEPTION_RESTORED``. The decision loop
never stalls on a dead device.

Cross-channel bleed: the congregation mic hears the pastor through the PA, so a
quiet copy of the sermon shows up on channel 8 and would be transcribed twice
(and mis-attributed). Before VAD/ASR each USB frame's RMS is compared with the
loudest *other* channel active in the same ~200 ms window; a channel more than
``AUDIO_BLEED_SUPPRESS_DB`` below the leader is treated as silence for that
frame (``AUDIO_BLEED_SUPPRESS_ENABLED``).

Regression gate (AC-5): with ``MGX_USB_ENABLED=false`` this behaves byte-for-byte
like the pre-WO meter-only observer.
"""

import asyncio
import contextlib
import time
from typing import Dict, Optional, Tuple

import numpy as np

from app.audio.silero_vad import make_channel_vad, resolve_provider
from app.audio.vad import ChannelVAD
from app.audio.whisper_service import multichannel_transcriber
from app.audio.yamaha_capture import configured_role_channels
from app.config import settings
from app.domain.observations import AudioObservation
from app.domain.service_context import service_context
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

BLEED_WINDOW_SECONDS = 0.2  # "co-active" = level sample within this of the frame
_SILENCE_DB = -90.0


def frame_rms_db(pcm) -> float:
    arr = np.asarray(pcm, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return _SILENCE_DB
    rms = float(np.sqrt(np.mean(np.square(arr))))
    return max(_SILENCE_DB, 20.0 * float(np.log10(max(rms, 1e-10))))


class AudioObserver:
    """Polls the meter feed and (when enabled) the USB PCM stream per role."""

    def __init__(self, poll_seconds: float = 0.25):
        self.poll_seconds = poll_seconds
        self._meter_vads: Dict[int, ChannelVAD] = {}
        self._usb_vads: Dict[int, object] = {}
        self._provider: Dict[int, str] = {}
        self._source: Dict[int, str] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._usb_active = False
        # Cross-channel bleed suppression state: channel -> (rms_db, frame_t)
        self._levels: Dict[int, Tuple[float, float]] = {}
        self._bleed_suppressed: Dict[int, int] = {}
        # Per-role speaking map (published to service_context on change only)
        self.speaking_roles: Dict[str, dict] = {}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        # Best-effort USB capture; failures degrade to the meter feed, loudly.
        if settings.mgx_usb_enabled:
            try:
                from app.audio.usb_capture import usb_capture

                usb_capture.subscribe(self._on_usb_frame)
                self._usb_active = await usb_capture.start()
                if not self._usb_active:
                    self._emit_degraded("usb capture did not start")
            except Exception as e:  # noqa: BLE001 (incl. UsbCaptureError)
                logger.warning("MGX USB capture unavailable; using meter feed", reason=str(e))
                self._emit_degraded(f"usb start failed: {e}")

        self._task = asyncio.create_task(self._run())
        logger.info("Audio observer started", usb_active=self._usb_active)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if settings.mgx_usb_enabled:
            with contextlib.suppress(Exception):
                from app.audio.usb_capture import usb_capture

                await usb_capture.stop()

    # -- cross-channel bleed suppression ------------------------------------
    def _is_bleed(self, channel: int, rms_db: float, t: float) -> bool:
        """True if this frame is just a quiet copy of a louder co-active channel."""
        if not settings.audio_bleed_suppress_enabled:
            return False
        if rms_db <= settings.speech_active_db:
            return False  # already silence-level; nothing to suppress
        loudest: Optional[float] = None
        for other, (db, ts) in self._levels.items():
            if other == channel or abs(t - ts) > BLEED_WINDOW_SECONDS:
                continue
            if db <= settings.speech_active_db:
                continue
            if loudest is None or db > loudest:
                loudest = db
        return loudest is not None and rms_db < loudest - settings.audio_bleed_suppress_db

    # -- USB (preferred) path -------------------------------------------------
    def _on_usb_frame(self, role: str, channel: int, pcm, t: float) -> None:
        """Called (on the loop thread) for each resampled per-channel frame."""
        vad = self._usb_vads.get(channel)
        if vad is None:
            vad, provider = make_channel_vad(want_silero=resolve_provider(True) == "silero")
            self._usb_vads[channel] = vad
            self._provider[channel] = provider
        previous = bool(getattr(vad, "speaking", False))

        arr = np.asarray(pcm, dtype=np.float32).reshape(-1)
        rms_db = frame_rms_db(arr)
        suppressed = self._is_bleed(channel, rms_db, t)
        self._levels[channel] = (rms_db, t)
        if suppressed:
            self._bleed_suppressed[channel] = self._bleed_suppressed.get(channel, 0) + 1
            arr = np.zeros_like(arr)
            rms_db = _SILENCE_DB

        # Silero consumes PCM; the energy fallback needs a scalar RMS (dB).
        if self._provider.get(channel) == "silero":
            state = vad.update(arr)
        else:
            state = vad.update(rms_db)

        self._record(channel, role, state.speaking, previous, source="usb")
        # VAD-gated ASR: silence (incl. suppressed bleed) is never streamed into Whisper.
        multichannel_transcriber.feed(role, channel, arr, state.speaking, t)

    # -- meter (fallback) path + arbitration ---------------------------------
    async def _run(self) -> None:
        from app.mixer.service import mixer_service

        while self._running:
            usb_healthy = self._usb_healthy_fn()
            for role_channel in configured_role_channels():
                channel = role_channel.channel
                healthy = usb_healthy(channel) if usb_healthy is not None else False
                desired = "usb" if healthy else "meter"
                self._arbitrate(channel, desired)

                if desired == "meter":
                    vad = self._meter_vads.setdefault(channel, ChannelVAD())
                    self._provider[channel] = "energy"
                    previous = vad.speaking
                    state = vad.update(mixer_service.channel_rms(channel))
                    self._record(channel, role_channel.role, state.speaking, previous, source="meter")

            await asyncio.sleep(self.poll_seconds)

    def _usb_healthy_fn(self):
        if not self._usb_active:
            return None
        from app.audio.usb_capture import usb_capture

        return usb_capture.healthy

    def _arbitrate(self, channel: int, desired: str) -> None:
        current = self._source.get(channel)
        if current == desired:
            return
        if current is not None:  # not the first assignment for this channel
            if desired == "meter":
                self._emit_degraded(f"channel {channel} fell back to meter feed", channel=channel)
            else:
                event_bus.publish(
                    {"event": "PERCEPTION_RESTORED", "payload": {"channel": channel, "source": "usb"}}
                )
                logger.info("Perception restored", channel=channel)
        self._source[channel] = desired

    # -- shared record + transition emit -------------------------------------
    def _record(self, channel: int, role: str, speaking: bool, previous: bool, source: str) -> None:
        observation = AudioObservation(
            channel=channel,
            speaker_role=role,
            speaking=speaking,
            confidence=1.0 if speaking else 0.0,
        )
        service_context.record_audio(observation)
        self._update_speaking_map(role, channel, speaking, source)
        if speaking != previous:
            # Payload shape preserved from the pre-WO observer for subscriber
            # compatibility; degradation is signaled by PERCEPTION_* events.
            event_bus.publish(
                {
                    "event": "AUDIO_STARTED" if speaking else "AUDIO_STOPPED",
                    "payload": observation.model_dump(mode="json"),
                }
            )

    def _update_speaking_map(self, role: str, channel: int, speaking: bool, source: str) -> None:
        """Maintain speaking_roles[role]; mutate only on a state change."""
        now = time.time()
        entry = self.speaking_roles.get(role)
        if entry is None:
            self.speaking_roles[role] = {
                "speaking": speaking,
                "since": now,
                "last_spoke_at": now if speaking else None,
                "channel": channel,
                "source": source,
            }
            return
        if entry["speaking"] == speaking:
            if speaking:
                entry["last_spoke_at"] = now
            if entry.get("source") != source:
                entry["source"] = source
            return
        entry["speaking"] = speaking
        entry["since"] = now
        entry["channel"] = channel
        entry["source"] = source
        entry["last_spoke_at"] = now  # start or end of speech both count as "spoke"

    def _emit_degraded(self, reason: str, channel: Optional[int] = None) -> None:
        logger.warning("Perception degraded", reason=reason, channel=channel)
        event_bus.publish(
            {"event": "PERCEPTION_DEGRADED", "payload": {"reason": reason, "channel": channel}}
        )

    # -- status ---------------------------------------------------------------
    def perception_status(self) -> dict:
        capture = None
        if self._usb_active:
            from app.audio.usb_capture import usb_capture as _uc

            capture = _uc
        roles = {}
        for rc in configured_role_channels():
            source = self._source.get(rc.channel, "meter")
            age = capture.frame_age(rc.channel) if capture is not None else float("inf")
            level = self._levels.get(rc.channel)
            roles[rc.role] = {
                "channel": rc.channel,
                "source": source,
                "vad_provider": self._provider.get(rc.channel, "energy"),
                "asr": source == "usb" and multichannel_transcriber.transcribes(rc.role),
                "last_frame_age": None if age == float("inf") else round(age, 3),
                "rms_db": None if level is None else round(level[0], 1),
                "bleed_suppressed": self._bleed_suppressed.get(rc.channel, 0),
            }
        status = {
            "usb_enabled": settings.mgx_usb_enabled,
            "usb_active": self._usb_active,
            "channels": roles,
            "speaking_roles": {role: dict(v) for role, v in self.speaking_roles.items()},
        }
        if capture is not None:
            status["capture"] = capture.status()
        return status


# Module-level singleton
audio_observer = AudioObserver()
