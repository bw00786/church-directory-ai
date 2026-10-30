"""Post-service review aggregation over the cycle log and the review event log."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.director import cycle_log
from app.director import review as mod
from app.director.review import ReviewRecorder, aggregate, available_dates, local_date, review_for

DAY = date(2026, 10, 25)
_BASE = datetime(2026, 10, 25, 15, 0, 0, tzinfo=timezone.utc)


def _ts(minutes: float) -> str:
    return (_BASE + timedelta(minutes=minutes)).isoformat()


@pytest.fixture(autouse=True)
def logs_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cycle_log, "_LOG_PATH", tmp_path / "cycles.jsonl")
    monkeypatch.setitem(settings.__dict__, "review_events_file", str(tmp_path / "review_events.jsonl"))
    yield tmp_path


def _cycle(minutes, decision="continue", confidence=0.4, latency=500, stale=False, mode="assisted", phase_accepted=False, reason="r"):
    return {
        "ts": _ts(minutes), "cycle_id": f"c{minutes}", "kind": "cycle", "latency_ms": latency, "stale": stale, "mode": mode,
        "decision": {"decision": decision, "confidence": confidence, "reason": reason},
        "phase": {"accepted": phase_accepted},
    }


def _action(minutes, status, detail=None, type_="PTZ_SELECT_ROLE", target="pastor"):
    return {"ts": _ts(minutes), "cycle_id": f"c{minutes}", "kind": "action", "action": {"type": type_, "target": target}, "status": status, "detail": detail}


def _event(minutes, event, **payload):
    return {"ts": _ts(minutes), "event": event, "payload": payload}


RECORDS = [
    _cycle(0, latency=400),
    _cycle(1, "transition", 0.9, latency=800, stale=True, reason="sermon began"),
    _cycle(2, "transition", 0.92, latency=1200, phase_accepted=True, mode="ai_directed"),
    {"ts": _ts(2.5), "cycle_id": "m1", "kind": "model", "error": "TimeoutError"},
    _action(1, "pending_approval"),
    _action(1.2, "executed_after_approval", "camera -> pastor (ok)"),
    _action(2, "executed", "cut", type_="ATEM_CUT", target=None),
    _action(2.1, "held", "min hold: last program change 3.0s ago (< 8s)"),
    _action(2.2, "held", "min hold: last program change 5.0s ago (< 8s)"),
    _action(2.3, "held", "cooldown: camera_change 2.0s ago"),
    _action(3, "rejected", "adaptive 'camera_change' threshold 0.90"),
    _action(3.1, "rejected_by_operator"),
    _action(4, "dropped", "expired"),
    _action(4.1, "dropped", "superseded by a newer proposal"),
]
EVENTS = [
    _event(0, "AI_DIRECTOR_MODE_CHANGED", mode="assisted"),
    _event(0.5, "PROGRAM_CHANGED", program_input=2, previous_input=1, commanded=False),
    _event(1.5, "PROGRAM_CHANGED", program_input=1, previous_input=2, commanded=True),
    _event(1.6, "AI_DIRECTOR_PAUSED", mode="manual", paused_from="assisted"),
    _event(1.7, "AI_DIRECTOR_RESUMED", mode="assisted"),
    _event(2.4, "PERCEPTION_DEGRADED", component="vad", reason="silero unavailable"),
    _event(2.6, "PERCEPTION_RESTORED", source="usb"),
]


def test_aggregate_totals():
    body = aggregate(DAY, RECORDS, EVENTS, include_operator=False)
    totals = body["totals"]
    assert body["date"] == "2026-10-25" and body["has_data"] is True
    assert totals["cycles"] == 3 and totals["stale_cycles"] == 1 and totals["model_errors"] == 1
    assert totals["decisions"] == {"continue": 1, "transition": 2}
    assert totals["phase_changes_accepted"] == 1
    assert totals["latency_ms"] == {"avg": 800, "p50": 800, "p95": 1200, "max": 1200}
    proposals = totals["proposals"]
    assert proposals["total"] == 10
    assert proposals["executed"] == 2 and proposals["approved"] == 1 and proposals["pending"] == 1
    assert proposals["rejected"] == 2 and proposals["rejected_by_operator"] == 1
    assert proposals["held"] == 3 and proposals["expired"] == 1 and proposals["dropped"] == 1
    assert totals["top_held_reasons"] == [{"reason": "min hold", "count": 2}, {"reason": "cooldown", "count": 1}]
    assert totals["executed_by_type"] == {"PTZ_SELECT_ROLE -> pastor": 1, "ATEM_CUT": 1}
    assert totals["program_changes"] == {"total": 2, "app": 1, "operator": 1}
    assert totals["mode_changes"] == [{"ts": _ts(0), "mode": "assisted"}]
    assert totals["pauses"] == 1
    assert totals["degraded_events"] == {"total": 1, "by_component": {"vad": 1}}
    assert totals["modes_seen"] == {"assisted": 2, "ai_directed": 1}
    assert totals["rehearsals"] == 0


def test_timeline_is_sorted_and_compact():
    body = aggregate(DAY, RECORDS, EVENTS, include_operator=False)
    timeline = body["timeline"]
    assert timeline["tail"] == [] and timeline["total"] == len(timeline["head"])
    stamps = [t["ts"] for t in timeline["head"]]
    assert stamps == sorted(stamps)
    kinds = {t["kind"] for t in timeline["head"]}
    assert kinds == {"decision", "action", "model_error", "event"}
    paused = next(t for t in timeline["head"] if t["status"] == "ai_director_paused")
    assert paused["summary"] == "paused (from assisted)"
    program = [t for t in timeline["head"] if t["summary"].startswith("program ->")]
    assert program[0]["summary"] == "program -> 2 (operator)" and program[1]["summary"] == "program -> 1 (app)"
    decision = next(t for t in timeline["head"] if t["kind"] == "decision")
    assert decision["summary"].startswith("transition (90%): sermon began") and decision["status"] == "stale"


def test_timeline_truncates_to_first_and_last_200():
    records = [_cycle(i / 10, "transition", 0.9) for i in range(500)]
    body = aggregate(DAY, records, [], include_operator=False)
    timeline = body["timeline"]
    assert timeline["total"] == 500 and len(timeline["head"]) == 200 and len(timeline["tail"]) == 200
    assert timeline["head"][0]["cycle_id"] == "c0.0" and timeline["tail"][-1]["cycle_id"] == "c49.9"


def test_rehearsal_windows_exclude_cycle_records():
    events = [_event(0.9, "REHEARSAL_STARTED", name="default"), _event(2.05, "REHEARSAL_STOPPED", name="default")]
    body = aggregate(DAY, RECORDS, events, include_operator=False)
    totals = body["totals"]
    assert totals["rehearsals"] == 1
    # cycles at 1 and 2 minutes plus actions at 1, 1.2, 2 fall inside the window.
    assert totals["rehearsal_records_excluded"] == 5
    assert totals["cycles"] == 1 and totals["proposals"]["total"] == 7


def test_review_for_reads_only_that_local_day(logs_in_tmp):
    other_day = {**_cycle(0), "ts": (_BASE + timedelta(days=1)).isoformat()}
    cycle_log._LOG_PATH.write_text("\n".join(json.dumps(r) for r in RECORDS + [other_day]) + "\nnot json\n", encoding="utf-8")
    mod._events_path().write_text("\n".join(json.dumps(e) for e in EVENTS) + "\n", encoding="utf-8")
    body = review_for(DAY)
    assert body["totals"]["cycles"] == 3
    assert "operator_advances" in body and "operator_shots" in body
    assert available_dates() == sorted({local_date(r["ts"]).isoformat() for r in RECORDS + [other_day]}, reverse=True)


def test_empty_day():
    body = review_for(date(2001, 1, 1))
    assert body["has_data"] is False and body["totals"]["cycles"] == 0 and body["timeline"]["total"] == 0
    assert available_dates() == []


def test_operator_shots_is_optional(monkeypatch):
    import sys
    import types

    assert mod._operator_shots(DAY) is None or isinstance(mod._operator_shots(DAY), dict)
    fake = types.ModuleType("app.director.operator_shots")
    fake.operator_shot_history = types.SimpleNamespace(summary=lambda **kw: {"shots": 3, "date": kw.get("date")})
    monkeypatch.setitem(sys.modules, "app.director.operator_shots", fake)
    assert mod._operator_shots(DAY) == {"shots": 3, "date": "2026-10-25"}


async def test_recorder_writes_selected_events(logs_in_tmp):
    from app.events.bus import event_bus

    recorder = ReviewRecorder()
    recorder.ensure_started()
    recorder.ensure_started()  # idempotent
    import asyncio

    await asyncio.sleep(0)
    event_bus.publish({"event": "PROGRAM_CHANGED", "payload": {"program_input": 2, "commanded": False, "huge": "x" * 1000}})
    event_bus.publish({"event": "TRANSCRIPT", "payload": {"text": "ignored"}})
    event_bus.publish({"event": "AI_DIRECTOR_PAUSED", "payload": {"mode": "manual", "paused_from": "assisted"}})
    for _ in range(5):
        await asyncio.sleep(0)
    await recorder.stop()
    lines = [json.loads(l) for l in mod._events_path().read_text(encoding="utf-8").splitlines()]
    assert [l["event"] for l in lines] == ["PROGRAM_CHANGED", "AI_DIRECTOR_PAUSED"]
    assert lines[0]["payload"] == {"program_input": 2, "commanded": False}
    assert "ts" in lines[0]


def test_review_routes(monkeypatch, logs_in_tmp):
    import app.api.director as director_api

    cycle_log._LOG_PATH.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n", encoding="utf-8")
    api = FastAPI()
    api.include_router(director_api.router)
    http = TestClient(api)
    assert http.get("/director/review/dates").json() == {"dates": [local_date(RECORDS[0]["ts"]).isoformat()]}
    body = http.get("/director/review", params={"date": "2026-10-25"}).json()
    assert body["totals"]["cycles"] == 3
    assert http.get("/director/review", params={"date": "yesterday"}).status_code == 422
    today = http.get("/director/review").json()
    assert today["date"] == date.today().isoformat()
