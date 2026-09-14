import asyncio
import io
import wave

from .config import VoiceSettings
from .models import AudioResult


def decode_audio(audio: AudioResult):
    import numpy as np

    if not audio.data or len(audio.data) > 3_000_000:
        raise ValueError("Empty or oversized TTS audio")
    with wave.open(io.BytesIO(audio.data), "rb") as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        if wav.getsampwidth() != 2 or channels != 1 or rate not in (16000, 22050, 24000, 44100, 48000):
            raise ValueError("TTS must return mono 16-bit PCM WAV")
        count = wav.getnframes()
        if not 0 < count <= rate * 45:
            raise ValueError("TTS audio duration out of range")
        data = wav.readframes(count)
        if len(data) != count * 2:
            raise ValueError("Truncated TTS audio")
    return np.frombuffer(data, dtype="<i2").astype("float32") / 32768, rate


class HeadsetPlayback:
    def __init__(self, config: VoiceSettings):
        self.config = config
        self.stream = None

    def device(self):
        import sounddevice as sd

        if not self.config.output_device or not self.config.output_host_api or not self.config.routing_verified:
            raise ValueError("Configure and physically verify a dedicated headset output first")
        apis = sd.query_hostapis()
        matches = [i for i, d in enumerate(sd.query_devices())
                   if d["name"] == self.config.output_device and d["max_output_channels"] > 0
                   and apis[d["hostapi"]]["name"] == self.config.output_host_api]
        if len(matches) != 1:
            raise ValueError("Configured headset unavailable or ambiguous; default output is forbidden")
        return matches[0]

    async def play(self, audio: AudioResult, tone: bool = False):
        import numpy as np
        import sounddevice as sd

        samples, rate = decode_audio(audio)
        device = self.device()
        if tone:
            t = np.arange(int(rate * .18)) / rate
            alert = .08 * np.sin(2 * np.pi * 660 * t) * np.sin(np.pi * t / .18)
            samples = np.concatenate((alert, np.zeros(int(rate * .12)), samples)).astype("float32")
        position = 0
        finished = asyncio.Event()
        loop = asyncio.get_running_loop()

        def callback(outdata, frames, _time, status):
            nonlocal position
            outdata.fill(0)
            if status:
                loop.call_soon_threadsafe(finished.set)
                raise sd.CallbackAbort
            chunk = samples[position:position + frames]
            outdata[:len(chunk), 0] = chunk
            position += len(chunk)
            if position >= len(samples):
                raise sd.CallbackStop

        stream = sd.OutputStream(device=device, samplerate=rate, channels=1, dtype="float32",
                                 callback=callback, finished_callback=lambda: loop.call_soon_threadsafe(finished.set))
        self.stream = stream
        try:
            stream.start()
            await asyncio.wait_for(finished.wait(), len(samples) / rate + 3)
            if position < len(samples):
                raise RuntimeError("Headset playback ended before audio completed")
        finally:
            stream.abort()
            stream.close()
            if self.stream is stream:
                self.stream = None

    def stop(self):
        if self.stream is not None:
            self.stream.abort()

    async def alert(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\x00\x00" * 240)
        await self.play(AudioResult(data=buffer.getvalue()), tone=True)