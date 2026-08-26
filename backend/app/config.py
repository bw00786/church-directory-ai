"""Application configuration from environment."""

import sys

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from .env."""
    
    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 4
    
    # ATEM Bridge
    atem_bridge_host: str = "127.0.0.1"
    atem_bridge_port: int = 8090
    atem_ip: str = "192.168.30.20"
    
    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_database: str = "church_production"
    postgres_user: str = "church"
    postgres_password: str = "changeme"
    postgres_pool_size: int = 10
    postgres_max_overflow: int = 20
    
    # Anthropic Claude
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    # Smaller/faster model used for quick classification tasks (e.g. cue-advance decisions)
    anthropic_fast_model: str = "claude-haiku-4-5-20251001"
    anthropic_base_url: str | None = None
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.0
    llm_timeout_seconds: int = 30

    # Voyage AI (Anthropic's recommended embeddings partner -- Anthropic does
    # not offer its own embeddings API). Used for production-memory retrieval
    # (app.memory.embeddings). Falls back to a local deterministic embedding
    # if unset, so the app still works without this key.
    voyage_api_key: str = ""
    voyage_embedding_model: str = "voyage-4-large"

    # Embedding provider selection (app.memory.embeddings): "auto" uses Voyage
    # (if VOYAGE_API_KEY is set) -> nomic-embed-text-v1.5 (free, local, no API
    # key) -> hashed bag-of-words (last resort). Force a specific tier for
    # testing/ops with "voyage" | "nomic" | "hashed".
    embedding_provider: str = "auto"
    nomic_model_name: str = "nomic-ai/nomic-embed-text-v1.5"
    # Pin a specific revision (git commit) for reproducibility/supply-chain
    # safety -- loading uses trust_remote_code=True (see app.memory.embeddings).
    nomic_model_revision: str = ""

    # Production Policy
    autonomous_camera_switching: bool = True
    autonomous_transitions: bool = True
    autonomous_stream_start: bool = False
    autonomous_stream_stop: bool = False
    autonomous_recording: bool = False
    
    # AI Behavior
    min_camera_hold_seconds: int = 8
    min_ai_action_confidence: float = 0.85
    max_consecutive_switches: int = 3
    camera_switch_cooldown_seconds: int = 5
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"
    
    # Security
    secret_key: str = "your-secret-key-here-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24
    
    # Features
    enable_mock_atem: bool = True
    # If True, probe the real ATEM bridge on each connect and use it when reachable,
    # falling back to the mock client automatically otherwise (overrides enable_mock_atem).
    atem_auto_detect: bool = True
    atem_probe_timeout_seconds: float = 1.5
    enable_ai_director: bool = True
    enable_vision_detection: bool = False
    vision_enabled: bool = False

    # PTZ Camera (example one-camera config; add more fields for more cameras)
    camera_1_name: str = "PTZOptics Camera 1"
    camera_1_host: str | None = None
    camera_1_port: int = 80  # HTTP-CGI port
    camera_1_username: str | None = None
    camera_1_password: str | None = None
    camera_1_visca_port: int = 1240  # VISCA-over-IP TCP port
    camera_1_visca_udp: bool = False  # use UDP (Sony header) instead of TCP

    # Continuous joystick control: auto-stop if no command arrives within this window
    camera_joystick_hold_timeout: float = 1.0

    # Service Director (scripted Sunday service)
    # ATEM program input mapping for the two cameras.
    atem_camera1_input: int = 1  # PTZOptics camera
    atem_camera2_input: int = 2  # EasyWorship laptop (slides)
    ptz_camera_id: int = 1  # CameraService id for the PTZOptics camera
    service_countdown_seconds: int = 300  # opening 5-minute countdown slide

    # Scheduled auto-start (wall clock). Days are comma-separated (mon..sun).
    service_auto_start_enabled: bool = False
    service_start_time: str = "10:00"  # local HH:MM
    service_start_days: str = "sun"
    service_autonomous: bool = True  # AI may auto-advance eligible cues

    # Yamaha DM3/MGX16 mixer (listen-only; the desk has no remote-control protocol).
    # Consumes the mgx-ai-mixer meter WebSocket to detect song start/end.
    enable_mock_mixer: bool = True
    mixer_ws_url: str = "ws://127.0.0.1:9000/ws"
    song_end_silence_db: float = -45.0   # RMS below this counts as "silent"
    song_end_hold_seconds: float = 3.0   # sustained silence that ends a song
    song_max_wait_seconds: float = 900.0  # give up auto-advance after this
    mock_song_seconds: float = 8.0       # simulated song length in mock mode

    # MGX16 channel -> role mapping (configuration, not hard-coded). These are
    # 1-based channel indices. They serve two consumers: the meter feed
    # (RMS-only) and, when MGX_USB_ENABLED, the USB MAIN multichannel PCM
    # stream (channel N of the device == desk channel N). Keep them aligned.
    mixer_pastor_channel: int = 1
    mixer_liturgist_channel: int = 2
    mixer_vocalist_channel: int = 4
    mixer_congregation_channel: int = 8

    # Voice-activity detection (energy threshold on the mixer RMS meter feed).
    # A channel is "speaking" once its RMS rises above the threshold, and stays
    # "speaking" until it holds below threshold for `speech_silence_hold_seconds`.
    speech_active_db: float = -40.0
    speech_silence_hold_seconds: float = 1.5

    # Whisper transcription (only meaningful for channels with real PCM, i.e.
    # local mic/line-in capture via app.identity.audio_capture — the Yamaha
    # meter feed itself has no raw audio). Disabled unless a whisper backend
    # (faster-whisper or openai-whisper) is installed.
    enable_whisper: bool = False
    whisper_model_size: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # MGX16 USB MAIN multichannel PCM capture (WO-MGX-USB-1). The MGX16's USB
    # interface delivers per-channel PCM to the host as a standard multichannel
    # USB audio device — real waveform for VAD/ASR, replacing meter-only RMS.
    # The meter feed remains as a degraded fallback (see AudioObserver arbiter).
    mgx_usb_enabled: bool = False
    mgx_usb_device_name: str = "MGX"       # device-name substring match
    mgx_usb_device_index: int | None = None  # explicit PortAudio index override
    mgx_usb_sample_rate: int = 48000       # raw capture rate; negotiated w/ driver
    mgx_usb_stall_seconds: float = 2.0     # no frames within this => degraded

    # Voice-activity detection provider. "auto" uses Silero when USB capture is
    # active and the energy detector otherwise; "silero"/"energy" force one.
    vad_provider: str = "auto"             # auto | silero | energy
    vad_silero_threshold: float = 0.5

    # Which role channels get per-channel Whisper ASR (comma-separated). The
    # congregation channel is VAD-only by default (no ASR).
    whisper_roles: str = "pastor,liturgist,vocalist"

    # EasyWorship slide control (Windows desktop; driven by keystroke injection).
    enable_mock_easyworship: bool = True
    easyworship_window_title: str = "EasyWorship"
    easyworship_send_mode: str = "foreground"  # "foreground" or "postmessage"
    # When set, control EasyWorship via a remote agent on the EW machine
    # instead of local keystroke injection (e.g. "http://192.168.30.40:8091").
    easyworship_agent_url: str | None = None
    # Key specs per action (e.g. "pagedown", "ctrl+pagedown", "f5").
    ew_key_next_slide: str = "pagedown"
    ew_key_prev_slide: str = "pageup"
    ew_key_next_item: str = "ctrl+pagedown"
    ew_key_prev_item: str = "ctrl+pageup"
    ew_key_clear: str = "f5"
    ew_key_logo: str = "f6"
    ew_key_black: str = "f7"
    ew_key_live: str = "f9"

    # PTZ camera roles (config, not hard-coded): role -> camera id + preset id.
    camera_role_pastor_camera: int = 1
    camera_role_pastor_preset: int = 1
    camera_role_liturgist_camera: int = 1
    camera_role_liturgist_preset: int = 4
    camera_role_vocalist_camera: int = 1
    camera_role_vocalist_preset: int = 2
    camera_role_congregation_camera: int = 1
    camera_role_congregation_preset: int = 6
    camera_role_choir_camera: int = 1
    camera_role_choir_preset: int = 5
    camera_role_wide_camera: int = 1
    camera_role_wide_preset: int = 6

    # AI Service Director (reasoning layer above the cue engine; see
    # docs/ai-director.md). Modes: "manual" (observe only), "assisted"
    # (propose, human approves), "ai_directed" (execute automatically,
    # still gated by the policy engine).
    ai_director_mode: str = "assisted"
    ai_director_poll_seconds: float = 4.0
    ai_director_min_confidence: float = 0.80
    confidence_camera_change: float = 0.85
    confidence_slide_change: float = 0.85
    confidence_atem_transition: float = 0.90

    # Retrieval-augmented context: on each decision cycle, search production
    # memory (past cue/AI actions + decisions, see app.memory) for similar
    # past moments and hand them to Claude as advisory-only history. Never
    # bypasses the policy engine -- it only informs the reasoning that
    # produces a DirectorDecision, which is gated exactly like any other.
    ai_director_use_memory_rag: bool = True
    ai_director_memory_results: int = 5
    ai_director_memory_min_similarity: float = 0.15

    # Vision observation & PTZ verification layer (WO-VISION-1). Gated by
    # vision_enabled (VISION_ENABLED); byte-identical to pre-WO when off.
    vision_detector: str = "auto"          # auto | yolo | opencv_hog
    vision_snapshot_hz: float = 1.0        # PTZOptics snapshot poll rate
    vision_ring_minutes: int = 10          # diagnostic frame ring buffer (memory)
    vision_min_fps: float = 0.2            # below this -> degradation event
    vision_stall_seconds: float = 3.0      # per-input no-frame stall threshold
    vision_program_device: str | None = None  # cv2 device index/name for program
    vision_frame_source: str = "live"      # live | dir
    vision_frame_dir: str | None = None    # FRAME_SOURCE=dir root (per-tag subdirs)

    # Per-role expected-subject ROI as fractional "x,y,w,h" (all in [0,1]).
    vision_role_roi_pastor: str = "0.30,0.15,0.40,0.75"
    vision_role_roi_liturgist: str = "0.30,0.15,0.40,0.75"
    vision_role_roi_vocalist: str = "0.25,0.15,0.50,0.75"
    vision_role_roi_congregation: str = "0.05,0.20,0.90,0.70"
    vision_role_roi_choir: str = "0.10,0.15,0.80,0.75"
    vision_role_roi_wide: str = "0.05,0.10,0.90,0.85"

    # PTZ verification (FR-3).
    ptz_verify_action: str = "enforce"     # enforce | log (observation-only)
    ptz_verify_delay_ms: int = 1500        # motor-settle delay before checking
    ptz_offset_max: float = 0.25           # max |subject offset| for a pass
    ptz_block_seconds: float = 8.0         # block preview->program on bad framing
    ptz_unverified_max: int = 5            # consecutive unverified -> assisted
    ptz_subject_wait_seconds: float = 30.0  # empty-stage re-check window

    # Claude-vision escalation (FR-4). Optional, off by default, out of hot loop.
    vision_llm_enabled: bool = False
    vision_llm_max_px: int = 1024
    vision_llm_max_per_min: int = 2

    # EasyWorship slide-change verification via OCR (WO-EWVERIFY-1/-2/-3).
    # Independent of vision_enabled -- confirms a commanded next/prev
    # slide/item action visibly changed the on-screen text, captured from a
    # *dedicated* camera-2/EasyWorship-laptop tap (separate from the switched
    # ATEM program feed, e.g. an HDMI splitter + capture device on the
    # laptop's own output). When enabled with an empty/unopenable device the
    # app hard-fails at startup (a verification feature that never runs is a
    # green light wired to nothing).
    slide_verify_enabled: bool = False
    slide_verify_device: str = ""          # cv2 device index/name for the slides feed
    slide_verify_poll_ms: int = 400        # stabilization poll interval
    slide_verify_timeout_seconds: float = 4.0  # give up -> alert + halt automation
    # Lyric-aware semantic verification (WO-EWVERIFY-3). Independent toggle on
    # top of SLIDE_VERIFY_ENABLED; enabling it without change-detection is a
    # startup error. Compares stable post-action OCR text to the expected
    # lyric text via fuzzy match; below SEMANTIC_THRESHOLD -> alert + halt.
    slide_verify_semantic_enabled: bool = False
    slide_verify_semantic_threshold: float = 0.75

    class Config:
        env_file = ".env"
        case_sensitive = False
    
    @property
    def atem_bridge_url(self) -> str:
        """Get the ATEM bridge base URL."""
        return f"http://{self.atem_bridge_host}:{self.atem_bridge_port}"
    
    @property
    def database_url(self) -> str:
        """Get PostgreSQL connection string."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_database}"
        )


# Global settings instance. Constructing this parses every env/.env value; an
# unparseable numeric/boolean value raises here (pydantic names the offending
# field) -- fail loud at startup, never silently fall back to defaults.
try:
    settings = Settings()
except Exception as exc:  # pragma: no cover - exercised via subprocess in tests
    print(f"FATAL: invalid configuration -- {exc}", file=sys.stderr)
    raise
