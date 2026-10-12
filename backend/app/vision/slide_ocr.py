"""OCR reader for the EasyWorship slide feed (WO-EWVERIFY-1).

Uses easyocr (pure-pip; downloads its own pretrained detection + recognition
models on first use -- the same "downloads on demand" pattern already used
for nomic-embed-text-v1.5, see app.memory.embeddings) to extract on-screen
text from a captured slide frame.

Never raises: if easyocr isn't installed, the model fails to load, or a read
fails, callers get an empty string -- "text unknown," never a fabricated
match/mismatch. See app.easyworship.slide_verification for how this is used.
"""

from __future__ import annotations

import threading

from app.logging_config import get_logger

logger = get_logger(__name__)


class SlideOCR:
    """Lazily-loaded OCR reader; the (~100MB) model loads once per process."""

    def __init__(self) -> None:
        self._reader = None
        self._lock = threading.Lock()

    def _get_reader(self):
        if self._reader is None:
            import easyocr

            self._reader = easyocr.Reader(["en"], gpu=False)
        return self._reader

    def read_text(self, frame) -> str:
        """Extract on-screen text from a captured BGR frame (as produced by
        app.vision.frame_capture). Returns "" if `frame` is None or OCR
        fails/is unavailable -- callers must treat that as "unknown", not
        "blank slide"."""
        return " ".join(self.read_segments(frame)).strip()

    def read_segments(self, frame) -> list[str]:
        """Like ``read_text`` but keeps each detected text block separate."""
        if frame is None:
            return []
        try:
            # The slide verifier and song follower share one reader across threads.
            with self._lock:
                results = self._get_reader().readtext(frame, detail=0)
            return [str(r).strip() for r in results if str(r).strip()]
        except Exception:
            logger.warning("Slide OCR read failed", exc_info=True)
            return []


# Module-level singleton
slide_ocr = SlideOCR()
