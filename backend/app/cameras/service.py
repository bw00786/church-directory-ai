"""Camera service implementation."""

from typing import List, Optional

from app.logging_config import get_logger
from app.cameras.ptz import PTZOpticsDriver

logger = get_logger(__name__)


class CameraService:
    """Service for controlling PTZ cameras.

    This simple service manages driver instances in memory and provides
    helper methods to register cameras and invoke PTZ actions.
    """

    def __init__(self):
        """Initialize camera service."""
        # camera_id -> metadata dict (host, port, username, password, name, visca_port)
        self._cameras: dict[int, dict] = {}
        # camera_id -> driver instance
        self._drivers: dict[int, PTZOpticsDriver] = {}
        logger.info("Camera service initialized")

    def register_camera(
        self,
        camera_id: int,
        host: str,
        port: int = 80,
        username: str | None = None,
        password: str | None = None,
        name: str | None = None,
        visca_port: int = 1240,
        visca_udp: bool = False,
    ) -> None:
        """Register a camera with network details.

        This does not connect immediately; call `connect_camera` to open a
        network session.
        """
        self._cameras[camera_id] = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "name": name or f"Camera {camera_id}",
            "visca_port": visca_port,
            "visca_udp": visca_udp,
        }
        logger.info("Camera registered", camera_id=camera_id, host=host, visca_port=visca_port)

    def list_cameras(self) -> List[dict]:
        """List registered cameras with connection status."""
        cameras = []
        for camera_id, meta in self._cameras.items():
            cameras.append(
                {
                    "camera_id": camera_id,
                    "name": meta.get("name", f"Camera {camera_id}"),
                    "host": meta.get("host"),
                    "connected": camera_id in self._drivers,
                }
            )
        return cameras

    async def connect_camera(self, camera_id: int) -> bool:
        """Create driver and connect to the camera."""
        meta = self._cameras.get(camera_id)
        if not meta:
            logger.warning("Camera not registered", camera_id=camera_id)
            return False

        driver = PTZOpticsDriver(
            visca_port=meta.get("visca_port", 1240),
            use_udp=meta.get("visca_udp", False),
        )
        ok = await driver.connect(
            meta["host"],
            meta.get("port", 80),
            meta.get("username"),
            meta.get("password"),
        )
        if ok:
            self._drivers[camera_id] = driver
            logger.info("Camera connected", camera_id=camera_id)
        else:
            logger.warning("Failed to connect camera", camera_id=camera_id)
        return ok

    async def get_camera_state(self, camera_id: int) -> dict:
        """Get current state of a camera.

        Args:
            camera_id: Camera ID

        Returns:
            Camera state dictionary.
        """
        logger.debug("Getting camera state", camera_id=camera_id)
        meta = self._cameras.get(camera_id, {})
        name = meta.get("name", f"Camera {camera_id}")
        driver = self._drivers.get(camera_id)
        if driver:
            status = await driver.get_status()
            return {"camera_id": camera_id, "name": name, **status}

        # Not connected / no driver
        return {"camera_id": camera_id, "name": name, "connected": False, "pan": 0.0, "tilt": 0.0, "zoom": 0.0}

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

    async def save_preset(self, camera_id: int, preset_id: int) -> bool:
        """Save the camera's current position as a preset."""
        logger.info("Saving camera preset", camera_id=camera_id, preset_id=preset_id)
        driver = self._drivers.get(camera_id)
        if not driver:
            logger.warning("No driver for camera", camera_id=camera_id)
            return False
        return await driver.save_preset(preset_id)

    async def drive_camera(
        self,
        camera_id: int,
        pan_dir: int = 0,
        tilt_dir: int = 0,
        zoom_dir: int = 0,
        pan_speed: int = 12,
        tilt_speed: int = 12,
        zoom_speed: int = 4,
    ) -> bool:
        """Start continuous (joystick-style) movement. Directions are -1/0/1."""
        driver = self._drivers.get(camera_id)
        if not driver:
            logger.warning("No driver for camera", camera_id=camera_id)
            return False
        ok = await driver.drive(pan_dir, tilt_dir, pan_speed, tilt_speed)
        if zoom_dir != 0:
            ok = await driver.zoom_drive(zoom_dir, zoom_speed) and ok
        return ok

    async def stop_camera(self, camera_id: int) -> bool:
        """Stop all camera motion."""
        driver = self._drivers.get(camera_id)
        if not driver:
            logger.warning("No driver for camera", camera_id=camera_id)
            return False
        return await driver.stop()

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

        return await driver.move_absolute(pan_deg=pan, tilt_deg=tilt, zoom_pct=zoom)


# Module-level singleton for dependency injection and startup wiring
camera_service = CameraService()
