"""ContextSync keeps ServiceContext current from bus events + initial sync."""

import asyncio
from types import SimpleNamespace

import pytest

from app.config import settings
from app.director import context_sync as sync_mod
from app.director.context_sync import ContextSync, camera_role_for_input
from app.domain.observations import AudioObservation
from app.domain.service_context import ServiceContext, service_context
from app.domain.service_state import ServiceState
from app.events.bus import event_bus

CAM1, CAM2 = 1, 2


@pytest.fixture(autouse=True)
def fresh_context(monkeypatch):
    """Reset the module singleton for each test and restore it afterwards."""
    saved = dict(service_context.__dict__)
    service_context.__dict__.update(ServiceContext().__dict__)
    monkeypatch.setattr(settings, "atem_camera1_input", CAM1)
    monkeypatch.setattr(settings, "atem_camera2_input", CAM2)
    monkeypatch.setattr(settings, "ptz_camera_id", 1)
    yield service_context
    service_context.__dict__.clear()
    service_context.__dict__.update(saved)


@pytest.fixture
def presets(monkeypatch):
    from app.cameras.service import camera_service

    table = {}
    monkeypatch.setattr(camera_service, "get_current_preset", lambda camera_id: table.get(camera_id))
    return table


def _msg(event, **payload):
    return {"event": event, "payload": payload}


# -- camera role resolution -----------------------------------------------------
def test_camera_role_for_input_slides_and_ptz(presets):
    assert camera_role_for_input(CAM2) == "slides"
    assert camera_role_for_input(None) is None
    assert camera_role_for_input(7) is None                   # unknown input
    assert camera_role_for_input(CAM1) is None                # PTZ, no preset recalled yet
    presets[1] = settings.camera_role_pastor_preset
    assert camera_role_for_input(CAM1) == "pastor"
    presets[1] = settings.camera_role_liturgist_preset
    assert camera_role_for_input(CAM1, current_role="pastor") == "liturgist"


def test_camera_role_prefers_current_role_when_presets_are_shared(presets, monkeypatch):
    monkeypatch.setattr(settings, "camera_role_wide_preset", 6)
    monkeypatch.setattr(settings, "camera_role_congregation_preset", 6)
    presets[1] = 6
    assert camera_role_for_input(CAM1, current_role="congregation") == "congregation"
    assert camera_role_for_input(CAM1, current_role="wide") == "wide"
    assert camera_role_for_input(CAM1, current_role="slides") in ("congregation", "wide")


# -- events -----------------------------------------------------------------------
def test_program_changed_updates_program_preview_cut_and_role(fresh_context, presets):
    sync = ContextSync()
    presets[1] = settings.camera_role_pastor_preset
    v = fresh_context.version
    assert sync.handle(_msg("PROGRAM_CHANGED", program_input=CAM1, previous_input=CAM2, preview_input=CAM2,
                            commanded=False, source="poll"))
    assert fresh_context.current_atem_program == CAM1
    assert fresh_context.current_atem_preview == CAM2
    assert fresh_context.current_camera_role == "pastor"
    assert fresh_context.last_cut_at is not None
    assert fresh_context.version > v
    assert sync.events_handled == 1 and sync.last_event == "PROGRAM_CHANGED"

    assert sync.handle(_msg("PROGRAM_CHANGED", program_input=CAM2, previous_input=CAM1, preview_input=CAM1))
    assert fresh_context.current_camera_role == "slides"
    assert fresh_context.snapshot()["atem_program"] == CAM2


def test_preview_changed_updates_preview_only(fresh_context):
    sync = ContextSync()
    fresh_context.set_program(CAM1)
    cut = fresh_context.last_cut_at
    assert sync.handle(_msg("PREVIEW_CHANGED", preview_input=CAM2, previous_input=CAM1, program_input=CAM1))
    assert fresh_context.current_atem_preview == CAM2
    assert fresh_context.current_atem_program == CAM1 and fresh_context.last_cut_at == cut


def test_easyworship_state_sets_item_slide_and_moves_phase(fresh_context, monkeypatch):
    from app.director.plan_builder import build_plan_from_script
    from app.director.script import build_default_service_script

    fresh_context.plan = build_plan_from_script(build_default_service_script())
    sync = ContextSync()
    # Label supplied in the payload (easyworship_service adds item_label).
    assert sync.handle(_msg("EASYWORSHIP_STATE", paired=True, pres_no=3, slide_no=2, item_label="Hymn of Praise"))
    assert fresh_context.current_easyworship_item == "Hymn of Praise"
    assert fresh_context.current_easyworship_slide == 2
    assert fresh_context.current_easyworship_pres_no == 3
    assert fresh_context.state == ServiceState.HYMN and fresh_context.state_source == "easyworship"

    # Label missing: resolved through easyworship_service.item_label_for(pres_no).
    from app.easyworship.service import easyworship_service

    monkeypatch.setattr(easyworship_service, "item_label_for", lambda pres_no: {4: "Scripture"}.get(pres_no))
    assert sync.handle(_msg("EASYWORSHIP_STATE", paired=True, pres_no=4, slide_no=1))
    assert fresh_context.current_easyworship_item == "Scripture"
    assert fresh_context.state == ServiceState.SCRIPTURE

    # Slide change within the same item: no phase change, slide updated.
    assert sync.handle(_msg("EASYWORSHIP_STATE", paired=True, pres_no=4, slide_no=2))
    assert fresh_context.current_easyworship_slide == 2 and fresh_context.state == ServiceState.SCRIPTURE


