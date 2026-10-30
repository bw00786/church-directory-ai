"""Cue engine: PTZ moves are staged off air (preset -> settle -> preview -> take).

Covers the pure planner (:func:`app.director.engine.plan_cue`) and the
ServiceDirector's execution of a plan against fake ATEM/camera services.
"""

import pytest

import app.director.engine as engine_mod
from app.config import settings
from app.director.engine import PlanStep, ServiceDirector, plan_cue
from app.director.models import ActionType, AdvanceTrigger, Cue, CueAction, ServiceScript

CAM1, CAM2 = 1, 2  # ATEM inputs: PTZ camera, slides
CAMERA_INPUTS = {1: CAM1}


def _atem(input_id, transition=None):
    return CueAction(type=ActionType.ATEM_PROGRAM, atem_input=input_id, transition=transition, description=f"atem {input_id}")


def _preset(preset_id, role=None, camera_id=1):
    return CueAction(type=ActionType.PTZ_PRESET, camera_id=camera_id, preset_id=preset_id, role=role, description=f"preset {preset_id}")


def _slide(op="next_item"):
    return CueAction(type=ActionType.SLIDE, slide_op=op, description=op)


def _ops(plan):
    return [s.op for s in plan]


# -- planner ------------------------------------------------------------------
def test_planner_stages_move_off_air_before_switch():
    plan = plan_cue([_atem(CAM1), _preset(3, "pastor")], current_program=CAM2, camera_inputs=CAMERA_INPUTS)
    assert _ops(plan) == ["action", "settle", "preview", "take"]
    assert plan[0].action.type == ActionType.PTZ_PRESET
    assert plan[1].camera_id == 1
    assert plan[2].atem_input == CAM1
    assert plan[3].atem_input == CAM1 and plan[3].transition == "cut"


def test_planner_order_independent_of_action_order():
    a = plan_cue([_atem(CAM1), _preset(3)], CAM2, CAMERA_INPUTS)
    b = plan_cue([_preset(3), _atem(CAM1)], CAM2, CAMERA_INPUTS)
    assert _ops(a) == _ops(b) == ["action", "settle", "preview", "take"]


def test_planner_unknown_program_is_staged():
    plan = plan_cue([_atem(CAM1), _preset(3)], None, CAMERA_INPUTS)
    assert _ops(plan) == ["action", "settle", "preview", "take"]


def test_planner_already_on_program_just_moves():
    plan = plan_cue([_atem(CAM1), _preset(3)], current_program=CAM1, camera_inputs=CAMERA_INPUTS)
    assert _ops(plan) == ["action"]
    assert plan[0].action.type == ActionType.PTZ_PRESET
    assert "already on program" in plan[0].reason


def test_planner_cover_with_hides_live_move():
    plan = plan_cue([_atem(CAM1), _preset(3)], CAM1, CAMERA_INPUTS, cover_with=CAM2)
    assert _ops(plan) == ["program", "action", "settle", "preview", "take"]
    assert plan[0].atem_input == CAM2  # cover on slides first
    assert plan[-1].atem_input == CAM1 and plan[-1].transition == "cut"


def test_planner_cover_with_same_input_is_ignored():
    plan = plan_cue([_atem(CAM1), _preset(3)], CAM1, CAMERA_INPUTS, cover_with=CAM1)
    assert _ops(plan) == ["action"]


def test_planner_ptz_only_cue_covered_when_live():
    plan = plan_cue([_preset(6)], CAM1, CAMERA_INPUTS, cover_with=CAM2)
    assert _ops(plan) == ["program", "action", "settle", "preview", "take"]
    assert plan[0].atem_input == CAM2 and plan[-1].atem_input == CAM1


def test_planner_ptz_only_cue_without_cover_just_moves():
    assert _ops(plan_cue([_preset(6)], CAM1, CAMERA_INPUTS)) == ["action"]
    assert _ops(plan_cue([_preset(6)], CAM2, CAMERA_INPUTS)) == ["action"]


