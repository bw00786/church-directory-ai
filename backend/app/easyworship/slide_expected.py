"""Expected-text provider for lyric-aware slide verification (WO-EWVERIFY-3).

Change-detection (WO-EWVERIFY-1/-2) proves a commanded slide action visibly
changed the on-screen text; it cannot prove the *correct* slide is showing.
For congregational singing there is an authoritative source of expected text:
the fuzzy lyric matcher already holds the full lyric text and current position
for the active song. This module exposes that as a provider the verifier can
query for "what text *should* be on screen now".

The provider returns ``None`` whenever no authoritative source exists -- sermon
slides, announcements, media items, or simply no lyric matcher wired in yet.
``None`` means "unknown, don't judge correctness", never "blank slide": callers
must treat it as a CHANGE_ONLY result, not a mismatch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Protocol, runtime_checkable

from app.logging_config import get_logger

logger = get_logger(__name__)


@runtime_checkable
class LyricMatcher(Protocol):
    """The subset of the fuzzy lyric matcher this provider depends on.

    Given the active song and the slide index that *should* now be displayed
    (post-action), return that slide's expected lyric text, or ``None`` when
    the position isn't a known lyric slide.
    """

    def expected_slide_text(self, song_id: str, slide_index: int) -> Optional[str]:
        ...


class ExpectedTextProvider(ABC):
    """Returns the text that should be on screen for a given song position."""

    @abstractmethod
    def expected_text(self, song_id: Optional[str], slide_index: Optional[int]) -> Optional[str]:
        """Expected on-screen text post-action, or ``None`` if unknown."""
        raise NotImplementedError


class LyricExpectedTextProvider(ExpectedTextProvider):
    """Backs onto the fuzzy lyric matcher's song state (no new lyric source).

    Until a matcher is wired in (``set_matcher``), every lookup returns
    ``None`` -- semantic verification then degrades safely to change-only.
    """

    def __init__(self, matcher: Optional[LyricMatcher] = None) -> None:
        self._matcher = matcher

    def set_matcher(self, matcher: Optional[LyricMatcher]) -> None:
        self._matcher = matcher

    def expected_text(self, song_id: Optional[str], slide_index: Optional[int]) -> Optional[str]:
        if self._matcher is None or song_id is None or slide_index is None:
            return None
        try:
            return self._matcher.expected_slide_text(song_id, slide_index)
        except Exception:
            logger.warning("Lyric matcher lookup failed", exc_info=True)
            return None


# Module-level singleton
expected_text_provider = LyricExpectedTextProvider()
