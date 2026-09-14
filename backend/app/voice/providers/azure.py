from xml.sax.saxutils import escape, quoteattr

import httpx

from ..config import TTSSettings
from ..models import AudioResult, ProsodyConfig, VoiceConfig


class AzureProvider:
    def __init__(self, config: TTSSettings):
        self.config = config

    async def synthesize(self, text: str, voice_config: VoiceConfig, prosody: ProsodyConfig) -> AudioResult:
        if not self.config.azure_key or not voice_config.voice_id:
            raise ValueError("Configure a TTS key and a stock female voice ID before testing")
        ssml = (
            '<speak version="1.0" xml:lang="en-US">'
            f'<voice name={quoteattr(voice_config.voice_id)}>'
            f'<prosody rate="{(prosody.speaking_rate - 1) * 100:.0f}%" pitch="0%">'
            f'{escape(text)}</prosody></voice></speak>'
        )
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            async with client.stream(
                "POST", f"https://{self.config.azure_region}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers={"Ocp-Apim-Subscription-Key": self.config.azure_key,
                         "Content-Type": "application/ssml+xml",
                         "X-Microsoft-OutputFormat": "riff-24000hz-16bit-mono-pcm"},
                content=ssml,
            ) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 3_000_000:
                        raise ValueError("TTS response exceeds audio limit")
        return AudioResult(data=bytes(data))