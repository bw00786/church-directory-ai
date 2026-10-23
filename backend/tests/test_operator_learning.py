"""Learning from the operator's own slide advances (manual mode)."""

from datetime import datetime, timezone

import pytest

from app.config import settings
from app.director.operator_learning import OperatorAdvanceHistory, OperatorAdvanceRecorder, describe
from app.director.text_follower import LiveSlides, SongFollowerService, TextFollower
from app.domain.service_context import TranscriptLine
from app.easyworship.remote_protocol import EWState

SLIDE_1 = "Amazing grace how sweet the sound that saved a wretch like me"
SLIDE_2 = "I once was lost but now am found was blind but now I see"


@pytest.fixture(autouse=True)
def learning_settings(monkeypatch):
    for name, value in {
        "operator_learning_enabled": True,
        "operator_learning_memory": True,
        "operator_learning_context_seconds": 20.0,
        "operator_learning_tail_words": 12,
        "operator_learning_min_samples": 2,
        "operator_learning_max_samples": 8,
        "operator_learning_min_dwell_fraction": 0.35,
        "song_follower_roles": "liturgist,congregation,vocalist",
        "song_follower_tail_words": 8,
        "song_follower_min_anchor_words": 3,
        "song_follower_ocr_learn": True,
    }.items():
        monkeypatch.setattr(settings, name, value)


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _recorder(tmp_path, *, clock, commanded=False, labels=None, known=None):
    memory, learned = [], []
    recorder = OperatorAdvanceRecorder(
        OperatorAdvanceHistory(str(tmp_path / "advances.json")),
        ew_state=lambda: None,
        read_ocr=lambda: "",
        commanded_recently=lambda: commanded,
        item_label=lambda pres_no: (labels or {}).get(pres_no),
        known_slide_text=lambda label, n: (known or {}).get((label, n), ""),
        learn_slide_text=lambda label, n, text: learned.append((label, n, text)),
        record_memory=lambda category, text, source: memory.append((category, text, source)),
        now=clock,
    )
    return recorder, memory, learned


def _ew(pres_no, slide_no):
    return EWState(paired=True, pres_no=pres_no, slide_no=slide_no, slide_rowid=pres_no * 100 + slide_no)


# -- recorder --------------------------------------------------------------------
async def test_manual_advance_is_recorded_with_camera_text_heard_words_and_timing(tmp_path):
    clock = _Clock()
    recorder, memory, learned = _recorder(tmp_path, clock=clock, labels={3: "Hymn of Praise"})
    await recorder.observe(_ew(3, 1), SLIDE_1)
    clock.t += 2
    await recorder.observe(_ew(3, 1), SLIDE_1)  # second agreeing camera read settles the text
    assert learned == [("Hymn of Praise", 1, SLIDE_1)]

    recorder.on_transcript("vocalist", "that saved a wretch like me", at=clock.t + 20)
    recorder.on_transcript("pastor", "ignored, not a follower role", at=clock.t + 21)
    clock.t += 22
    record = await recorder.observe(_ew(3, 2), SLIDE_1)  # screen still shows the old slide

    assert record["kind"] == "slide"
    assert (record["label"], record["from_slide"], record["to_slide"]) == ("Hymn of Praise", 1, 2)
    assert record["dwell"] == 24.0 and record["delay"] == 2.0
    assert record["role"] == "vocalist" and record["tail"] == "that saved a wretch like me"
    assert record["slide_text"] == SLIDE_1
    category, text, source = memory[0]
    assert category == "operator_slide_advance" and source == "easyworship:Hymn of Praise"
    assert "that saved a wretch like me" in text

    # The stale camera read right after the change isn't taken as the new slide's text.
    assert recorder.status()["current"]["text"] == ""


async def test_advances_commanded_by_the_app_are_not_learned(tmp_path):
    clock = _Clock()
    recorder, memory, _ = _recorder(tmp_path, clock=clock, commanded=True)
    await recorder.observe(_ew(3, 1), None)
    clock.t += 10
    assert await recorder.observe(_ew(3, 2), None) is None
    assert memory == []


async def test_item_change_and_going_back_go_to_memory_but_not_slide_history(tmp_path):
    clock = _Clock()
    recorder, memory, _ = _recorder(
        tmp_path, clock=clock, labels={3: "Hymn of Praise", 4: "Scripture"},
        known={("Hymn of Praise", 2): SLIDE_2, ("Hymn of Praise", 3): "third slide words here"},
    )
    await recorder.observe(_ew(3, 3), None)
    clock.t += 5
    back = await recorder.observe(_ew(3, 2), None)
    clock.t += 5
    item = await recorder.observe(_ew(4, 1), None)
    assert back["kind"] == "back" and item["kind"] == "item"
    assert item["to_label"] == "Scripture"
    assert len(memory) == 2
    assert recorder.history.summary()["slides"] == 0


