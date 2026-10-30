"""Learning from the operator's camera work: attribution, sample fields,
reframes, persisted history + thresholds, medians, production-memory write."""

import json
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.director import operator_shots as mod
from app.director.operator_shots import OperatorShotHistory, OperatorShotRecorder, describe
from app.domain.service_context import ServiceContext
from app.domain.service_state import ServiceState


@pytest.fixture(autouse=True)
def shot_settings(monkeypatch):
    for name, value in {
        "operator_shots_enabled": True,
        "operator_shots_memory": True,
        "operator_shots_max_samples": 12,
        "operator_shots_min_samples": 3,
        "operator_shots_min_share": 0.6,
        "operator_shots_command_window_seconds": 3.0,
        "operator_shots_context_seconds": 20.0,
        "operator_shots_tail_words": 12,
        "operator_shots_speaker_window_seconds": 15.0,
    }.items():
        monkeypatch.setitem(settings.__dict__, name, value)


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


ROLE_SHOTS = {("pastor", 1, 3), ("wide", 1, 1), ("liturgist", 1, 2)}


def _rig(tmp_path, *, clock, state=ServiceState.SERMON, app_acted=False, presets=None):
    """Recorder with injected collaborators: input 1 = PTZ camera 1, input 2 = slides."""
    presets = presets if presets is not None else {1: 3}
    memory = []
    context = ServiceContext(_mono=clock, _wall=clock)
    context.set_state(state, "cue")
    context.set_easyworship_item("Sermon")

    def role_for_input(atem_input, current_role):
        if atem_input == 2:
            return "slides"
        if atem_input == 1:
            preset = presets.get(1)
            for role, cam, pid in ROLE_SHOTS:
                if (cam, pid) == (1, preset):
                    return role
        return None

    recorder = OperatorShotRecorder(
        OperatorShotHistory(str(tmp_path / "shots.json")),
        context=context,
        camera_role_for_input=role_for_input,
        camera_for_input=lambda atem_input: 1 if atem_input == 1 else None,
        input_for_camera=lambda camera_id: 1 if camera_id == 1 else None,
        current_preset=lambda camera_id: presets.get(camera_id),
        role_for_shot=lambda cam, pid: next((r for r, c, p in ROLE_SHOTS if (c, p) == (cam, pid)), None),
        ptz_cameras=lambda: [1],
        app_acted_recently=lambda: app_acted,
        record_memory=lambda sample, text: memory.append((sample, text)),
        now=clock,
    )
    return recorder, context, memory, presets


def _program(to, previous, *, commanded=False, preview=None, transition=False):
    return {"event": "PROGRAM_CHANGED", "payload": {
        "program_input": to, "previous_input": previous, "preview_input": preview,
        "transition_in_progress": transition, "source": "poll", "commanded": commanded, "mock": False,
    }}


# -- attribution -------------------------------------------------------------------
async def test_operator_cut_is_recorded_with_full_sample(tmp_path):
    clock = _Clock()
    recorder, context, memory, _ = _rig(tmp_path, clock=clock)
    recorder.seed(program=2, camera_role="slides")
    await recorder.handle({"event": "AUDIO_STARTED", "payload": {"speaker_role": "pastor"}})
    await recorder.handle({"event": "TRANSCRIPT", "payload": {"role": "pastor", "text": "let us turn to the gospel of John chapter three"}})
    clock.t += 2.5
    sample = await recorder.handle(_program(1, 2, preview=2))

    assert sample["kind"] == "cut"
    assert sample["service_state"] == "sermon"
    assert sample["speaker_role"] == "pastor" and sample["singing"] is False
    assert (sample["from_input"], sample["to_input"]) == (2, 1)
    assert sample["from_camera_role"] == "slides" and sample["to_camera_role"] == "pastor"
    assert sample["camera_id"] == 1 and sample["preset_id"] == 3
    assert sample["hold_seconds"] == 2.5
    assert sample["transition"] == "cut"
    assert sample["delay_since_speaker_change"] == 2.5
    assert sample["transcript_tail"] == "let us turn to the gospel of John chapter three"
    assert sample["easyworship_item"] == "Sermon"
    assert sample["plan_element"]["state"] == "sermon"
    assert recorder.status()["program"] == 1 and recorder.status()["camera_role"] == "pastor"

    stored, text = memory[0]
    assert stored is sample
    assert text.startswith("Operator cut to pastor (preset 3) from slides during sermon while the pastor was speaking")
    assert "2.5s after the speaker changed" in text


