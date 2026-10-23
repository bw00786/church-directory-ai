"""Learn from the operator's own slide advances.

When the live EasyWorship slide changes and this app didn't command it, the
operator clicked it (manual mode, or EasyWorship driven by hand). Each such
advance is recorded with what the system saw and heard at that moment:

- the slide's text, read off camera 2 (the EasyWorship PC's output) by OCR,
  or the supplied/learned slide text when the camera isn't available;
- the last words heard on the follower's channels, who said them, and how
  long after the last word the click came;
- how long the slide was up, the EasyWorship item (service-plan label) and
  slide number.

Two stores:

- ``OperatorAdvanceHistory`` (OPERATOR_LEARNING_FILE), keyed by slide *text*
  so a song is recognised in any later service whatever its plan label. The
  song follower uses it for learned advance cues and typical slide durations.
- Production memory (category ``operator_slide_advance``), so the AI
  director's retrieval sees how past services were actually run next to the
  cue history of uploaded service plans.

Changes are detected from EasyWorship's remote-protocol read-back when it is
connected, else from camera-2 OCR alone.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional, Tuple

from app.config import settings
from app.easyworship.slide_verification import fuzzy_ratio, normalize
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

MEMORY_CATEGORY = "operator_slide_advance"
_SAME_SLIDE_RATIO = 0.85
_MAX_ENTRIES = 2_000
_MAX_TEXT_CHARS = 2_000
_OCR_INTERVAL_SECONDS = 1.5
# After a change, OCR still showing the old slide is ignored for this long.
_OCR_SETTLE_SECONDS = 4.0
_TRANSCRIPT_LINES = 200


def _similar(a: str, b: str) -> float:
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() < _SAME_SLIDE_RATIO or matcher.quick_ratio() < _SAME_SLIDE_RATIO:
        return 0.0
    return matcher.ratio()


class OperatorAdvanceHistory:
    """Operator advance samples per slide text, persisted as JSON."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = Path(path or settings.operator_learning_file)
        self._entries: Dict[str, dict] = {}
        self._lookup_cache: Dict[str, Optional[str]] = {}
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
            for entry in raw.get("slides", []):
                text = str(entry["text"])
                self._entries[normalize(text)] = {
                    "text": text,
                    "labels": [str(x) for x in entry.get("labels", [])],
                    "samples": [dict(s) for s in entry.get("samples", [])],
                }
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Could not read operator advance history", path=str(self._path), exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps({"slides": list(self._entries.values())}, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            logger.warning("Could not save operator advance history", path=str(self._path), exc_info=True)

    def _find(self, text: str) -> Optional[str]:
        key = normalize(text)
        if not key:
            return None
        if key in self._entries:
            return key
        if key in self._lookup_cache:
            return self._lookup_cache[key]
        best, best_score = None, _SAME_SLIDE_RATIO
        for other in self._entries:
            score = _similar(key, other)
            if score >= best_score:
                best, best_score = other, score
        self._lookup_cache[key] = best
        return best

    def _samples(self, text: str) -> List[dict]:
        with self._lock:
            self._ensure_loaded()
            key = self._find(text)
            return list(self._entries[key]["samples"]) if key else []

    def record(self, slide_text: str, sample: dict) -> None:
        text = " ".join((slide_text or "").split())[:_MAX_TEXT_CHARS]
        if not normalize(text):
            return
        with self._lock:
            self._ensure_loaded()
            key = self._find(text) or normalize(text)
            entry = self._entries.setdefault(key, {"text": text, "labels": [], "samples": []})
            label = sample.get("label")
            if label and label not in entry["labels"]:
                entry["labels"].append(label)
            entry["samples"].append(sample)
            del entry["samples"][: -max(1, settings.operator_learning_max_samples)]
            if len(self._entries) > _MAX_ENTRIES:
                oldest = min(self._entries, key=lambda k: self._entries[k]["samples"][-1].get("at", ""))
                self._entries.pop(oldest)
            self._lookup_cache.clear()
            self._save()

    def advance_cues(self, slide_text: str) -> List[str]:
        """Phrases the operator advanced after, once there are enough samples to trust."""
        samples = self._samples(slide_text)
        if len(samples) < settings.operator_learning_min_samples:
            return []
        cues: List[str] = []
        for sample in reversed(samples):
            words = (sample.get("tail") or "").split()[-settings.song_follower_tail_words:]
            if len(words) >= settings.song_follower_min_anchor_words and " ".join(words) not in cues:
                cues.append(" ".join(words))
        return cues

    def typical_dwell(self, slide_text: str) -> Optional[float]:
        """Median seconds the operator kept this slide up."""
        dwells = [float(s["dwell"]) for s in self._samples(slide_text) if s.get("dwell")]
        if len(dwells) < settings.operator_learning_min_samples:
            return None
        return statistics.median(dwells)

    def summary(self, limit: int = 50) -> dict:
        with self._lock:
            self._ensure_loaded()
            entries = sorted(
                self._entries.values(), key=lambda e: e["samples"][-1].get("at", "") if e["samples"] else "",
                reverse=True,
            )
            out = []
            for e in entries[:limit]:
                dwells = [float(s["dwell"]) for s in e["samples"] if s.get("dwell")]
                out.append({
                    "text": e["text"][:200],
                    "labels": e["labels"],
                    "samples": len(e["samples"]),
                    "median_dwell_seconds": round(statistics.median(dwells), 1) if dwells else None,
                    "last_heard": e["samples"][-1].get("tail") if e["samples"] else None,
                })
            return {"slides": len(self._entries), "recent": out}


