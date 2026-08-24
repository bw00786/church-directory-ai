"""Simple energy-threshold voice-activity detection on a channel's RMS meter.

There's no raw waveform available from the Yamaha meter feed (see
app.mixer.service), so this is a threshold + hold-time detector on the RMS
value rather than a classic PCM-based VAD. It still gives clean
speaking/not-speaking transitions instead of reacting to every meter jitter.
"""

import time
from dataclasses import dataclass

from app.config import settings


@dataclass
class VADState:
    speaking: bool = False
    since: float = 0.0  # monotonic time of the last state change
    _silence_started: float | None = None


class ChannelVAD:
    """Tracks speaking/silence for one mixer channel."""

    def __init__(
        self,
        active_threshold_db: float | None = None,
        silence_hold_seconds: float | None = None,
    ):
        self.active_threshold_db = (
            settings.speech_active_db if active_threshold_db is None else active_threshold_db
        )
        self.silence_hold_seconds = (
            settings.speech_silence_hold_seconds if silence_hold_seconds is None else silence_hold_seconds
        )
        self._state = VADState(since=time.monotonic())

    def update(self, rms_db: float) -> VADState:
        """Feed a new RMS reading; returns the (possibly updated) state."""
        now = time.monotonic()
        active = rms_db > self.active_threshold_db

        if active:
            self._state._silence_started = None
            if not self._state.speaking:
                self._state = VADState(speaking=True, since=now)
        else:
            if self._state.speaking:
                if self._state._silence_started is None:
                    self._state._silence_started = now
                elif now - self._state._silence_started >= self.silence_hold_seconds:
                    self._state = VADState(speaking=False, since=now)

        return self._state

    @property
    def speaking(self) -> bool:
        return self._state.speaking
