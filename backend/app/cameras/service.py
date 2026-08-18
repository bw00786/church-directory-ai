"""Camera service implementation."""

from typing import List, Optional

from app.logging_config import get_logger
from app.cameras.ptz import PZOpticDriver

logger = get_logger(__name__)


class CameraService:
    """Service for controlling PTZ cameras.

    This simple service manages driver instances in memory and provides
    helper methods to register cameras and invoke PTZ actions.
    """

    def __init__(self):
        """Initialize camera service."""
        # camera_id -> metadata dict (host, port, username, password)
        self._cameras: dict[int, dict] = {}
        # camera_id -> driver instance
        self._drivers: dict[int, PZOpticDriver] = {}
        logger.info("Camera service initialized")

    def register_camera(self, camera_id: int, host: str, port: int = 80, username: str | None = None, password: str | None = None) -> None:
        """Register a camera with network details.

        This does not connect immediately; call `connect_camera` to open a
        network session.
        """
        self._cameras[camera_id] = {"host": host, "port": port, "username": username, "password": password}
        logger.info("Camera registered", camera_id=camera_id, host=host)

    async def connect_camera(self, camera_id: int) -> bool:
        """Create driver and connect to the camera."""
        meta = self._cameras.get(camera_id)
        if not meta:
            logger.warning("Camera not registered", camera_id=camera_id)
            return False

        driver = PZOpticDriver()
        ok = await driver.connect(meta["host"], meta.get("port", 80), meta.get("username"), meta.get("password"))
        if ok:
            self._drivers[camera_id] = driver
            logger.info("Camera connected", camera_id=camera_id)
        else:
            logger.warning("Failed to connect camera", camera_id=camera_id)
        return ok


    # Module-level singleton for dependency injection and startup wiring
    camera_service = CameraService()

    async def get_camera_state(self, camera_id: int) -> dict:
        """Get current state of a camera.

        Args:
            camera_id: Camera ID

        Returns:
            Camera state dictionary.
        """
        logger.debug("Getting camera state", camera_id=camera_id)
        driver = self._drivers.get(camera_id)
        if driver:
            status = await driver.get_status()
            return {"camera_id": camera_id, "name": f"Camera {camera_id}", **status}

        # Not connected / no driver
        meta = self._cameras.get(camera_id, {})
        return {"camera_id": camera_id, "name": meta.get("name", f"Camera {camera_id}"), "connected": False, "pan": 0.0, "tilt": 0.0, "zoom": 0.0}

    async def move_to_preset(self, camera_id: int, preset_id: int) -> bool:
        """Move camera to a preset position.

        Args:
            camera_id: Camera ID
            preset_id: Preset ID

        Returns:
            True if successful.
        """
        logger.info("Moving camera to preset", camera_id=camera_id, preset_id=preset_id)
        driver = self._drivers.get(camera_id)
        if not driver:
            logger.warning("No driver for camera", camera_id=camera_id)
            return False
        return await driver.move_to_preset(preset_id)

    async def move_camera(
        self,
        camera_id: int,
        pan: Optional[float] = None,
        tilt: Optional[float] = None,
        zoom: Optional[float] = None,
    ) -> bool:
        """Move a PTZ camera.

        Args:
            camera_id: Camera ID
            pan: Pan angle (degrees)
            tilt: Tilt angle (degrees)
            zoom: Zoom level (0-100%)

        Returns:
            True if successful.
        """
        logger.info(
            "Moving camera",
            camera_id=camera_id,
            pan=pan,
            tilt=tilt,
            zoom=zoom,
        )
        driver = self._drivers.get(camera_id)
        if not driver:
            logger.warning("No driver for camera", camera_id=camera_id)
            return False

        ok = True
        if pan is not None:
            ok = ok and await driver.pan(pan)
        if tilt is not None:
            ok = ok and await driver.tilt(tilt)
        if zoom is not None:
            ok = ok and await driver.zoom(zoom)
        return ok