@dataclass
class _Slide:
    key: Optional[tuple]          # EasyWorship identity; None when only OCR is available
    pres_no: Optional[int]
    slide_no: Optional[int]
    label: Optional[str]
    started_at: float
    text: str = ""
    candidate: str = ""
    candidate_at: Optional[float] = None
    stale_text: str = ""


def _position(state) -> Optional[tuple]:
    if state is None or not getattr(state, "paired", True):
        return None
    key = (state.pres_no, state.slide_no, state.slide_rowid, state.imagehash)
    return key if any(v is not None for v in key) else None


def _ew_state():
    from app.easyworship.service import easyworship_service

    return easyworship_service.live_state


def _commanded_recently() -> bool:
    from app.easyworship.service import easyworship_service

    return easyworship_service.commanded_within(settings.operator_learning_command_window_seconds)


def _item_label(pres_no: Optional[int]) -> Optional[str]:
    from app.director.slide_text_source import live_item_label
    from app.easyworship.service import easyworship_service

    return easyworship_service.item_label_for(pres_no) or live_item_label()


def _known_slide_text(label: Optional[str], slide_no: Optional[int]) -> str:
    from app.director.slide_text_source import learned_slide_texts, slide_text_library

    if not label or not slide_no:
        return ""
    slides = slide_text_library.get(label)
    if slides and 0 < slide_no <= len(slides):
        return slides[slide_no - 1]
    return learned_slide_texts.get(label, slide_no) or ""


def _learn_slide_text(label: Optional[str], slide_no: Optional[int], text: str) -> None:
    from app.director.slide_text_source import learned_slide_texts

    if settings.song_follower_ocr_learn and label and slide_no and text:
        learned_slide_texts.record(label, slide_no, text)


def _read_ocr() -> str:
    from app.director.slide_text_source import read_slide_text

    return read_slide_text()


def _record_memory(category: str, text: str, source: str) -> None:
    from app.memory.production_memory import memory_manager

    memory_manager.record_observation(category=category, text=text, source=source)