async def test_without_easyworship_the_camera_detects_the_advance(tmp_path):
    clock = _Clock()
    recorder, memory, _ = _recorder(tmp_path, clock=clock)
    for _ in range(2):
        await recorder.observe(None, SLIDE_1)
        clock.t += 2
    recorder.on_transcript("congregation", "saved a wretch like me", at=clock.t)
    clock.t += 30
    assert await recorder.observe(None, SLIDE_2) is None  # one read could be a transition frame
    clock.t += 2
    record = await recorder.observe(None, SLIDE_2)
    assert record["kind"] == "slide" and record["slide_text"] == SLIDE_1
    assert record["dwell"] == 34.0  # timed from the first read of the new slide
    assert recorder.history.advance_cues(SLIDE_1) == []  # one sample isn't enough yet


async def test_slide_text_falls_back_to_known_texts_without_camera(tmp_path):
    clock = _Clock()
    recorder, _, _ = _recorder(tmp_path, clock=clock, labels={3: "Hymn"}, known={("Hymn", 1): SLIDE_1})
    await recorder.observe(_ew(3, 1), None)
    clock.t += 15
    record = await recorder.observe(_ew(3, 2), None)
    assert record["slide_text"] == SLIDE_1


def test_describe_item_change():
    text = describe({"kind": "item", "label": "Hymn", "to_label": "Sermon", "dwell": 61.2, "tail": "", "slide_text": ""})
    assert text == "Operator moved from Hymn to Sermon after 61s"


# -- history ---------------------------------------------------------------------
def _sample(tail, dwell):
    return {"at": datetime.now(timezone.utc).isoformat(), "label": "Hymn", "from_slide": 1,
            "dwell": dwell, "delay": 1.0, "role": "vocalist", "tail": tail}


def test_history_cues_and_dwell_need_enough_samples_and_match_ocr_jitter(tmp_path):
    path = tmp_path / "advances.json"
    history = OperatorAdvanceHistory(str(path))
    history.record(SLIDE_1, _sample("how sweet the sound that saved a wretch like me", 20))
    assert history.advance_cues(SLIDE_1) == []
    assert history.typical_dwell(SLIDE_1) is None

    history.record(SLIDE_1 + " CCLl", _sample("sound that saved a wretch like mee", 30))  # OCR noise
    reloaded = OperatorAdvanceHistory(str(path))
    assert reloaded.typical_dwell(SLIDE_1) == 25.0
    assert reloaded.advance_cues(SLIDE_1) == [
        "sound that saved a wretch like mee", "the sound that saved a wretch like me",
    ]
    assert reloaded.summary()["slides"] == 1


# -- song follower uses what was learned -------------------------------------------
class _History:
    def __init__(self, cues=None, dwell=None):
        self.cues, self.dwell = cues or {}, dwell

    def advance_cues(self, text):
        return self.cues.get(text, [])

    def typical_dwell(self, text):
        return self.dwell


def _line(text, role="vocalist"):
    return TranscriptLine(speaker_role=role, text=text, timestamp=datetime.now(timezone.utc))


def _service(monkeypatch, history, clock):
    monkeypatch.setattr(settings, "song_follower_enabled", True)
    monkeypatch.setattr(settings, "song_follower_cooldown_seconds", 0.0)
    monkeypatch.setattr(settings, "song_follower_min_confidence", 0.72)
    monkeypatch.setattr(settings, "song_follower_screen_weight", 0.0)
    svc = SongFollowerService(
        text_source=lambda: LiveSlides(song_id="lib:hymn:1", slides=[SLIDE_1, SLIDE_2], index=0), now=clock,
    )
    svc.set_history(history)
    return svc


def test_learned_operator_cue_triggers_an_advance(monkeypatch):
    # ASR keeps mishearing the closing line: the slide text never matches, the operator's cue does.
    history = _History(cues={SLIDE_1: ["that shaved a rest like knee"]})
    svc = _service(monkeypatch, history, _Clock())
    actions = svc.process([_line("amazing grace how sweet the sound"), _line("that shaved a rest like knee")])
    assert actions and actions[0].parameters["trigger"] == "operator_cue"
    assert "learned from the operator" in actions[0].reason


def test_without_learned_cues_noisy_asr_does_not_advance(monkeypatch):
    svc = _service(monkeypatch, _History(), _Clock())
    assert svc.process([_line("amazing grace how sweet the sound"), _line("that shaved a rest like knee")]) == []


def test_advance_held_until_slide_has_been_up_long_enough(monkeypatch):
    clock = _Clock()
    svc = _service(monkeypatch, _History(dwell=40.0), clock)
    full = "amazing grace how sweet the sound that saved a wretch like me"
    assert svc.process([_line(full)]) == []  # 0s of the usual 40s
    clock.t += 15  # past 35% of 40s
    actions = svc.process([_line("that saved a wretch like me")])
    assert actions and actions[0].parameters["trigger"] == "slide_end"


def test_follower_cue_tokens_need_minimum_words():
    f = TextFollower(min_anchor_words=3)
    f.load("s", [SLIDE_1, SLIDE_2], advance_cues=[["amen", "saved a wretch"], []])
    assert f.status()["learned_cues"] == 1
