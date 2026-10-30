"""OCR reader for the EasyWorship slide feed (WO-EWVERIFY-1).

Uses easyocr (pure-pip; downloads its own pretrained detection + recognition
models on first use -- the same "downloads on demand" pattern already used
for nomic-embed-text-v1.5, see app.memory.embeddings) to extract on-screen
text from a captured slide frame.

Never raises. Two read surfaces:

* :meth:`SlideOCR.read` returns an :class:`OcrRead` that distinguishes
  "OCR could not run" (``ok=False``: no frame, easyocr missing, model failed
  to load, read raised) from "OCR ran and the slide has this text" (``ok=True``,
  possibly with empty ``text`` for a blank slide). The slide verifier uses this
  so an *unavailable* reader is reported as "unknown" rather than mistaken for
  "the slide did not change" (which halts automation).
* :meth:`read_text` / :meth:`read_segments` keep the original "" / ``[]``
  convention for callers that only need best-effort text (song follower).

GPU use is controlled by ``SLIDE_OCR_GPU`` (default off: the EasyWorship tap
machine is usually a laptop without CUDA). Reads take 1-5 s on CPU, so callers
on the event loop must run them in a worker thread (see
``app.vision.executors``); this module is thread-safe.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class OcrRead:
    """Result of one OCR attempt.

    ``ok`` is True only when the reader actually ran on a frame; ``reason``
    explains an ``ok=False`` result (``no_frame``, ``reader_unavailable``,
    ``read_failed``).
    """

    ok: bool
    segments: tuple = ()
    reason: Optional[str] = None

    @property
    def text(self) -> str:
        return " ".join(self.segments).strip()


class SlideOCR:
    """Lazily-loaded OCR reader; the (~100MB) model loads once per process."""

    def __init__(self, gpu: Optional[bool] = None) -> None:
        self._reader = None
        self._gpu = gpu
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None

    @property
    def gpu(self) -> bool:
        return bool(settings.slide_ocr_gpu) if self._gpu is None else self._gpu

    @property
    def available(self) -> bool:
        """True once a reader has loaded; False before first use or after a
        load failure (the next read retries the load)."""
        return self._reader is not None

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def _get_reader(self):
        if self._reader is None:
            import easyocr

            self._reader = easyocr.Reader(["en"], gpu=self.gpu)
            self._load_error = None
            logger.info("Slide OCR reader loaded", gpu=self.gpu)
        return self._reader

    def read(self, frame) -> OcrRead:
        """OCR a captured BGR frame (as produced by app.vision.frame_capture).
        Never raises; see :class:`OcrRead` for the ok/unknown distinction."""
        if frame is None:
            return OcrRead(ok=False, reason="no_frame")
        # The slide verifier and song follower share one reader across threads.
        with self._lock:
            try:
                reader = self._get_reader()
            except Exception as e:  # noqa: BLE001 - optional dependency / model download
                self._load_error = f"{type(e).__name__}: {e}"
                logger.warning("Slide OCR reader unavailable", error=self._load_error)
                return OcrRead(ok=False, reason="reader_unavailable")
            try:
                results = reader.readtext(frame, detail=0)
            except Exception:
                logger.warning("Slide OCR read failed", exc_info=True)
                return OcrRead(ok=False, reason="read_failed")
        segments = tuple(str(r).strip() for r in results if str(r).strip())
        return OcrRead(ok=True, segments=segments)

    def read_text(self, frame) -> str:
        """Extract on-screen text from a captured frame. Returns "" if `frame`
        is None or OCR fails/is unavailable -- callers must treat that as
        "unknown", not "blank slide" (use :meth:`read` to tell them apart)."""
        return self.read(frame).text

    def read_segments(self, frame) -> list[str]:
        """Like ``read_text`` but keeps each detected text block separate."""
        return list(self.read(frame).segments)


# Module-level singleton
slide_ocr = SlideOCR()
