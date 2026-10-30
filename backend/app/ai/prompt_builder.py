"""Builds the AI Service Director's per-cycle user prompt.

The system prompt (prompts/service_director.txt) carries the role, shot
grammar, pacing rules and the output spec with worked examples; this module
renders the *live* part as clearly delimited sections a human director would
glance at: timing, who is speaking, where we are in the plan, what is on air,
vision, transcript, recent actions, house rules, retrieved memory.

Every section degrades gracefully when the ServiceContext snapshot lacks a
key (older snapshots, tests), so the prompt never depends on fields another
component has not published yet. The whole prompt is kept within the model's
context window (OLLAMA_NUM_CTX) by trimming the bulkiest sections first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_HOUSE_RULES_PATH = PROMPTS_DIR / "house_rules_default.md"
DEFAULT_HOUSE_RULES_FILE = "data/house-rules.md"

SECTION_ORDER = (
    "TIMING",
    "WHO IS SPEAKING",
    "PLAN CURSOR",
    "ON AIR",
    "VISION",
    "RECENT TRANSCRIPT",
    "RECENT ACTIONS + PREVIOUS DECISION",
    "HOUSE RULES",
    "MEMORY",
)

# Conservative chars-per-token for mixed prose/JSON on Qwen-class tokenizers.
_CHARS_PER_TOKEN = 3.5
# Tokens kept free for the model's reply and chat-template overhead.
_OUTPUT_RESERVE_TOKENS = 512

SINGING_STATES = {"hymn", "congregational_song", "closing_hymn"}


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (no tokenizer dependency)."""
    if not text:
        return 0
    return int(math.ceil(len(text) / _CHARS_PER_TOKEN))


def prompt_token_budget(system_prompt_tokens: int = 0) -> int:
    """Tokens available for the user prompt inside OLLAMA_NUM_CTX."""
    reserve = max(_OUTPUT_RESERVE_TOKENS, int(getattr(settings, "ai_director_max_tokens", 320) or 320) + 128)
    return max(512, int(settings.ollama_num_ctx) - system_prompt_tokens - reserve)


def transcript_confidence_floor() -> float:
    value = getattr(settings, "ai_director_transcript_min_confidence", 0.3)
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.3


# ---------------------------------------------------------------------------
# House rules (operator-editable)


_house_rules_cache: dict[str, Any] = {"path": None, "mtime": None, "text": None}


def house_rules_path() -> Path:
    return Path(str(getattr(settings, "ai_director_house_rules_file", DEFAULT_HOUSE_RULES_FILE) or DEFAULT_HOUSE_RULES_FILE))


def load_house_rules() -> str:
    """Operator's house rules from AI_DIRECTOR_HOUSE_RULES_FILE when present,
    else the shipped defaults. Cached by path+mtime so edits apply live."""
    path = house_rules_path()
    try:
        mtime = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mtime = None
    cache = _house_rules_cache
    if cache["path"] == str(path) and cache["mtime"] == mtime and cache["text"] is not None:
        return cache["text"]
    text = ""
    if mtime is not None:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("House rules file unreadable; using defaults", path=str(path))
            text = ""
    if not text:
        text = DEFAULT_HOUSE_RULES_PATH.read_text(encoding="utf-8").strip()
    cache.update(path=str(path), mtime=mtime, text=text)
    return text


# ---------------------------------------------------------------------------
# Section renderers. Each takes the snapshot (plus helpers) and returns lines.


def _fmt_seconds(value: Any) -> str:
    if value is None:
        return "unknown"
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return str(value)
    if seconds < 0:
        return "unknown"
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    if seconds >= 120:
        return f"{seconds / 60:.1f} min"
    return f"{seconds:.0f} s"


def _age_from(ts: Any, now: datetime) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return max(0.0, now.timestamp() - float(ts))
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (now - ts).total_seconds())
    return None


