from .models import Priority, ProsodyConfig, VoiceConfig


def prosody_for(priority: Priority, persona: VoiceConfig) -> ProsodyConfig:
    return ProsodyConfig(
        speaking_rate=persona.speaking_rate * (0.95 if priority == Priority.CRITICAL else 1),
        expressiveness=persona.expressiveness,
        emotional_style="professional" if priority == Priority.CRITICAL else "calm",
    )