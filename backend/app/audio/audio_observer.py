"""Source-arbitrating audio observer (WO-MGX-USB-1).

Preferred perception source is the MGX16 **USB MAIN** PCM stream: real
per-channel waveform -> Silero VAD -> role-attributed Whisper ASR. The
listen-only **meter feed** (RMS-only energy VAD, no ASR) is the degraded
fallback. Both may run; USB wins per channel while healthy.

Degradation ladder: a channel with no USB frame within ``MGX_USB_STALL_SECONDS``
falls back to the meter feed and emits ``PERCEPTION_DEGRADED``; when USB frames
resume it switches back and emits ``PERCEPTION_RESTORED``. The decision loop
never stalls on a dead device.

Regression gate (AC-5): with ``MGX_USB_ENABLED=false`` this behaves byte-for-byte
like the pre-WO meter-only observer.
"""

import asyncio
import contextlib
from typing import Dict, Optional

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

    # -- USB (preferred) path -------------------------------------------------
    def _on_usb_frame(self, role: str, channel: int, pcm, t: float) -> None:
        """Called (on the loop thread) for each resampled per-channel frame."""
        vad = self._usb_vads.get(channel)
        if vad is None:
            vad, provider = make_channel_vad(want_silero=resolve_provider(True) == "silero")
            self._usb_vads[channel] = vad
            self._provider[channel] = provider
        previous = bool(getattr(vad, "speaking", False))

        # Silero consumes PCM; the energy fallback needs a scalar RMS (dB).
        if self._provider.get(channel) == "silero":
            state = vad.update(pcm)
        else:
            arr = np.asarray(pcm, dtype=np.float32).reshape(-1)
            rms = float(np.sqrt(np.mean(np.square(arr)))) if arr.size else 0.0
            state = vad.update(20.0 * float(np.log10(max(rms, 1e-10))))

        self._record(channel, role, state.speaking, previous, source="usb")
        # VAD-gated ASR: silence is never streamed into Whisper.
        multichannel_transcriber.feed(role, channel, pcm, state.speaking, t)

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
        if speaking != previous:
            # Payload shape preserved from the pre-WO observer for subscriber
            # compatibility; degradation is signaled by PERCEPTION_* events.
            event_bus.publish(
                {
                    "event": "AUDIO_STARTED" if speaking else "AUDIO_STOPPED",
                    "payload": observation.model_dump(mode="json"),
                }
            )

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
            roles[rc.role] = {
                "channel": rc.channel,
                "source": source,
                "vad_provider": self._provider.get(rc.channel, "energy"),
                "asr": source == "usb" and multichannel_transcriber.transcribes(rc.role),
                "last_frame_age": None if age == float("inf") else round(age, 3),
            }
        return {
            "usb_enabled": settings.mgx_usb_enabled,
            "usb_active": self._usb_active,
            "channels": roles,
        }


# Module-level singleton
audio_observer = AudioObserver()
