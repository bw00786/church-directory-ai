"""Live text-follow slide advance (song lyrics + read liturgy).

Aligns the live ASR transcript (liturgist ch 2, vocalist, congregation ch 8)
against the text of the live EasyWorship slide and proposes
``EASYWORSHIP_NEXT_SLIDE`` once the reader reaches the end of that slide.

The congregation reads from the screen, so the next slide has to appear
*before* they need it: the primary trigger is "the closing words of the current
slide were heard". When the next slide's text is known, hearing its opening
words also triggers (catch-up for text people know by heart, e.g. the Lord's
Prayer).

Two layers:

- ``TextFollower`` -- a pure, hardware-free aligner. ``load`` the slide texts,
  feed transcript lines to ``observe``; it returns a ``FollowProposal`` when the
  live slide is finished. It also implements the ``LyricMatcher`` protocol so it
  can back ``app.easyworship.slide_expected.expected_text_provider``.
- ``SongFollowerService`` -- the integration wrapper the AI Director runtime
  drives: it pulls the live slide text from an injectable source (see
  ``app.director.slide_text_source``), feeds transcript lines to the follower,
  and emits ``DirectorAction`` proposals. It never touches hardware; the runtime
  routes proposals through the manual/assisted/ai_directed mode gate.

Everything is gated by ``settings.song_follower_enabled``.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional, Protocol, Sequence, Tuple, Union

from app.config import settings
from app.director.action_models import DirectorAction, DirectorActionType
from app.domain.service_context import TranscriptLine
from app.logging_config import get_logger

logger = get_logger(__name__)

_WORD_RE = re.compile(r"[a-z0-9']+")
# The reader must have covered at least this much of the slide before its
# closing words count, so a repeated closing phrase heard early can't fire.
_MIN_SLIDE_COVERAGE = 0.5
_HEARD_WINDOW_WORDS = 200


def _tokenize(text: str) -> List[str]:
    """Lowercase word tokens; punctuation and case are ignored for matching."""
    return _WORD_RE.findall((text or "").replace("\u2019", "'").lower())


def _lcs_len(a: Sequence[str], b: Sequence[str]) -> int:
    """Longest common subsequence length (order-preserving, tolerant of ASR drops)."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def _match_ratio(heard: Sequence[str], target: Sequence[str]) -> float:
    """Fraction of ``target`` heard, in order, within ``heard``."""
    return _lcs_len(heard, target) / len(target) if target else 0.0


def fuse_confidence(audio: float, screen: Optional[float], screen_weight: float) -> float:
    """Blend the transcript match with camera-2 screen agreement (``None`` = no screen evidence)."""
    if screen is None or screen_weight <= 0:
        return audio
    w = min(screen_weight, 1.0)
    return max(0.0, min(1.0, (1 - w) * audio + w * screen))


def parse_role_weights(spec: str) -> dict:
    """``"vocalist:1.0,congregation:0.85"`` -> ``{role: weight}``; bad entries are ignored."""
    weights = {}
    for part in (spec or "").split(","):
        role, _, value = part.partition(":")
        try:
            weights[role.strip().lower()] = max(0.0, min(1.0, float(value)))
        except ValueError:
            continue
    return weights


@dataclass
class FollowProposal:
    """A proposal to advance from ``from_index`` to ``to_index``."""

    song_id: str
    from_index: int
    to_index: int
    confidence: float
    matched_text: str


@dataclass
class LiveSlides:
    """The slide text for the live item, as supplied by a ``SlideTextSource``."""

    song_id: str
    slides: List[str]
    index: Optional[int] = None  # live position when known (EasyWorship slide_no - 1)
    bounded: bool = True          # False when only the current slide is known (OCR)


SourceResult = Union[LiveSlides, Tuple[str, List[str]], None]


class SlideTextSource(Protocol):
    """Supplies the slide text for the currently-live item.

    Returns ``LiveSlides`` (or a ``(song_id, [slide_text, ...])`` tuple), or
    ``None`` when the live item shouldn't be followed (sermon, media, nothing
    known). Sources may also provide ``async refresh()``, ``invalidate()`` and
    ``screen_agreement(slide_text) -> Optional[float]`` (camera-2 evidence).
    """

    def __call__(self) -> SourceResult:
        ...