def render_timing(snapshot: dict, now: datetime) -> list[str]:
    clock = snapshot.get("updated_at") or now.isoformat(timespec="seconds")
    lines = [
        f"Clock: {clock}",
        f"Service state: {snapshot.get('service_state', 'unknown')}",
        f"Time in this state: {_fmt_seconds(snapshot.get('seconds_in_state'))}",
        f"Since last cut: {_fmt_seconds(snapshot.get('seconds_since_last_cut'))}",
    ]
    song_phase = snapshot.get("song_phase")
    if song_phase is None and isinstance(snapshot.get("singing"), dict):
        song_phase = snapshot["singing"].get("phase")
    if song_phase:
        lines.append(f"Song phase: {song_phase}")
    return lines


def render_speaking(snapshot: dict, now: datetime) -> list[str]:
    roles = snapshot.get("speaking_roles")
    singing_roles = set()
    for line in snapshot.get("transcript_recent") or []:
        if isinstance(line, dict) and line.get("is_singing") and (line.get("age_seconds") or 0) <= 20:
            singing_roles.add(line.get("role"))
    if isinstance(roles, dict) and roles:
        lines = []
        for role, info in roles.items():
            info = info if isinstance(info, dict) else {}
            speaking = bool(info.get("speaking"))
            if speaking:
                since = info.get("since")
                for_s = _fmt_seconds(info.get("speaking_for") if info.get("speaking_for") is not None else _age_from(since, now))
                detail = f"SPEAKING for {for_s}"
            else:
                last = info.get("last_spoke_at")
                ago = _age_from(last, now) if last is not None else None
                detail = f"silent (last spoke {_fmt_seconds(ago)} ago)" if ago is not None else "silent (has not spoken)"
            if role in singing_roles or info.get("singing") or info.get("is_singing"):
                detail += " [singing]"
            lines.append(f"- {role}: {detail}")
        active = snapshot.get("active_speakers")
        if active:
            lines.append(f"Active now: {', '.join(str(a) for a in active)}")
        return lines
    speaker = snapshot.get("speaker")
    if speaker and snapshot.get("speaking"):
        tag = " [singing]" if speaker in singing_roles else ""
        return [f"- {speaker}: SPEAKING{tag}", "(per-role timing not available)"]
    if speaker:
        return [f"- {speaker}: last heard, now silent", "(per-role timing not available)"]
    return ["Nobody is speaking (no audio observation yet)."]


def _element_summary(element: Any) -> dict:
    if element is None:
        return {}
    if isinstance(element, dict):
        return element
    # app.domain.service_plan.ServiceElement
    state = getattr(element, "type", None)
    return {
        "label": getattr(element, "id", None),
        "state": getattr(state, "value", state),
        "speaker": getattr(element, "speaker", None),
        "camera_role": getattr(element, "camera_role", None),
        "easyworship_item": getattr(element, "easyworship_item", None),
    }


def _fmt_element(prefix: str, element: Optional[dict]) -> str:
    if not element:
        return f"{prefix}: (none)"
    parts = [str(element.get("label") or element.get("id") or "?")]
    if element.get("state"):
        parts.append(f"state={element['state']}")
    if element.get("speaker"):
        parts.append(f"speaker={element['speaker']}")
    if element.get("camera_role"):
        parts.append(f"camera={element['camera_role']}")
    item = element.get("easyworship_item") or element.get("easyworship")
    if item:
        parts.append(f"slides={item!s}")
    return f"{prefix}: " + "; ".join(parts)


