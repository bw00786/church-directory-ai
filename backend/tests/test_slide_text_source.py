"""Tests for the song follower's slide-text sources and in-item slide advance."""

import pytest

from app.config import settings
from app.director.slide_text_source import (
    LiveSlideTextSource,
    OcrSlideReader,
    SlideTextLibrary,
    split_slides,
)
from app.easyworship.remote_protocol import EWState
from app.easyworship.service import EasyWorshipService


def test_split_slides_on_blank_lines():
    text = "The Lord be with you.\nAnd also with you.\n\n  \nLift up your hearts.\r\n\r\nWe lift them up."
    assert split_slides(text) == [
        "The Lord be with you. And also with you.",
        "Lift up your hearts.",
        "We lift them up.",
    ]


def test_library_persists_and_matches_labels_case_insensitively(tmp_path):
    path = tmp_path / "texts.json"
    lib = SlideTextLibrary(str(path))
    lib.set("Call to Worship", "One\n\nTwo")
    assert lib.get("call to  worship") == ["One", "Two"]
    assert SlideTextLibrary(str(path)).get("CALL TO WORSHIP") == ["One", "Two"]
    assert lib.delete("Call to Worship") is True
    assert SlideTextLibrary(str(path)).get("Call to Worship") is None


def test_library_rejects_bad_input(tmp_path):
    lib = SlideTextLibrary(str(tmp_path / "t.json"))
    with pytest.raises(ValueError):
        lib.set("", "text")
    with pytest.raises(ValueError):
        lib.set("Scripture", "   \n\n  ")


def test_library_version_changes_on_update(tmp_path):
    lib = SlideTextLibrary(str(tmp_path / "t.json"))
    lib.set("Scripture", "a")
    v1 = lib.version("Scripture")
    lib.set("Scripture", "b")
    assert lib.version("scripture") == v1 + 1


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


async def test_ocr_reader_drops_copyright_footer_and_ignores_jitter():
    clock = _Clock()
    screen = {"segments": ["Amazing grace how sweet the sound", "CCLI License # 12345"]}
    reader = OcrSlideReader(read_segments=lambda: screen["segments"], now=clock)
    await reader.refresh()
    first = reader.current()
    assert first.slides == ["Amazing grace how sweet the sound"]
    assert first.bounded is False

    screen["segments"] = ["Amazing grace how sweet the sounc"]  # OCR jitter
    clock.t += 5
    await reader.refresh()
    assert reader.current().song_id == first.song_id


