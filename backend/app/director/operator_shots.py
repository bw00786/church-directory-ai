"""Learn from the operator's own camera work (Phase 3).

When the ATEM program changes and this app didn't command it, the operator
cut it (panel, or a hand on the switcher). Each such cut is recorded with what
the system knew at that moment: the service state and plan element, who was
speaking (and whether they were singing), the shot taken (ATEM input, framing
role, camera preset), how long the previous shot was up, cut vs auto
transition, the last words heard, and how long after a speaker change the cut
came. Preset recalls the operator makes while the PTZ camera is on program or
preview are recorded as ``kind: "reframe"`` samples (role/preset only).

Two stores, mirroring :mod:`app.director.operator_learning`:

- ``OperatorShotHistory`` (``OPERATOR_SHOTS_FILE``), keyed by
  ``(service_state, speaker_role)`` and keeping the newest
  ``OPERATOR_SHOTS_MAX_SAMPLES`` per key. ``preferred_shot`` /
  ``typical_hold`` / ``speaker_change_latency`` are the hooks the role table
  and the action engine's pacing consult.
- Production memory (category ``operator_shot``) with the structured fields
  as JSONB metadata, so ``summarize_house_style`` can turn them into learned
  HOUSE RULES bullets for the prompt.

Attribution: ``PROGRAM_CHANGED`` carries ``commanded`` (app.atem.service); a
change is the operator's only when that is False *and* neither the camera
service (``camera_service.commanded_within``) nor the AI runtime's
recent-effects ledger shows the app acting within
``OPERATOR_SHOTS_COMMAND_WINDOW_SECONDS``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import statistics
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple

from app.config import settings
from app.domain.service_context import SINGING_ROLES, service_context
from app.domain.service_state import SONG_STATES
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

MEMORY_CATEGORY = "operator_shot"
EVENT_OPERATOR_SHOT = "OPERATOR_SHOT"

# Bus events the recorder consumes (payloads: app.atem.events, audio_observer, whisper).
CONSUMED_EVENTS = ("PROGRAM_CHANGED", "PREVIEW_CHANGED", "AUDIO_STARTED", "AUDIO_STOPPED", "TRANSCRIPT",
                   "PTZ_PRESTAGED")

_TRANSCRIPT_LINES = 200
_MAX_KEYS = 500
# A program change seen this soon after ``transition_in_progress`` was an AUTO.
_TRANSITION_SECONDS = 2.5
_UNSET = object()

# Settings this module reads (``settings.<name>``); defaults apply until they
# exist in app.config. Tests patch them with ``monkeypatch.setitem(settings.__dict__, ...)``.
SETTING_DEFAULTS: Dict[str, Any] = {
    "operator_shots_enabled": True,
    "operator_shots_file": "data/operator-shots.json",
    "operator_shots_max_samples": 12,
    "operator_shots_min_samples": 3,
    "operator_shots_min_share": 0.6,
    "operator_shots_command_window_seconds": 3.0,
    "operator_shots_poll_seconds": 1.0,
    "operator_shots_context_seconds": 20.0,
    "operator_shots_tail_words": 12,
    "operator_shots_speaker_window_seconds": 15.0,
    "operator_shots_memory": True,
}


def _setting(name: str) -> Any:
    return getattr(settings, name, SETTING_DEFAULTS[name])


def _state_value(state: Any) -> str:
    return str(getattr(state, "value", state) or "")


def _norm_role(role: Optional[str]) -> Optional[str]:
    role = (role or "").strip().lower()
    return role or None


def _shot_target(sample: dict) -> Optional[str]:
    """What the operator took: the framing role, else the ATEM input."""
    role = sample.get("to_camera_role")
    if role:
        return str(role)
    if sample.get("to_input") is not None:
        return f"input {sample['to_input']}"
    return None


# -- history -------------------------------------------------------------------
class OperatorShotHistory:
    """Operator shot samples per ``(service_state, speaker_role)``, persisted as JSON."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path_override = path
        self._path: Optional[Path] = None
        self._entries: Dict[Tuple[str, str], List[dict]] = {}
        self._loaded = False
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = Path(self._path_override or _setting("operator_shots_file"))
        return self._path

    @staticmethod
    def _key(state: Any, speaker_role: Optional[str]) -> Tuple[str, str]:
        return _state_value(state), _norm_role(speaker_role) or ""

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for entry in raw.get("entries", []):
                key = self._key(entry["service_state"], entry.get("speaker_role"))
                self._entries[key] = [dict(s) for s in entry.get("samples", [])]
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Could not read operator shot history", path=str(self.path), exc_info=True)

    def _save(self) -> None:
        entries = [
            {"service_state": state, "speaker_role": role or None, "samples": samples}
            for (state, role), samples in self._entries.items()
        ]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps({"version": 1, "entries": entries}, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            logger.warning("Could not save operator shot history", path=str(self.path), exc_info=True)

    def record(self, sample: dict) -> None:
        state = _state_value(sample.get("service_state"))
        if not state:
            return
        with self._lock:
            self._ensure_loaded()
            key = self._key(state, sample.get("speaker_role"))
            samples = self._entries.setdefault(key, [])
            samples.append(dict(sample))
            del samples[: -max(1, int(_setting("operator_shots_max_samples")))]
            if len(self._entries) > _MAX_KEYS:
                oldest = min(self._entries, key=lambda k: self._entries[k][-1].get("at", "") if self._entries[k] else "")
                self._entries.pop(oldest)
            self._save()

    def samples(self, state: Any, speaker_role: Optional[str] = None, *, kinds: Iterable[str] = ("cut", "reframe")) -> List[dict]:
        """Samples for ``state`` (all speakers when ``speaker_role`` is None), oldest first."""
        state = _state_value(state)
        kinds = tuple(kinds)
        with self._lock:
            self._ensure_loaded()
            if speaker_role is None:
                picked = [s for (st, _), ss in self._entries.items() if st == state for s in ss]
            else:
                picked = list(self._entries.get(self._key(state, speaker_role), []))
        picked = [s for s in picked if s.get("kind", "cut") in kinds]
        picked.sort(key=lambda s: s.get("at", ""))
        return picked

    # -- query API -------------------------------------------------------------
    def preferred_shot(self, state: Any, speaker_role: Optional[str] = None) -> Optional[dict]:
        """The shot the operator takes for ``speaker_role`` in ``state`` when a
        clear majority exists: ``{camera_role, atem_input, camera_id, preset_id,
        samples, total, share}``; None below the sample/share thresholds."""
        samples = [s for s in self.samples(state, speaker_role) if _shot_target(s)]
        if not samples:
            return None
        counts: Counter[str] = Counter(_shot_target(s) for s in samples)  # type: ignore[misc]
        target, count = counts.most_common(1)[0]
        share = count / len(samples)
        if count < int(_setting("operator_shots_min_samples")) or share < float(_setting("operator_shots_min_share")):
            return None
        chosen = [s for s in samples if _shot_target(s) == target]

        def mode(field: str) -> Optional[Any]:
            values = [s[field] for s in chosen if s.get(field) is not None]
            return Counter(values).most_common(1)[0][0] if values else None

        return {
            "camera_role": mode("to_camera_role"),
            "atem_input": mode("to_input"),
            "camera_id": mode("camera_id"),
            "preset_id": mode("preset_id"),
            "samples": count,
            "total": len(samples),
            "share": round(share, 2),
        }

    def _median(self, state: Any, field: str) -> Optional[float]:
        values = [float(s[field]) for s in self.samples(state, kinds=("cut",)) if s.get(field) is not None]
        if len(values) < int(_setting("operator_shots_min_samples")):
            return None
        return round(statistics.median(values), 1)

    def typical_hold(self, state: Any) -> Optional[float]:
        """Median seconds the operator kept a shot up before cutting in ``state``."""
        return self._median(state, "hold_seconds")

    def speaker_change_latency(self, state: Any) -> Optional[float]:
        """Median seconds between a speaker change and the operator's cut in ``state``."""
        return self._median(state, "delay_since_speaker_change")

    def summary(self, limit: int = 50) -> dict:
        with self._lock:
            self._ensure_loaded()
            keys = list(self._entries)
            all_samples = [s for ss in self._entries.values() for s in ss]
        all_samples.sort(key=lambda s: s.get("at", ""), reverse=True)
        states = sorted({state for state, _ in keys})
        preferences = []
        for state, role in sorted(keys):
            preferences.append({
                "service_state": state,
                "speaker_role": role or None,
                "samples": len(self._entries.get((state, role), [])),
                "preferred": self.preferred_shot(state, role or None),
            })
        return {
            "keys": len(keys),
            "samples": len(all_samples),
            "recent": all_samples[: max(1, limit)],
            "preferences": preferences,
            "typical_hold_seconds": {s: self.typical_hold(s) for s in states},
            "speaker_change_latency_seconds": {s: self.speaker_change_latency(s) for s in states},
        }


# -- default collaborators -------------------------------------------------------
def _camera_role_for_input(atem_input: Optional[int], current_role: Optional[str]) -> Optional[str]:
    from app.director.context_sync import camera_role_for_input

    return camera_role_for_input(atem_input, current_role)


def _camera_for_input(atem_input: Optional[int]) -> Optional[int]:
    if atem_input is None:
        return None
    from app.director.context_sync import _camera_for_input as lookup

    return lookup(int(atem_input))


def _input_for_camera(camera_id: int) -> Optional[int]:
    try:
        from app.vision.verification import camera_to_atem_input

        mapped = camera_to_atem_input(int(camera_id))
        return int(mapped) if mapped is not None else None
    except Exception:  # pragma: no cover - defensive
        return None


def _current_preset(camera_id: Optional[int]) -> Optional[int]:
    if camera_id is None:
        return None
    from app.cameras.service import camera_service

    getter = getattr(camera_service, "get_current_preset", None)
    return getter(int(camera_id)) if callable(getter) else None


def _role_for_shot(camera_id: int, preset_id: int) -> Optional[str]:
    from app.cameras.service import CAMERA_ROLES, configured_role_shot

    for role in CAMERA_ROLES:
        if configured_role_shot(role) == (int(camera_id), int(preset_id)):
            return role
    return None


def _ptz_cameras() -> List[int]:
    return [int(getattr(settings, "ptz_camera_id", 1))]


def _app_acted_recently() -> bool:
    """Did the app recall a preset or change program within the attribution window?"""
    window = float(_setting("operator_shots_command_window_seconds"))
    try:
        from app.cameras.service import camera_service

        within = getattr(camera_service, "commanded_within", None)
        if callable(within) and within(window):
            return True
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        from app.director.ai_director_runtime import ai_director_runtime

        queue = getattr(ai_director_runtime, "queue", None)
        changed = getattr(queue, "program_changed_within", None)
        if callable(changed) and changed(window):
            return True
    except Exception:  # pragma: no cover - defensive
        pass
    return False


def _record_memory(sample: dict, text: str) -> None:
    from app.memory.production_memory import memory_manager

    extra = {
        key: sample.get(key)
        for key in ("kind", "preset_id", "camera_id", "from_input", "from_camera_role", "hold_seconds",
                    "transition", "singing", "delay_since_speaker_change", "on_air")
    }
    plan = sample.get("plan_element") or {}
    if plan.get("id"):
        extra["plan_element"] = plan.get("id")
        extra["plan_label"] = plan.get("label")
    if sample.get("to_input") is not None:
        source = f"atem:{sample['to_input']}"
    else:
        source = f"camera:{sample.get('camera_id')}"
    memory_manager.record_observation(
        MEMORY_CATEGORY, text, source,
        service_state=sample.get("service_state"), speaker_role=sample.get("speaker_role"),
        camera_role=sample.get("to_camera_role"), atem_input=sample.get("to_input"),
        easyworship_item=sample.get("easyworship_item"), metadata=extra,
    )


# -- recorder ----------------------------------------------------------------------
class OperatorShotRecorder:
    """Event-bus subscriber that records the operator's cuts and reframes."""

    def __init__(
        self,
        history: Optional[OperatorShotHistory] = None,
        *,
        context=service_context,
        camera_role_for_input: Callable[[Optional[int], Optional[str]], Optional[str]] = _camera_role_for_input,
        camera_for_input: Callable[[Optional[int]], Optional[int]] = _camera_for_input,
        input_for_camera: Callable[[int], Optional[int]] = _input_for_camera,
        current_preset: Callable[[Optional[int]], Optional[int]] = _current_preset,
        role_for_shot: Callable[[int, int], Optional[str]] = _role_for_shot,
        ptz_cameras: Callable[[], List[int]] = _ptz_cameras,
        app_acted_recently: Callable[[], bool] = _app_acted_recently,
        record_memory: Callable[[dict, str], None] = _record_memory,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.history = history or operator_shot_history
        self._context = context
        self._camera_role_for_input = camera_role_for_input
        self._camera_for_input = camera_for_input
        self._input_for_camera = input_for_camera
        self._current_preset = current_preset
        self._role_for_shot = role_for_shot
        self._ptz_cameras = ptz_cameras
        self._app_acted_recently = app_acted_recently
        self._record_memory = record_memory
        self._now = now
        # Tracked from bus payloads (independent of ContextSync's ordering).
        self._program: Optional[int] = None
        self._program_since: Optional[float] = None
        self._program_role: Optional[str] = None
        self._preview: Optional[int] = None
        self._speaker: Optional[str] = None
        self._speaker_changed_at: Optional[float] = None
        self._speaking: set = set()
        self._singing_roles: Dict[str, bool] = {}
        self._heard: Deque[Tuple[float, str, str]] = deque(maxlen=_TRANSCRIPT_LINES)
        self._presets: Dict[int, Any] = {}
        self._transition_seen_at = float("-inf")
        # Predictive PTZ (app.director.predictive_ptz) announces its own preset
        # recalls on the bus; a preset change seen right after one is not a reframe.
        self._app_moved_at = float("-inf")
        self._recorded = 0
        self._last: Optional[dict] = None
        self._tasks: List[asyncio.Task] = []

    # -- inputs ------------------------------------------------------------------
    def seed(self, program: Optional[int] = None, preview: Optional[int] = None,
             camera_role: Optional[str] = None, at: Optional[float] = None) -> None:
        """Adopt the current program/preview (from the context) without recording a cut."""
        if program is not None:
            self._program = int(program)
            self._program_since = self._now() if at is None else at
        if preview is not None:
            self._preview = int(preview)
        if camera_role is not None:
            self._program_role = camera_role
        for camera_id in self._ptz_cameras():
            self._presets[camera_id] = self._current_preset(camera_id)

    def on_transcript(self, role: str, text: str, at: Optional[float] = None, is_singing: Optional[bool] = None) -> None:
        role = _norm_role(role) or ""
        if text and text.strip():
            self._heard.append((self._now() if at is None else at, role, text.strip()))
        if is_singing is not None and role:
            self._singing_roles[role] = bool(is_singing)

    def on_audio(self, role: str, speaking: bool, at: Optional[float] = None) -> None:
        role = _norm_role(role)
        if not role:
            return
        now = self._now() if at is None else at
        if speaking:
            self._speaking.add(role)
            if role != self._speaker:
                self._speaker = role
                self._speaker_changed_at = now
        else:
            self._speaking.discard(role)

    async def handle(self, message) -> Optional[dict]:
        """Apply one bus message; returns the sample recorded, if any."""
        if not isinstance(message, dict):
            return None
        event = message.get("event")
        payload = message.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        if event == "PROGRAM_CHANGED":
            return await self._on_program_changed(payload)
        if event == "PREVIEW_CHANGED":
            self._note_transition(payload)
            preview = payload.get("preview_input")
            if preview is not None:
                self._preview = int(preview)
        elif event in ("AUDIO_STARTED", "AUDIO_STOPPED"):
            role = payload.get("speaker_role") or payload.get("role")
            speaking = payload.get("speaking", event == "AUDIO_STARTED")
            self.on_audio(str(role or ""), bool(speaking))
        elif event == "TRANSCRIPT":
            self.on_transcript(str(payload.get("role", "")), str(payload.get("text", "")),
                               is_singing=payload.get("is_singing"))
        elif event == "PTZ_PRESTAGED":
            self._app_moved_at = self._now()
        return None

    def _app_moved_recently(self) -> bool:
        window = float(_setting("operator_shots_command_window_seconds"))
        return self._now() - self._app_moved_at <= window or self._app_acted_recently()

    # -- program changes --------------------------------------------------------------
    def _note_transition(self, payload: dict) -> None:
        if payload.get("transition_in_progress"):
            self._transition_seen_at = self._now()

    def _transition(self, payload: dict, now: float) -> Optional[str]:
        if "transition_in_progress" not in payload:
            return None
        if payload.get("transition_in_progress") or now - self._transition_seen_at <= _TRANSITION_SECONDS:
            return "auto"
        return "cut"

    async def _on_program_changed(self, payload: dict) -> Optional[dict]:
        to_input = payload.get("program_input")
        if to_input is None:
            return None
        to_input = int(to_input)
        now = self._now()
        self._note_transition(payload)
        previous = payload.get("previous_input", self._program)
        from_input = int(previous) if previous is not None else self._program
        hold = None if self._program_since is None else round(max(0.0, now - self._program_since), 1)
        to_role = self._camera_role_for_input(to_input, self._program_role)
        sample = None
        operator = not payload.get("commanded") and not self._app_acted_recently()
        if operator and _setting("operator_shots_enabled"):
            sample = await self._record_cut(from_input, to_input, to_role, hold, self._transition(payload, now), now)
        self._program = to_input
        self._program_since = now
        self._program_role = to_role
        preview = payload.get("preview_input")
        if preview is not None:
            self._preview = int(preview)
        return sample

    def _speaker_now(self) -> Optional[str]:
        if self._speaker is not None:
            return self._speaker
        getter = getattr(self._context, "current_speaker", None)
        return _norm_role(getter()) if callable(getter) else None

    def _singing(self, speaker: Optional[str], state: str) -> bool:
        if speaker:
            if self._singing_roles.get(speaker):
                return True
            entry = (getattr(self._context, "speaking_roles", None) or {}).get(speaker)
            if entry and entry.get("is_singing"):
                return True
            if speaker in SINGING_ROLES:
                return True
        return state in {s.value for s in SONG_STATES}

    def _tail(self, at: float) -> str:
        window = float(_setting("operator_shots_context_seconds"))
        words = " ".join(text for t, _, text in self._heard if at - window <= t <= at).split()
        return " ".join(words[-int(_setting("operator_shots_tail_words")):])

    def _plan_element(self) -> Optional[dict]:
        cursor = getattr(self._context, "plan_cursor", None)
        current = (cursor() or {}).get("current") if callable(cursor) else None
        if not current:
            return None
        return {"id": current.get("id"), "label": current.get("label"), "state": current.get("state")}

    def _base_sample(self, kind: str) -> dict:
        state = _state_value(getattr(self._context, "state", ""))
        speaker = self._speaker_now()
        return {
            "kind": kind,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "service_state": state,
            "plan_element": self._plan_element(),
            "easyworship_item": getattr(self._context, "current_easyworship_item", None),
            "speaker_role": speaker,
            "singing": self._singing(speaker, state),
        }

    async def _record_cut(self, from_input: Optional[int], to_input: int, to_role: Optional[str],
                          hold: Optional[float], transition: Optional[str], now: float) -> dict:
        camera_id = self._camera_for_input(to_input)
        delay = None
        if self._speaker_changed_at is not None:
            since = now - self._speaker_changed_at
            if 0 <= since <= float(_setting("operator_shots_speaker_window_seconds")):
                delay = round(since, 1)
        sample = self._base_sample("cut")
        sample.update({
            "from_input": from_input,
            "to_input": to_input,
            "from_camera_role": self._program_role,
            "to_camera_role": to_role,
            "camera_id": camera_id,
            "preset_id": self._current_preset(camera_id) if camera_id is not None else None,
            "hold_seconds": hold,
            "transition": transition,
            "transcript_tail": self._tail(now),
            "delay_since_speaker_change": delay,
        })
        await self._store(sample)
        return sample

    # -- reframes ----------------------------------------------------------------------
    async def poll(self) -> Optional[dict]:
        """Look at the PTZ cameras' current presets; record an operator reframe on air."""
        recorded = None
        for camera_id in self._ptz_cameras():
            preset = self._current_preset(camera_id)
            previous = self._presets.get(camera_id, _UNSET)
            self._presets[camera_id] = preset
            if previous is _UNSET or preset is None or preset == previous:
                continue
            atem_input = self._input_for_camera(camera_id)
            on_air = None
            if atem_input is not None and atem_input == self._program:
                on_air = "program"
            elif atem_input is not None and atem_input == self._preview:
                on_air = "preview"
            role = self._role_for_shot(camera_id, preset)
            if on_air == "program" and role:
                self._program_role = role
            if on_air is None or self._app_moved_recently() or not _setting("operator_shots_enabled"):
                continue
            sample = self._base_sample("reframe")
            sample.update({
                "camera_id": camera_id,
                "preset_id": preset,
                "to_camera_role": role,
                "to_input": atem_input,
                "on_air": on_air,
            })
            await self._store(sample)
            recorded = sample
        return recorded

    # -- storage -------------------------------------------------------------------------
    async def _store(self, sample: dict) -> None:
        try:
            await asyncio.to_thread(self.history.record, sample)
        except Exception:
            logger.warning("Could not save operator shot sample", exc_info=True)
        if _setting("operator_shots_memory"):
            try:
                await asyncio.to_thread(self._record_memory, sample, describe(sample))
            except Exception:
                logger.warning("Could not record operator shot to production memory", exc_info=True)
        self._recorded += 1
        self._last = sample
        event_bus.publish({"event": EVENT_OPERATOR_SHOT, "payload": sample})
        logger.info("Operator shot recorded", kind=sample["kind"], state=sample["service_state"],
                    speaker=sample.get("speaker_role"), shot=_shot_target(sample), preset=sample.get("preset_id"))

    # -- lifecycle -------------------------------------------------------------------------
    async def start(self) -> None:
        if not _setting("operator_shots_enabled") or self._tasks:
            return
        self.seed(
            program=getattr(self._context, "current_atem_program", None),
            preview=getattr(self._context, "current_atem_preview", None),
            camera_role=getattr(self._context, "current_camera_role", None),
        )
        self._tasks = [asyncio.create_task(self._event_loop()), asyncio.create_task(self._poll_loop())]
        logger.info("Operator shot learning started", consumes=list(CONSUMED_EVENTS))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def _event_loop(self) -> None:
        queue = await event_bus.subscribe()
        try:
            while True:
                message = await queue.get()
                if isinstance(message, dict) and message.get("event") in CONSUMED_EVENTS:
                    try:
                        await self.handle(message)
                    except Exception:
                        logger.exception("Operator shot handler failed", event=message.get("event"))
        finally:
            with contextlib.suppress(Exception):
                await event_bus.unsubscribe(queue)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:
                logger.exception("Operator shot poll failed")
            await asyncio.sleep(float(_setting("operator_shots_poll_seconds")))

    def status(self) -> dict:
        return {
            "enabled": bool(_setting("operator_shots_enabled")),
            "running": bool(self._tasks),
            "recorded_this_run": self._recorded,
            "last_sample": self._last,
            "program": self._program,
            "preview": self._preview,
            "camera_role": self._program_role,
            "speaker": self._speaker,
            "presets": {str(k): v for k, v in self._presets.items()},
            "consumes": list(CONSUMED_EVENTS),
        }


def _duration(seconds: float) -> str:
    if seconds >= 90:
        minutes = f"{seconds / 60:.1f}".rstrip("0").rstrip(".")
        return f"{minutes} min"
    return f"{seconds:.0f}s"


def describe(sample: dict) -> str:
    """One readable line for production memory (what the AI director retrieves)."""
    state = (sample.get("service_state") or "the service").replace("_", " ")
    speaker = sample.get("speaker_role")
    doing = "singing" if sample.get("singing") else "speaking"
    who = f" while the {speaker} was {doing}" if speaker else ""
    shot = _shot_target(sample)
    preset_id = sample.get("preset_id")
    if shot is None:
        shot, preset = (f"preset {preset_id}", "") if preset_id is not None else ("another shot", "")
    else:
        preset = f" (preset {preset_id})" if preset_id is not None else ""
    if sample.get("kind") == "reframe":
        text = (f"Operator reframed camera {sample.get('camera_id')} to {shot}{preset} "
                f"on {sample.get('on_air') or 'air'} during {state}{who}")
        return text
    origin = sample.get("from_camera_role") or (
        f"input {sample['from_input']}" if sample.get("from_input") is not None else None
    )
    text = f"Operator cut to {shot}{preset}"
    if origin:
        text += f" from {origin}"
    text += f" during {state}{who}"
    if sample.get("hold_seconds") is not None:
        text += f" after {_duration(float(sample['hold_seconds']))} on the previous shot"
    if sample.get("transition") == "auto":
        text += " (auto transition)"
    if sample.get("delay_since_speaker_change") is not None:
        text += f"; {float(sample['delay_since_speaker_change']):.1f}s after the speaker changed"
    if sample.get("transcript_tail"):
        text += f"; heard: \"{sample['transcript_tail']}\""
    return text


# Module-level singletons
operator_shot_history = OperatorShotHistory()
operator_shot_recorder = OperatorShotRecorder()