def render_plan_cursor(snapshot: dict, plan_elements: Sequence[Any]) -> list[str]:
    cursor = snapshot.get("plan_cursor")
    elements = [_element_summary(e) for e in plan_elements]
    if isinstance(cursor, dict) and cursor:
        index = cursor.get("index")
        total = cursor.get("total") or len(elements)
        current = cursor.get("current")
        nxt = cursor.get("next")
        previous = cursor.get("previous")
        if previous is None and isinstance(index, int) and 0 <= index - 1 < len(elements):
            previous = elements[index - 1]
        if current is None and isinstance(index, int) and 0 <= index < len(elements):
            current = elements[index]
        if nxt is None and isinstance(index, int) and 0 <= index + 1 < len(elements):
            nxt = elements[index + 1]
        pos = f"You are at element {index + 1} of {total}." if isinstance(index, int) else "Position in plan: unknown."
    else:
        state = snapshot.get("service_state")
        index = next((i for i, e in enumerate(elements) if e.get("state") == state), None)
        total = len(elements)
        previous = elements[index - 1] if index is not None and index > 0 else None
        current = elements[index] if index is not None else None
        nxt = elements[index + 1] if index is not None and index + 1 < total else (elements[0] if elements and index is None else None)
        pos = (f"You are at element {index + 1} of {total} (inferred from the service state)."
               if index is not None else f"Position in plan: not yet matched ({total} planned elements).")
    lines = [
        pos,
        _fmt_element("Previous", _element_summary(previous)),
        _fmt_element("Current", _element_summary(current)),
        _fmt_element("Next", _element_summary(nxt)),
    ]
    if elements:
        lines.append("Full plan (> marks current):")
        for i, element in enumerate(elements):
            marker = ">" if index is not None and i == index else " "
            bits = [str(element.get("label") or element.get("id") or "?")]
            if element.get("state"):
                bits.append(str(element["state"]))
            if element.get("camera_role"):
                bits.append(f"cam={element['camera_role']}")
            item = element.get("easyworship_item") or element.get("easyworship")
            if item:
                bits.append(f"slides={item}")
            lines.append(f"{marker} {i + 1}. " + " | ".join(bits))
    lines.append("The plan is a guide: deviate when the room is clearly doing something else.")
    return lines


def render_on_air(snapshot: dict) -> list[str]:
    program = snapshot.get("atem_program")
    preview = snapshot.get("atem_preview")
    lines = [
        f"ATEM program input: {program if program is not None else 'unknown'}",
        f"ATEM preview input: {preview if preview is not None else 'unknown'}",
        f"Camera role on air: {snapshot.get('camera_role') or 'unknown'}",
        f"EasyWorship item: {snapshot.get('easyworship_item') or 'unknown'}",
    ]
    slide = snapshot.get("easyworship_slide")
    if slide is None and isinstance(snapshot.get("easyworship"), dict):
        slide = snapshot["easyworship"].get("slide_no")
    if slide is not None:
        lines.append(f"EasyWorship slide: {slide}")
    return lines


def render_vision(snapshot: dict) -> list[str]:
    vision = snapshot.get("vision")
    if not isinstance(vision, dict) or not vision:
        return ["No vision data (vision disabled or no frames yet). Do not assume the stage is empty."]
    lines = []
    health = vision.get("program_health")
    if health is not None:
        lines.append(f"Program feed health: {health}")
    cameras = vision.get("cameras") or {}
    if isinstance(cameras, dict) and cameras:
        for key, obs in cameras.items():
            obs = obs if isinstance(obs, dict) else {}
            present = obs.get("person_present")
            in_roi = obs.get("person_in_roi")
            present_txt = "person present" if present else ("EMPTY (no person)" if present is False else "person: unknown")
            roi_txt = "framed in ROI" if in_roi else ("not in ROI" if in_roi is False else "ROI: unknown")
            lines.append(f"- {key}: {present_txt}; {roi_txt}; frame {obs.get('frame_health') or 'unknown'}")
    else:
        lines.append("No per-camera detections yet.")
    semantic = vision.get("semantic") or {}
    if isinstance(semantic, dict) and semantic:
        lines.append("Semantic cues: " + ", ".join(f"{k}={v}" for k, v in list(semantic.items())[:6]))
    return lines or ["No vision data."]


