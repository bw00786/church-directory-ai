"""Tests for the Claude-vision semantic tier (WO-VISION-1 FR-4)."""

import asyncio

from app.config import settings
from app.vision.semantic import SemanticVision


def _enabled(monkeypatch, sv):
    monkeypatch.setattr(settings, "vision_llm_enabled", True)
    monkeypatch.setattr(sv, "_encode", lambda frame: "b64")
    # A frame must be present for _encode to be reached.
    from app.vision.frame_capture import frame_capture

    frame_capture.push_frame("program", object())


def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "vision_llm_enabled", False)
    sv = SemanticVision()
    out = asyncio.run(sv.ask("operator_query"))
    assert out["available"] is False and out["reason"] == "disabled"


def test_trigger_gating(monkeypatch):
    sv = SemanticVision()
    _enabled(monkeypatch, sv)
    out = asyncio.run(sv.ask("not_a_real_trigger"))
    assert out["available"] is False and "trigger not allowed" in out["reason"]


def test_rate_limiting(monkeypatch):
    monkeypatch.setattr(settings, "vision_llm_max_per_min", 2)
    sv = SemanticVision()
    _enabled(monkeypatch, sv)

    async def fake_invoke(image_b64, question):
        return '{"scene_description": "ok"}'
    monkeypatch.setattr(sv, "_invoke", fake_invoke)

    assert asyncio.run(sv.ask("operator_query"))["available"] is True
    assert asyncio.run(sv.ask("operator_query"))["available"] is True
    third = asyncio.run(sv.ask("operator_query"))
    assert third["available"] is False and third["reason"] == "rate_limited"


def test_typed_fields_and_no_actions(monkeypatch):
    sv = SemanticVision()
    _enabled(monkeypatch, sv)

    async def fake_invoke(image_b64, question):
        # Model tries to sneak in action-shaped keys; they must be dropped.
        return (
            '{"someone_at_pulpit": true, "congregation_standing": false, '
            '"people_count_estimate": 3, "scene_description": "pastor speaking", '
            '"action": "ATEM_CUT", "type": "PTZ_SELECT_ROLE", "camera": "pastor"}'
        )
    monkeypatch.setattr(sv, "_invoke", fake_invoke)

    out = asyncio.run(sv.ask("ptz_verify_failed"))
    assert out["available"] is True
    fields = out["fields"]
    assert fields["someone_at_pulpit"] is True
    assert fields["people_count_estimate"] == 3
    # No action-shaped keys ever survive parsing.
    for forbidden in ("action", "type", "camera"):
        assert forbidden not in fields
