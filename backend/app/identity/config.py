"""Identity recognition and memory configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class IdentitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    face_recognition_enabled: bool = True
    voice_diarization_enabled: bool = True

    # Cosine-similarity threshold for accepting a face match against the
    # roster. 0.363 is OpenCV's own published operating point for the SFace
    # model (models_data/face_recognition_sface_2021dec.onnx), used whenever
    # those weights are present (FaceEmbedder.deep_mode == True, the default
    # in this repo). If the model files are missing and face recognition
    # falls back to the classical Haar+LBP histogram method, raise this
    # (e.g. ~0.6-0.75) -- that method's similarity scores are on a different,
    # less-discriminating scale and 0.363 would be far too permissive there.
    face_match_threshold: float = 0.363
    voice_match_threshold: float = 0.70

    # Liveness / anti-spoofing gate applied to face recognition on the live
    # vision feed (not to manual roster enrollment). Combines a texture check
    # (real skin has more high-frequency detail than a printed photo or a
    # re-captured screen) with a temporal micro-motion check across recent
    # frames of the same track (a rigidly held photo shows near-zero motion).
    # This raises the bar against the common "hold up a printed photo" attack;
    # it does NOT detect a video replay of the real person. Matches that fail
    # the gate are still logged (for audit) but do not update roster
    # "last seen" stats or the role->preset learning table.
    liveness_check_enabled: bool = True
    liveness_score_threshold: float = 0.35

    # Role -> preset learning confidence floor. A learned preset is only
    # trusted once it has at least this many co-occurrence observations AND
    # leads the runner-up preset by at least this many -- otherwise the cue
    # sheet's configured fallback preset is used. Prevents a single early
    # misidentification (or a handful of ambiguous frames) from steering the
    # camera before real data has accumulated.
    min_role_preset_samples: int = 8
    min_role_preset_margin: int = 3

    # How long an un-enrolled provisional voice cluster is remembered before
    # being forgotten (session memory only, never persisted).
    unknown_speaker_ttl_seconds: float = 300.0

    # Minimum silence RMS below which a voice frame is classified as silence.
    voice_silence_rms: float = 0.02

    # Local audio capture (real PCM, not the mixer's RMS-only meter feed).
    # The Yamaha MGX16 has no raw-audio remote protocol, so genuine
    # diarization requires a physical audio input on the machine running the
    # backend -- e.g. the mixer's monitor/aux output or a room mic wired into
    # a line-in/USB interface. Disabled by default since it requires that
    # hardware hookup and a `sounddevice`-compatible input device.
    enable_audio_capture: bool = False
    audio_capture_device: str | None = None  # sounddevice name/index; None = system default
    audio_capture_sample_rate: int = 16000
    audio_capture_window_seconds: float = 2.0
    audio_capture_channel_name: str = "room_mic"
    # Service-plan role this capture device represents ("pastor", "liturgist",
    # "vocalist", "congregation"), used to tag AudioObservations for the AI
    # Director's ServiceContext. None = use audio_capture_channel_name as-is.
    audio_capture_role: str | None = None
