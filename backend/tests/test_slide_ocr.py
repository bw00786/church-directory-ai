"""Tests for SlideOCR: OCR reader with a safe no-crash contract."""

import pytest

from app.config import settings
from app.vision.slide_ocr import SlideOCR


def set_setting(monkeypatch, name, value):
    """Patch an app setting that may not exist yet in app.config (pydantic
    refuses unknown instance attributes, so fall back to the class)."""
    if name in type(settings).model_fields:
        monkeypatch.setattr(settings, name, value)
    else:
        monkeypatch.setattr(type(settings), name, value, raising=False)


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


# -- tri-state read() ----------------------------------------------------------
def test_read_distinguishes_no_frame_from_blank_slide():
    ocr = SlideOCR()
    ocr._reader = _FakeReader(results=[])

    none_read = ocr.read(None)
    assert none_read.ok is False and none_read.reason == "no_frame"

    blank = ocr.read(object())
    assert blank.ok is True and blank.text == "" and blank.segments == ()


def test_read_reports_read_failure_as_not_ok():
    ocr = SlideOCR()
    ocr._reader = _FakeReader(error=RuntimeError("boom"))
    read = ocr.read(object())
    assert read.ok is False and read.reason == "read_failed"
    assert ocr.available is True  # reader still loaded; the read itself failed


def test_read_reports_missing_reader_as_unavailable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "easyocr":
            raise ImportError("No module named easyocr")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    ocr = SlideOCR()
    read = ocr.read(object())
    assert read.ok is False and read.reason == "reader_unavailable"
    assert ocr.available is False
    assert "ImportError" in ocr.load_error
    assert ocr.read_text(object()) == ""
    assert ocr.read_segments(object()) == []


def test_gpu_flag_follows_setting(monkeypatch):
    set_setting(monkeypatch, "slide_ocr_gpu", False)
    assert SlideOCR().gpu is False
    set_setting(monkeypatch, "slide_ocr_gpu", True)
    assert SlideOCR().gpu is True
    assert SlideOCR(gpu=False).gpu is False  # explicit override wins


def test_reader_loaded_with_gpu_setting(monkeypatch):
    import sys
    import types

    set_setting(monkeypatch, "slide_ocr_gpu", True)
    seen = {}

    class Reader:
        def __init__(self, langs, gpu):
            seen["langs"] = langs
            seen["gpu"] = gpu

        def readtext(self, frame, detail=0):
            return ["Hello"]

    monkeypatch.setitem(sys.modules, "easyocr", types.SimpleNamespace(Reader=Reader))
    ocr = SlideOCR()
    assert ocr.read(object()).text == "Hello"
    assert seen == {"langs": ["en"], "gpu": True}
    assert ocr.available is True