def _transcript_entries(snapshot: dict, context_lines: Iterable[Any], now: datetime) -> list[dict]:
    structured = snapshot.get("transcript_recent")
    if isinstance(structured, list):
        return [e for e in structured if isinstance(e, dict)]
    entries = []
    for line in context_lines or []:
        entries.append({
            "role": getattr(line, "speaker_role", None),
            "text": getattr(line, "text", ""),
            "age_seconds": _age_from(getattr(line, "timestamp", None), now),
            "confidence": None,
            "is_singing": None,
        })
    if entries:
        return entries
    raw = snapshot.get("recent_transcript")
    if isinstance(raw, str) and raw.strip():
        for raw_line in raw.splitlines():
            role, _, text = raw_line.partition(": ")
            entries.append({"role": role if text else None, "text": text or raw_line,
                            "age_seconds": None, "confidence": None, "is_singing": None})
    return entries


def render_transcript(snapshot: dict, context_lines: Iterable[Any], now: datetime, *,
                      floor: float, limit: int = 12) -> tuple[list[str], int]:
    """Returns (lines, dropped_count)."""
    entries = _transcript_entries(snapshot, context_lines, now)
    kept, dropped = [], 0
    for entry in entries:
        confidence = entry.get("confidence")
        if confidence is not None:
            try:
                if float(confidence) < floor:
                    dropped += 1
                    continue
            except (TypeError, ValueError):
                pass
        kept.append(entry)
    kept = kept[-limit:]
    if not kept:
        note = f" ({dropped} low-confidence line(s) hidden)" if dropped else ""
        return [f"No recent transcript{note}."], dropped
    lines = []
    for entry in kept:
        age = entry.get("age_seconds")
        age_txt = f"[{float(age):.0f}s ago]" if isinstance(age, (int, float)) else "[age ?]"
        conf = entry.get("confidence")
        conf_txt = f" (conf {float(conf):.2f})" if isinstance(conf, (int, float)) else ""
        sing = " [singing]" if entry.get("is_singing") else ""
        lines.append(f"{age_txt} {entry.get('role') or 'unknown'}{conf_txt}{sing}: {str(entry.get('text') or '').strip()}")
    if dropped:
        lines.append(f"({dropped} line(s) below confidence {floor:.2f} hidden)")
    return lines, dropped


def render_recent_actions(snapshot: dict) -> list[str]:
    actions = snapshot.get("last_actions") or []
    lines = ["Recent actions (oldest first):"]
    if actions:
        lines.extend(f"- {a}" for a in list(actions)[-8:])
    else:
        lines.append("- none yet")
    previous = snapshot.get("previous_decision") or snapshot.get("last_decision")
    if isinstance(previous, dict) and previous:
        acts = previous.get("actions") or []
        act_txt = ", ".join(
            f"{a.get('type')}{'->' + str(a.get('camera_role')) if a.get('camera_role') else ''}"
            for a in acts if isinstance(a, dict)
        ) or "none"
        try:
            conf = f"{float(previous.get('confidence', 0.0)):.2f}"
        except (TypeError, ValueError):
            conf = "?"
        lines.append(
            f"Previous decision: {previous.get('decision', '?')} (confidence {conf}); "
            f"actions: {act_txt}; reason: {str(previous.get('reason') or '').strip()[:160]}"
        )
    else:
        lines.append("Previous decision: none this service.")
    return lines


def render_house_rules(house_rules: str, house_style: Sequence[str]) -> list[str]:
    lines = [house_rules.strip() or "(no house rules configured)"]
    if house_style:
        lines.append("")
        lines.append("Learned from this operator's own past decisions (same service state):")
        lines.extend(f"- {rule}" for rule in house_style)
    return lines


def render_memory(history: Sequence[dict] | str) -> list[str]:
    if isinstance(history, str):
        return [history]
    if not history:
        return ["None found for this moment."]
    lines = ["Advisory only: past moments can be stale or from a different service. Live signals win."]
    for item in history:
        meta = item.get("metadata") or {}
        tags = [str(item.get("service_date") or ""), str(item.get("category") or "")]
        if meta.get("service_state"):
            tags.append(f"state={meta['service_state']}")
        if meta.get("speaker_role"):
            tags.append(f"speaker={meta['speaker_role']}")
        if meta.get("outcome"):
            tags.append(f"outcome={meta['outcome']}")
        sim = item.get("similarity")
        sim_txt = f" (similarity={float(sim):.2f})" if isinstance(sim, (int, float)) else ""
        lines.append(f"- [{', '.join(t for t in tags if t)}] {str(item.get('text') or '').strip()}{sim_txt}")
    return lines


