from .config import VoiceSettings
from .models import AttentionInput, Priority


def classify(event: AttentionInput) -> Priority:
    if event.critical or event.policy_block or event.consequence == "critical":
        return Priority.CRITICAL
    if event.hardware_failure or event.execution_failure or event.state_mismatch:
        return Priority.WARNING
    if event.operator_required and event.consequence in ("medium", "high"):
        return Priority.ATTENTION
    if event.routine or event.consequence == "low":
        return Priority.SILENT
    return Priority.BACKGROUND


def permits(priority: Priority, config: VoiceSettings, muted: bool) -> bool:
    if muted or not config.enabled or config.mode == "off":
        return False
    minimum = Priority.CRITICAL if config.mode == "emergency" else Priority.ATTENTION
    if config.mode == "testing":
        minimum = Priority.BACKGROUND
    return priority >= minimum