"""FastAPI application initialization."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.logging_config import setup_logging, get_logger
from app.api import register_routes
from app.vision.manager import VisionManager
from app.cameras.service import camera_service
from app.config import settings as global_settings

# Setup logging
setup_logging(log_format=settings.log_format, log_level=settings.log_level)
logger = get_logger(__name__)

vision_manager = VisionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Startup
    logger.info("Church Production Director starting...", version="1.0.0")
    logger.info(
        "configuration",
        atem_ip=settings.atem_ip,
        mock_atem_enabled=settings.enable_mock_atem,
        ai_director_enabled=settings.enable_ai_director,
        vision_enabled=settings.vision_enabled,
    )

    if settings.vision_enabled:
        await vision_manager.start()

    # Start the mixer meter feed (listen-only) for song-end detection.
    try:
        from app.mixer.service import mixer_service
        await mixer_service.start()
    except Exception:
        logger.exception("Failed to start mixer service")

    # Start local microphone/line-in capture for real-audio voice diarization
    # (disabled by default; requires ENABLE_AUDIO_CAPTURE=true and hardware).
    try:
        from app.identity.audio_capture import audio_capture_service
        await audio_capture_service.start()
    except Exception:
        logger.exception("Failed to start audio capture service")

    # Start recording director cue activity into production memory.
    try:
        from app.memory.event_recorder import memory_event_recorder
        await memory_event_recorder.start()
    except Exception:
        logger.exception("Failed to start memory event recorder")

    # Start the scheduled auto-start loop for the service director.
    try:
        from app.director.scheduler import service_scheduler
        await service_scheduler.start()
    except Exception:
        logger.exception("Failed to start service scheduler")

    # Connect EasyWorship slide control (mock unless on the Windows desktop).
    try:
        from app.easyworship.service import easyworship_service
        await easyworship_service.start()
    except Exception:
        logger.exception("Failed to start EasyWorship service")

    # Automatic camera registration from config (camera_1)
    try:
        if global_settings.camera_1_host:
            camera_service.register_camera(
                1,
                global_settings.camera_1_host,
                port=global_settings.camera_1_port,
                username=global_settings.camera_1_username,
                password=global_settings.camera_1_password,
                name=global_settings.camera_1_name,
                visca_port=global_settings.camera_1_visca_port,
                visca_udp=global_settings.camera_1_visca_udp,
            )
            # attempt connect in background
            try:
                import asyncio
                asyncio.create_task(camera_service.connect_camera(1))
            except Exception:
                logger.exception("Failed to start camera connect task")
    except Exception:
        logger.exception("Failed to auto-register camera from config")
    
    yield
    
    if settings.vision_enabled:
        await vision_manager.stop()
    try:
        from app.mixer.service import mixer_service
        await mixer_service.stop()
    except Exception:
        logger.exception("Failed to stop mixer service")
    try:
        from app.identity.audio_capture import audio_capture_service
        await audio_capture_service.stop()
    except Exception:
        logger.exception("Failed to stop audio capture service")
    try:
        from app.memory.event_recorder import memory_event_recorder
        await memory_event_recorder.stop()
    except Exception:
        logger.exception("Failed to stop memory event recorder")
    try:
        from app.director.scheduler import service_scheduler
        await service_scheduler.stop()
    except Exception:
        logger.exception("Failed to stop service scheduler")
    logger.info("Church Production Director shutting down...")


# Create FastAPI app
app = FastAPI(
    title="Church Production Director API",
    description="AI-assisted church worship production control",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health endpoint
@app.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "healthy", "version": "1.0.0"}


@app.get("/health/live")
async def health_live():
    """Liveness probe - application is running."""
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready():
    """Readiness probe - application can handle requests."""
    from app.atem.service import AtemService
    from app.dependencies import get_atem_service
    
    # Could add checks for:
    # - Database connectivity
    # - ATEM bridge availability
    # - Anthropic API availability
    
    return {
        "status": "ready",
        "checks": {
            "api": "ok",
        }
    }


# Root endpoint
@app.get("/")
async def root():
    """API root endpoint."""
    return {
        "name": "Church Production Director",
        "version": "1.0.0",
        "docs": "/docs",
    }


# Register all routes
register_routes(app)

logger.info("FastAPI app created and configured", routes_registered=True)
