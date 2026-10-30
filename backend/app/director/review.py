"""Post-service review: one day's AI Director activity, aggregated.

Sources (all local files; nothing here touches hardware or the network):

- the decision-cycle audit log (``cycle_log``, ``data/ai_director_cycles.jsonl``):
  cycles (latency, staleness, decisions), action outcomes (executed, approved,
  rejected, held, dropped/expired) and model failures;
- the review event log (``settings.review_events_file``), written by
  :class:`ReviewRecorder` from the event bus: ATEM program changes (operator
  vs app), AI mode changes and pauses, perception degraded/restored, rehearsal
  windows;
- what was learned from the operator's own slide advances
  (``operator_learning.operator_advance_history.summary()``) and, when the
  module exists, operator camera shots (``app.director.operator_shots``).

Cycle-log records that fall inside a rehearsal window are excluded, so a
Saturday rehearsal never shows up as Sunday's service.

Dates are **local** calendar dates (timestamps in both logs are UTC ISO-8601).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import statistics
import threading
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from app.config import settings
from app.events.bus import event_bus
from app.logging_config import get_logger

from . import cycle_log

logger = get_logger(__name__)

TIMELINE_EDGE = 200

# Bus events worth keeping for the review (small, bounded payloads).
RECORDED_EVENTS: frozenset[str] = frozenset(
    {
        "PROGRAM_CHANGED",
        "AI_DIRECTOR_MODE_CHANGED",
        "AI_DIRECTOR_PAUSED",
        "AI_DIRECTOR_RESUMED",
        "PERCEPTION_DEGRADED",
        "PERCEPTION_RESTORED",
        "AI_DECISION_STALE",
        "REHEARSAL_STARTED",
        "REHEARSAL_STOPPED",
    }
)
_PAYLOAD_KEYS = (
    "program_input", "previous_input", "commanded", "source", "mode", "paused_from",
    "component", "reason", "channel", "name", "entries", "position", "decisions", "cycle_id", "drift",
)

EXECUTED_STATUSES = ("executed", "executed_after_approval")
REJECTED_STATUSES = ("rejected", "rejected_by_operator")


def _events_path() -> Path:
    return Path(getattr(settings, "review_events_file", "data/review_events.jsonl"))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def local_date(ts: Any) -> Optional[date]:
    parsed = parse_ts(ts)
    return parsed.astimezone().date() if parsed else None


def _iter_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _records_for(path: Path, day: date) -> List[dict]:
    return [r for r in _iter_jsonl(path) if local_date(r.get("ts")) == day]


# -- event recorder -----------------------------------------------------------------


class ReviewRecorder:
    """Appends selected bus events to the review log. Start once per process."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def ensure_started(self) -> None:
        """Start from any coroutine (route handlers, lifespan); idempotent."""
        if self.running:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self._listen())

    async def start(self) -> None:
        self.ensure_started()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _listen(self) -> None:
        queue = await event_bus.subscribe()
        try:
            while True:
                message = await queue.get()
                if isinstance(message, dict) and message.get("event") in RECORDED_EVENTS:
                    self.record(str(message["event"]), message.get("payload"))
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await event_bus.unsubscribe(queue)

    def record(self, event: str, payload: Any) -> None:
        compact: Dict[str, Any] = {}
        if isinstance(payload, dict):
            compact = {k: payload[k] for k in _PAYLOAD_KEYS if k in payload}
        line = {"ts": _utcnow_iso(), "event": event, "payload": compact}
        path = _events_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, default=str) + "\n")
        except OSError:
            logger.warning("review event log write failed", exc_info=True)


review_recorder = ReviewRecorder()


# -- aggregation ----------------------------------------------------------------------


def rehearsal_windows(events: Iterable[dict]) -> List[Tuple[datetime, Optional[datetime]]]:
    windows: List[Tuple[datetime, Optional[datetime]]] = []
    open_at: Optional[datetime] = None
    for ev in events:
        ts = parse_ts(ev.get("ts"))
        if ts is None:
            continue
        if ev.get("event") == "REHEARSAL_STARTED":
            open_at = ts
        elif ev.get("event") == "REHEARSAL_STOPPED" and open_at is not None:
            windows.append((open_at, ts))
            open_at = None
    if open_at is not None:
        windows.append((open_at, None))
    return windows


