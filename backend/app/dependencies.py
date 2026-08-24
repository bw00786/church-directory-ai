"""FastAPI dependency injection setup."""

from typing import AsyncGenerator

from app.atem.service import AtemService
from app.identity.audio_capture import AudioCaptureService, audio_capture_service
from app.identity.service import IdentityService, identity_service
from app.memory.production_memory import MemoryManager, memory_manager
from app.policy.engine import PolicyEngine
from app.vision.manager import VisionManager, vision_manager


_atem_service: AtemService | None = None
_policy_engine: PolicyEngine | None = None
_vision_manager: VisionManager | None = None


async def get_atem_service() -> AsyncGenerator[AtemService, None]:
    """Get ATEM service dependency."""
    yield get_atem_service_instance()


def get_atem_service_instance() -> AtemService:
    """Get the shared ATEM service singleton (non-generator accessor)."""
    global _atem_service

    if _atem_service is None:
        _atem_service = AtemService()

    return _atem_service


async def get_policy_engine() -> AsyncGenerator[PolicyEngine, None]:
    """Get policy engine dependency."""
    yield get_policy_engine_instance()


def get_policy_engine_instance() -> PolicyEngine:
    """Get the shared PolicyEngine singleton (non-generator accessor)."""
    global _policy_engine

    if _policy_engine is None:
        from app.config import settings
        _policy_engine = PolicyEngine(
            autonomous_camera_switching=settings.autonomous_camera_switching,
            autonomous_transitions=settings.autonomous_transitions,
            autonomous_stream_start=settings.autonomous_stream_start,
            autonomous_stream_stop=settings.autonomous_stream_stop,
            autonomous_recording=settings.autonomous_recording,
            action_confidence_thresholds={
                "camera_change": settings.confidence_camera_change,
                "slide_change": settings.confidence_slide_change,
                "atem_transition": settings.confidence_atem_transition,
            },
        )

    return _policy_engine


async def get_vision_manager() -> AsyncGenerator[VisionManager, None]:
    """Get VisionManager dependency."""
    global _vision_manager

    if _vision_manager is None:
        _vision_manager = vision_manager
    
    yield _vision_manager


async def get_identity_service() -> AsyncGenerator[IdentityService, None]:
    """Get IdentityService dependency (face/voice recognition + roster memory)."""
    yield identity_service


async def get_audio_capture_service() -> AsyncGenerator[AudioCaptureService, None]:
    """Get AudioCaptureService dependency (local mic/line-in voice diarization)."""
    yield audio_capture_service


async def get_memory_manager() -> AsyncGenerator[MemoryManager, None]:
    """Get MemoryManager dependency (production memory / past-service search)."""
    yield memory_manager
