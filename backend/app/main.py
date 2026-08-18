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


@asynccontextmanager
vision_manager = VisionManager()

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

    # Automatic camera registration from config (camera_1)
    try:
        if global_settings.camera_1_host:
            camera_service.register_camera(
                1,
                global_settings.camera_1_host,
                port=global_settings.camera_1_port,
                username=global_settings.camera_1_username,
                password=global_settings.camera_1_password,
            )
            # attempt connect in background
            try:
                import asyncio
                asyncio.create_task(camera_service.connect_camera(1))
            except Exception:
                pass
    except Exception:
        logger.exception("Failed to auto-register camera from config")
    
    yield
    
    if settings.vision_enabled:
        await vision_manager.stop()
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
    # - Ollama availability
    
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