def test_planner_transition_auto_vs_cut():
    cut = plan_cue([_atem(CAM1), _preset(3)], CAM2, CAMERA_INPUTS, transition="cut")
    auto = plan_cue([_atem(CAM1), _preset(3)], CAM2, CAMERA_INPUTS, transition="auto")
    assert cut[-1].op == "take" and cut[-1].transition == "cut"
    assert auto[-1].op == "take" and auto[-1].transition == "auto"


def test_planner_action_transition_overrides_cue_default():
    plan = plan_cue([_atem(CAM1, transition="auto"), _preset(3)], CAM2, CAMERA_INPUTS, transition="cut")
    assert plan[-1].transition == "auto"


def test_planner_plain_switch_cut_is_direct_program():
    plan = plan_cue([_atem(CAM2), _slide()], CAM1, CAMERA_INPUTS)
    assert _ops(plan) == ["program", "action"]
    assert plan[0].atem_input == CAM2
    assert plan[1].action.type == ActionType.SLIDE


def test_planner_plain_switch_auto_goes_via_preview():
    plan = plan_cue([_atem(CAM2)], CAM1, CAMERA_INPUTS, transition="auto")
    assert _ops(plan) == ["preview", "take"]
    assert plan[1].transition == "auto"


def test_planner_plain_switch_to_live_input_is_skipped():
    plan = plan_cue([_atem(CAM2), _slide()], CAM2, CAMERA_INPUTS)
    assert _ops(plan) == ["skip", "action"]


def test_planner_keeps_other_actions_in_order():
    note = CueAction(type=ActionType.NOTE, note="open mic", description="open mic")
    plan = plan_cue([_slide(), _atem(CAM1), note, _preset(3)], CAM2, CAMERA_INPUTS)
    assert _ops(plan) == ["action", "action", "settle", "preview", "take", "action"]
    assert plan[0].action.type == ActionType.SLIDE
    assert plan[-1].action.type == ActionType.NOTE


def test_planner_camera_without_input_mapping_moves_as_written():
    # Unknown camera -> input mapping: cannot stage, falls back to the written order.
    plan = plan_cue([_atem(CAM1), _preset(3, camera_id=7)], CAM2, CAMERA_INPUTS)
    assert _ops(plan) == ["program", "action"]


# -- engine execution ---------------------------------------------------------
class _FakeAtem:
    def __init__(self, program=CAM2):
        self.calls = []
        self.program = program
        self.preview = None

    async def get_state(self):
        class _S:
            pass

        s = _S()
        s.program_input = self.program
        return s

    async def set_program(self, input_id):
        self.calls.append(("set_program", input_id))
        self.program = input_id

    async def set_preview(self, input_id):
        self.calls.append(("set_preview", input_id))
        self.preview = input_id

    async def cut(self):
        self.calls.append(("cut", None))
        self.program, self.preview = self.preview, self.program

    async def auto(self):
        self.calls.append(("auto", None))
        self.program, self.preview = self.preview, self.program


class _FakeCameras:
    def __init__(self, log):
        self.log = log

    async def move_to_preset(self, camera_id, preset_id):
        self.log.append(("move_to_preset", camera_id, preset_id))
        return True


class _FakeCamerasWithWait(_FakeCameras):
    async def wait_for_move(self, camera_id):
        self.log.append(("wait_for_move", camera_id))


@pytest.fixture(autouse=True)
def _restore_service_context():
    """ServiceDirector.start() rewrites the global service_context.plan; put it back."""
    ctx = engine_mod.service_context
    saved = {k: getattr(ctx, k) for k in ("plan", "current_atem_program", "current_camera_role", "current_easyworship_item")
             if hasattr(ctx, k)}
    yield
    for k, v in saved.items():
        setattr(ctx, k, v)


