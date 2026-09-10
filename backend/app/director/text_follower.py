"""Phase 1: live text-follow slide advance (song lyrics + read liturgy).

The AI Director already confirms that a *commanded* slide change happened
(app.easyworship.slide_verification) but nothing decides *when* to advance a
lyric/reading slide from where the congregation actually is. This module closes
that gap: it aligns the live ASR transcript (vocalist / liturgist /
congregation channels) against the known ordered slide text of the live item
and proposes an ``EASYWORSHIP_NEXT`` as the sung/read position crosses into the
next slide.

Two layers:

- ``TextFollower`` -- a pure, hardware-free alignment component. Feed it the
  ordered slide texts (``load``) and successive transcript lines (``observe``);
  it returns a ``FollowProposal`` when the next slide's opening text is heard.
  It also implements the ``LyricMatcher`` protocol so it can back
  ``app.easyworship.slide_expected.expected_text_provider`` (WO-EWVERIFY-3).
- ``SongFollowerService`` -- the integration wrapper the AI Director runtime
  drives: it pulls the live item's slide text from an injectable source, feeds
  new transcript lines to the follower, and emits policy-gated ``DirectorAction``
  proposals. It never touches hardware; the runtime routes proposals through the
  same manual/assisted/ai_directed mode gate as any AI decision.

Everything is gated by ``settings.song_follower_enabled`` (default off).
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional, Protocol, Tuple

from app.config import settings
from app.director.action_models import DirectorAction, DirectorActionType
from app.domain.service_context import TranscriptLine
from app.logging_config import get_logger

logger = get_logger(__name__)

_WORD_RE = re.compile(r"[a-z0-9']+")


def _tokenize(text: str) -> List[str]:
    """Lowercase word tokens; punctuation and case are ignored for matching."""
    return _WORD_RE.findall((text or "").lower())


def _ordered_overlap(heard: List[str], target: List[str]) -> float:
    """Fraction of ``target`` that appears as an ordered subsequence in ``heard``.

    This answers "how much of the next slide's text have we started hearing?"
    without requiring an exact contiguous match (ASR drops/duplicates words).
    """
    if not target:
        return 0.0
    i = 0
    for word in heard:
        if word == target[i]:
            i += 1
            if i == len(target):
                break
    return i / len(target)


@dataclass
class FollowProposal:
    """A proposal to advance from ``from_index`` to ``to_index``."""

    song_id: str
    from_index: int
    to_index: int
    confidence: float
    matched_text: str


class SlideTextSource(Protocol):
    """Supplies the ordered slide text for the currently-live item.

    Returns ``(song_id, [slide_text, ...])`` for the live song/reading, or
    ``None`` when the live item has no known text (sermon, media, nothing
    configured). Wiring a real source (EasyWorship schedule / songbook) is a
    deployment concern; the follower is inert until one is provided.
    """

    def __call__(self) -> Optional[Tuple[str, List[str]]]:
        ...


class TextFollower:
    """Pure ASR-to-slide aligner. No hardware, no I/O -- fully unit-testable."""

    def __init__(
        self,
        *,
        min_confidence: float = 0.72,
        min_anchor_words: int = 3,
        window_words: int = 40,
    ) -> None:
        self._min_confidence = min_confidence
        self._min_anchor_words = max(1, min_anchor_words)
        self._song_id: Optional[str] = None
        self._raw_slides: List[str] = []
        self._slides: List[List[str]] = []
        self._index = 0
        self._heard: Deque[str] = deque(maxlen=window_words)
        self._proposed_to: Optional[int] = None

    # -- lifecycle ------------------------------------------------------------
    def load(self, song_id: str, slides: List[str]) -> None:
        """Load the ordered slide text for a song/reading; resets to slide 0."""
        self._song_id = song_id
        self._raw_slides = list(slides)
        self._slides = [_tokenize(s) for s in slides]
        self._index = 0
        self._heard.clear()
        self._proposed_to = None

    @property
    def loaded(self) -> bool:
        return bool(self._slides)

    @property
    def song_id(self) -> Optional[str]:
        return self._song_id

    @property
    def current_index(self) -> int:
        return self._index

    def confirm_advance(self, to_index: Optional[int] = None) -> None:
        """Mark a proposed advance as actually applied (executed/approved)."""
        self._index = to_index if to_index is not None else self._index + 1
        self._index = max(0, min(self._index, max(len(self._slides) - 1, 0)))
        self._proposed_to = None
        self._heard.clear()

    def reject_advance(self) -> None:
        """Discard the pending proposal so a later, better match can re-propose."""
        self._proposed_to = None

    # -- alignment ------------------------------------------------------------
    def observe(self, text: str) -> Optional[FollowProposal]:
        """Feed one transcript line; return an advance proposal or ``None``."""
        words = _tokenize(text)
        if not words:
            return None
        self._heard.extend(words)
        if not self._slides:
            return None

        next_index = self._index + 1
        if next_index >= len(self._slides):
            return None
        target = self._slides[next_index]
        if not target:
            return None
        if self._proposed_to == next_index:
            return None  # already proposed this advance; wait for confirm/reject

        heard = list(self._heard)
        anchor_len = min(self._min_anchor_words, len(target))
        anchor = target[:anchor_len]
        anchor_hits = sum(1 for w in anchor if w in heard)
        if anchor_hits < anchor_len:
            return None

        score = _ordered_overlap(heard, target)
        if score < self._min_confidence:
            return None

        self._proposed_to = next_index
        return FollowProposal(
            song_id=self._song_id or "",
            from_index=self._index,
            to_index=next_index,
            confidence=score,
            matched_text=" ".join(target[:6]),
        )

    # -- LyricMatcher protocol (app.easyworship.slide_expected) --------------
    def expected_slide_text(self, song_id: str, slide_index: int) -> Optional[str]:
        """Back the slide verifier's expected-text provider (WO-EWVERIFY-3)."""
        if song_id != self._song_id:
            return None
        if 0 <= slide_index < len(self._raw_slides):
            return self._raw_slides[slide_index]
        return None