# ---------------------------------------------------------------------------


@dataclass
class PromptBuild:
    text: str
    sections: dict[str, str]
    token_estimate: int
    budget: int
    trimmed: list[str] = field(default_factory=list)
    dropped_transcript_lines: int = 0


def _assemble(sections: dict[str, str]) -> str:
    parts = []
    for name in SECTION_ORDER:
        body = sections.get(name, "").rstrip()
        parts.append(f"=== {name} ===\n{body}")
    parts.append("=== DECIDE ===\nReturn exactly one JSON object matching the OUTPUT SPEC. What should happen next?")
    return "\n\n".join(parts)


def build_director_prompt(
    snapshot: dict,
    plan_elements: Sequence[Any] = (),
    *,
    context_lines: Iterable[Any] = (),
    history: Sequence[dict] | str = (),
    house_rules: Optional[str] = None,
    house_style: Sequence[str] = (),
    now: Optional[datetime] = None,
    token_budget: Optional[int] = None,
    transcript_floor: Optional[float] = None,
) -> PromptBuild:
    now = now or datetime.now(timezone.utc)
    floor = transcript_confidence_floor() if transcript_floor is None else transcript_floor
    rules = load_house_rules() if house_rules is None else house_rules
    budget = prompt_token_budget() if token_budget is None else token_budget

    transcript_lines, dropped = render_transcript(snapshot, context_lines, now, floor=floor)
    sections = {
        "TIMING": "\n".join(render_timing(snapshot, now)),
        "WHO IS SPEAKING": "\n".join(render_speaking(snapshot, now)),
        "PLAN CURSOR": "\n".join(render_plan_cursor(snapshot, plan_elements)),
        "ON AIR": "\n".join(render_on_air(snapshot)),
        "VISION": "\n".join(render_vision(snapshot)),
        "RECENT TRANSCRIPT": "\n".join(transcript_lines),
        "RECENT ACTIONS + PREVIOUS DECISION": "\n".join(render_recent_actions(snapshot)),
        "HOUSE RULES": "\n".join(render_house_rules(rules, house_style)),
        "MEMORY": "\n".join(render_memory(history)),
    }
    trimmed: list[str] = []
    text = _assemble(sections)
    estimate = estimate_tokens(text)

    # Trim the bulkiest, least decision-critical material first.
    if estimate > budget and not isinstance(history, str) and len(history) > 2:
        sections["MEMORY"] = "\n".join(render_memory(list(history)[:2]))
        trimmed.append("memory")
        text = _assemble(sections); estimate = estimate_tokens(text)
    if estimate > budget:
        short_lines, dropped = render_transcript(snapshot, context_lines, now, floor=floor, limit=4)
        sections["RECENT TRANSCRIPT"] = "\n".join(short_lines)
        trimmed.append("transcript")
        text = _assemble(sections); estimate = estimate_tokens(text)
    if estimate > budget and house_style:
        sections["HOUSE RULES"] = "\n".join(render_house_rules(rules, ()))
        trimmed.append("house_style")
        text = _assemble(sections); estimate = estimate_tokens(text)
    if estimate > budget:
        allowed_chars = max(200, int(len(rules) - (estimate - budget) * _CHARS_PER_TOKEN))
        sections["HOUSE RULES"] = rules[:allowed_chars].rstrip() + "\n(house rules truncated to fit the context window)"
        trimmed.append("house_rules")
        text = _assemble(sections); estimate = estimate_tokens(text)
    if estimate > budget:
        logger.warning("AI Director prompt exceeds the context budget even after trimming",
                       tokens=estimate, budget=budget)

    return PromptBuild(text=text, sections=sections, token_estimate=estimate, budget=budget,
                       trimmed=trimmed, dropped_transcript_lines=dropped)
