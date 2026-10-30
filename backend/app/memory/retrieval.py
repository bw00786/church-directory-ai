"""Structured retrieval helpers for the AI director's production memory.

`MemoryManager.search` delegates to model-isolated pgvector cosine queries
(dimension-specific HNSW expression indexes; no lexical/hybrid search). The
director path adds two things on top:

- structured pre-filters (service_state / category / speaker_role, stored as
  JSONB ``metadata`` on each observation) applied *before* similarity ranking,
  so "what did we do during the sermon" is answered by sermon rows, not by
  whichever sentence happens to share a few hashed tokens; and
- `summarize_house_style`: a cheap, LLM-free aggregation of the operator's
  own behaviour (slide advances, shots, approvals/overrides) into a handful
  of bullet rules the prompt's HOUSE RULES section appends.

Everything here is pure Python over already-fetched rows so it is unit-testable
without PostgreSQL.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable

HOUSE_STYLE_CATEGORIES = (
    "operator_slide_advance",
    "operator_shot",
    "operator_override",
    "operator_approval",
)

HASHED_SPACE_PREFIX = "hashed:"

_DWELL_RE = re.compile(r"after (\d+(?:\.\d+)?)s")
_DELAY_RE = re.compile(r"(\d+(?:\.\d+)?)s before the click")
_IN_ITEM_RE = re.compile(r"\bin ([^;,]+?) (?:after|from)\b")
_PROPOSED_RE = re.compile(r"AI proposed (\w+)(?: -> (\S+))?.*operator (approved|rejected)", re.IGNORECASE)


def is_hashed_space(space: str | None) -> bool:
    """True for the last-resort hashed bag-of-words tier (near-random for RAG)."""
    return bool(space) and str(space).startswith(HASHED_SPACE_PREFIX)


def _meta(row: Any) -> dict:
    meta = getattr(row, "meta", None)
    if meta is None and isinstance(row, dict):
        meta = row.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _text(row: Any) -> str:
    return str(getattr(row, "text", None) or (row.get("text", "") if isinstance(row, dict) else ""))


def _category(row: Any) -> str:
    return str(getattr(row, "category", None) or (row.get("category", "") if isinstance(row, dict) else ""))


def observation_matches_state(row: Any, state: str, easyworship_item: str | None = None) -> bool:
    """Structured rows match on metadata.service_state; legacy operator-advance
    text (no metadata) matches when it names the state's EasyWorship item."""
    meta = _meta(row)
    row_state = meta.get("service_state")
    if row_state:
        return str(row_state) == state
    if easyworship_item and _category(row) == "operator_slide_advance":
        return f" in {easyworship_item} " in f"{_text(row)} " or f" in {easyworship_item}" in _text(row)
    return False


def _duration(seconds: float) -> str:
    """``~14 s`` below 90 s, else ``~4 min`` (one decimal when it matters)."""
    if seconds >= 90:
        minutes = f"{seconds / 60:.1f}".rstrip("0").rstrip(".")
        return f"{minutes} min"
    return f"{seconds:.0f} s"