async def test_ocr_reader_waits_for_new_slide_after_invalidate(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_ocr_wait_seconds", 3.0)
    clock = _Clock()
    screen = {"segments": ["The Lord be with you"]}
    reader = OcrSlideReader(read_segments=lambda: screen["segments"], now=clock)
    await reader.refresh()
    old_id = reader.current().song_id

    reader.invalidate()
    assert reader.current() is None
    await reader.refresh()  # screen hasn't changed yet
    assert reader.current() is None

    screen["segments"] = ["Lift up your hearts"]
    await reader.refresh()
    new = reader.current()
    assert new.slides == ["Lift up your hearts"] and new.song_id != old_id


async def test_ocr_reader_accepts_identical_slide_after_wait(monkeypatch):
    monkeypatch.setattr(settings, "song_follower_ocr_wait_seconds", 3.0)
    clock = _Clock()
    reader = OcrSlideReader(read_segments=lambda: ["Alleluia"], now=clock)
    await reader.refresh()
    old_id = reader.current().song_id
    reader.invalidate()
    clock.t += 4
    await reader.refresh()
    assert reader.current().song_id != old_id


def _source(tmp_path, *, active=True, label="Call to Worship", slide_number=None, ocr_segments=None):
    lib = SlideTextLibrary(str(tmp_path / "t.json"))
    ocr = OcrSlideReader(read_segments=lambda: ocr_segments or [])
    return lib, LiveSlideTextSource(
        lib, ocr, active=lambda: active, label=lambda: label, slide_number=lambda: slide_number
    )


async def test_source_prefers_library_with_live_slide_number(tmp_path):
    lib, source = _source(tmp_path, slide_number=2, ocr_segments=["ignored"])
    lib.set("Call to Worship", "One\n\nTwo\n\nThree")
    await source.refresh()
    live = source()
    assert live.slides == ["One", "Two", "Three"]
    assert live.index == 1 and live.bounded is True


async def test_source_falls_back_to_ocr(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "song_follower_ocr_enabled", True)
    _, source = _source(tmp_path, ocr_segments=["Give thanks to the Lord"])
    await source.refresh()
    live = source()
    assert live.slides == ["Give thanks to the Lord"] and live.bounded is False


async def test_source_inactive_outside_followed_cues(tmp_path):
    lib, source = _source(tmp_path, active=False)
    lib.set("Call to Worship", "One\n\nTwo")
    await source.refresh()
    assert source() is None


# -- EasyWorship: advance within the live item only -----------------------------
class _RollDriver:
    """Remote-style state; next_slide past the last slide rolls into the next item."""

    supports_confirmation = False

    def __init__(self, slides_per_item=3):
        self.state = EWState(paired=True, pres_no=2, slide_no=1)
        self.slides_per_item = slides_per_item
        self.sent = []

    async def connect(self):
        return True

    async def is_connected(self):
        return True

    async def send_action(self, action):
        self.sent.append(action)
        s = self.state
        if action == "next_slide":
            if s.slide_no < self.slides_per_item:
                s.slide_no += 1
            else:
                s.pres_no, s.slide_no = s.pres_no + 1, 1
        elif action == "prev_slide":
            if s.slide_no > 1:
                s.slide_no -= 1
            else:
                s.pres_no, s.slide_no = s.pres_no - 1, self.slides_per_item
        s.status_count += 1
        return True


async def test_next_slide_in_item_advances():
    driver = _RollDriver()
    svc = EasyWorshipService(driver=driver)
    assert await svc.next_slide_in_item() is True
    assert (driver.state.pres_no, driver.state.slide_no) == (2, 2)
    assert svc.live_slide_number() == 2


async def test_next_slide_in_item_steps_back_at_end_of_item():
    driver = _RollDriver()
    driver.state.slide_no = 3
    svc = EasyWorshipService(driver=driver)
    assert await svc.next_slide_in_item() is False
    assert driver.sent == ["next_slide", "prev_slide"]
    assert (driver.state.pres_no, driver.state.slide_no) == (2, 3)


def test_slide_texts_api_roundtrip(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.api.director as director_api

    monkeypatch.setattr(director_api, "slide_text_library", SlideTextLibrary(str(tmp_path / "t.json")))
    api = FastAPI()
    api.include_router(director_api.router)
    client = TestClient(api)

    resp = client.put("/director/slide-texts", json={"label": "Scripture", "text": "In the beginning\n\nwas the Word"})
    assert resp.status_code == 200 and resp.json()["slides"] == ["In the beginning", "was the Word"]
    assert client.get("/director/slide-texts").json()["items"][0]["label"] == "Scripture"
    assert client.put("/director/slide-texts", json={"label": "Scripture", "text": " \n\n "}).status_code == 422
    assert client.delete("/director/slide-texts/Scripture").status_code == 200
    assert client.delete("/director/slide-texts/Scripture").status_code == 404
    assert client.get("/director/follower").status_code == 200


def test_whisper_roles_include_follower_roles(monkeypatch):
    from app.audio.whisper_service import whisper_roles

    monkeypatch.setattr(settings, "whisper_roles", "pastor,liturgist")
    monkeypatch.setattr(settings, "song_follower_roles", "liturgist,congregation")
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    assert whisper_roles() == {"pastor", "liturgist", "congregation"}
    monkeypatch.setattr(settings, "song_follower_enabled", False)
    assert whisper_roles() == {"pastor", "liturgist"}
