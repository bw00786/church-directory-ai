from .config import TTSSettings
from .models import TTSProvider


def build_provider(config: TTSSettings) -> TTSProvider:
    if config.provider == "azure":
        from .providers.azure import AzureProvider
        return AzureProvider(config)
    raise ValueError("TTS is disabled; select a provider in server configuration")