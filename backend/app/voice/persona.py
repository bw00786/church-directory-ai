from .models import AttentionInput, Priority


PERSONA = "Calm, warm, concise and professional. State the problem; never narrate routine work or sound panicked."


def message_for(event: AttentionInput, priority: Priority, operator: str) -> str:
    names = {str(i): name for i, name in enumerate(("One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight"), 1)}
    camera = f"Camera {names[event.resource]}" if event.resource in names else "A camera"
    messages = {
        "approval_required": "I need your approval for a production change. Please review the confirmation panel.",
        "cue_uncertainty": "I'm not sure the current cue has finished. Please review it before advancing.",
        "slide_unconfirmed": "EasyWorship didn't confirm the slide change. Please check the displayed slide.",
        "camera_failure": f"{camera} isn't responding. Please check the camera controls.",
        "state_mismatch": "I can't confirm the expected production state. Please check the monitor.",
        "stream_failure": "The stream appears to have disconnected. I need your attention.",
        "recording_failure": "Recording may have stopped. Please check the recorder.",
        "perception_degraded": "A production input is degraded. Please check the monitoring panel.",
        "mixer_failure": "The mixer connection needs attention. Please check the mixer panel.",
        "execution_failure": "A production action failed. Please check the result before trying again.",
        "policy_violation": "An unsafe production action was blocked. Please review the alert.",
        "attention_required": "A production decision needs your attention. Please review the pending actions.",
        "test": "This is a test of the AI Production Director headset.",
    }
    text = messages.get(event.event_type, "Please review the production monitoring panel.")
    if operator and (priority == Priority.CRITICAL or event.approval_token):
        return f"{operator}, {text[0].lower()}{text[1:]}"
    return text