async def test_app_commanded_cut_is_not_learned(tmp_path):
    clock = _Clock()
    recorder, _, memory, _ = _rig(tmp_path, clock=clock)
    recorder.seed(program=2)
    assert await recorder.handle(_program(1, 2, commanded=True)) is None
    assert memory == []
    # Tracking still follows the app's cut so the next operator hold is measured from it.
    assert recorder.status()["program"] == 1
    clock.t += 7
    sample = await recorder.handle(_program(2, 1))
    assert sample["hold_seconds"] == 7.0 and sample["from_camera_role"] == "pastor"


async def test_cut_inside_camera_command_window_is_not_learned(tmp_path):
    clock = _Clock()
    recorder, _, memory, _ = _rig(tmp_path, clock=clock, app_acted=True)
    recorder.seed(program=2)
    assert await recorder.handle(_program(1, 2)) is None
    assert memory == [] and recorder.history.summary()["samples"] == 0


def test_default_attribution_uses_camera_service_window(monkeypatch):
    from app.cameras.service import camera_service

    monkeypatch.setitem(settings.__dict__, "operator_shots_command_window_seconds", 3.0)
    monkeypatch.setattr(camera_service, "_last_command_at", float("-inf"))
    assert camera_service.commanded_within(3.0) is False
    camera_service.note_command(1, 3)
    assert camera_service.commanded_within(3.0) is True
    assert mod._app_acted_recently() is True
    monkeypatch.setattr(camera_service, "_last_command_at", float("-inf"))


async def test_engine_paths_note_camera_commands(monkeypatch):
    from app.director.action_engine import ActionEngine

    notes = []

    class _Cam:
        def note_command(self, camera_id=None, preset_id=None):
            notes.append((camera_id, preset_id))

    ActionEngine._note_camera_command(_Cam())
    ActionEngine._note_camera_command(_Cam(), 1, 3)
    ActionEngine._note_camera_command(object())  # fakes without the hook are tolerated
    assert notes == [(None, None), (1, 3)]


async def test_disabled_recorder_tracks_but_does_not_record(tmp_path, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "operator_shots_enabled", False)
    clock = _Clock()
    recorder, _, memory, _ = _rig(tmp_path, clock=clock)
    recorder.seed(program=2)
    assert await recorder.handle(_program(1, 2)) is None
    assert memory == [] and recorder.status()["enabled"] is False


# -- sample details ---------------------------------------------------------------------
async def test_auto_transition_singing_and_speaker_window(tmp_path):
    clock = _Clock()
    recorder, context, _, presets = _rig(tmp_path, clock=clock, state=ServiceState.HYMN)
    presets[1] = 1  # camera parked on wide
    recorder.seed(program=2)
    await recorder.handle({"event": "AUDIO_STARTED", "payload": {"speaker_role": "vocalist"}})
    clock.t += 40  # the speaker change is too old to count as a reaction
    await recorder.handle({"event": "PREVIEW_CHANGED", "payload": {"preview_input": 1, "transition_in_progress": True}})
    clock.t += 1
    sample = await recorder.handle(_program(1, 2, transition=False))
    assert sample["transition"] == "auto"
    assert sample["singing"] is True and sample["speaker_role"] == "vocalist"
    assert sample["to_camera_role"] == "wide" and sample["preset_id"] == 1
    assert sample["delay_since_speaker_change"] is None
    assert "(auto transition)" in describe(sample)


async def test_first_cut_has_no_hold_and_unknown_transition(tmp_path):
    clock = _Clock()
    recorder, _, _, _ = _rig(tmp_path, clock=clock)
    sample = await recorder.handle({"event": "PROGRAM_CHANGED", "payload": {"program_input": 1, "previous_input": 2}})
    assert sample["hold_seconds"] is None and sample["transition"] is None
    assert sample["speaker_role"] is None
    assert describe(sample) == "Operator cut to pastor (preset 3) from input 2 during sermon"