@pytest.fixture
def wiring(monkeypatch):
    log = []
    atem = _FakeAtem()
    cameras = _FakeCameras(log)
    # Share one call log across ATEM and camera so ordering is observable.
    for name in ("set_program", "set_preview", "cut", "auto"):
        orig = getattr(atem, name)

        async def wrapped(*args, _orig=orig, _name=name):
            await _orig(*args)
            log.append((_name,) + tuple(args))

        setattr(atem, name, wrapped)

    monkeypatch.setattr(engine_mod, "get_atem_service_instance", lambda: atem)
    monkeypatch.setattr(engine_mod, "camera_service", cameras)
    monkeypatch.setattr(engine_mod.identity_service, "best_preset_for_role", lambda role, camera_id: None)
    monkeypatch.setattr(settings, "ptz_verify_delay_ms", 0, raising=False)
    monkeypatch.setattr(settings, "ptz_camera_id", 1)
    monkeypatch.setattr(settings, "atem_camera1_input", CAM1)
    monkeypatch.setattr(settings, "atem_camera2_input", CAM2)

    settled = []
    orig_sleep = engine_mod.asyncio.sleep

    async def sleep(seconds):
        settled.append(seconds)
        await orig_sleep(0)

    monkeypatch.setattr(engine_mod.asyncio, "sleep", sleep)
    return atem, cameras, log, settled


def _director(*cues, transition="cut", cover_with=None):
    built = [
        Cue(id=f"c{i}", name=f"c{i}", actions=list(actions), advance=AdvanceTrigger.MANUAL, transition=transition, cover_with=cover_with)
        for i, actions in enumerate(cues)
    ]
    d = ServiceDirector(ServiceScript(name="t", cues=built))
    d._running = True
    return d


async def test_engine_executes_preset_settle_preview_cut(wiring):
    atem, cameras, log, settled = wiring
    atem.program = CAM2
    d = _director([_atem(CAM1), _preset(3, "pastor")])
    await d._enter_cue(0)
    assert log == [("move_to_preset", 1, 3), ("set_preview", CAM1), ("cut",)]
    assert settled == [0.0]  # settle delay honoured (ptz_verify_delay_ms)
    assert atem.program == CAM1


async def test_engine_uses_camera_move_completion_when_available(wiring, monkeypatch):
    atem, _, log, settled = wiring
    monkeypatch.setattr(engine_mod, "camera_service", _FakeCamerasWithWait(log))
    atem.program = CAM2
    d = _director([_atem(CAM1), _preset(3)])
    await d._enter_cue(0)
    assert log == [("move_to_preset", 1, 3), ("wait_for_move", 1), ("set_preview", CAM1), ("cut",)]
    assert settled == []


async def test_engine_auto_transition_uses_atem_auto(wiring):
    atem, _, log, _ = wiring
    atem.program = CAM2
    d = _director([_atem(CAM1), _preset(3)], transition="auto")
    await d._enter_cue(0)
    assert log[-2:] == [("set_preview", CAM1), ("auto",)]


async def test_engine_already_on_program_moves_without_switching(wiring):
    atem, _, log, _ = wiring
    atem.program = CAM1
    d = _director([_atem(CAM1), _preset(3)])
    await d._enter_cue(0)
    assert log == [("move_to_preset", 1, 3)]


async def test_engine_cover_with_switches_away_first(wiring):
    atem, _, log, _ = wiring
    atem.program = CAM1
    d = _director([_atem(CAM1), _preset(3)], cover_with=CAM2)
    await d._enter_cue(0)
    assert log == [("set_program", CAM2), ("move_to_preset", 1, 3), ("set_preview", CAM1), ("cut",)]
    assert atem.program == CAM1


async def test_engine_learned_role_preset_overrides_configured(wiring, monkeypatch):
    atem, _, log, _ = wiring
    atem.program = CAM2
    monkeypatch.setattr(engine_mod.identity_service, "best_preset_for_role", lambda role, camera_id: 9 if role == "pastor" else None)
    d = _director([_atem(CAM1), _preset(3, "pastor")])
    await d._enter_cue(0)
    assert log[0] == ("move_to_preset", 1, 9)


