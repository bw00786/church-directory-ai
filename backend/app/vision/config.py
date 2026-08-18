"""Vision subsystem configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class VisionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    vision_enabled: bool = True

    camera_1_rtsp_url: str | None = None
    camera_2_rtsp_url: str | None = None
    camera_3_rtsp_url: str | None = None
    camera_4_rtsp_url: str | None = None

    video_fps: int = 30
    vision_fps: int = 3

    person_detector_model: str | None = None
    person_detector_device: str = "cpu"
    person_detector_confidence: float = 0.5

    vision_event_threshold: float = 0.75
    ai_action_threshold: float = 0.85

    vision_debug: bool = False
    store_event_frames: bool = False
    event_frame_retention_days: int = 14

    composition_weight_size: float = 0.35
    composition_weight_centering: float = 0.25
    composition_weight_headroom: float = 0.2
    composition_weight_visibility: float = 0.2

    event_debounce_seconds: float = 1.0
    min_event_stability_seconds: float = 1.0
    max_person_track_age_seconds: float = 2.0
    min_camera_hold_seconds: int = 8
    camera_switch_cooldown_seconds: int = 5
    camera_recommendation_minimum_score_difference: float = 0.15
    camera_recommendation_minimum_hold_time: float = 5.0
    max_camera_quality_change: float = 0.05