async def test_speaker_falls_back_to_context_and_stopped_speaker_is_kept(tmp_path):
    clock = _Clock()
    recorder, context, _, _ = _rig(tmp_path, clock=clock)
    context.set_speaking("liturgist", True)
    recorder.seed(program=2)
    sample = await recorder.handle(_program(1, 2))
    assert sample["speaker_role"] == "liturgist"  # nothing heard on the bus yet: ask the context
    await recorder.handle({"event": "AUDIO_STARTED", "payload": {"role": "pastor"}})
    await recorder.handle({"event": "AUDIO_STOPPED", "payload": {"role": "pastor"}})
    clock.t += 5
    sample = await recorder.handle(_program(2, 1))
    assert sample["speaker_role"] == "pastor"  # the last speaker, even in the pause after


# -- reframes -------------------------------------------------------------------------------
async def test_operator_reframe_on_program_is_recorded(tmp_path):
    clock = _Clock()
    recorder, _, memory, presets = _rig(tmp_path, clock=clock)
    recorder.seed(program=1, camera_role="pastor")
    assert await recorder.poll() is None  # unchanged preset
    presets[1] = 1
    await recorder.handle({"event": "AUDIO_STARTED", "payload": {"speaker_role": "pastor"}})
    sample = await recorder.poll()
    assert sample["kind"] == "reframe" and sample["on_air"] == "program"
    assert (sample["camera_id"], sample["preset_id"], sample["to_camera_role"], sample["to_input"]) == (1, 1, "wide", 1)
    assert sample["speaker_role"] == "pastor"
    assert recorder.status()["camera_role"] == "wide"
    assert memory[-1][1] == "Operator reframed camera 1 to wide (preset 1) on program during sermon while the pastor was speaking"
    assert await recorder.poll() is None  # seen once


async def test_reframe_on_preview_recorded_but_off_air_or_app_moves_ignored(tmp_path):
    clock = _Clock()
    recorder, _, memory, presets = _rig(tmp_path, clock=clock)
    recorder.seed(program=2, preview=1)
    presets[1] = 2
    sample = await recorder.poll()
    assert sample["on_air"] == "preview" and sample["to_camera_role"] == "liturgist"

    recorder.seed(program=2, preview=2)  # camera neither on program nor preview
    presets[1] = 3
    assert await recorder.poll() is None

    recorder._app_acted_recently = lambda: True  # the cue engine/AI recalled it
    recorder.seed(program=1)
    presets[1] = 1
    assert await recorder.poll() is None
    assert len(memory) == 1


async def test_predictive_prestage_is_not_learned_as_a_reframe(tmp_path):
    clock = _Clock()
    recorder, _, memory, presets = _rig(tmp_path, clock=clock)
    recorder.seed(program=2, preview=1)
    presets[1] = 2
    await recorder.handle({"event": "PTZ_PRESTAGED", "payload": {"role": "liturgist", "camera_id": 1, "atem_preview": 1}})
    clock.t += 1
    assert await recorder.poll() is None  # predictive_ptz moved it, not the operator
    presets[1] = 1
    clock.t += 5  # outside the command window: the operator's own reframe again counts
    assert (await recorder.poll())["to_camera_role"] == "wide"
    assert len(memory) == 1


# -- history -----------------------------------------------------------------------------------
def _cut(state="sermon", speaker="pastor", role="pastor", to_input=1, preset=3, hold=None, delay=None, at=None, **extra):
    sample = {"kind": "cut", "at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "service_state": state, "speaker_role": speaker, "to_camera_role": role, "to_input": to_input,
              "camera_id": 1 if to_input == 1 else None, "preset_id": preset, "hold_seconds": hold,
              "delay_since_speaker_change": delay}
    sample.update(extra)
    return sample


def test_history_persists_and_caps_samples(tmp_path, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "operator_shots_max_samples", 2)
    path = tmp_path / "shots.json"
    history = OperatorShotHistory(str(path))
    for i in range(3):
        history.record(_cut(hold=10 + i, at=f"2026-10-30T10:00:0{i}+00:00"))
    history.record(_cut(speaker=None, role="wide", preset=1))
    raw = json.loads(path.read_text())
    assert raw["version"] == 1 and len(raw["entries"]) == 2
    reloaded = OperatorShotHistory(str(path))
    kept = reloaded.samples("sermon", "pastor")
    assert [s["hold_seconds"] for s in kept] == [11, 12]
    assert reloaded.samples(ServiceState.SERMON, None)[-1]["speaker_role"] is None
    assert reloaded.summary()["keys"] == 2 and reloaded.summary()["samples"] == 3


