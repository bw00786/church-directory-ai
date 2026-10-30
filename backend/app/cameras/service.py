"""Camera service implementation."""

import time
from typing import Awaitable, List, Optional, Tuple

from app.events.bus import event_bus
from app.logging_config import get_logger
from app.cameras.ptz import PTZOpticsDriver

logger = get_logger(__name__)

# Service-plan framing roles with a (camera, preset) shot: the rig profile's
# ``roles`` table (app.rig), which with no rig.yaml is exactly the
# camera_role_<role>_camera/_preset settings. Shared by the cue sheet
# (app.director.script), the cue engine, the AI Director's PTZ_SELECT_ROLE and
# the operator assistant so there is a single role -> preset table. Dynamic:
# iterate it (``for role in CAMERA_ROLES``) or ``"pastor" in CAMERA_ROLES``;
# the role names change when a profile is loaded, so do not copy it.
_SETTINGS_ROLES: Tuple[str, ...] = ("pastor", "liturgist", "vocalist", "congregation", "choir", "wide")


def _settings_role_shot(role: str) -> Tuple[Optional[int], Optional[int]]:
    from app.config import settings

    camera_id = getattr(settings, f"camera_role_{role}_camera", None)
    preset_id = getattr(settings, f"camera_role_{role}_preset", None)
    if camera_id is None or preset_id is None:
        return None, None
    return int(camera_id), int(preset_id)


def _profile():
    # An invalid rig.yaml raises RigProfileError here: fail loud, never fall
    # back to a role table the operator did not configure.
    from app.rig import rig

    return rig()


def camera_roles() -> Tuple[str, ...]:
    """Role names that resolve to a shot: the profile's roles plus any
    camera_role_* settings role the profile does not mention."""
    profile = _profile()
    names = list(profile.role_names()) if profile is not None else []
    for role in _SETTINGS_ROLES:
        if role not in names:
            names.append(role)
    return tuple(names)


class _RoleTable:
    """Live, read-only view of :func:`camera_roles` with tuple semantics."""

    def __iter__(self):
        return iter(camera_roles())

    def __contains__(self, role) -> bool:
        return role in camera_roles()

    def __len__(self) -> int:
        return len(camera_roles())

    def __getitem__(self, index):
        return camera_roles()[index]

    def __eq__(self, other) -> bool:
        return tuple(camera_roles()) == tuple(other) if isinstance(other, (tuple, list, _RoleTable)) else NotImplemented

    def __hash__(self) -> int:  # pragma: no cover - identity only
        return id(self)

    def __repr__(self) -> str:
        return f"CAMERA_ROLES{camera_roles()!r}"


CAMERA_ROLES = _RoleTable()


def configured_role_shot(role: str) -> Tuple[Optional[int], Optional[int]]:
    """``(camera_id, preset_id)`` configured for ``role``: the rig profile's
    ``roles`` entry, else the ``camera_role_*`` settings (so a profile that
    omits a role does not regress it), or ``(None, None)`` for an unknown
    role. Configuration only -- no learned override (see
    :func:`resolve_role_shot`)."""
    role = (role or "").strip().lower()
    if not role:
        return None, None
    profile = _profile()
    if profile is not None:
        camera_id, preset_id = profile.role_shot(role)
        if camera_id is not None and preset_id is not None:
            return int(camera_id), int(preset_id)
    return _settings_role_shot(role)


