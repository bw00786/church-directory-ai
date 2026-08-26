"""Tests for SlideOCR: OCR reader with a safe no-crash contract."""

import pytest

from app.vision.slide_ocr import SlideOCR


class _FakeReader:
    def __init__(self, results=None, error=None):
        self._results = results if results is not None else ["Amazing Grace", "verse 1"]
        self._error = error

    def readtext(self, frame, detail=0):
        if self._error:
            raise self._error
        return self._results


def test_read_text_returns_empty_for_none_frame():
    ocr = SlideOCR()
    assert ocr.read_text(None) == ""


def test_read_text_joins_results():
    ocr = SlideOCR()
    ocr._reader = _FakeReader(results=["Amazing Grace", "verse 1"])

    assert ocr.read_text(object()) == "Amazing Grace verse 1"


def test_read_text_returns_empty_on_ocr_failure():
    ocr = SlideOCR()
    ocr._reader = _FakeReader(error=RuntimeError("model unavailable"))

    assert ocr.read_text(object()) == ""
