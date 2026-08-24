"""Combines per-channel VAD (Yamaha DM3 meters) into AudioObservations.

This is the primary "is X speaking?" signal for the AI Director: it polls the
mixer's per-channel RMS (see app.mixer.service / app.audio.yamaha_capture) for
the four configured roles (pastor, liturgist, vocalist, congregation) and
turns speaking-state transitions into AudioObservation events.

Transcription (Whisper) is not available on this path — the Yamaha meter feed
has no raw PCM. When ENABLE_WHISPER is on, transcripts instead arrive via
app.identity.audio_capture (local mic/line-in capture) and are merged into the
same ServiceContext; see that module for details.
"""

import asyncio
import contextlib
from typing import Dict, Optional

from app.audio.vad import ChannelVAD
from app.audio.yamaha_capture import configured_role_channels
from app.domain.observations import AudioObservation
from app.domain.service_context import service_context
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)


class AudioObserver:
    """Polls mixer RMS per role/channel and emits AudioObservations."""

    def __init__(self, poll_seconds: float = 0.25):
        self.poll_seconds = poll_seconds
        self._vads: Dict[int, ChannelVAD] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Audio observer started")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        from app.mixer.service import mixer_service

        while self._running:
            for role_channel in configured_role_channels():
                vad = self._vads.setdefault(role_channel.channel, ChannelVAD())
                rms = mixer_service.channel_rms(role_channel.channel)
                previous_speaking = vad.speaking
                state = vad.update(rms)

                observation = AudioObservation(
                    channel=role_channel.channel,
                    speaker_role=role_channel.role,
                    speaking=state.speaking,
                    confidence=1.0 if state.speaking else 0.0,
                )
                service_context.record_audio(observation)

                if state.speaking != previous_speaking:
                    event_bus.publish(
                        {
                            "event": "AUDIO_STARTED" if state.speaking else "AUDIO_STOPPED",
                            "payload": observation.model_dump(mode="json"),
                        }
                    )

            await asyncio.sleep(self.poll_seconds)


# Module-level singleton
audio_observer = AudioObserver()