class TextFollower:
    """Pure ASR-to-slide aligner. No hardware, no I/O -- fully unit-testable."""

    def __init__(
        self,
        *,
        min_confidence: float = 0.72,
        min_anchor_words: int = 3,
        tail_words: int = 8,
    ) -> None:
        self._min_confidence = min_confidence
        self._min_anchor_words = max(1, min_anchor_words)
        self._tail_words = max(1, tail_words)
        self._song_id: Optional[str] = None
        self._raw_slides: List[str] = []
        self._slides: List[List[str]] = []
        self._bounded = True
        self._stale = False
        self._index = 0
        self._heard: Deque[str] = deque(maxlen=_HEARD_WINDOW_WORDS)
        self._proposed_to: Optional[int] = None

    # -- lifecycle ------------------------------------------------------------
    def load(self, song_id: str, slides: List[str], index: int = 0, bounded: bool = True) -> None:
        """Load the slide text for a song/reading and start at ``index``."""
        self._song_id = song_id
        self._raw_slides = list(slides)
        self._slides = [_tokenize(s) for s in slides]
        self._bounded = bounded
        self._stale = False
        self._index = max(0, min(index, max(len(self._slides) - 1, 0)))
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

    @property
    def pending(self) -> bool:
        return self._proposed_to is not None

    @property
    def bounded(self) -> bool:
        return self._bounded

    @property
    def waiting_for_new_slide(self) -> bool:
        return self._stale

    @property
    def current_text(self) -> Optional[str]:
        return self._raw_slides[self._index] if self._raw_slides else None

    def sync_index(self, index: int) -> bool:
        """Follow a position change made elsewhere (operator, EasyWorship read-back).

        Returns True when the move was not this follower's own pending advance.
        """
        if not self._bounded or not 0 <= index < len(self._slides) or index == self._index:
            return False
        own = index == self._proposed_to
        self._index = index
        self._proposed_to = None
        self._heard.clear()
        return not own

    def confirm_advance(self, to_index: Optional[int] = None) -> None:
        """Mark a proposed advance as actually applied (executed/approved)."""
        if self._bounded:
            target = to_index if to_index is not None else self._index + 1
            self._index = max(0, min(target, max(len(self._slides) - 1, 0)))
        else:
            # Only the old slide's text is known; wait for the source to reload.
            self._stale = True
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
        if not self._slides or self._stale or self._proposed_to is not None:
            return None

        next_index = self._index + 1
        has_next = next_index < len(self._slides)
        if self._bounded and not has_next:
            return None

        heard = list(self._heard)
        current = self._slides[self._index]
        score, matched = self._finished_score(heard, current), current[-self._tail_words:]
        if score is None and has_next:
            nxt = self._slides[next_index]
            score, matched = self._started_score(heard, current, nxt), nxt[: self._tail_words]
        if score is None:
            return None

        self._proposed_to = next_index
        return FollowProposal(
            song_id=self._song_id or "",
            from_index=self._index,
            to_index=next_index,
            confidence=round(score, 3),
            matched_text=" ".join(matched),
        )

    def _finished_score(self, heard: List[str], current: List[str]) -> Optional[float]:
        """Score when the closing words of the live slide were just heard."""
        if not current:
            return None
        tail = current[-self._tail_words:]
        recent = heard[-(len(tail) * 2 + 4):]
        score = _match_ratio(recent, tail)
        if score < self._min_confidence:
            return None
        if _match_ratio(heard, current) < _MIN_SLIDE_COVERAGE:
            return None
        return score

    def _started_score(self, heard: List[str], current: List[str], nxt: List[str]) -> Optional[float]:
        """Score when the reader is already into the next slide (catch-up)."""
        opening = nxt[: self._tail_words]
        if not opening:
            return None
        # A next slide that opens like the current one (repeated chorus) can't
        # be told apart from re-reading the current slide.
        if _match_ratio(current, opening) >= 0.5:
            return None
        anchor = opening[: min(self._min_anchor_words, len(opening))]
        if _match_ratio(heard, anchor) < 1.0:
            return None
        score = _match_ratio(heard[-(len(opening) * 2 + 4):], opening)
        return score if score >= self._min_confidence else None

    # -- LyricMatcher protocol (app.easyworship.slide_expected) --------------
    def expected_slide_text(self, song_id: str, slide_index: int) -> Optional[str]:
        """Back the slide verifier's expected-text provider (WO-EWVERIFY-3)."""
        if song_id != self._song_id or not self._bounded:
            return None
        if 0 <= slide_index < len(self._raw_slides):
            return self._raw_slides[slide_index]
        return None

    def status(self) -> dict:
        return {
            "song_id": self._song_id,
            "slide_index": self._index,
            "slide_count": len(self._slides) if self._bounded else None,
            "current_text": self._raw_slides[self._index] if self._raw_slides else None,
            "source": "library" if self._bounded else "ocr",
            "pending_advance_to": self._proposed_to,
            "waiting_for_new_slide": self._stale,
        }


def _as_live(current: SourceResult) -> Optional[LiveSlides]:
    if current is None or isinstance(current, LiveSlides):
        return current
    song_id, slides = current[0], current[1]
    return LiveSlides(song_id=song_id, slides=list(slides))