def resolve_role_shot(role: str) -> Tuple[Optional[int], Optional[int], str]:
    """``(camera_id, preset_id, source)`` for ``role``.

    Precedence: the shot the operator actually uses for this role in the
    current part of the service (operator shot learning, ``"operator"``), then
    the preset identity recognition has learned for the role on that camera
    (``"learned"``), then the rig profile / settings table (``"configured"``).
    Unknown roles give ``(None, None, "unknown")``.
    """
    camera_id, preset_id = configured_role_shot(role)
    if camera_id is None:
        return None, None, "unknown"
    try:
        from app.director.operator_shots import operator_shot_history
        from app.domain.service_context import service_context

        preferred = operator_shot_history.preferred_shot(service_context.state, role.strip().lower())
    except Exception:
        logger.warning("Operator shot lookup unavailable", role=role, exc_info=True)
        preferred = None
    if (
        preferred
        and preferred.get("camera_id") is not None
        and preferred.get("preset_id") is not None
        and (int(preferred["camera_id"]), int(preferred["preset_id"])) != (camera_id, preset_id)
    ):
        return int(preferred["camera_id"]), int(preferred["preset_id"]), "operator"
    try:
        from app.identity.service import identity_service

        learned = identity_service.best_preset_for_role(role.strip().lower(), camera_id)
    except Exception:
        logger.warning("Learned role/preset lookup unavailable", role=role, exc_info=True)
        learned = None
    if learned is not None and preset_id is not None and int(learned) != preset_id:
        return camera_id, int(learned), "learned"
    return camera_id, preset_id, "configured"


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
        # camera_id -> last preset_id commanded via move_to_preset
        self._current_preset: dict[int, int] = {}
        # Monotonic time of the last preset recall the *application* issued
        # (cue engine, AI action engine); see note_command / commanded_within.
        self._last_command_at: float = float("-inf")
        logger.info("Camera service initialized")

    # -- command attribution ---------------------------------------------------
    def note_command(self, camera_id: Optional[int] = None, preset_id: Optional[int] = None) -> None:
        """Record that the app itself is about to recall a preset.

        The operator UI (``POST /cameras/{id}/preset/{n}``) does not call this,
        so a preset change seen without a recent note is the operator's own
        reframe (app.director.operator_shots). Mirrors
        ``easyworship_service.commanded_within``.
        """
        self._last_command_at = time.monotonic()
        logger.debug("Camera command noted", camera_id=camera_id, preset_id=preset_id)

    def commanded_within(self, seconds: float) -> bool:
        """Did this app recall a camera preset in the last ``seconds``?"""
        return time.monotonic() - self._last_command_at <= seconds

    def _connection_failed(self, camera_id: int) -> None:
        event_bus.publish({"event": "CAMERA_CONNECTION_FAILED",
                           "payload": {"camera_id": camera_id}})

    async def _command(self, camera_id: int, action: str, command: Awaitable[bool]) -> bool:
        ok = False
        try:
            ok = await command
            return ok
        finally:
            if not ok:
                event_bus.publish({"event": "TOOL_EXECUTION_FAILED",
                                   "payload": {"camera_id": camera_id, "action": action}})

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
            self._connection_failed(camera_id)
            return False

        driver = PTZOpticsDriver(
            visca_port=meta.get("visca_port", 1240),
            use_udp=meta.get("visca_udp", False),
        )
        try:
            ok = await driver.connect(
                meta["host"],
                meta.get("port", 80),
                meta.get("username"),
                meta.get("password"),
            )
        except Exception:
            self._connection_failed(camera_id)
            raise
        if ok:
            self._drivers[camera_id] = driver
            logger.info("Camera connected", camera_id=camera_id)
        else:
            logger.warning("Failed to connect camera", camera_id=camera_id)
            self._connection_failed(camera_id)
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
            self._connection_failed(camera_id)
            return False
        ok = await self._command(camera_id, "camera_move_to_preset", driver.move_to_preset(preset_id))
        if ok:
            self._current_preset[camera_id] = preset_id
        return ok

    def get_current_preset(self, camera_id: int) -> int | None:
        """Last preset_id successfully commanded on this camera, if any."""
        return self._current_preset.get(camera_id)

    async def move_to_role(self, role: str) -> bool:
        """Move to the camera/preset for a service-plan role (e.g. "pastor",
        "liturgist", "vocalist", "congregation", "choir", "wide", or any role
        the rig profile defines) -- the profile's ``roles`` table / the
        app.config camera_role_* settings, or the preset identity recognition
        has learned for the role (:func:`resolve_role_shot`). This is the same
        lookup the cue sheet and cue engine use, so the AI Director's
        PTZ_SELECT_ROLE and a scripted cue frame the role identically. Keeps
        the AI Director hardware-independent (it selects roles, never raw PTZ
        coordinates or camera ids); see docs/ai-director.md.
        """
        camera_id, preset_id, source = resolve_role_shot(role)
        if camera_id is None or preset_id is None:
            logger.warning("Unknown camera role", role=role)
            return False
        logger.info("Moving camera to role", role=role, camera_id=camera_id, preset_id=preset_id, source=source)
        return await self.move_to_preset(camera_id, preset_id)

    async def save_preset(self, camera_id: int, preset_id: int) -> bool:
        """Save the camera's current position as a preset."""
        logger.info("Saving camera preset", camera_id=camera_id, preset_id=preset_id)
        driver = self._drivers.get(camera_id)
        if not driver:
            logger.warning("No driver for camera", camera_id=camera_id)
            self._connection_failed(camera_id)
            return False
        return await self._command(camera_id, "camera_save_preset", driver.save_preset(preset_id))

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
            self._connection_failed(camera_id)
            return False
        ok = await self._command(
            camera_id, "camera_drive", driver.drive(pan_dir, tilt_dir, pan_speed, tilt_speed)
        )
        if zoom_dir != 0:
            ok = await self._command(
                camera_id, "camera_zoom", driver.zoom_drive(zoom_dir, zoom_speed)
            ) and ok
        return ok

    async def stop_camera(self, camera_id: int) -> bool:
        """Stop all camera motion."""
        driver = self._drivers.get(camera_id)
        if not driver:
            logger.warning("No driver for camera", camera_id=camera_id)
            self._connection_failed(camera_id)
            return False
        return await self._command(camera_id, "camera_stop", driver.stop())

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
            self._connection_failed(camera_id)
            return False

        return await self._command(
            camera_id, "camera_move_absolute",
            driver.move_absolute(pan_deg=pan, tilt_deg=tilt, zoom_pct=zoom),
        )


