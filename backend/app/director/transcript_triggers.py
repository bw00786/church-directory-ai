"""Deterministic transcript cues for the scripted worship service."""

import re
from typing import Optional


_CALL_TO_WORSHIP_START = re.compile(
    r"\b(call\s+to\s+worship|opening\s+prayer|let(?:'s|\s+us)\s+worship|please\s+stand)\b",
    re.IGNORECASE,
)
_OPENING_PRAYER_END = re.compile(
    r"\b(amen|in\s+jesus['’]?\s+name)\b",
    re.IGNORECASE,
)
_SCRIPTURE_END = re.compile(
    r"\b((this|that)\s+is\s+the\s+word\s+of\s+(the\s+lord|god)|thanks\s+be\s+to\s+god|the\s+word\s+of\s+the\s+lord)\b",
    re.IGNORECASE,
)


def classify_transcript(cue_id: str, role: str, text: str) -> Optional[tuple[str, float]]:
    """Return ``(reason, confidence)`` for a safe cue transition, if any.

    Only the liturgist/pastor microphone may trigger spoken-service cues. The
    caller still applies the director's autonomous-mode and confidence policy.
    """
    if role not in {"liturgist", "pastor"}:
        return None
    normalized = text.strip()
    if not normalized:
        return None

    if cue_id == "call_to_worship_liturgist" and _CALL_TO_WORSHIP_START.search(normalized):
        return "transcript indicates Call to Worship or opening prayer has begun", 0.93
    if cue_id == "call_to_worship_slides" and _OPENING_PRAYER_END.search(normalized):
        return "transcript indicates the opening prayer has ended", 0.90
    if cue_id == "scripture_reading" and _SCRIPTURE_END.search(normalized):
        return "transcript indicates scripture reading has ended", 0.94
    return None