def test_easyworship_state_without_pres_no_keeps_last_item(fresh_context):
    sync = ContextSync()
    fresh_context.set_easyworship_item("Scripture", 1, 4)
    assert sync.handle(_msg("EASYWORSHIP_STATE", paired=False, pres_no=None, slide_no=None))
    assert fresh_context.current_easyworship_item == "Scripture"


def test_audio_started_stopped_maintain_speaking_roles(fresh_context):
    sync = ContextSync()
    started = AudioObservation(channel=1, speaker_role="pastor", speaking=True).model_dump(mode="json")
    assert sync.handle({"event": "AUDIO_STARTED", "payload": started})
    assert fresh_context.speaking_roles["pastor"]["speaking"] is True
    assert fresh_context.snapshot()["speaker"] == "pastor"

    stopped = AudioObservation(channel=1, speaker_role="pastor", speaking=False).model_dump(mode="json")
    assert sync.handle({"event": "AUDIO_STOPPED", "payload": stopped})
    assert fresh_context.speaking_roles["pastor"]["speaking"] is False
    assert fresh_context.snapshot()["speaking"] is False

    # Payload without an explicit speaking flag: derived from the event name.
    assert sync.handle(_msg("AUDIO_STARTED", role="vocalist"))
    assert fresh_context.speaking_roles["vocalist"]["speaking"] is True


def test_transcript_event_backfills_timing_and_singing(fresh_context):
    sync = ContextSync()
    fresh_context.set_speaking("vocalist", True)
    fresh_context.record_audio(AudioObservation(channel=4, speaker_role="vocalist", speaking=True, transcript="how great"))
    assert sync.handle(_msg("TRANSCRIPT", role="vocalist", channel=4, text="how great", confidence=0.9,
                            t_start=10.0, t_end=12.5, duration_ms=2500, is_singing=True))
    line = fresh_context.transcript[-1]
    assert (line.t_start, line.t_end, line.is_singing, line.confidence) == (10.0, 12.5, True, 0.9)
    assert fresh_context.speaking_roles["vocalist"]["is_singing"] is True


def test_cue_entered_sets_cursor_role_and_state(fresh_context):
    from app.director.plan_builder import build_plan_from_script
    from app.director.script import build_default_service_script

    fresh_context.plan = build_plan_from_script(build_default_service_script())
    sync = ContextSync()
    assert sync.handle(_msg("DIRECTOR_CUE_ENTERED", cue_id="sermon", index=9, total=19, label="Sermon — Pastor",
                            state="sermon", speaker_role="pastor", camera_role="pastor", easyworship_item=None,
                            followable=False))
    assert fresh_context.plan_index == 9
    assert fresh_context.current_camera_role == "pastor"
    assert fresh_context.state == ServiceState.SERMON and fresh_context.state_source == "cue"
    cursor = fresh_context.snapshot()["plan_cursor"]
    assert cursor["index"] == 9 and cursor["current"]["id"] == "sermon" and cursor["source"] == "cue"
    assert cursor["next"]["id"] == "communion_pastor"


def test_unrelated_messages_are_ignored(fresh_context):
    sync = ContextSync()
    v = fresh_context.version
    assert sync.handle({"type": "director", "data": {}}) is False
    assert sync.handle("not a dict") is False
    assert sync.handle(_msg("AI_DECISION", decision="continue")) is False
    assert fresh_context.version == v and sync.events_handled == 0


# -- run loop + lifecycle -----------------------------------------------------------
async def test_run_loop_consumes_bus_events(fresh_context, monkeypatch):
    sync = ContextSync()

    async def no_initial():
        return {}

    monkeypatch.setattr(sync, "initial_sync", no_initial)
    await sync.start()
    try:
        event_bus.publish(_msg("PREVIEW_CHANGED", preview_input=CAM1, previous_input=CAM2, program_input=CAM2))
        for _ in range(20):
            await asyncio.sleep(0)
            if fresh_context.current_atem_preview == CAM1:
                break
        assert fresh_context.current_atem_preview == CAM1
        assert sync.status()["running"] is True and sync.status()["events_handled"] == 1
    finally:
        await sync.stop()
    assert sync.running is False
    assert "DIRECTOR_CUE_ENTERED" in sync.status()["consumes"]


