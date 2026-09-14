import math

from .models import AttentionInput


WARNINGS = {
    "EASYWORSHIP_UNCONFIRMED": "slide_unconfirmed",
    "EASYWORSHIP_SLIDE_STUCK": "slide_unconfirmed",
    "EASYWORSHIP_SLIDE_MISMATCH": "state_mismatch",
    "SLIDE_VERIFY_HALT": "state_mismatch",
    "SLIDE_VERIFY_FAILED": "slide_unconfirmed",
    "CAMERA_CONNECTION_FAILED": "camera_failure",
    "PERCEPTION_DEGRADED": "perception_degraded",
    "MIXER_CONNECTION_FAILED": "mixer_failure",
    "ATEM_STATE_MISMATCH": "state_mismatch",
    "ATEM_CUT_BLOCKED": "state_mismatch",
    "PTZ_VERIFY_FAILED": "state_mismatch",
    "TOOL_EXECUTION_FAILED": "execution_failure",
}
CRITICALS = {"STREAM_FAILURE": "stream_failure", "RECORDING_FAILURE": "recording_failure",
             "POLICY_VIOLATION": "policy_violation", "CRITICAL_STATE_LOST": "state_mismatch"}
ROUTINE = {"AI_DECISION", "AUDIO_STARTED", "AUDIO_STOPPED", "TRANSCRIPT", "PTZ_PRESTAGED",
           "AI_ACTION_APPROVED", "AI_ACTION_PROPOSED", "EASYWORSHIP_STATE", "SERVICE_ENDED",
           "SERVICE_ENDING", "SERVICE_SHUTDOWN_BUNDLE", "AI_DIRECTOR_MODE_CHANGED",
           "VISION_OBSERVATION", "VISION_SEMANTIC", "VISION_RECOMMENDATION", "VISION_POLICY_DECISION",
           "VISION_EVENT", "VISION_STARTED", "VISION_STOPPED", "IDENTITY_MATCHED",
           "PTZ_VERIFY_PENDING", "PTZ_VERIFIED"}


def from_bus(message: dict) -> AttentionInput | None:
    if str(message.get("type", "")).startswith("voice_"):
        return None
    name = str(message.get("event") or message.get("type") or "")
    if name.startswith("voice_") or name.startswith("VOICE_"):
        return None
    raw = message.get("payload", message.get("data", {}))
    data = raw if isinstance(raw, dict) else {}
    resource = str(data.get("camera_id") or data.get("camera") or data.get("input") or data.get("channel") or data.get("source") or data.get("component") or data.get("action") or "production")[:100]
    confidence = data.get("confidence", 1.0)
    if isinstance(raw, list) and raw:
        confidence = min((item.get("confidence", 0) for item in raw if isinstance(item, dict)
                          and isinstance(item.get("confidence", 0), (int, float))), default=0)
    if not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
        confidence = 0
    base = dict(source=name[:100], resource=resource, confidence=max(0, min(1, confidence)))
    if name == "director_action" and data.get("action") == "error":
        return AttentionInput(event_type="execution_failure", execution_failure=True, consequence="high", **base)
    if name == "director_suggestion":
        return AttentionInput(event_type="cue_uncertainty", operator_required=True, consequence="medium", **base)
    if name in CRITICALS:
        return AttentionInput(event_type=CRITICALS[name], critical=True, consequence="critical", **base)
    if name in WARNINGS:
        return AttentionInput(event_type=WARNINGS[name], hardware_failure=True, consequence="high", **base)
    if name == "ASSISTANT_CONFIRMATION_REQUIRED":
        return AttentionInput(event_type="approval_required", operator_required=True, consequence="high",
                              approval_token=data.get("token"), **base)
    if name in ("AI_ACTIONS_PENDING_APPROVAL", "AI_ACTION_REJECTED"):
        return AttentionInput(event_type="attention_required", operator_required=True, consequence="medium", **base)
    if name == "PERCEPTION_RESTORED":
        return AttentionInput(event_type="perception_degraded", resolved=True, routine=True, **base)
    if name == "ASSISTANT_CONFIRMATION_RESOLVED":
        return AttentionInput(event_type="approval_required", resolved=True, routine=True,
                              approval_token=data.get("token"), **base)
    if name in ROUTINE or name.startswith("director"):
        return AttentionInput(event_type=name[:100], routine=True, **base)
    # Unknown events cannot acquire speech authority from arbitrary payload fields.
    return AttentionInput(event_type=name[:100] or "unknown", consequence="medium", **base)