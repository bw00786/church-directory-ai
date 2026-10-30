"""Rehearsal (replay) mode as a service.

Replays a recorded service -- a chronological list of ``AudioObservation``
shaped entries (or ``{"role", "text"}`` transcript lines) -- through the AI
Service Director and a *private* ``AIDirectorRuntime`` so the operator can
watch what the AI would have proposed or executed, without touching hardware.

Safety contract (never move real hardware in a rehearsal):

- The replay uses its own ``ServiceContext``, proposal queue and action engine;
  the live context, queue and cue engine are untouched.
- The action engine is ``RehearsalActionEngine``: the policy and pacing gates
  are real, but dispatch only *records* the action (the "mock client").
- A rehearsal refuses to start unless every ``ENABLE_MOCK_*`` flag is on or
  ``settings.force_mock_during_rehearsal`` is set, and never while the cue
  engine reports a running service.

This supersedes ``backend/scripts/replay_service.py`` (kept as a CLI).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.ai.decision import DirectorDecision
from app.config import settings
from app.director.action_engine import ActionEngine, _category_for, _seconds_since
from app.director.action_models import ActionResult, DirectorAction
from app.director.ai_director_runtime import VALID_MODES, AIDirectorRuntime, _to_director_action
from app.domain.observations import AudioObservation
from app.domain.service_context import ServiceContext
from app.domain.service_state import ServiceState
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)

SAMPLES_DIR = Path(__file__).resolve().parent / "rehearsal_samples"
MAX_ENTRIES = 5_000
MAX_LOG = 500
_MIN_SPEED, _MAX_SPEED = 0.1, 50.0

Decider = Callable[[ServiceContext], Awaitable[DirectorDecision]]


def _setting(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def mocks_all_enabled() -> bool:
    return bool(settings.enable_mock_atem and settings.enable_mock_mixer and settings.enable_mock_easyworship)


def rehearsal_allowed() -> tuple[bool, str]:
    """May a rehearsal run right now? (hardware must be mocked or forced to mocks)."""
    if not mocks_all_enabled() and not _setting("force_mock_during_rehearsal", False):
        return False, (
            "real hardware is configured (ENABLE_MOCK_* not all true); "
            "set FORCE_MOCK_DURING_REHEARSAL=true to rehearse against mock clients"
        )
    with contextlib.suppress(Exception):
        from app.director.engine import service_director

        if service_director.status().running:
            return False, "a service is running; stop it before rehearsing"
    return True, "ok"


class RehearsalActionEngine(ActionEngine):
    """Real policy + pacing gates; dispatch is recorded instead of executed."""

    def __init__(self, policy_engine, **kwargs) -> None:
        super().__init__(policy_engine, **kwargs)
        self.executed: List[dict] = []
        # The rehearsal's private context (set by the service); never the live one.
        self.context: Optional[ServiceContext] = None

    def _seconds_since_last_cut(self, now: float) -> Optional[float]:
        # Pace against this rehearsal's own cuts only, not the live context's last cut.
        if self._last_program_change_at is None:
            return None
        return max(0.0, now - self._last_program_change_at)

    def _on_air_role(self) -> Optional[str]:
        context = self.context
        if context is None:
            return None
        if context.current_camera_role:
            return context.current_camera_role
        obs = context.last_observation
        if obs is not None and obs.speaking and obs.speaker_role:
            return obs.speaker_role
        return None

    def _role_speaking_since(self, role: str, now: float) -> Optional[float]:
        # Speech boundaries come from the rehearsal context, never the live one.
        context = self.context
        if context is None:
            return None
        entry = context.speaking_roles.get(role)
        if not isinstance(entry, dict) or not entry.get("speaking"):
            return None
        since = _seconds_since(entry.get("since"), now_mono=now, now_wall=time.time())
        return now - since if since is not None else now

    async def execute(self, action: DirectorAction, *, pace: bool = True) -> ActionResult:
        category = _category_for(action.type)
        if category == "service_state":
            allowed, reason = True, None
        else:
            allowed, reason = self.policy_engine.check_ai_decision(category, action.confidence)
        if not allowed:
            return ActionResult(action=action, approved=False, executed=False, detail=reason or "rejected")
        now = self._clock()
        held_reason = self.pacing_hold(action, now) if pace else None
        if held_reason:
            return ActionResult(
                action=action, approved=True, executed=False, detail=held_reason, held=True, held_reason=held_reason
            )
        if category in ("atem_transition", "camera_change"):
            self._last_program_change_at = now
        self._last_by_category[category] = now
        detail = f"mock {action.type.value}" + (f" -> {action.target}" if action.target else "")
        self.executed.append({"at": datetime.now(timezone.utc).isoformat(), **action.model_dump(mode="json"), "detail": detail})
        return ActionResult(action=action, approved=True, executed=True, detail=detail)


class RehearsalRuntime(AIDirectorRuntime):
    """An isolated runtime: no PTZ pre-staging, no shutdown bundle, mock dispatch."""

    async def _prestage(self, proposals) -> None:  # never touch cameras/preview
        return None

    async def _maybe_shutdown(self, action: DirectorAction) -> None:  # never run the stop bundle
        return None


def _build_runtime(mode: str) -> RehearsalRuntime:
    from app.policy.engine import PolicyEngine

    runtime = RehearsalRuntime(action_engine=RehearsalActionEngine(PolicyEngine()))
    runtime.set_mode(mode)
    return runtime


def _to_observation(entry: Dict[str, Any], channel_for_role: Dict[str, int]) -> AudioObservation:
    """Accept AudioObservation dicts and ``{"role": ..., "text": ...}`` transcript lines."""
    data = dict(entry)
    role = str(data.get("speaker_role") or data.get("role") or "").lower()
    text = str(data.get("transcript") if data.get("transcript") is not None else data.get("text") or "")
    return AudioObservation(
        channel=int(data.get("channel", channel_for_role.get(role, 0))),
        speaker_role=role,
        speaking=bool(data.get("speaking", True)),
        transcript=text,
        confidence=float(data.get("confidence", 0.9)),
        duration_ms=int(data.get("duration_ms", 0)),
    )


def _role_channels() -> Dict[str, int]:
    return {
        "pastor": settings.mixer_pastor_channel,
        "liturgist": settings.mixer_liturgist_channel,
        "vocalist": settings.mixer_vocalist_channel,
        "congregation": settings.mixer_congregation_channel,
    }


def load_recording(payload: Any) -> tuple[dict, List[dict]]:
    """Normalise a recording file: a bare list or ``{"name", "description", "entries"}``."""
    if isinstance(payload, list):
        meta, entries = {}, payload
    elif isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        meta = {k: payload.get(k) for k in ("name", "description")}
        entries = payload["entries"]
    else:
        raise ValueError("recording must be a list of observations or an object with an 'entries' list")
    entries = [e for e in entries if isinstance(e, dict)]
    if not entries:
        raise ValueError("recording has no entries")
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"recording has more than {MAX_ENTRIES} entries")
    return meta, entries


def recordings_dir() -> Path:
    return Path(_setting("rehearsal_recordings_dir", "data/recordings"))


def list_samples() -> List[dict]:
    """Bundled sample recordings (``rehearsal_samples/*.json``) plus operator recordings."""
    found: List[dict] = []
    for base, kind in ((SAMPLES_DIR, "sample"), (recordings_dir(), "recording")):
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.json")):
            try:
                meta, entries = load_recording(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            found.append(
                {
                    "id": path.stem,
                    "kind": kind,
                    "name": meta.get("name") or path.stem,
                    "description": meta.get("description") or "",
                    "entries": len(entries),
                }
            )
    return found


def resolve_recording_path(*, sample: Optional[str], file: Optional[str]) -> Path:
    """Only bundled samples and files inside the recordings directory may be replayed."""
    if sample:
        path = SAMPLES_DIR / f"{Path(sample).name}.json"
        if path.is_file():
            return path
        path = recordings_dir() / f"{Path(sample).name}.json"
        if path.is_file():
            return path
        raise FileNotFoundError(f"no sample recording '{sample}'")
    if file:
        candidate = Path(file)
        base = recordings_dir().resolve()
        resolved = (candidate if candidate.is_absolute() else base / candidate).resolve()
        if base not in resolved.parents or not resolved.is_file():
            raise FileNotFoundError(f"recording must be a file inside {base}")
        return resolved
    raise FileNotFoundError("give a sample id or a recording file")


class RehearsalService:
    """Owns at most one rehearsal at a time."""

    def __init__(
        self,
        *,
        decide: Optional[Decider] = None,
        runtime_factory: Callable[[str], AIDirectorRuntime] = _build_runtime,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._decide = decide
        self._runtime_factory = runtime_factory
        self._sleep = sleep
        self._task: Optional[asyncio.Task] = None
        self._runtime: Optional[AIDirectorRuntime] = None
        self._context: Optional[ServiceContext] = None
        self._reset()

    def _reset(self) -> None:
        self._name: Optional[str] = None
        self._source: Optional[str] = None
        self._mode: str = "ai_directed"
        self._speed: float = 1.0
        self._total = 0
        self._position = 0
        self._started_at: Optional[str] = None
        self._finished_at: Optional[str] = None
        self._state: str = "idle"  # idle | running | finished | stopped | failed
        self._error: Optional[str] = None
        self._decisions: List[dict] = []

    async def _default_decide(self, context: ServiceContext) -> DirectorDecision:
        from app.ai.service_director import ai_service_director

        return await ai_service_director.decide(context)

    # -- lifecycle ----------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(
        self,
        *,
        sample: Optional[str] = None,
        file: Optional[str] = None,
        speed: float = 1.0,
        mode: str = "ai_directed",
    ) -> dict:
        if self.running:
            raise RuntimeError("a rehearsal is already running")
        allowed, why = rehearsal_allowed()
        if not allowed:
            raise PermissionError(why)
        if mode not in VALID_MODES:
            raise ValueError(f"invalid mode '{mode}'")
        speed = float(speed)
        if not (_MIN_SPEED <= speed <= _MAX_SPEED):
            raise ValueError(f"speed must be between {_MIN_SPEED} and {_MAX_SPEED}")
        path = resolve_recording_path(sample=sample, file=file)
        try:
            meta, entries = load_recording(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not read recording: {type(exc).__name__}") from exc

        self._reset()
        self._name = meta.get("name") or path.stem
        self._source = path.name
        self._mode, self._speed, self._total = mode, speed, len(entries)
        self._context = ServiceContext()
        self._runtime = self._runtime_factory(mode)
        engine = self._runtime._engine()
        if hasattr(engine, "context"):
            engine.context = self._context
        self._state = "running"
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._task = asyncio.create_task(self._run(entries))
        event_bus.publish(
            {
                "event": "REHEARSAL_STARTED",
                "payload": {"name": self._name, "entries": self._total, "speed": speed, "mode": mode},
            }
        )
        logger.info("Rehearsal started", name=self._name, entries=self._total, speed=speed, mode=mode)
        return self.status()

    async def stop(self) -> dict:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self._state = "stopped"
            self._finished_at = datetime.now(timezone.utc).isoformat()
            self._publish_stopped("stopped by operator")
        self._task = None
        return self.status()

    def _publish_stopped(self, reason: str) -> None:
        event_bus.publish(
            {
                "event": "REHEARSAL_STOPPED",
                "payload": {
                    "name": self._name,
                    "reason": reason,
                    "position": self._position,
                    "entries": self._total,
                    "decisions": len(self._decisions),
                },
            }
        )

    async def _run(self, entries: List[dict]) -> None:
        channels = _role_channels()
        default_gap = float(_setting("rehearsal_default_gap_seconds", 2.0))
        try:
            for index, entry in enumerate(entries):
                gap = entry.get("delay_seconds", default_gap if index else 0.0)
                try:
                    gap = max(0.0, float(gap))
                except (TypeError, ValueError):
                    gap = default_gap
                if gap:
                    await self._sleep(gap / self._speed)
                await self._step(index, entry, channels)
                self._position = index + 1
            self._state = "finished"
            self._finished_at = datetime.now(timezone.utc).isoformat()
            self._publish_stopped("finished")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a rehearsal must never take the app down
            logger.exception("Rehearsal failed")
            self._state, self._error = "failed", f"{type(exc).__name__}: {exc}"
            self._finished_at = datetime.now(timezone.utc).isoformat()
            self._publish_stopped("failed")

    async def _step(self, index: int, entry: dict, channels: Dict[str, int]) -> None:
        assert self._context is not None and self._runtime is not None
        context, runtime = self._context, self._runtime
        observation = _to_observation(entry, channels)
        context.record_audio(observation)
        if observation.transcript:
            # A transcribed segment also means the role was speaking just now.
            context.set_speaking(observation.speaker_role, True)

        started = time.perf_counter()
        decide = self._decide or self._default_decide
        try:
            decision = await decide(context)
        except Exception as exc:  # noqa: BLE001
            decision = DirectorDecision(decision="continue", confidence=0.0, reason=f"decide failed: {type(exc).__name__}")
        latency_ms = decision.latency_ms if decision.latency_ms is not None else int((time.perf_counter() - started) * 1000)
        runtime._last_cycle_id = decision.cycle_id

        if decision.service_state and decision.confidence >= float(_setting("ai_director_min_confidence", 0.8)):
            with contextlib.suppress(ValueError):
                context.set_state(ServiceState(decision.service_state), source="rehearsal")

        actions = [a for a in (_to_director_action(spec, decision) for spec in decision.actions) if a]
        outcome = await runtime.propose(actions, source="ai_director") if actions else {"proposals": [], "dropped": []}
        executed = getattr(runtime._engine(), "executed", [])
        self._decisions.append(
            {
                "index": index,
                "at": datetime.now(timezone.utc).isoformat(),
                "observation": {
                    "speaker_role": observation.speaker_role,
                    "speaking": observation.speaking,
                    "transcript": observation.transcript[:200],
                },
                "decision": decision.decision,
                "confidence": round(float(decision.confidence), 3),
                "reason": decision.reason[:300],
                "service_state": context.state.value,
                "latency_ms": latency_ms,
                "actions": [a.model_dump(mode="json") for a in actions],
                "proposals": [p.get("id") for p in outcome.get("proposals", [])],
                "dropped": len(outcome.get("dropped", [])),
                "executed": len(executed),
            }
        )
        del self._decisions[:-MAX_LOG]

    # -- views ----------------------------------------------------------------------
    def status(self) -> dict:
        runtime = self._runtime
        engine = runtime._engine() if runtime is not None else None
        executed = list(getattr(engine, "executed", []))
        return {
            "state": self._state,
            "running": self.running,
            "name": self._name,
            "source": self._source,
            "mode": self._mode,
            "speed": self._speed,
            "position": self._position,
            "total": self._total,
            "progress": round(self._position / self._total, 3) if self._total else 0.0,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "error": self._error,
            "service_state": self._context.state.value if self._context is not None else None,
            "decisions": list(self._decisions),
            "proposals": runtime.pending_proposals() if runtime is not None else [],
            "executed": executed[-MAX_LOG:],
            "mock_only": True,
        }


rehearsal_service = RehearsalService()
