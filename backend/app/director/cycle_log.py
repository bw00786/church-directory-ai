"""Per-cycle JSONL log of AI Director decision cycles.

Every decision cycle is logged as one line in ``data/ai_director_cycles.jsonl``,
keyed by a cycle id and timestamped. A line ties together the inputs the model
saw (context snapshot, service plan, retrieved history), the model's raw reply,
the parsed decision, and the outcome of each action that cycle produced
(pending / executed / rejected, with detail). Each action entry carries the
same ``cycle_id`` so a cycle can be reconstructed and audited end to end.

This is a local audit log, complementary to production memory (Postgres): it
works without a database and records the full inputs/outputs, not just summaries.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.logging_config import get_logger

logger = get_logger(__name__)

_LOG_PATH = Path("data") / "ai_director_cycles.jsonl"
_write_lock = threading.Lock()


def new_cycle_id() -> str:
    return uuid.uuid4().hex


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(record: Dict[str, Any]) -> None:
    record = {"ts": _timestamp(), **record}
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock, _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        # Audit logging must never break a live decision cycle.
        logger.warning("AI Director cycle log write failed", exc_info=True)


def log_model_cycle(
    cycle_id: str,
    inputs: Dict[str, Any],
    raw_response: Optional[str],
    decision: Optional[Dict[str, Any]],
    error: Optional[str] = None,
) -> None:
    _append(
        {
            "cycle_id": cycle_id,
            "kind": "model",
            "inputs": inputs,
            "raw_response": raw_response,
            "decision": decision,
            "error": error,
        }
    )


def log_action_outcome(
    cycle_id: str,
    action: Dict[str, Any],
    status: str,
    detail: Optional[str] = None,
) -> None:
    _append(
        {
            "cycle_id": cycle_id,
            "kind": "action",
            "action": action,
            "status": status,
            "detail": detail,
        }
    )


def read_cycles(limit: int = 100) -> List[Dict[str, Any]]:
    """Return the newest ``limit`` log lines, newest last. Tolerates a corrupt tail."""
    if not _LOG_PATH.exists():
        return []
    try:
        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
