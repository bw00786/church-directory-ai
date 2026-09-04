"""Tests for the assistant tools (query + tiered control) and confirmation flow."""

import json

import pytest

from app.agents import assistant_tools


def _parse(tool_result: str) -> dict:
    return json.loads(tool_result)


def test_list_past_services_returns_json(monkeypatch):
    monkeypatch.setattr(
        assistant_tools.memory_manager, "list_services", lambda limit=20: [{"service_date": "2026-08-16", "observation_count": 3}]
    )
    result = _parse(assistant_tools.list_past_services.invoke({"limit": 20}))
    assert result == [{"service_date": "2026-08-16", "observation_count": 3}]


def test_who_preached_found(monkeypatch):
    monkeypatch.setattr(
        assistant_tools.identity_service,
        "who_was_seen",
        lambda role, service_date: {"person_name": "Pastor Jim", "role": role, "confidence": 0.9, "sighting_count": 4},
    )
    result = _parse(assistant_tools.who_preached.invoke({"service_date": "2026-08-16"}))
    assert result["found"] is True
    assert result["person_name"] == "Pastor Jim"


def test_who_preached_not_found(monkeypatch):
    monkeypatch.setattr(assistant_tools.identity_service, "who_was_seen", lambda role, service_date: None)
    result = _parse(assistant_tools.who_preached.invoke({"service_date": "2026-08-16"}))
    assert result["found"] is False