def summarize_house_style(rows: Iterable[Any], state: str, *, max_rules: int = 6) -> list[str]:
    """Turn operator observations for one service state into 3-6 bullet rules.

    Cheap aggregation only (medians and counters); no LLM. Returns fewer than
    three bullets when there is not enough evidence rather than padding with
    guesses -- the prompt template already carries the generic defaults.

    ``operator_shot`` rows written by app.director.operator_shots carry
    ``kind`` (cut/reframe), ``preset_id``, ``hold_seconds`` and
    ``delay_since_speaker_change`` in their metadata; those give the
    "you stay on X (preset N) 92% of the time and cut to wide roughly every
    4 min", speaker-change latency and typical-hold bullets. Legacy shot rows
    (camera_role only) still produce the per-speaker shot bullets.
    """
    rows = list(rows)
    label = state.replace("_", " ")
    bullets: list[str] = []

    dwells: list[float] = []
    delays: list[float] = []
    backs = 0
    shots: Counter[tuple[str, str]] = Counter()
    inputs: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    approved: Counter[str] = Counter()
    # Structured shot samples (app.director.operator_shots).
    shot_targets: Counter[str] = Counter()
    shot_presets: dict[str, Counter[int]] = defaultdict(Counter)
    holds: list[float] = []
    holds_to: dict[str, list[float]] = defaultdict(list)       # target -> hold of the shot it replaced
    holds_from_to: dict[tuple[str, str], list[float]] = defaultdict(list)
    latencies: list[float] = []

    for row in rows:
        category = _category(row)
        meta = _meta(row)
        text = _text(row)
        if category == "operator_slide_advance":
            if "went back" in text or meta.get("kind") == "back":
                backs += 1
                continue
            dwell = meta.get("dwell")
            if dwell is None:
                match = _DWELL_RE.search(text)
                dwell = float(match.group(1)) if match else None
            if dwell is not None:
                dwells.append(float(dwell))
            delay = meta.get("delay")
            if delay is None:
                match = _DELAY_RE.search(text)
                delay = float(match.group(1)) if match else None
            if delay is not None:
                delays.append(float(delay))
        elif category == "operator_shot":
            speaker = meta.get("speaker_role") or "anyone"
            camera = meta.get("camera_role")
            if camera:
                shots[(str(speaker), str(camera))] += 1
            if meta.get("atem_input") is not None:
                inputs[str(meta["atem_input"])] += 1
            kind = meta.get("kind")
            target = str(camera) if camera else (
                f"input {meta['atem_input']}" if meta.get("atem_input") is not None else None
            )
            if kind in ("cut", "reframe") and target:
                shot_targets[target] += 1
                if meta.get("preset_id") is not None:
                    try:
                        shot_presets[target][int(meta["preset_id"])] += 1
                    except (TypeError, ValueError):
                        pass
            if kind == "cut":
                hold = meta.get("hold_seconds")
                if hold is not None:
                    holds.append(float(hold))
                    if target:
                        holds_to[target].append(float(hold))
                        origin = meta.get("from_camera_role")
                        if origin:
                            holds_from_to[(str(origin), target)].append(float(hold))
                latency = meta.get("delay_since_speaker_change")
                if latency is not None:
                    latencies.append(float(latency))
        elif category in ("operator_override", "operator_approval"):
            action = meta.get("action_type")
            target = meta.get("camera_role") or meta.get("easyworship_item") or meta.get("target")
            outcome = meta.get("outcome")
            if not action:
                match = _PROPOSED_RE.search(text)
                if match:
                    action, target, outcome = match.group(1), match.group(2), match.group(3).lower()
            if not action:
                continue
            key = f"{action}{f' -> {target}' if target else ''}"
            if (outcome or ("rejected" if category == "operator_override" else "approved")) == "rejected":
                rejected[key] += 1
            else:
                approved[key] += 1

    if dwells:
        median = statistics.median(dwells)
        bullets.append(
            f"During {label} the operator advances slides after ~{median:.0f}s on a slide (n={len(dwells)})."
        )
    if delays:
        median_delay = statistics.median(delays)
        bullets.append(
            f"Slides in {label} are advanced ~{median_delay:.1f}s after the last words of the slide are heard; "
            f"do not propose EASYWORSHIP_NEXT_SLIDE before the closing words."
        )
    if backs:
        bullets.append(
            f"The operator went back a slide {backs} time{'s' if backs != 1 else ''} in {label}: "
            f"an early advance costs more than a late one here."
        )
    dominant: str | None = None
    if sum(shot_targets.values()) >= 3:
        dominant, count = shot_targets.most_common(1)[0]
        share = count / sum(shot_targets.values())
        preset = ""
        if shot_presets.get(dominant):
            preset = f" (preset {shot_presets[dominant].most_common(1)[0][0]})"
        sentence = f"During {label} you stay on the {dominant} shot{preset} {share:.0%} of the time"
        others = [t for t, _ in shot_targets.most_common() if t != dominant]
        if others:
            secondary = others[0]
            cadence = holds_from_to.get((dominant, secondary)) or holds_to.get(secondary) or []
            if cadence:
                sentence += f" and cut to {secondary} roughly every {_duration(statistics.median(cadence))}"
            else:
                sentence += f"; {secondary} is your usual cutaway"
        bullets.append(sentence + ".")
    for (speaker, camera), count in shots.most_common(2):
        if count >= 2 and camera != dominant:
            who = "someone" if speaker == "anyone" else f"the {speaker}"
            bullets.append(f"When {who} speaks during {label}, the operator takes the {camera} shot ({count}x).")
    if inputs and dominant is None:
        program, count = inputs.most_common(1)[0]
        if count >= 3:
            bullets.append(f"ATEM input {program} is the usual program source during {label} ({count}x).")
    if len(latencies) >= 3:
        bullets.append(
            f"Speaker change -> cut within ~{statistics.median(latencies):.1f} s during {label} "
            f"(n={len(latencies)}); match that, do not cut on the first syllable."
        )
    if len(holds) >= 3:
        bullets.append(
            f"Typical hold during {label} ~{_duration(statistics.median(holds))} before the operator cuts "
            f"(n={len(holds)})."
        )
    for key, count in rejected.most_common(2):
        if count >= 2:
            bullets.append(
                f"The operator rejected {key} {count}x during {label}; propose it only with strong live evidence."
            )
    for key, count in approved.most_common(1):
        if count >= 3 and key not in rejected:
            bullets.append(f"The operator reliably approves {key} during {label} ({count}x).")

    return bullets[:max_rules]