async def test_engine_falls_back_to_context_when_atem_state_unavailable(wiring, monkeypatch):
    atem, _, log, _ = wiring

    async def broken():
        raise ConnectionError("ATEM not connected")

    monkeypatch.setattr(atem, "get_state", broken)
    monkeypatch.setattr(engine_mod.service_context, "current_atem_program", CAM1)
    d = _director([_atem(CAM1), _preset(3)])
    await d._enter_cue(0)
    assert log == [("move_to_preset", 1, 3)]  # treated as already on program


async def test_engine_default_sheet_pastor_cue_is_staged(wiring):
    """The real announcements cue (ATEM cam1 + pastor preset) never pans on air."""
    atem, _, log, _ = wiring
    atem.program = CAM2
    d = ServiceDirector()
    d._running = True
    index = next(i for i, c in enumerate(d.script.cues) if c.id == "announcements")
    await d._enter_cue(index)
    assert [c[0] for c in log] == ["move_to_preset", "set_preview", "cut"]
    assert log[0][2] == settings.camera_role_pastor_preset


async def test_engine_abandons_plan_when_superseded_during_settle(wiring, monkeypatch):
    """A manual Next during the settle wait must not take the old cue's shot."""
    atem, _, log, _ = wiring
    atem.program = CAM2
    d = _director([_atem(CAM1), _preset(3)], [_atem(CAM2)])

    async def sleep_then_advance(seconds):
        d._token += 1  # simulates next()/goto() arriving mid-settle

    monkeypatch.setattr(engine_mod.asyncio, "sleep", sleep_then_advance)
    await d._enter_cue(0)
    assert log == [("move_to_preset", 1, 3)]  # no preview, no cut


def test_plan_step_is_frozen():
    step = PlanStep("settle")
    with pytest.raises(Exception):
        step.op = "take"  # type: ignore[misc]


# -- context sync hooks -------------------------------------------------------
async def test_enter_cue_publishes_cue_entered_event_with_plan_entry(wiring):
    atem, _, _, _ = wiring
    atem.program = CAM2
    d = ServiceDirector()
    d._running = True
    queue = await engine_mod.event_bus.subscribe()
    try:
        index = next(i for i, c in enumerate(d.script.cues) if c.id == "sermon")
        await d._enter_cue(index)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        await engine_mod.event_bus.unsubscribe(queue)
    entered = [e for e in events if isinstance(e, dict) and e.get("event") == engine_mod.EVENT_CUE_ENTERED]
    assert len(entered) == 1
    payload = entered[0]["payload"]
    assert payload["cue_id"] == "sermon" and payload["index"] == index
    assert payload["total"] == len(d.script.cues)
    assert payload["label"] == "Sermon — Pastor"
    assert payload["state"] == "sermon"
    assert payload["speaker_role"] == "pastor" and payload["camera_role"] == "pastor"
    assert payload["easyworship_item"] is None and payload["followable"] is False
    # The event is published before the cue's actions run (so the context is
    # right by the time the first cut lands).
    first_action = next(i for i, e in enumerate(events) if isinstance(e, dict) and e.get("type") == "director_action")
    assert events.index(entered[0]) < first_action


async def test_engine_program_changes_go_through_context_setters(wiring):
    atem, _, _, _ = wiring
    atem.program = CAM2
    ctx = engine_mod.service_context
    ctx.set_program(CAM2)
    version = ctx.version
    d = _director([_atem(CAM1), _preset(3, "pastor")])
    await d._enter_cue(0)
    assert ctx.current_atem_program == CAM1
    assert ctx.current_camera_role == "pastor"
    assert ctx.last_cut_at is not None
    assert ctx.version > version