def _in_windows(ts: Optional[datetime], windows: List[Tuple[datetime, Optional[datetime]]]) -> bool:
    if ts is None:
        return False
    return any(start <= ts and (end is None or ts <= end) for start, end in windows)


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def _held_reason_key(detail: Any) -> str:
    text = str(detail or "").strip()
    return (text.split(":", 1)[0] if ":" in text else text).strip().lower() or "unspecified"


def _action_label(action: Any) -> str:
    if not isinstance(action, dict):
        return "action"
    label = str(action.get("type") or "action")
    if action.get("target"):
        label += f" -> {action['target']}"
    return label


def _event_summary(ev: dict) -> str:
    event, payload = str(ev.get("event")), ev.get("payload") or {}
    if event == "PROGRAM_CHANGED":
        who = "app" if payload.get("commanded") else "operator"
        return f"program -> {payload.get('program_input', payload.get('input', '?'))} ({who})"
    if event == "AI_DIRECTOR_MODE_CHANGED":
        return f"mode -> {payload.get('mode')}"
    if event == "AI_DIRECTOR_PAUSED":
        return f"paused (from {payload.get('paused_from')})"
    if event == "AI_DIRECTOR_RESUMED":
        return f"resumed -> {payload.get('mode')}"
    if event in ("PERCEPTION_DEGRADED", "PERCEPTION_RESTORED"):
        return f"{event.split('_')[1].lower()}: {payload.get('component') or payload.get('source') or payload.get('reason') or ''}".strip()
    if event.startswith("REHEARSAL_"):
        return f"rehearsal {event.split('_')[1].lower()}: {payload.get('name') or ''}".strip()
    return event.lower()


def _operator_advances() -> Optional[dict]:
    try:
        from app.director.operator_learning import operator_advance_history

        return operator_advance_history.summary(limit=20)
    except Exception:  # noqa: BLE001 - optional source
        logger.debug("operator advance summary unavailable", exc_info=True)
        return None


def _operator_shots(day: date) -> Optional[dict]:
    """Operator camera shots, when another module provides them (getattr-guarded)."""
    try:
        module = importlib.import_module("app.director.operator_shots")
    except ImportError:
        return None
    try:
        for name in ("operator_shot_history", "operator_shots", "operator_shot_recorder"):
            store = getattr(module, name, None)
            summary = getattr(store, "summary", None)
            if callable(summary):
                try:
                    return summary(date=day.isoformat())
                except TypeError:
                    return summary()
        summary = getattr(module, "summary", None)
        if callable(summary):
            return summary()
    except Exception:  # noqa: BLE001
        logger.debug("operator shots summary unavailable", exc_info=True)
    return None


