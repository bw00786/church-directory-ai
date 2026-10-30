"""Keep the AI Director's ServiceContext current from hard signals.

Before this service existed the context's hardware fields were written only by
the AI's own action engine, so a cue-engine cut, an operator switching on the
panel or EasyWorship moving on its own left the prompt saying "program: None".
``ContextSync`` subscribes to the event bus and mirrors:

- ``PROGRAM_CHANGED`` / ``PREVIEW_CHANGED`` (app.atem.service) -> ATEM
  program/preview, ``last_cut_at``, and the framing role on air (``"slides"``
  for the EasyWorship input; for the PTZ camera the role whose configured
  ``camera_role_*`` shot matches the camera's current preset);
- ``EASYWORSHIP_STATE`` (remote-protocol read-back) -> live item label
  (``item_label`` in the payload, else ``easyworship_service.item_label_for``)
  and slide number, then ``phase_reconciler.apply_hard_signals("easyworship")``;
- ``AUDIO_STARTED`` / ``AUDIO_STOPPED`` (app.audio.audio_observer) -> per-role
  ``speaking_roles``; ``TRANSCRIPT`` back-fills timing / ``is_singing`` on
  the matching transcript line;
- ``DIRECTOR_CUE_ENTERED`` (app.director.engine) -> plan cursor, camera role
  and ``phase_reconciler.apply_hard_signals("cue", state=...)``.

On start it performs an initial sync from the live services (ATEM state when
connected, EasyWorship live state, the audio observer's speaking map, the
director's current cue). Start it before the AI runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Callable, Dict, Optional

from app.config import settings
from app.domain.service_context import service_context
from app.domain.service_state import ServiceState
from app.events.bus import event_bus
from app.logging_config import get_logger

from .phase import phase_reconciler

logger = get_logger(__name__)

# Events this service consumes (payloads: app.atem.events.BUS_EVENTS,
# EWState.as_dict() + item_label, AudioObservation, whisper TRANSCRIPT,
# engine.EVENT_CUE_ENTERED).
CONSUMED_EVENTS = (
    "PROGRAM_CHANGED",
    "PREVIEW_CHANGED",
    "ATEM_CONNECTION_CHANGED",
    "EASYWORSHIP_STATE",
    "AUDIO_STARTED",
    "AUDIO_STOPPED",
    "TRANSCRIPT",
    "DIRECTOR_CUE_ENTERED",
)

SLIDES_ROLE = "slides"


def _camera_for_input(atem_input: int) -> Optional[int]:
    """CameraService id feeding ``atem_input`` (PTZ camera only), else None."""
    try:
        from app.vision.verification import camera_to_atem_input
    except Exception:  # pragma: no cover
        camera_to_atem_input = None  # type: ignore[assignment]
    candidates = [settings.ptz_camera_id, 1]
    for camera_id in candidates:
        mapped = camera_to_atem_input(camera_id) if camera_to_atem_input else None
        if mapped is not None and int(mapped) == atem_input:
            return int(camera_id)
    if atem_input == settings.atem_camera1_input:
        return int(settings.ptz_camera_id)
    return None


def camera_role_for_input(atem_input: Optional[int], current_role: Optional[str] = None) -> Optional[str]:
    """Framing role now on air for ``atem_input``.

    ``"slides"`` for the EasyWorship input. For the PTZ camera, the role from
    the shared ``camera_role_*`` table whose (camera, preset) matches the
    camera's last recalled preset -- preferring ``current_role`` when several
    roles share a preset. None when unknown (other inputs, preset unknown).
    """
    if atem_input is None:
        return None
    atem_input = int(atem_input)
    if atem_input == int(settings.atem_camera2_input):
        return SLIDES_ROLE
    camera_id = _camera_for_input(atem_input)
    if camera_id is None:
        return None
    from app.cameras.service import CAMERA_ROLES, camera_service, configured_role_shot

    getter = getattr(camera_service, "get_current_preset", None)
    preset = getter(camera_id) if callable(getter) else None
    current_shot = configured_role_shot(current_role) if current_role else (None, None)
    if preset is None:
        # Nothing recalled yet this session: keep a role that lives on this camera.
        return current_role if current_shot[0] == camera_id else None
    if current_shot == (camera_id, preset):
        return current_role
    for role in CAMERA_ROLES:
        if configured_role_shot(role) == (camera_id, preset):
            return role
    return current_role if current_shot[0] == camera_id else None


class ContextSync:
    """Event-bus subscriber that mirrors hard signals into ``service_context``."""

    def __init__(self) -> None:
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.events_handled = 0
        self.last_event: Optional[str] = None
        self.last_error: Optional[str] = None
        self.initial: Dict[str, bool] = {}
        self._handlers: Dict[str, Callable[[dict], None]] = {
            "PROGRAM_CHANGED": self._on_program_changed,
            "PREVIEW_CHANGED": self._on_preview_changed,
            "ATEM_CONNECTION_CHANGED": self._on_atem_connection,
            "EASYWORSHIP_STATE": self._on_easyworship_state,
            "AUDIO_STARTED": self._on_audio,
            "AUDIO_STOPPED": self._on_audio,
            "TRANSCRIPT": self._on_transcript,
            "DIRECTOR_CUE_ENTERED": self._on_cue_entered,
        }

    # -- lifecycle ------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._queue = await event_bus.subscribe()
        try:
            self.initial = await self.initial_sync()
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Context initial sync failed", error=str(e))
        self._task = asyncio.create_task(self._run())
        logger.info("Context sync started", initial=self.initial)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._queue is not None:
            await event_bus.unsubscribe(self._queue)
            self._queue = None

    async def _run(self) -> None:
        assert self._queue is not None
        while self._running:
            message = await self._queue.get()
            try:
                self.handle(message)
            except Exception as e:
                self.last_error = str(e)
                logger.warning("Context sync handler failed", error=str(e), exc_info=True)

    # -- initial sync -----------------------------------------------------------
    async def initial_sync(self) -> Dict[str, bool]:
        """Seed the context from the services' current state. Each source is
        best-effort; the result says which ones contributed."""
        synced = {"atem": False, "easyworship": False, "audio": False, "cue": False}
        try:
            from app.dependencies import get_atem_service_instance

            atem = get_atem_service_instance()
            if await atem.is_connected():
                state = await atem.get_state()
                program = getattr(state, "program_input", None)
                if program is not None and int(program) > 0:
                    self._apply_program(int(program))
                preview = getattr(state, "preview_input", None)
                if preview is not None and int(preview) > 0:
                    service_context.set_preview(int(preview))
                synced["atem"] = True
        except Exception as e:
            logger.debug("Context sync: ATEM state unavailable", error=str(e))

        try:
            from app.easyworship.service import easyworship_service

            state = easyworship_service.live_state
            if state is not None and getattr(state, "pres_no", None):
                payload = dict(state.as_dict()) if hasattr(state, "as_dict") else {"pres_no": state.pres_no}
                payload.setdefault("item_label", easyworship_service.item_label_for(state.pres_no))
                self._on_easyworship_state(payload)
                synced["easyworship"] = True
        except Exception as e:
            logger.debug("Context sync: EasyWorship state unavailable", error=str(e))

        try:
            from app.audio.audio_observer import audio_observer

            roles = getattr(audio_observer, "speaking_roles", {}) or {}
            for role, entry in roles.items():
                service_context.set_speaking(role, bool(entry.get("speaking")), at=entry.get("since"))
            synced["audio"] = bool(roles)
        except Exception as e:
            logger.debug("Context sync: audio observer unavailable", error=str(e))

        try:
            from app.director.engine import service_director
            from app.director.plan_builder import plan_entry_for_cue

            status = service_director.status()
            cue = service_director.current_cue()
            if status.running and cue is not None:
                entry = plan_entry_for_cue(cue)
                self._on_cue_entered(
                    {
                        "cue_id": cue.id,
                        "index": status.cue_index,
                        "label": cue.name,
                        "state": entry.state.value,
                        "camera_role": entry.camera_role,
                    }
                )
                synced["cue"] = True
        except Exception as e:
            logger.debug("Context sync: director state unavailable", error=str(e))
        return synced

    # -- dispatch -------------------------------------------------------------------
    def handle(self, message) -> bool:
        """Apply one bus message; True when it was one of ours."""
        if not isinstance(message, dict):
            return False
        event = message.get("event")
        handler = self._handlers.get(event) if isinstance(event, str) else None
        if handler is None:
            return False
        payload = message.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        if event in ("AUDIO_STARTED", "AUDIO_STOPPED") and "speaking" not in payload:
            payload = dict(payload, speaking=event == "AUDIO_STARTED")
        handler(payload)
        self.events_handled += 1
        self.last_event = event
        return True

    # -- handlers -------------------------------------------------------------------
    def _apply_program(self, program: Optional[int]) -> None:
        role = camera_role_for_input(program, service_context.current_camera_role)
        service_context.set_program(program)
        service_context.set_camera_role(role)

    def _on_program_changed(self, payload: dict) -> None:
        program = payload.get("program_input")
        if program is None:
            return
        self._apply_program(int(program))
        preview = payload.get("preview_input")
        if preview is not None:
            service_context.set_preview(int(preview))

    def _on_preview_changed(self, payload: dict) -> None:
        preview = payload.get("preview_input")
        if preview is not None:
            service_context.set_preview(int(preview))

    def _on_atem_connection(self, payload: dict) -> None:
        # The ATEM's state is re-read on the next poll; nothing to mirror here
        # except that a lost connection means program/preview are now unknown.
        if payload.get("connected") is False and payload.get("previous") is True:
            logger.info("Context sync: ATEM disconnected; program/preview kept as last known")

    def _on_easyworship_state(self, payload: dict) -> None:
        pres_no = payload.get("pres_no")
        if not pres_no:
            return  # nothing live / not paired: keep the last known item
        label = payload.get("item_label")
        if label is None:
            try:
                from app.easyworship.service import easyworship_service

                label = easyworship_service.item_label_for(int(pres_no))
            except Exception as e:
                logger.debug("Context sync: EasyWorship label lookup failed", error=str(e))
                label = None
        slide_no = payload.get("slide_no")
        item_changed = label != service_context.current_easyworship_item
        service_context.set_easyworship_item(
            label, int(slide_no) if slide_no else None, int(pres_no)
        )
        if item_changed and label:
            phase_reconciler.apply_hard_signals("easyworship")

    def _on_audio(self, payload: dict) -> None:
        role = payload.get("speaker_role") or payload.get("role")
        if not role:
            return
        service_context.set_speaking(str(role).lower(), bool(payload.get("speaking")))

    def _on_transcript(self, payload: dict) -> None:
        role = payload.get("role")
        text = payload.get("text")
        if not role or not text:
            return
        service_context.annotate_transcript(
            str(role).lower(),
            str(text),
            confidence=payload.get("confidence"),
            t_start=payload.get("t_start"),
            t_end=payload.get("t_end"),
            is_singing=payload.get("is_singing"),
        )
        singing = payload.get("is_singing")
        if singing is not None:
            entry = service_context.speaking_roles.get(str(role).lower())
            if entry is not None:
                entry["is_singing"] = bool(singing)

    def _on_cue_entered(self, payload: dict) -> None:
        index = payload.get("index")
        service_context.set_plan_cursor(int(index) if index is not None else None)
        camera_role = payload.get("camera_role")
        if camera_role:
            service_context.set_camera_role(camera_role)
        state: Optional[ServiceState] = None
        raw = payload.get("state")
        if raw:
            try:
                state = ServiceState(str(raw))
            except ValueError:
                state = None
        phase_reconciler.apply_hard_signals("cue", state=state, detail=payload.get("cue_id"))

    # -- status ---------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "running": self._running,
            "events_handled": self.events_handled,
            "last_event": self.last_event,
            "last_error": self.last_error,
            "initial_sync": dict(self.initial),
            "consumes": list(CONSUMED_EVENTS),
            "context_version": service_context.version,
        }


# Module-level singleton
context_sync = ContextSync()
