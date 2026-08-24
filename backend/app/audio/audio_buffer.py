"""Per-channel rolling audio/transcript buffer.

Keeps the most recent transcript lines per mixer channel so the AI Director's
context window (app.domain.service_context) has continuity even when
individual AudioObservations only cover a short window each.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict


@dataclass
class ChannelBuffer:
    lines: Deque[str] = field(default_factory=lambda: deque(maxlen=20))

    def add(self, text: str) -> None:
        if text:
            self.lines.append(text)

    def recent_text(self, limit: int = 5) -> str:
        return " ".join(list(self.lines)[-limit:])


class AudioBuffer:
    """Per-channel ChannelBuffer registry."""

    def __init__(self) -> None:
        self._channels: Dict[int, ChannelBuffer] = {}

    def for_channel(self, channel: int) -> ChannelBuffer:
        return self._channels.setdefault(channel, ChannelBuffer())

    def add(self, channel: int, text: str) -> None:
        self.for_channel(channel).add(text)
