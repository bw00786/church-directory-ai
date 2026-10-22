"""Where the song follower gets the live slide's text.

1. Operator-supplied texts per EasyWorship item (``SlideTextLibrary``,
   persisted to SONG_FOLLOWER_TEXTS_FILE and managed via
   /api/director/slide-texts). Slides are separated by blank lines and must
   match the item's slide breaks in EasyWorship. Gives exact text, the slide
   count (so the follower never runs past the last slide) and lyric-aware
   slide verification.
2. OCR of the camera-2 slides feed (the EasyWorship PC's output, captured via
   SLIDE_VERIFY_DEVICE) when no text was supplied for the live item. Only the
   current slide is known, so the follower advances when its closing words are
   heard and then re-reads the screen. Slides read this way are remembered per
   item + slide number (``LearnedSlideTexts``), so later passes through the item
   also know the next slide (catch-up when the reader is already into it).

With supplied texts the camera-2 OCR still runs as independent evidence: how
well the screen matches the slide the follower thinks is live is blended into
each proposal's confidence (SONG_FOLLOWER_SCREEN_WEIGHT).

SONG_FOLLOWER_SCOPE decides when to follow: "cues" = only while the cue engine
is on a cue listed in SONG_FOLLOWER_CUES (or, when it isn't running, while the
AI's service state is one of those parts of the service); "texts" = those, plus
any live item with supplied texts; "always" = any live item.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app.config import settings
from app.director.text_follower import LiveSlides
from app.domain.service_state import ServiceState
from app.easyworship.slide_verification import (
    SLIDE_TAG,
    fuzzy_ratio,
    register_slide_capture_input,
)
from app.logging_config import get_logger
from app.vision.frame_capture import frame_capture
from app.vision.slide_ocr import slide_ocr

logger = get_logger(__name__)

MAX_LABEL_CHARS = 100
MAX_TEXT_CHARS = 50_000
MAX_SLIDES = 300
MAX_LEARNED_SLIDE_CHARS = 2_000

_FOLLOW_STATES = {
    ServiceState.CALL_TO_WORSHIP,
    ServiceState.OPENING_PRAYER,
    ServiceState.SCRIPTURE,
    ServiceState.HYMN,
    ServiceState.CONGREGATIONAL_SONG,
    ServiceState.CLOSING_HYMN,
}

# Footer text EasyWorship puts on song slides; it isn't read aloud.
_OCR_JUNK = re.compile(
    r"(\u00a9|\bccli\b|copyright|public domain|licen[cs]e|all rights reserved|words\s*(and|&)\s*music)",
    re.IGNORECASE,
)
_SAME_SLIDE_RATIO = 0.85
_OCR_MIN_INTERVAL_SECONDS = 1.5


def _key(label: str) -> str:
    return " ".join(label.split()).casefold()


def split_slides(text: str) -> List[str]:
    """Blank-line separated blocks -> slide texts."""
    blocks = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n"))
    return [" ".join(b.split()) for b in blocks if b.strip()]


class SlideTextLibrary:
    """Operator-supplied slide texts keyed by EasyWorship item label."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = Path(path or settings.song_follower_texts_file)
        self._items: Dict[str, dict] = {}
        self._versions: Dict[str, int] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for entry in raw.get("items", []):
                label, slides = str(entry["label"]), [str(s) for s in entry["slides"]]
                self._items[_key(label)] = {"label": label, "slides": slides}
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Could not read slide texts file", path=str(self._path), exc_info=True)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps({"items": list(self._items.values())}, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, label: Optional[str]) -> Optional[List[str]]:
        if not label:
            return None
        with self._lock:
            self._ensure_loaded()
            entry = self._items.get(_key(label))
            return list(entry["slides"]) if entry else None

    def version(self, label: str) -> int:
        return self._versions.get(_key(label), 0)

    def set(self, label: str, text: str) -> List[str]:
        label = " ".join((label or "").split())
        if not label or len(label) > MAX_LABEL_CHARS:
            raise ValueError(f"Item label must be 1-{MAX_LABEL_CHARS} characters")
        if len(text or "") > MAX_TEXT_CHARS:
            raise ValueError(f"Text is too long ({MAX_TEXT_CHARS} characters maximum)")
        slides = split_slides(text)
        if not slides:
            raise ValueError("No slide text found (separate slides with a blank line)")
        if len(slides) > MAX_SLIDES:
            raise ValueError(f"Too many slides ({MAX_SLIDES} maximum)")
        with self._lock:
            self._ensure_loaded()
            key = _key(label)
            self._items[key] = {"label": label, "slides": slides}
            self._versions[key] = self._versions.get(key, 0) + 1
            self._save()
        return slides

    def delete(self, label: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            key = _key(label)
            if self._items.pop(key, None) is None:
                return False
            self._versions[key] = self._versions.get(key, 0) + 1
            self._save()
            return True

    def items(self) -> List[dict]:
        with self._lock:
            self._ensure_loaded()
            return [{"label": e["label"], "slides": list(e["slides"])} for e in self._items.values()]


class LearnedSlideTexts:
    """Slide texts read off camera 2, keyed by EasyWorship item label + slide number."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = Path(path or settings.song_follower_ocr_learn_file)
        self._items: Dict[str, dict] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for entry in raw.get("items", []):
                slides = {int(n): str(t) for n, t in dict(entry["slides"]).items()}
                self._items[_key(str(entry["label"]))] = {"label": str(entry["label"]), "slides": slides}
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Could not read learned slide texts", path=str(self._path), exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            items = [
                {"label": e["label"], "slides": {str(n): t for n, t in sorted(e["slides"].items())}}
                for e in self._items.values()
            ]
            tmp.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            logger.warning("Could not save learned slide texts", path=str(self._path), exc_info=True)

    def get(self, label: Optional[str], number: Optional[int]) -> Optional[str]:
        if not label or not number:
            return None
        with self._lock:
            self._ensure_loaded()
            entry = self._items.get(_key(label))
            return entry["slides"].get(number) if entry else None

    def record(self, label: str, number: int, text: str) -> bool:
        """Remember a slide's text; returns True when it was new or changed."""
        label = " ".join((label or "").split())
        text = " ".join((text or "").split())[:MAX_LEARNED_SLIDE_CHARS]
        if not label or len(label) > MAX_LABEL_CHARS or not text or not 1 <= number <= MAX_SLIDES:
            return False
        with self._lock:
            self._ensure_loaded()
            entry = self._items.setdefault(_key(label), {"label": label, "slides": {}})
            old = entry["slides"].get(number)
            # OCR jitters between reads; only a real change is worth a write.
            if old and fuzzy_ratio(old, text) >= _SAME_SLIDE_RATIO:
                return False
            entry["slides"][number] = text
            self._save()
            return True

    def items(self) -> List[dict]:
        with self._lock:
            self._ensure_loaded()
            return [
                {"label": e["label"], "slides": {n: t for n, t in sorted(e["slides"].items())}}
                for e in self._items.values()
            ]


def _clean_ocr(segments: List[str]) -> str:
    return " ".join(s for s in segments if not _OCR_JUNK.search(s)).strip()


def _read_slides_feed() -> List[str]:
    frame, _ = frame_capture.get_frame(SLIDE_TAG)
    return slide_ocr.read_segments(frame)


class OcrSlideReader:
    """Reads the live slide's text from the camera-2 slides feed.

    After an advance (``invalidate``) it reports nothing until the screen shows
    different text, or SONG_FOLLOWER_OCR_WAIT_SECONDS pass (two identical
    slides in a row), so the follower never re-matches the old slide.
    """

    def __init__(
        self,
        read_segments: Callable[[], List[str]] = _read_slides_feed,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = read_segments
        self._now = now
        self._text = ""
        self._generation = 0
        self._stale_since: Optional[float] = None
        self._stale_text = ""
        self._last_read_at: Optional[float] = None
        self.reads = 0

    @property
    def text(self) -> str:
        return self._text

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def stale(self) -> bool:
        return self._stale_since is not None

    def invalidate(self) -> None:
        self._stale_since = self._now()
        self._stale_text = self._text

    async def refresh(self) -> None:
        now = self._now()
        if (
            self._stale_since is None
            and self._last_read_at is not None
            and now - self._last_read_at < _OCR_MIN_INTERVAL_SECONDS
        ):
            return
        text = _clean_ocr(await asyncio.to_thread(self._read))
        self._last_read_at = self._now()
        self.reads += 1
        if self._stale_since is not None:
            changed = bool(text) and fuzzy_ratio(text, self._stale_text) < _SAME_SLIDE_RATIO
            if changed or self._now() - self._stale_since >= settings.song_follower_ocr_wait_seconds:
                self._stale_since = None
                self._text = text
                self._generation += 1
            return
        # OCR jitters between reads of the same slide; only a real change reloads.
        if not text or not self._text or fuzzy_ratio(text, self._text) < _SAME_SLIDE_RATIO:
            if text != self._text:
                self._generation += 1
            self._text = text

    def current(self) -> Optional[LiveSlides]:
        if self._stale_since is not None or not self._text:
            return None
        return LiveSlides(song_id=f"ocr:{self._generation}", slides=[self._text], bounded=False)


def follow_cue_ids() -> set:
    return {c.strip() for c in settings.song_follower_cues.split(",") if c.strip()}


def _current_cue():
    from app.director.engine import service_director

    return service_director.current_cue() if service_director.status().running else None


def follow_active() -> bool:
    """Is the live part of the service one whose slides are read/sung aloud?"""
    from app.director.engine import service_director
    from app.domain.service_context import service_context

    if service_director.status().running:
        cue = service_director.current_cue()
        return cue is not None and cue.id in follow_cue_ids()
    return service_context.state in _FOLLOW_STATES


def live_item_label() -> Optional[str]:
    """EasyWorship schedule item the follower should look up texts for."""
    from app.director.plan_builder import easyworship_item_for_cue
    from app.domain.service_context import service_context

    cue = _current_cue()
    if cue is not None:
        label = easyworship_item_for_cue(cue)
        if label:
            return label
    return service_context.current_easyworship_item


class LiveSlideTextSource:
    """``SlideTextSource`` for the song follower: library first, then OCR."""

    def __init__(
        self,
        library: Optional[SlideTextLibrary] = None,
        ocr: Optional[OcrSlideReader] = None,
        *,
        active: Callable[[], bool] = follow_active,
        label: Callable[[], Optional[str]] = live_item_label,
        slide_number: Optional[Callable[[], Optional[int]]] = None,
        learned: Optional[LearnedSlideTexts] = None,
    ) -> None:
        self.library = library or slide_text_library
        self.ocr = ocr or OcrSlideReader()
        self.learned = learned or learned_slide_texts
        self._active = active
        self._label = label
        self._slide_number = slide_number or _live_slide_number
        self._learn_seen: Optional[tuple] = None

    def _library_slides(self) -> tuple[Optional[str], Optional[List[str]]]:
        label = self._label()
        return label, self.library.get(label)

    def _following(self, has_texts: bool) -> bool:
        scope = settings.song_follower_scope
        return scope == "always" or (scope == "texts" and has_texts) or self._active()

    def __call__(self) -> Optional[LiveSlides]:
        label, slides = self._library_slides()
        if not self._following(bool(slides)):
            return None
        if slides:
            number = self._slide_number()
            return LiveSlides(
                song_id=f"lib:{_key(label)}:{self.library.version(label)}",
                slides=slides,
                index=number - 1 if number else None,
            )
        if not settings.song_follower_ocr_enabled:
            return None
        live = self.ocr.current()
        if live is not None and settings.song_follower_ocr_learn:
            number = self._slide_number()
            upcoming = self.learned.get(label, number + 1) if number else None
            if upcoming:
                live.slides.append(upcoming)
        return live

    async def refresh(self) -> None:
        if not settings.song_follower_ocr_enabled:
            return
        label, slides = self._library_slides()
        if not self._following(bool(slides)):
            return
        if slides and settings.song_follower_screen_weight <= 0:
            return
        await self.ocr.refresh()
        if not slides:
            self._learn(label)

    def _learn(self, label: Optional[str]) -> None:
        """Record the OCR'd slide once two fresh reads agree on slide number + text."""
        if not settings.song_follower_ocr_learn:
            return
        number = self._slide_number()
        if not label or not number or self.ocr.stale or not self.ocr.text:
            self._learn_seen = None
            return
        key = (_key(label), number, self.ocr.generation)
        if self._learn_seen is not None and self._learn_seen[0] == key:
            if self.ocr.reads > self._learn_seen[1]:
                self.learned.record(label, number, self.ocr.text)
            return
        self._learn_seen = (key, self.ocr.reads)

    def screen_agreement(self, slide_text: str) -> Optional[float]:
        """Fuzzy match of the live camera-2 text to ``slide_text``; ``None`` when the screen is unknown."""
        if not settings.song_follower_ocr_enabled or self.ocr.stale or not self.ocr.text:
            return None
        return fuzzy_ratio(self.ocr.text, slide_text)

    def invalidate(self) -> None:
        self.ocr.invalidate()


def _live_slide_number() -> Optional[int]:
    from app.easyworship.service import easyworship_service

    return easyworship_service.live_slide_number()


async def start_song_follower_capture() -> None:
    """Make sure the camera-2 slides feed is captured for follower OCR.

    Slide verification registers the same input when it's enabled; otherwise
    do it here. Unlike slide verification this never fails startup: without a
    capture device the follower still works from operator-supplied texts.
    """
    if not (settings.song_follower_enabled and settings.song_follower_ocr_enabled):
        return
    if settings.slide_verify_enabled:
        return
    use_dir = settings.vision_frame_source == "dir" and bool(settings.vision_frame_dir)
    if not use_dir and not settings.slide_verify_device:
        logger.warning(
            "Song follower OCR has no slides feed; set SLIDE_VERIFY_DEVICE to the camera-2 "
            "(EasyWorship PC) capture device, or supply slide texts"
        )
        return
    register_slide_capture_input()
    await frame_capture.start()
    logger.info("Song follower slides capture started", device=settings.slide_verify_device)


# Module-level singletons
slide_text_library = SlideTextLibrary()
learned_slide_texts = LearnedSlideTexts()
live_slide_text_source = LiveSlideTextSource()
