"""Phase 3: service-end recognition + shutdown bundle.

The state machine has ``BENEDICTION`` and ``POST_SERVICE`` states but nothing
autonomously reaches them or acts on them. This module recognizes that the
service has ended and proposes the wind-down:

1. **Arm** when the benediction is detected -- either the formal state becomes
   ``BENEDICTION`` or a benediction/dismissal keyword is heard.
2. **Fire** once armed and the room stays quiet for
   ``service_end_silence_seconds`` (the congregation has been dismissed / the
   postlude has faded), proposing a ``SERVICE_STATE_CHANGE`` to ``post_service``.
3. **Shut down** (optional, ``service_end_auto_shutdown`` + ai_directed): request
    operator approval to stop the ATEM stream/recording, blank EasyWorship, and
    home the cameras. Stream/record proposals retain their policy gates.

Gated by ``settings.service_end_enabled`` (default off). The state-change
proposal itself touches no hardware; the shutdown bundle is separate and
policy-gated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from app.config import settings
from app.director.action_models import DirectorAction, DirectorActionType
from app.domain.service_context import TranscriptLine
from app.domain.service_state import ServiceState
from app.events.bus import event_bus
from app.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ServiceEndProposal:
    reason: str
    confidence: float
    actions: List[DirectorAction] = field(default_factory=list)


class ServiceEndRecognizer:
    """Detects end-of-service and proposes the transition to POST_SERVICE."""

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._armed = False
        self._armed_reason: Optional[str] = None
        self._last_voice_at = now()
        self._fired = False

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def fired(self) -> bool:
        return self._fired

    def _keywords(self) -> List[str]:
        return [k.strip().lower() for k in settings.service_end_keywords.split(",") if k.strip()]

    def arm(self, reason: str) -> None:
        if self._armed:
            return
        self._armed = True
        self._armed_reason = reason
        self._last_voice_at = self._now()
        logger.info("Service-end recognizer armed", reason=reason)
        event_bus.publish({"event": "SERVICE_ENDING", "payload": {"reason": reason}})

    def observe_transcript(self, line: TranscriptLine) -> None:
        if not settings.service_end_enabled:
            return
        self._last_voice_at = self._now()
        if self._armed:
            return
        text = (line.text or "").lower()
        for keyword in self._keywords():
            if keyword and keyword in text:
                self.arm(f"heard '{keyword}'")
                return

    def observe_state(self, state: ServiceState) -> None:
        if not settings.service_end_enabled or self._armed:
            return
        if state == ServiceState.BENEDICTION:
            self.arm("service state benediction")

    def note_voice(self) -> None:
        """Mark that someone is currently speaking (resets the silence timer)."""
        self._last_voice_at = self._now()

    def evaluate(self) -> Optional[ServiceEndProposal]:
        """Fire once the armed service has been quiet long enough."""
        if not settings.service_end_enabled or not self._armed or self._fired:
            return None
        quiet_for = self._now() - self._last_voice_at
        if quiet_for < settings.service_end_silence_seconds:
            return None

        self._fired = True
        reason = (
            f"{self._armed_reason or 'benediction'} + {quiet_for:.0f}s sustained quiet"
        )
        logger.info("Service end detected", reason=reason)
        event_bus.publish({"event": "SERVICE_ENDED", "payload": {"reason": reason}})
        action = DirectorAction(
            type=DirectorActionType.SERVICE_STATE_CHANGE,
            target=ServiceState.POST_SERVICE.value,
            parameters={"source": "service_end"},
            confidence=1.0,
            reason=reason,
        )
        return ServiceEndProposal(reason=reason, confidence=1.0, actions=[action])

    def reset(self) -> None:
        self._armed = False
        self._armed_reason = None
        self._fired = False
        self._last_voice_at = self._now()


async def run_shutdown_bundle(policy_engine=None) -> dict:
    """Best-effort wind-down; stream/record stops always need operator approval."""
    from app.policy.permissions import Permission

    if policy_engine is None:
        from app.dependencies import get_policy_engine_instance

        policy_engine = get_policy_engine_instance()

    def _allowed(permission: Permission) -> bool:
        try:
            return policy_engine.check_permission(permission, actor="ai")
        except Exception:
            return False

    done: dict = {}

    if _allowed(Permission.STOP_STREAM):
        done["stop_stream"] = _request_stop("atem_stop_stream", "Stop live streaming")
    if _allowed(Permission.STOP_RECORDING):
        done["stop_recording"] = _request_stop("atem_stop_recording", "Stop recording")
    done["easyworship_black"] = await _safe(_easyworship_black())
    done["cameras_home"] = await _safe(_cameras_home())

    logger.info("Service shutdown bundle complete", **{k: v for k, v in done.items()})
    event_bus.publish({"event": "SERVICE_SHUTDOWN_BUNDLE", "payload": done})
    return done


async def _safe(coro) -> bool:
    try:
        result = await coro
        return bool(result) if result is not None else True
    except Exception:
        logger.warning("Shutdown step failed", exc_info=True)
        return False


def _request_stop(action: str, description: str):
    try:
        from app.agents.assistant_tools import _register_pending

        token = _register_pending(action, {}, description)
        return {"pending_confirmation": token, "action": action}
    except Exception:
        logger.warning("Shutdown approval request failed", exc_info=True)
        return False


async def _easyworship_black():
    from app.easyworship.service import easyworship_service

    return await easyworship_service.action("black")


async def _cameras_home():
    from app.cameras.service import camera_service

    return await camera_service.move_to_role("wide")


# Module-level singleton.
service_end_recognizer = ServiceEndRecognizer()