async def test_handler_errors_do_not_kill_the_loop(fresh_context, monkeypatch):
    sync = ContextSync()

    async def no_initial():
        return {}

    monkeypatch.setattr(sync, "initial_sync", no_initial)

    def boom(payload):
        raise RuntimeError("bad payload")

    sync._handlers["PREVIEW_CHANGED"] = boom
    await sync.start()
    try:
        event_bus.publish(_msg("PREVIEW_CHANGED", preview_input=CAM1))
        event_bus.publish(_msg("PROGRAM_CHANGED", program_input=CAM2))
        for _ in range(20):
            await asyncio.sleep(0)
            if fresh_context.current_atem_program == CAM2:
                break
        assert fresh_context.current_atem_program == CAM2
        assert sync.last_error == "bad payload"
    finally:
        await sync.stop()


# -- initial sync -----------------------------------------------------------------------
async def test_initial_sync_seeds_context_from_live_services(fresh_context, monkeypatch, presets):
    from app.director.plan_builder import build_plan_from_script
    from app.director.script import build_default_service_script

    fresh_context.plan = build_plan_from_script(build_default_service_script())
    presets[1] = settings.camera_role_liturgist_preset

    class _Atem:
        async def is_connected(self):
            return True

        async def get_state(self):
            return SimpleNamespace(program_input=CAM1, preview_input=CAM2)

    import app.dependencies as deps
    monkeypatch.setattr(deps, "get_atem_service_instance", lambda: _Atem())

    from app.easyworship.service import easyworship_service
    ew_state = SimpleNamespace(pres_no=7, slide_no=3, as_dict=lambda: {"pres_no": 7, "slide_no": 3, "paired": True})
    monkeypatch.setattr(type(easyworship_service), "live_state", property(lambda self: ew_state))
    monkeypatch.setattr(easyworship_service, "item_label_for", lambda pres_no: "Scripture" if pres_no == 7 else None)

    from app.audio.audio_observer import audio_observer
    monkeypatch.setattr(audio_observer, "speaking_roles",
                        {"liturgist": {"speaking": True, "since": 123.0, "last_spoke_at": 124.0}})

    from app.director.engine import service_director
    cue = {c.id: c for c in build_default_service_script().cues}["scripture_reading"]
    monkeypatch.setattr(service_director, "status", lambda: SimpleNamespace(running=True, cue_index=7))
    monkeypatch.setattr(service_director, "current_cue", lambda: cue)

    sync = ContextSync()
    synced = await sync.initial_sync()
    assert synced == {"atem": True, "easyworship": True, "audio": True, "cue": True}
    assert fresh_context.current_atem_program == CAM1 and fresh_context.current_atem_preview == CAM2
    assert fresh_context.current_easyworship_item == "Scripture" and fresh_context.current_easyworship_slide == 3
    assert fresh_context.speaking_roles["liturgist"] == {"speaking": True, "since": 123.0, "last_spoke_at": 123.0,
                                                          "is_singing": None}
    assert fresh_context.plan_index == 7
    assert fresh_context.current_camera_role == "liturgist"
    assert fresh_context.state == ServiceState.SCRIPTURE and fresh_context.state_source == "cue"


async def test_initial_sync_tolerates_disconnected_services(fresh_context, monkeypatch):
    class _Atem:
        async def is_connected(self):
            return False

        async def get_state(self):
            raise AssertionError("must not be called when disconnected")

    import app.dependencies as deps
    monkeypatch.setattr(deps, "get_atem_service_instance", lambda: _Atem())
    from app.easyworship.service import easyworship_service
    monkeypatch.setattr(type(easyworship_service), "live_state", property(lambda self: None))
    from app.audio.audio_observer import audio_observer
    monkeypatch.setattr(audio_observer, "speaking_roles", {})

    sync = ContextSync()
    synced = await sync.initial_sync()
    assert synced == {"atem": False, "easyworship": False, "audio": False, "cue": False}
    assert fresh_context.current_atem_program is None
    assert fresh_context.state == ServiceState.PRE_SERVICE


async def test_start_runs_initial_sync_before_listening(fresh_context, monkeypatch):
    sync = ContextSync()
    calls = []

    async def fake_initial():
        calls.append("initial")
        return {"atem": False}

    monkeypatch.setattr(sync, "initial_sync", fake_initial)
    await sync.start()
    try:
        assert calls == ["initial"] and sync.initial == {"atem": False}
        await sync.start()  # idempotent
        assert calls == ["initial"]
    finally:
        await sync.stop()


def test_module_singleton_exists():
    assert isinstance(sync_mod.context_sync, ContextSync)
