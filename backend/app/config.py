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
    
    # Ollama & AI
    ollama_base_url: str = "http://127.0.0.1:11434"
    gemma_model: str = "gemma3"
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
    camera_1_host: str | None = None
    camera_1_port: int = 80
    camera_1_username: str | None = None
    camera_1_password: str | None = None
    # Optional vendor HTTP endpoints (fallback)
    camera_1_http_ptz_path: str | None = None
    camera_1_http_preset_path: str | None = None

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