def aggregate(day: date, cycle_records: List[dict], events: List[dict], *, include_operator: bool = True) -> dict:
    """Pure aggregation of one day's records (tested directly)."""
    events = sorted(events, key=lambda e: str(e.get("ts", "")))
    windows = rehearsal_windows(events)
    records = [r for r in cycle_records if not _in_windows(parse_ts(r.get("ts")), windows)]
    excluded = len(cycle_records) - len(records)

    cycles = [r for r in records if r.get("kind") == "cycle"]
    actions = [r for r in records if r.get("kind") == "action"]
    models = [r for r in records if r.get("kind") == "model"]

    latencies = [float(c["latency_ms"]) for c in cycles if isinstance(c.get("latency_ms"), (int, float))]
    decisions = Counter(str((c.get("decision") or {}).get("decision") or "unknown") for c in cycles)
    modes = Counter(str(c.get("mode") or "unknown") for c in cycles)
    phase_accepted = sum(1 for c in cycles if (c.get("phase") or {}).get("accepted"))

    statuses = Counter(str(a.get("status") or "unknown") for a in actions)
    held = [a for a in actions if a.get("status") == "held"]
    dropped = [a for a in actions if a.get("status") == "dropped"]
    expired = [a for a in dropped if "expired" in str(a.get("detail") or "").lower()]
    held_reasons = Counter(_held_reason_key(a.get("detail")) for a in held)
    action_types = Counter(_action_label(a.get("action")) for a in actions if a.get("status") in EXECUTED_STATUSES)

    program = [e for e in events if e.get("event") == "PROGRAM_CHANGED"]
    program_app = sum(1 for e in program if (e.get("payload") or {}).get("commanded"))
    mode_changes = [
        {"ts": e.get("ts"), "mode": (e.get("payload") or {}).get("mode")}
        for e in events
        if e.get("event") == "AI_DIRECTOR_MODE_CHANGED"
    ]
    pauses = sum(1 for e in events if e.get("event") == "AI_DIRECTOR_PAUSED")
    degraded = [e for e in events if e.get("event") == "PERCEPTION_DEGRADED"]
    degraded_by = Counter(
        str((e.get("payload") or {}).get("component") or (e.get("payload") or {}).get("source") or (e.get("payload") or {}).get("reason") or "unknown")
        for e in degraded
    )

    timeline: List[dict] = []
    for c in cycles:
        decision = c.get("decision") or {}
        if decision.get("decision") and decision.get("decision") != "continue":
            timeline.append(
                {
                    "ts": c.get("ts"), "kind": "decision", "cycle_id": c.get("cycle_id"),
                    "summary": f"{decision.get('decision')} ({round(float(decision.get('confidence') or 0) * 100)}%): {str(decision.get('reason') or '')[:120]}",
                    "status": "stale" if c.get("stale") else None,
                }
            )
    for a in actions:
        timeline.append(
            {
                "ts": a.get("ts"), "kind": "action", "cycle_id": a.get("cycle_id"),
                "summary": _action_label(a.get("action")), "status": a.get("status"),
                "detail": (str(a.get("detail"))[:120] if a.get("detail") else None),
            }
        )
    for m in models:
        if m.get("error"):
            timeline.append({"ts": m.get("ts"), "kind": "model_error", "cycle_id": m.get("cycle_id"), "summary": str(m.get("error"))[:120], "status": "error"})
    for e in events:
        timeline.append({"ts": e.get("ts"), "kind": "event", "summary": _event_summary(e), "status": str(e.get("event")).lower()})
    timeline.sort(key=lambda t: str(t.get("ts") or ""))

    total = len(timeline)
    if total > 2 * TIMELINE_EDGE:
        head, tail = timeline[:TIMELINE_EDGE], timeline[-TIMELINE_EDGE:]
    else:
        head, tail = timeline, []

    result = {
        "date": day.isoformat(),
        "has_data": bool(records or events),
        "totals": {
            "cycles": len(cycles),
            "decisions": dict(decisions),
            "stale_cycles": sum(1 for c in cycles if c.get("stale")),
            "model_errors": sum(1 for m in models if m.get("error")),
            "phase_changes_accepted": phase_accepted,
            "latency_ms": {
                "avg": round(statistics.fmean(latencies)) if latencies else None,
                "p50": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
                "max": max(latencies) if latencies else None,
            },
            "proposals": {
                "total": len(actions),
                "executed": sum(statuses[s] for s in EXECUTED_STATUSES),
                "approved": statuses["executed_after_approval"],
                "pending": statuses["pending_approval"],
                "rejected": sum(statuses[s] for s in REJECTED_STATUSES),
                "rejected_by_operator": statuses["rejected_by_operator"],
                "held": len(held),
                "expired": len(expired),
                "dropped": len(dropped) - len(expired),
                "by_status": dict(statuses),
            },
            "executed_by_type": dict(action_types.most_common(10)),
            "top_held_reasons": [{"reason": r, "count": n} for r, n in held_reasons.most_common(5)],
            "program_changes": {"total": len(program), "app": program_app, "operator": len(program) - program_app},
            "mode_changes": mode_changes[-20:],
            "pauses": pauses,
            "degraded_events": {"total": len(degraded), "by_component": dict(degraded_by.most_common(10))},
            "modes_seen": dict(modes),
            "rehearsals": len(windows),
            "rehearsal_records_excluded": excluded,
        },
        "timeline": {"total": total, "head": head, "tail": tail},
    }
    if include_operator:
        result["operator_advances"] = _operator_advances()
        result["operator_shots"] = _operator_shots(day)
    return result


def cycle_records_for(day: date) -> List[dict]:
    return _records_for(cycle_log._LOG_PATH, day)


def events_for(day: date) -> List[dict]:
    return _records_for(_events_path(), day)


def review_for(day: date) -> dict:
    return aggregate(day, cycle_records_for(day), events_for(day))


def available_dates() -> List[str]:
    """Local dates that have cycle-log or review-event data, newest first."""
    seen: set[str] = set()
    for path in (cycle_log._LOG_PATH, _events_path()):
        for record in _iter_jsonl(path):
            day = local_date(record.get("ts"))
            if day is not None:
                seen.add(day.isoformat())
    return sorted(seen, reverse=True)
