from .config import TTSSettings
from .models import TTSProvider


def build_provider(config: TTSSettings) -> TTSProvider:
    if config.provider == "piper":
        from .providers.piper import PiperProvider
        return PiperProvider(config)
    raise ValueError("TTS is disabled; select a provider in server configuration")