def register_from_profile(service: "CameraService", profile=None, *, connect: bool = True) -> List[int]:
    """Register every camera in the rig profile's ``cameras`` list with
    ``service`` and (optionally) start connecting the real ones in the
    background. Returns the registered camera ids.

    Replaces main.py's hard-coded camera_1 block: with no rig.yaml the profile
    holds exactly camera 1 from the ``camera_1_*`` settings, so behaviour is
    unchanged. Cameras without a host are skipped (same as an unset
    CAMERA_1_HOST); ``driver: mock`` cameras are registered but never dialled.
    """
    import asyncio

    if profile is None:
        from app.rig import rig

        profile = rig()
    registered: List[int] = []
    for camera in profile.cameras:
        host = camera.host
        if not host:
            if camera.driver == "mock":
                host = "mock"
            else:
                logger.info("Camera has no host; not registered", camera_id=camera.id, name=camera.name)
                continue
        service.register_camera(
            camera.id,
            host,
            port=camera.port,
            username=camera.resolved_username(),
            password=camera.resolved_password(),
            name=camera.name,
            visca_port=camera.visca_port,
            visca_udp=camera.visca_udp,
        )
        registered.append(camera.id)
        if connect and camera.driver != "mock":
            try:
                asyncio.get_running_loop().create_task(service.connect_camera(camera.id))
            except RuntimeError:
                logger.warning("No running event loop; camera not connected", camera_id=camera.id)
            except Exception:
                logger.exception("Failed to start camera connect task", camera_id=camera.id)
    return registered


# Module-level singleton for dependency injection and startup wiring
camera_service = CameraService()