class OperatorAdvanceRecorder:
    """Watches EasyWorship + camera 2 and records advances the operator made."""

    def __init__(
        self,
        history: Optional[OperatorAdvanceHistory] = None,
        *,
        ew_state: Callable[[], object] = _ew_state,
        read_ocr: Callable[[], str] = _read_ocr,
        commanded_recently: Callable[[], bool] = _commanded_recently,
        item_label: Callable[[Optional[int]], Optional[str]] = _item_label,
        known_slide_text: Callable[[Optional[str], Optional[int]], str] = _known_slide_text,
        learn_slide_text: Callable[[Optional[str], Optional[int], str], None] = _learn_slide_text,
        record_memory: Callable[[str, str, str], None] = _record_memory,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.history = history or operator_advance_history
        self._ew_state = ew_state
        self._read_ocr = read_ocr
        self._commanded_recently = commanded_recently
        self._item_label = item_label
        self._known_slide_text = known_slide_text
        self._learn_slide_text = learn_slide_text
        self._record_memory = record_memory
        self._now = now
        self._current: Optional[_Slide] = None
        self._heard: Deque[Tuple[float, str, str]] = deque(maxlen=_TRANSCRIPT_LINES)
        self._last_ocr_at = float("-inf")
        self._recorded = 0
        self._last: Optional[dict] = None
        self._tasks: List[asyncio.Task] = []

    # -- inputs ----------------------------------------------------------------
    def on_transcript(self, role: str, text: str, at: Optional[float] = None) -> None:
        if text and text.strip():
            self._heard.append((self._now() if at is None else at, (role or "").lower(), text.strip()))

    async def _maybe_read_ocr(self) -> Optional[str]:
        if self._now() - self._last_ocr_at < _OCR_INTERVAL_SECONDS:
            return None
        self._last_ocr_at = self._now()
        try:
            return await asyncio.to_thread(self._read_ocr)
        except Exception:
            logger.warning("Operator learning OCR read failed", exc_info=True)
            return None

    async def poll(self) -> Optional[dict]:
        return await self.observe(self._ew_state(), await self._maybe_read_ocr())

    # -- change detection -------------------------------------------------------
    def _new_slide(self, state, key: Optional[tuple], at: float, stale_text: str = "") -> _Slide:
        pres_no = getattr(state, "pres_no", None) if key else None
        slide_no = getattr(state, "slide_no", None) if key else None
        return _Slide(key=key, pres_no=pres_no, slide_no=slide_no, label=self._item_label(pres_no),
                      started_at=at, stale_text=stale_text)

    async def observe(self, state, ocr_text: Optional[str]) -> Optional[dict]:
        """Feed one look at EasyWorship + camera 2; returns the advance record, if any."""
        now = self._now()
        key = _position(state)
        cur = self._current
        if cur is None:
            self._current = self._new_slide(state, key, now)
            self._apply_ocr(ocr_text, now)
            return None

        if key is not None:
            if cur.key is None:
                # EasyWorship read-back just became available: adopt it, no advance.
                cur.key, cur.pres_no, cur.slide_no = key, state.pres_no, state.slide_no
                cur.label = cur.label or self._item_label(state.pres_no)
            elif key != cur.key:
                new = self._new_slide(state, key, now, stale_text=cur.text)
                record = await self._on_advance(cur, new, now)
                self._current = new
                self._apply_ocr(ocr_text, now)
                return record
            self._apply_ocr(ocr_text, now)
            return None

        # No EasyWorship read-back: the screen itself is the only evidence.
        if cur.key is not None:
            cur.key = None
        if not ocr_text:
            return None
        if not cur.text:
            self._apply_ocr(ocr_text, now)
            return None
        if fuzzy_ratio(ocr_text, cur.text) >= _SAME_SLIDE_RATIO:
            cur.candidate, cur.candidate_at = "", None
            return None
        if not cur.candidate or fuzzy_ratio(ocr_text, cur.candidate) < _SAME_SLIDE_RATIO:
            cur.candidate, cur.candidate_at = ocr_text, now
            return None
        changed_at = cur.candidate_at or now
        new = _Slide(key=None, pres_no=None, slide_no=None, label=cur.label or self._item_label(None),
                     started_at=changed_at, text=ocr_text)
        record = await self._on_advance(cur, new, changed_at)
        self._current = new
        return record

    def _apply_ocr(self, ocr_text: Optional[str], now: float) -> None:
        """Settle the current slide's text from two agreeing OCR reads."""
        cur = self._current
        if cur is None or not ocr_text or cur.text:
            return
        if (
            cur.stale_text
            and fuzzy_ratio(ocr_text, cur.stale_text) >= _SAME_SLIDE_RATIO
            and now - cur.started_at < _OCR_SETTLE_SECONDS
        ):
            return  # the screen hasn't caught up with EasyWorship yet
        if cur.candidate and fuzzy_ratio(ocr_text, cur.candidate) >= _SAME_SLIDE_RATIO:
            cur.text, cur.candidate = ocr_text, ""
            self._learn_slide_text(cur.label, cur.slide_no, cur.text)
        else:
            cur.candidate = ocr_text

    # -- recording -------------------------------------------------------------
    def _heard_before(self, at: float) -> Tuple[str, Optional[str], Optional[float]]:
        roles = {r.strip() for r in settings.song_follower_roles.split(",") if r.strip()}
        lines = [
            (t, role, text) for t, role, text in self._heard
            if at - settings.operator_learning_context_seconds <= t <= at and (not roles or role in roles)
        ]
        if not lines:
            return "", None, None
        words = " ".join(text for _, _, text in lines).split()
        tail = " ".join(words[-settings.operator_learning_tail_words:])
        last_t, last_role, _ = lines[-1]
        return tail, last_role, round(at - last_t, 1)

    async def _on_advance(self, prev: _Slide, new: _Slide, at: float) -> Optional[dict]:
        if self._commanded_recently():
            return None  # the app (AI director or cue sheet) moved it, not the operator
        same_item = prev.pres_no is None or new.pres_no is None or prev.pres_no == new.pres_no
        forward = prev.slide_no is None or new.slide_no is None or new.slide_no > prev.slide_no
        kind = "item" if not same_item else ("slide" if forward else "back")
        tail, role, delay = self._heard_before(at)
        from_text = prev.text or self._known_slide_text(prev.label, prev.slide_no)
        record = {
            "kind": kind,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "label": prev.label,
            "to_label": new.label,
            "from_slide": prev.slide_no,
            "to_slide": new.slide_no,
            "dwell": round(max(0.0, at - prev.started_at), 1),
            "delay": delay,
            "role": role,
            "tail": tail,
            "slide_text": from_text[:_MAX_TEXT_CHARS],
        }
        if kind == "slide" and from_text:
            sample = {k: record[k] for k in ("at", "label", "from_slide", "dwell", "delay", "role", "tail")}
            await asyncio.to_thread(self.history.record, from_text, sample)
        if settings.operator_learning_memory:
            try:
                await asyncio.to_thread(
                    self._record_memory, MEMORY_CATEGORY, describe(record),
                    f"easyworship:{prev.label}" if prev.label else "easyworship",
                )
            except Exception:
                logger.warning("Could not record operator advance to production memory", exc_info=True)
        self._recorded += 1
        self._last = record
        event_bus.publish({"event": "OPERATOR_SLIDE_ADVANCE", "payload": record})
        logger.info("Operator slide advance recorded", kind=kind, label=prev.label,
                    from_slide=prev.slide_no, to_slide=new.slide_no, dwell=record["dwell"])
        return record

    # -- lifecycle -------------------------------------------------------------
    async def start(self) -> None:
        if not settings.operator_learning_enabled or self._tasks:
            return
        from app.director.slide_text_source import ensure_slides_capture

        if not await ensure_slides_capture():
            logger.info("Operator learning without camera 2 (no SLIDE_VERIFY_DEVICE); "
                        "using EasyWorship read-back and known slide texts only")
        self._tasks = [asyncio.create_task(self._poll_loop()), asyncio.create_task(self._transcript_loop())]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:
                logger.exception("Operator learning poll failed")
            await asyncio.sleep(settings.operator_learning_poll_seconds)

    async def _transcript_loop(self) -> None:
        queue = await event_bus.subscribe()
        try:
            while True:
                message = await queue.get()
                if isinstance(message, dict) and message.get("event") == "TRANSCRIPT":
                    payload = message.get("payload") or {}
                    self.on_transcript(str(payload.get("role", "")), str(payload.get("text", "")))
        finally:
            with contextlib.suppress(Exception):
                await event_bus.unsubscribe(queue)

    def status(self) -> dict:
        cur = self._current
        return {
            "enabled": settings.operator_learning_enabled,
            "running": bool(self._tasks),
            "recorded_this_run": self._recorded,
            "last_advance": self._last,
            "current": None if cur is None else {
                "label": cur.label, "slide": cur.slide_no, "text": cur.text[:200],
                "source": "easyworship" if cur.key else "camera",
            },
        }


def describe(record: dict) -> str:
    """One readable line for production memory (what the AI director retrieves)."""
    where = f" in {record['label']}" if record.get("label") else ""
    if record["kind"] == "item":
        text = f"Operator moved from {record.get('label') or 'an item'} to {record.get('to_label') or 'the next item'}"
    elif record["kind"] == "back":
        text = f"Operator went back a slide{where}"
    else:
        moved = (f" from slide {record['from_slide']} to {record['to_slide']}"
                 if record.get("from_slide") and record.get("to_slide") else "")
        text = f"Operator advanced the slide{where}{moved}"
    text += f" after {record['dwell']:.0f}s"
    if record.get("tail"):
        text += f"; last heard ({record.get('role') or 'unknown'}): \"{record['tail']}\""
        if record.get("delay") is not None:
            text += f", {record['delay']:.1f}s before the click"
    if record.get("slide_text"):
        text += f"; slide: \"{record['slide_text'][:160]}\""
    return text


# Module-level singletons
operator_advance_history = OperatorAdvanceHistory()
operator_advance_recorder = OperatorAdvanceRecorder()