class SongFollowerService:
    """Drives a ``TextFollower`` from live transcript and emits proposals.

    The runtime calls ``process`` with the newest transcript lines and routes
    the returned actions through its mode gate, then calls ``notify_executed``
    for any that actually ran so the follower stays in sync.
    """

    def __init__(
        self,
        text_source: Optional[SlideTextSource] = None,
        follower: Optional[TextFollower] = None,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source = text_source
        self._follower = follower or TextFollower(
            min_confidence=settings.song_follower_min_confidence,
            min_anchor_words=settings.song_follower_min_anchor_words,
        )
        self._now = now
        self._last_advance_at = 0.0
        self._warned_no_source = False

    @property
    def follower(self) -> TextFollower:
        return self._follower

    def set_text_source(self, source: Optional[SlideTextSource]) -> None:
        self._source = source

    def _roles(self) -> set:
        return {r.strip() for r in settings.song_follower_roles.split(",") if r.strip()}

    def _sync_song(self) -> bool:
        """Ensure the follower holds the live item's slides. Returns loaded?"""
        if self._source is None:
            if not self._warned_no_source:
                logger.info("Song follower has no slide-text source; inactive")
                self._warned_no_source = True
            return False
        try:
            current = self._source()
        except Exception:
            logger.warning("Song follower text source failed", exc_info=True)
            return False
        if not current:
            return False
        song_id, slides = current
        if song_id != self._follower.song_id:
            self._follower.load(song_id, slides)
        return self._follower.loaded

    def process(self, new_lines: List[TranscriptLine]) -> List[DirectorAction]:
        """Feed new transcript lines; return advance proposals as actions."""
        if not settings.song_follower_enabled or not new_lines:
            return []
        if not self._sync_song():
            return []

        roles = self._roles()
        actions: List[DirectorAction] = []
        for line in new_lines:
            if roles and line.speaker_role not in roles:
                continue
            proposal = self._follower.observe(line.text)
            if proposal is None:
                continue
            if self._now() - self._last_advance_at < settings.song_follower_cooldown_seconds:
                self._follower.reject_advance()
                continue
            self._last_advance_at = self._now()
            actions.append(
                DirectorAction(
                    type=DirectorActionType.EASYWORSHIP_NEXT,
                    parameters={
                        "song_id": proposal.song_id,
                        "to_index": proposal.to_index,
                        "source": "song_follower",
                    },
                    confidence=proposal.confidence,
                    reason=(
                        f"Congregation reached slide {proposal.to_index + 1} "
                        f"(\"{proposal.matched_text}\", match={proposal.confidence:.2f})"
                    ),
                )
            )
        return actions

    def notify_executed(self, action: DirectorAction) -> None:
        """Advance the follower's position after a proposal actually ran."""
        if action.parameters.get("source") != "song_follower":
            return
        self._follower.confirm_advance(action.parameters.get("to_index"))


# Module-level singleton wired by the runtime.
song_follower_service = SongFollowerService()
