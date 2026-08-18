"""Application configuration from environment."""

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
    anthropic_base_url: str | None = None
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.0
    llm_timeout_seconds: int = 30
    
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

    # Yamaha MGX16 mixer (listen-only; the desk has no remote-control protocol).
    # Consumes the mgx-ai-mixer meter WebSocket to detect song start/end.
    enable_mock_mixer: bool = True
    mixer_ws_url: str = "ws://127.0.0.1:9000/ws"
    song_end_silence_db: float = -45.0   # RMS below this counts as "silent"
    song_end_hold_seconds: float = 3.0   # sustained silence that ends a song
    song_max_wait_seconds: float = 900.0  # give up auto-advance after this
    mock_song_seconds: float = 8.0       # simulated song length in mock mode

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


# Global settings instance
settings = Settings()
