"""Audio intelligence package.

Pipeline (see docs/ai-director.md), preferred path first:

    MGX16 USB MAIN per-channel PCM (app.audio.usb_capture)
        -> SileroChannelVAD (neural VAD, app.audio.silero_vad)
        -> MultiChannelTranscriber (per-role Whisper, VAD-gated)
        -> AudioObservation -> event bus -> ServiceContext

    Fallback per channel when USB stalls (app.audio.audio_observer arbiter):

    Meter feed RMS (app.mixer.service / app.audio.yamaha_capture)
        -> ChannelVAD (energy threshold + hold time)
        -> AudioObservation (no transcript) -> ServiceContext
"""