class SongFollowerService:
    """Drives a ``TextFollower`` from live transcript and emits proposals.

    The runtime awaits ``refresh`` (OCR sources read the screen there), calls
    ``process`` with new transcript lines, routes the returned actions through
    its mode gate, then calls ``notify_executed`` / ``notify_rejected``.
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
            tail_words=settings.song_follower_tail_words,
        )
        self._now = now
        self._last_advance_at = 0.0
        self._warned_no_source = False
        self._refresh_lock = asyncio.Lock()

    @property
    def follower(self) -> TextFollower:
        return self._follower

    def set_text_source(self, source: Optional[SlideTextSource]) -> None:
        self._source = source

    def _roles(self) -> set:
        return {r.strip() for r in settings.song_follower_roles.split(",") if r.strip()}

    async def refresh(self) -> None:
        """Let the source update itself (e.g. OCR the slides feed) before ``process``."""
        refresh = getattr(self._source, "refresh", None)
        if refresh is None:
            return
        # The transcript listener and the slide watcher both refresh; OCR reads must not overlap.
        async with self._refresh_lock:
            try:
                await refresh()
            except Exception:
                logger.warning("Song follower text source refresh failed", exc_info=True)

    def _sync_song(self) -> Tuple[bool, bool]:
        """Ensure the follower holds the live item's slides.

        Returns ``(loaded, moved_externally)``; the latter is True when the live
        slide changed without a pending/just-executed follower advance.
        """
        if self._source is None:
            if not self._warned_no_source:
                logger.info("Song follower has no slide-text source; inactive")
                self._warned_no_source = True
            return False, False
        try:
            live = _as_live(self._source())
        except Exception:
            logger.warning("Song follower text source failed", exc_info=True)
            return False, False
        if live is None or not live.slides:
            return False, False
        f = self._follower
        moved = False
        if live.song_id != f.song_id:
            moved = f.loaded and not f.pending and not f.waiting_for_new_slide
            f.load(live.song_id, live.slides, index=live.index or 0, bounded=live.bounded)
        elif live.index is not None:
            moved = f.sync_index(live.index)
        return f.loaded, moved

    def resync(self) -> bool:
        """Pick up slide changes made outside the follower (e.g. the operator clicked ahead)."""
        if not settings.song_follower_enabled:
            return False
        moved = self._sync_song()[1]
        if moved:
            logger.info("Song follower resynced to a manual slide change", **self._follower.status())
        return moved

    def is_stale(self, action: DirectorAction) -> bool:
        """Would running this follower proposal now skip or repeat a slide?"""
        params = action.parameters
        if params.get("source") != "song_follower":
            return False
        if params.get("song_id") != self._follower.song_id:
            return True
        from_index = params.get("from_index")
        return self._follower.bounded and from_index is not None and from_index != self._follower.current_index

    def _role_weight(self, role: str) -> float:
        return parse_role_weights(settings.song_follower_role_weights).get(role, 1.0)

    def _screen_agreement(self) -> Optional[float]:
        """How well camera 2 agrees with the slide the follower thinks is live."""
        # With OCR as the text source the slide text *is* the screen: no independent evidence.
        if not self._follower.bounded:
            return None
        agreement = getattr(self._source, "screen_agreement", None)
        text = self._follower.current_text
        if agreement is None or not text:
            return None
        try:
            return agreement(text)
        except Exception:
            logger.warning("Song follower screen agreement failed", exc_info=True)
            return None

    def process(self, new_lines: List[TranscriptLine]) -> List[DirectorAction]:
        """Feed new transcript lines; return advance proposals as actions."""
        if not settings.song_follower_enabled or not new_lines:
            return []
        if not self._sync_song()[0]:
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
            audio = proposal.confidence * self._role_weight(line.speaker_role)
            screen = self._screen_agreement()
            confidence = round(fuse_confidence(audio, screen, settings.song_follower_screen_weight), 3)
            if confidence < settings.song_follower_min_confidence:
                # Weak evidence (e.g. room-mic ASR alone): let a stronger line re-propose.
                self._follower.reject_advance()
                continue
            self._last_advance_at = self._now()
            screen_note = f", screen={screen:.2f}" if screen is not None else ""
            actions.append(
                DirectorAction(
                    type=DirectorActionType.EASYWORSHIP_NEXT_SLIDE,
                    parameters={
                        "song_id": proposal.song_id,
                        "from_index": proposal.from_index,
                        "to_index": proposal.to_index,
                        "source": "song_follower",
                        "role": line.speaker_role,
                        "audio_confidence": round(audio, 3),
                        "screen_agreement": round(screen, 3) if screen is not None else None,
                    },
                    confidence=confidence,
                    reason=(
                        f"{line.speaker_role} finished slide {proposal.from_index + 1} "
                        f"(\"{proposal.matched_text}\", match={proposal.confidence:.2f}{screen_note})"
                    ),
                )
            )
        return actions

    def notify_executed(self, action: DirectorAction) -> None:
        """Advance the follower's position after a proposal actually ran."""
        if action.parameters.get("source") != "song_follower":
            return
        if action.parameters.get("song_id") != self._follower.song_id:
            return  # the watcher already loaded the new slide
        self._follower.confirm_advance(action.parameters.get("to_index"))
        invalidate = getattr(self._source, "invalidate", None)
        if invalidate is not None:
            invalidate()

    def notify_rejected(self, action: DirectorAction) -> None:
        """Let a later, better match re-propose after a veto/rejection."""
        if action.parameters.get("source") == "song_follower":
            self._follower.reject_advance()

    def status(self) -> dict:
        return {
            "enabled": settings.song_follower_enabled,
            "scope": settings.song_follower_scope,
            "roles": sorted(self._roles()),
            "has_source": self._source is not None,
            **self._follower.status(),
        }


# Module-level singleton wired by the runtime.
song_follower_service = SongFollowerService()