def test_history_record_ignores_samples_without_state(tmp_path):
    history = OperatorShotHistory(str(tmp_path / "shots.json"))
    history.record({"kind": "cut", "service_state": ""})
    assert history.summary()["samples"] == 0 and not (tmp_path / "shots.json").exists()


def test_preferred_shot_thresholds(tmp_path):
    history = OperatorShotHistory(str(tmp_path / "shots.json"))
    history.record(_cut()); history.record(_cut())
    assert history.preferred_shot("sermon", "pastor") is None  # 2 < min_samples 3
    history.record(_cut())
    preferred = history.preferred_shot("sermon", "pastor")
    assert preferred == {"camera_role": "pastor", "atem_input": 1, "camera_id": 1, "preset_id": 3,
                         "samples": 3, "total": 3, "share": 1.0}
    history.record(_cut(role="wide", preset=1)); history.record(_cut(role="wide", preset=1))
    assert history.preferred_shot("sermon", "pastor") == {**preferred, "total": 5, "share": 0.6}
    history.record(_cut(role="slides", to_input=2, preset=None))
    assert history.preferred_shot("sermon", "pastor") is None  # 3/6 = 0.5 < min share
    assert history.preferred_shot("announcement", "pastor") is None
    assert history.preferred_shot(ServiceState.SERMON) is None  # all speakers in the state: same split


def test_preferred_shot_counts_reframes_and_unknown_role_inputs(tmp_path):
    history = OperatorShotHistory(str(tmp_path / "shots.json"))
    history.record(_cut(role=None, to_input=3, preset=None))
    history.record(_cut(role=None, to_input=3, preset=None))
    history.record({"kind": "reframe", "at": "2026-10-30T10:00:00+00:00", "service_state": "sermon",
                    "speaker_role": "pastor", "camera_id": 1, "preset_id": 5, "to_camera_role": None, "to_input": 3})
    preferred = history.preferred_shot("sermon", "pastor")
    assert preferred["camera_role"] is None and preferred["atem_input"] == 3 and preferred["preset_id"] == 5
    assert preferred["samples"] == 3


def test_typical_hold_and_latency_medians(tmp_path):
    history = OperatorShotHistory(str(tmp_path / "shots.json"))
    history.record(_cut(hold=10, delay=1.0))
    history.record(_cut(hold=30, delay=3.0, speaker="liturgist", role="liturgist", preset=2))
    assert history.typical_hold("sermon") is None  # 2 < min_samples
    history.record(_cut(hold=20, delay=None, speaker=None, role="wide", preset=1))
    assert history.typical_hold("sermon") == 20.0
    assert history.speaker_change_latency("sermon") is None  # only two delays
    history.record(_cut(hold=None, delay=2.0))
    assert history.typical_hold("sermon") == 20.0 and history.speaker_change_latency("sermon") == 2.0
    history.record({"kind": "reframe", "at": "x", "service_state": "sermon", "speaker_role": "pastor",
                    "camera_id": 1, "preset_id": 4, "hold_seconds": 999, "to_camera_role": "pastor", "to_input": 1})
    assert history.typical_hold("sermon") == 20.0  # reframes never count toward holds
    summary = history.summary(limit=2)
    assert summary["typical_hold_seconds"] == {"sermon": 20.0}
    assert summary["speaker_change_latency_seconds"] == {"sermon": 2.0}
    assert len(summary["recent"]) == 2
    assert {p["speaker_role"] for p in summary["preferences"]} == {"pastor", "liturgist", None}