def test_high_risk_tool_registers_pending_not_executed(monkeypatch):
    """Calling request_start_streaming must NOT touch the real ATEM service."""
    calls = []
    monkeypatch.setattr(
        "app.agents.assistant_tools.get_atem_service_instance",
        lambda: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    result = _parse(assistant_tools.request_start_streaming.invoke({}))
    assert "pending_confirmation" in result
    token = result["pending_confirmation"]
    assert token in assistant_tools.pending_actions
    assert calls == []


@pytest.mark.asyncio
async def test_execute_pending_runs_the_real_action(monkeypatch):
    class FakeAtem:
        async def start_stream(self):
            return True

    monkeypatch.setattr(assistant_tools, "get_atem_service_instance", lambda: FakeAtem())

    result = _parse(assistant_tools.request_start_streaming.invoke({}))
    token = result["pending_confirmation"]

    outcome = await assistant_tools.execute_pending(token)
    assert outcome["ok"] is True
    # Token is single-use.
    assert token not in assistant_tools.pending_actions


@pytest.mark.asyncio
async def test_execute_pending_unknown_token():
    outcome = await assistant_tools.execute_pending("does-not-exist")
    assert outcome["ok"] is False
    assert "Unknown" in outcome["error"]


def test_discard_pending():
    result = _parse(assistant_tools.request_stop_recording.invoke({}))
    token = result["pending_confirmation"]
    assert assistant_tools.discard_pending(token) is True
    assert assistant_tools.discard_pending(token) is False


# -- PTZ camera tools ---------------------------------------------------------


@pytest.mark.asyncio
async def test_camera_move_to_role_uses_configured_role(monkeypatch):
    calls = []

    async def fake_move_to_role(role):
        calls.append(role)
        return True

    monkeypatch.setattr(assistant_tools.camera_service, "move_to_role", fake_move_to_role)
    result = _parse(await assistant_tools.camera_move_to_role.ainvoke({"role": "Pastor"}))
    assert result == {"ok": True, "role": "pastor"}
    assert calls == ["pastor"]


@pytest.mark.asyncio
async def test_camera_move_to_role_rejects_unknown_role():
    result = _parse(await assistant_tools.camera_move_to_role.ainvoke({"role": "drummer"}))
    assert result["ok"] is False
    assert "unknown role" in result["error"]


@pytest.mark.asyncio
async def test_camera_nudge_drives_then_stops(monkeypatch):
    events = []

    async def fake_drive(camera_id, **kwargs):
        events.append(("drive", camera_id, kwargs["pan_dir"], kwargs["tilt_dir"], kwargs["zoom_dir"]))
        return True

    async def fake_stop(camera_id):
        events.append(("stop", camera_id))
        return True

    monkeypatch.setattr(assistant_tools.camera_service, "drive_camera", fake_drive)
    monkeypatch.setattr(assistant_tools.camera_service, "stop_camera", fake_stop)
    result = _parse(
        await assistant_tools.camera_nudge.ainvoke({"direction": "zoom_in", "camera_id": 1, "seconds": 0.01})
    )
    assert result["ok"] is True
    assert events == [("drive", 1, 0, 0, 1), ("stop", 1)]


@pytest.mark.asyncio
async def test_camera_nudge_clamps_duration(monkeypatch):
    async def fake_drive(camera_id, **kwargs):
        return True

    async def fake_stop(camera_id):
        return True

    monkeypatch.setattr(assistant_tools.camera_service, "drive_camera", fake_drive)
    monkeypatch.setattr(assistant_tools.camera_service, "stop_camera", fake_stop)
    monkeypatch.setattr(assistant_tools, "MAX_NUDGE_SECONDS", 0.1)
    result = _parse(await assistant_tools.camera_nudge.ainvoke({"direction": "left", "seconds": 999}))
    assert result["seconds"] == 0.1


@pytest.mark.asyncio
async def test_camera_move_absolute_requires_a_value():
    result = _parse(await assistant_tools.camera_move_absolute.ainvoke({"camera_id": 1}))
    assert result["ok"] is False


def test_request_camera_save_preset_is_confirmation_gated(monkeypatch):
    called = []

    async def fake_save(camera_id, preset_id):
        called.append((camera_id, preset_id))
        return True

    monkeypatch.setattr(assistant_tools.camera_service, "save_preset", fake_save)
    result = _parse(assistant_tools.request_camera_save_preset.invoke({"camera_id": 1, "preset_id": 1}))
    assert "pending_confirmation" in result
    assert "pastor" in result["description"]  # preset 1 is the pastor role by default
    assert called == []


@pytest.mark.asyncio
async def test_execute_pending_saves_preset(monkeypatch):
    called = []

    async def fake_save(camera_id, preset_id):
        called.append((camera_id, preset_id))
        return True

    monkeypatch.setattr(assistant_tools.camera_service, "save_preset", fake_save)
    token = _parse(assistant_tools.request_camera_save_preset.invoke({"camera_id": 1, "preset_id": 9}))[
        "pending_confirmation"
    ]
    outcome = await assistant_tools.execute_pending(token)
    assert outcome == {"ok": True}
    assert called == [(1, 9)]


def test_list_camera_roles_reports_config():
    result = _parse(assistant_tools.list_camera_roles.invoke({}))
    assert set(result) == set(assistant_tools.CAMERA_ROLES)
    assert result["pastor"]["camera_id"] == 1


# -- ATEM tools ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_atem_show_source_slides_cut(monkeypatch):
    class State:
        program_input = 2
        preview_input = 1

    class FakeAtem:
        def __init__(self):
            self.calls = []

        async def set_program(self, input_id):
            self.calls.append(("program", input_id))
            return State()

        async def set_preview(self, input_id):
            self.calls.append(("preview", input_id))
            return State()

        async def auto(self):
            self.calls.append(("auto",))
            return State()

    atem = FakeAtem()
    monkeypatch.setattr(assistant_tools, "get_atem_service_instance", lambda: atem)
    result = _parse(await assistant_tools.atem_show_source.ainvoke({"source": "slides"}))
    assert result["ok"] is True and result["program_input"] == 2
    assert atem.calls == [("program", 2)]

    result = _parse(await assistant_tools.atem_show_source.ainvoke({"source": "camera", "transition": "auto"}))
    assert result["ok"] is True
    assert atem.calls[-2:] == [("preview", 1), ("auto",)]


@pytest.mark.asyncio
async def test_atem_show_source_unknown():
    result = _parse(await assistant_tools.atem_show_source.ainvoke({"source": "drone"}))
    assert result["ok"] is False


# -- EasyWorship tools --------------------------------------------------------


@pytest.mark.asyncio
async def test_easyworship_select_item(monkeypatch):
    calls = []

    async def fake_select(label):
        calls.append(label)
        return True

    monkeypatch.setattr(assistant_tools.easyworship_service, "select_item", fake_select)
    result = _parse(await assistant_tools.easyworship_select_item.ainvoke({"label": "Sermon"}))
    assert result["ok"] is True and result["item"] == "Sermon"
    assert calls == ["Sermon"]
    assert "status" in result


@pytest.mark.asyncio
async def test_easyworship_slide_action_rejects_unknown():
    result = _parse(await assistant_tools.easyworship_slide_action.ainvoke({"action": "explode"}))
    assert result["ok"] is False


def test_list_easyworship_items_matches_plan():
    result = _parse(assistant_tools.list_easyworship_items.invoke({}))
    assert "Sermon" in result["items"]


# -- Mixer (software-DSP layer via mgx-ai-mixer) -------------------------------


def test_get_mixer_status_reports_roles_analysis_and_dsp(monkeypatch):
    monkeypatch.setattr(assistant_tools.mixer_service, "_levels", {1: -20.0, 2: -80.0, 4: -30.0, 8: -85.0})
    result = _parse(assistant_tools.get_mixer_status.invoke({}))
    assert "console-only" in result["control"]
    assert result["channels"]["pastor"]["channel"] == 1
    assert result["channels"]["pastor"]["active"] is True
    assert result["channels"]["liturgist"]["active"] is False
    assert "analysis" in result and "dsp" in result
    assert "recent_transcript" in result


@pytest.mark.asyncio
async def test_mixer_set_hpf_resolves_role_to_channel(monkeypatch):
    calls = []

    async def fake_apply(channel, action, param="", amount="", reason=""):
        calls.append((channel, action, param))
        return {"applied": True, "detail": f"HPF on @ {param}"}

    monkeypatch.setattr(assistant_tools.mixer_service, "apply_move", fake_apply)
    result = _parse(await assistant_tools.mixer_set_hpf.ainvoke({"channel": "vocalist", "frequency_hz": 140}))
    assert result["applied"] is True and result["channel"] == 4
    assert calls == [(4, "hpf", "140 Hz")]


@pytest.mark.asyncio
async def test_mixer_set_hpf_rejects_bad_input():
    assert _parse(await assistant_tools.mixer_set_hpf.ainvoke({"channel": "drummer"}))["ok"] is False
    assert _parse(await assistant_tools.mixer_set_hpf.ainvoke({"channel": "1", "frequency_hz": 5000}))["ok"] is False


@pytest.mark.asyncio
async def test_mixer_eq_cut_vs_boost(monkeypatch):
    calls = []

    async def fake_apply(channel, action, param="", amount="", reason=""):
        calls.append((channel, action, param, amount))
        return {"applied": True}

    monkeypatch.setattr(assistant_tools.mixer_service, "apply_move", fake_apply)
    await assistant_tools.mixer_eq.ainvoke({"channel": "pastor", "frequency_hz": 300, "gain_db": -3})
    await assistant_tools.mixer_eq.ainvoke({"channel": "2", "frequency_hz": 4000, "gain_db": 2})
    assert calls == [(1, "eq_cut", "300 Hz", "-3.0 dB"), (2, "eq_boost", "4000 Hz", "+2.0 dB")]


@pytest.mark.asyncio
async def test_mixer_kill_feedback_sends_channel_command(monkeypatch):
    sent = []

    async def fake_command(text):
        sent.append(text)
        return {"understood": text, "applied": [{"notch": "2.4k Hz"}]}

    monkeypatch.setattr(assistant_tools.mixer_service, "command", fake_command)
    result = _parse(await assistant_tools.mixer_kill_feedback.ainvoke({"channel": "liturgist"}))
    assert sent == ["channel 2 has feedback, fix it"]
    assert result["applied"]


@pytest.mark.asyncio
async def test_mixer_guards_in_mock_mode(monkeypatch):
    monkeypatch.setattr(assistant_tools.mixer_service, "mock", True)
    guard = _parse(await assistant_tools.mixer_set_feedback_guard.ainvoke({"enabled": True, "excluded_channels": [4]}))
    assert guard["enabled"] is True and guard["excluded"] == [4]
    keeper = _parse(await assistant_tools.mixer_set_mix_keeper.ainvoke({"enabled": True}))
    assert keeper["enabled"] is True


def test_request_mixer_engage_dsp_is_confirmation_gated(monkeypatch):
    called = []

    async def fake_engage(engage):
        called.append(engage)
        return {"engaged": engage}

    monkeypatch.setattr(assistant_tools.mixer_service, "engage_dsp", fake_engage)
    result = _parse(assistant_tools.request_mixer_engage_dsp.invoke({"engage": True}))
    assert "pending_confirmation" in result and "ENGAGE" in result["description"]
    assert called == []


@pytest.mark.asyncio
async def test_execute_pending_engages_mixer_dsp(monkeypatch):
    called = []

    async def fake_engage(engage):
        called.append(engage)
        return {"engaged": engage}

    monkeypatch.setattr(assistant_tools.mixer_service, "engage_dsp", fake_engage)
    token = _parse(assistant_tools.request_mixer_engage_dsp.invoke({"engage": True}))["pending_confirmation"]
    outcome = await assistant_tools.execute_pending(token)
    assert outcome["ok"] is True and called == [True]


def test_all_new_tools_registered():
    names = {t.name for t in assistant_tools.ALL_TOOLS}
    for expected in (
        "camera_move_to_role",
        "camera_move_absolute",
        "camera_nudge",
        "camera_stop",
        "get_camera_state",
        "list_camera_roles",
        "atem_set_preview",
        "atem_show_source",
        "easyworship_select_item",
        "easyworship_goto_slide",
        "get_easyworship_status",
        "list_easyworship_items",
        "get_mixer_status",
        "mixer_command",
        "mixer_set_hpf",
        "mixer_eq",
        "mixer_compressor",
        "mixer_trim",
        "mixer_kill_feedback",
        "mixer_set_feedback_guard",
        "mixer_set_mix_keeper",
        "mixer_analyze_and_advise",
        "mixer_reset_dsp",
        "request_camera_save_preset",
        "request_mixer_engage_dsp",
    ):
        assert expected in names
