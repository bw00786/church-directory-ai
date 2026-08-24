"""Policy rules engine."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from app.logging_config import get_logger
from .permissions import Permission, ActionConstraints

logger = get_logger(__name__)


@dataclass
class PermissionPolicy:
    """Policy configuration for permissions."""
    
    # Hardware controls
    autonomous_camera_switching: bool = True
    autonomous_transitions: bool = True
    autonomous_stream_start: bool = False
    autonomous_stream_stop: bool = False
    autonomous_recording: bool = False
    
    # AI behavior tuning
    min_camera_hold_seconds: int = 8
    min_ai_action_confidence: float = 0.85
    max_consecutive_switches: int = 3
    camera_switch_cooldown_seconds: int = 5


class PolicyEngine:
    """Enforces permissions and constraints on actions."""
    
    def __init__(
        self,
        autonomous_camera_switching: bool = True,
        autonomous_transitions: bool = True,
        autonomous_stream_start: bool = False,
        autonomous_stream_stop: bool = False,
        autonomous_recording: bool = False,
        min_camera_hold_seconds: int = 8,
        min_ai_action_confidence: float = 0.85,
        max_consecutive_switches: int = 3,
        camera_switch_cooldown_seconds: int = 5,
        action_confidence_thresholds: Optional[Dict[str, float]] = None,
    ):
        """Initialize policy engine.
        
        Args:
            autonomous_camera_switching: Allow AI to switch cameras
            autonomous_transitions: Allow AI to perform transitions
            autonomous_stream_start: Allow AI to start stream
            autonomous_stream_stop: Allow AI to stop stream
            autonomous_recording: Allow AI to start/stop recording
            min_camera_hold_seconds: Minimum time camera stays on screen
            min_ai_action_confidence: Minimum confidence for AI action
            max_consecutive_switches: Max consecutive switches
            camera_switch_cooldown_seconds: Cooldown between switches
            action_confidence_thresholds: Per-action-category minimum
                confidence (e.g. {"camera_change": 0.85, "slide_change": 0.85,
                "atem_transition": 0.90}), used by the AI Director's action
                engine (see app.director.action_engine).
        """
        self.policy = PermissionPolicy(
            autonomous_camera_switching=autonomous_camera_switching,
            autonomous_transitions=autonomous_transitions,
            autonomous_stream_start=autonomous_stream_start,
            autonomous_stream_stop=autonomous_stream_stop,
            autonomous_recording=autonomous_recording,
            min_camera_hold_seconds=min_camera_hold_seconds,
            min_ai_action_confidence=min_ai_action_confidence,
            max_consecutive_switches=max_consecutive_switches,
            camera_switch_cooldown_seconds=camera_switch_cooldown_seconds,
        )
        self.action_confidence_thresholds: Dict[str, float] = action_confidence_thresholds or {}
        
        # Track action history for constraints
        self._action_history: Dict[str, list[tuple[datetime, int]]] = {}
        self._last_action_time: Dict[str, datetime] = {}
    
    def check_permission(self, permission: Permission, actor: str = "ai") -> bool:
        """Check if a permission is granted.
        
        Args:
            permission: Permission to check
            actor: Who is requesting (e.g., "ai", "human")
            
        Returns:
            True if permission is granted.
        """
        if actor != "ai":
            # Humans can do anything
            return True
        
        # AI permissions
        if permission == Permission.SWITCH_CAMERA:
            return self.policy.autonomous_camera_switching
        elif permission == Permission.PREVIEW_CAMERA:
            return self.policy.autonomous_camera_switching
        elif permission == Permission.PERFORM_CUT:
            return self.policy.autonomous_transitions
        elif permission == Permission.PERFORM_AUTO:
            return self.policy.autonomous_transitions
        elif permission == Permission.START_STREAM:
            return self.policy.autonomous_stream_start
        elif permission == Permission.STOP_STREAM:
            return self.policy.autonomous_stream_stop
        elif permission == Permission.START_RECORDING:
            return self.policy.autonomous_recording
        elif permission == Permission.STOP_RECORDING:
            return self.policy.autonomous_recording
        else:
            return False
    
    def can_action_execute(
        self,
        action_name: str,
        actor: str = "ai",
        confidence: float = 1.0,
    ) -> tuple[bool, Optional[str]]:
        """Check if an action can execute given constraints.
        
        Args:
            action_name: Name of the action
            actor: Who is requesting
            confidence: Confidence level of the action (for AI)
            
        Returns:
            Tuple of (allowed, reason).
        """
        if actor != "ai":
            return True, None
        
        # Check basic permission
        if not self.check_permission(Permission(action_name), actor):
            return False, "Permission denied"
        
        # Check confidence threshold
        if confidence < self.policy.min_ai_action_confidence:
            return False, f"Confidence {confidence:.2f} below threshold {self.policy.min_ai_action_confidence}"
        
        # Check cooldown
        if action_name in self._last_action_time:
            elapsed = (datetime.now() - self._last_action_time[action_name]).total_seconds()
            if action_name.startswith("switch_camera"):
                min_interval = self.policy.camera_switch_cooldown_seconds
                if elapsed < min_interval:
                    return False, f"Action cooldown (elapsed {elapsed:.1f}s, min {min_interval}s)"
        
        # Check consecutive actions
        if action_name in self._action_history:
            if len(self._action_history[action_name]) >= self.policy.max_consecutive_switches:
                return False, f"Max consecutive actions ({self.policy.max_consecutive_switches}) reached"
        
        return True, None
    
    def record_action(self, action_name: str, value: int = 0) -> None:
        """Record that an action was executed.
        
        Args:
            action_name: Name of the action
            value: Value associated with action (e.g., camera ID)
        """
        now = datetime.now()
        
        # Record in history
        if action_name not in self._action_history:
            self._action_history[action_name] = []
        
        self._action_history[action_name].append((now, value))
        
        # Keep only recent history (within 2x max hold time)
        cutoff = now - timedelta(
            seconds=self.policy.min_camera_hold_seconds * 2
        )
        self._action_history[action_name] = [
            (t, v) for t, v in self._action_history[action_name]
            if t > cutoff
        ]
        
        # Record last action time
        self._last_action_time[action_name] = now
        
        logger.debug("Action recorded", action=action_name, value=value)
    
    def get_constraints(self, permission: Permission) -> ActionConstraints:
        """Get constraints for a permission.
        
        Args:
            permission: Permission to get constraints for
            
        Returns:
            Action constraints.
        """
        if permission in (Permission.SWITCH_CAMERA, Permission.PREVIEW_CAMERA):
            return ActionConstraints(
                min_interval=self.policy.camera_switch_cooldown_seconds,
                max_consecutive=self.policy.max_consecutive_switches,
                min_confidence=self.policy.min_ai_action_confidence,
                permitted=self.policy.autonomous_camera_switching,
            )
        elif permission in (Permission.PERFORM_CUT, Permission.PERFORM_AUTO):
            return ActionConstraints(
                min_interval=0.0,
                max_consecutive=999,
                min_confidence=self.policy.min_ai_action_confidence,
                permitted=self.policy.autonomous_transitions,
            )
        elif permission == Permission.START_STREAM:
            return ActionConstraints(
                min_interval=5.0,  # Don't toggle stream too fast
                max_consecutive=1,
                min_confidence=0.95,
                permitted=self.policy.autonomous_stream_start,
            )
        elif permission == Permission.STOP_STREAM:
            return ActionConstraints(
                min_interval=5.0,
                max_consecutive=1,
                min_confidence=0.95,
                permitted=self.policy.autonomous_stream_stop,
            )
        else:
            return ActionConstraints(permitted=False)

    def check_ai_decision(
        self,
        action_category: str,
        confidence: float,
        *,
        actor: str = "ai",
    ) -> tuple[bool, Optional[str]]:
        """Validate a proposed AI Director action against its category's
        confidence threshold (see ``action_confidence_thresholds``).

        Args:
            action_category: e.g. "camera_change", "slide_change",
                "atem_transition".
            confidence: The AI's confidence for this specific decision.
            actor: "ai" enforces the threshold; any other actor (human) passes.

        Returns:
            (allowed, reason).
        """
        if actor != "ai":
            return True, None

        threshold = self.action_confidence_thresholds.get(
            action_category, self.policy.min_ai_action_confidence
        )
        if confidence < threshold:
            return False, (
                f"Confidence {confidence:.2f} below '{action_category}' threshold {threshold:.2f}"
            )
        return True, None