# -- production memory -----------------------------------------------------------------------
def test_memory_write_carries_structured_metadata(monkeypatch):
    calls = []

    class _Manager:
        def record_observation(self, category, text, source="system", **kwargs):
            calls.append((category, text, source, kwargs))

    import app.memory.production_memory as pm

    monkeypatch.setattr(pm, "memory_manager", _Manager())
    sample = _cut(hold=12.0, delay=2.5, from_input=2, from_camera_role="slides", transition="cut", singing=False,
                  plan_element={"id": "sermon", "label": "Sermon", "state": "sermon"}, easyworship_item="Sermon")
    mod._record_memory(sample, describe(sample))
    category, text, source, kwargs = calls[0]
    assert category == "operator_shot" and source == "atem:1"
    assert text.startswith("Operator cut to pastor (preset 3) from slides during sermon while the pastor was speaking after 12s")
    assert kwargs["service_state"] == "sermon" and kwargs["speaker_role"] == "pastor"
    assert kwargs["camera_role"] == "pastor" and kwargs["atem_input"] == 1 and kwargs["easyworship_item"] == "Sermon"
    meta = kwargs["metadata"]
    assert meta["kind"] == "cut" and meta["preset_id"] == 3 and meta["camera_id"] == 1
    assert meta["hold_seconds"] == 12.0 and meta["delay_since_speaker_change"] == 2.5
    assert meta["from_camera_role"] == "slides" and meta["transition"] == "cut" and meta["singing"] is False
    assert meta["plan_element"] == "sermon" and meta["plan_label"] == "Sermon"
    # build_metadata keeps the structured fields the house-style summary reads.
    built = pm.build_metadata(
        service_state=kwargs["service_state"], speaker_role=kwargs["speaker_role"], category=category,
        camera_role=kwargs["camera_role"], atem_input=kwargs["atem_input"],
        easyworship_item=kwargs["easyworship_item"], extra=kwargs["metadata"])
    assert built["kind"] == "cut" and built["hold_seconds"] == 12.0 and built["atem_input"] == 1


async def test_memory_failure_does_not_block_recording(tmp_path, monkeypatch):
    clock = _Clock()
    recorder, _, _, _ = _rig(tmp_path, clock=clock)

    def boom(sample, text):
        raise RuntimeError("db down")

    recorder._record_memory = boom
    recorder.seed(program=2)
    sample = await recorder.handle(_program(1, 2))
    assert sample is not None and recorder.history.summary()["samples"] == 1
    assert recorder.status()["recorded_this_run"] == 1 and recorder.status()["last_sample"] is sample


async def test_memory_write_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "operator_shots_memory", False)
    clock = _Clock()
    recorder, _, memory, _ = _rig(tmp_path, clock=clock)
    recorder.seed(program=2)
    assert await recorder.handle(_program(1, 2)) is not None
    assert memory == []


async def test_bus_event_is_published_for_each_sample(tmp_path, monkeypatch):
    published = []
    monkeypatch.setattr(mod.event_bus, "publish", lambda message: published.append(message))
    clock = _Clock()
    recorder, _, _, _ = _rig(tmp_path, clock=clock)
    recorder.seed(program=2)
    await recorder.handle(_program(1, 2))
    assert published[0]["event"] == "OPERATOR_SHOT" and published[0]["payload"]["kind"] == "cut"


async def test_start_seeds_from_context_and_stop_cancels(tmp_path, monkeypatch):
    clock = _Clock()
    recorder, context, _, _ = _rig(tmp_path, clock=clock)
    context.set_program(2, "slides")
    context.set_preview(1)
    await recorder.start()
    try:
        status = recorder.status()
        assert status["running"] and status["program"] == 2 and status["preview"] == 1
        assert status["camera_role"] == "slides" and status["presets"] == {"1": 3}
        assert status["consumes"] == ["PROGRAM_CHANGED", "PREVIEW_CHANGED", "AUDIO_STARTED", "AUDIO_STOPPED",
                                      "TRANSCRIPT", "PTZ_PRESTAGED"]
    finally:
        await recorder.stop()
    assert recorder.status()["running"] is False


def test_describe_minimal_samples():
    assert describe({"kind": "cut", "service_state": "announcement", "to_input": 3}) == \
        "Operator cut to input 3 during announcement"
    assert describe({"kind": "reframe", "service_state": "hymn", "camera_id": 1, "preset_id": 4, "on_air": "preview",
                     "speaker_role": "vocalist", "singing": True}) == \
        "Operator reframed camera 1 to preset 4 on preview during hymn while the vocalist was singing"
    long_hold = describe({"kind": "cut", "service_state": "sermon", "to_camera_role": "wide", "hold_seconds": 240})
    assert long_hold == "Operator cut to wide during sermon after 4 min on the previous shot"
