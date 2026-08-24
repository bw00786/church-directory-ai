"""Audio intelligence package.

Pipeline (see docs/ai-director.md):

    Yamaha DM3 channel (RMS meter feed, via app.mixer.service)
        -> ChannelVAD (energy threshold + hold time)
        -> AudioObserver (per configured role/channel)
        -> optional WhisperService transcript (only for channels with real
           PCM, i.e. wired to app.identity.audio_capture's local capture)
        -> AudioObservation -> event bus -> ServiceContext
"""
