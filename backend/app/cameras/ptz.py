"""PTZ camera driver abstraction."""

from abc import ABC, abstractmethod
from typing import Optional

from app.cameras.visca import ViscaOverIPClient
from app.logging_config import get_logger

logger = get_logger(__name__)


class PTZDriver(ABC):
    """Abstract base class for PTZ camera drivers."""
    
    @abstractmethod
    async def connect(self, host: str, port: int = 80, username: str | None = None, password: str | None = None) -> bool:
        """Connect to camera.

        Args:
            host: camera host or IP
            port: camera port (typically 80)
            username: optional auth username
            password: optional auth password
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> bool:
        """Disconnect from camera."""
        pass
    
    @abstractmethod
    async def is_connected(self) -> bool:
        """Check if camera is connected."""
        pass
    
    @abstractmethod
    async def get_status(self) -> dict:
        """Get camera status."""
        pass
    
    @abstractmethod
    async def pan(self, angle: float) -> bool:
        """Pan camera."""
        pass
    
    @abstractmethod
    async def tilt(self, angle: float) -> bool:
        """Tilt camera."""
        pass
    
    @abstractmethod
    async def zoom(self, level: float) -> bool:
        """Zoom camera."""
        pass
    
    @abstractmethod
    async def move_to_preset(self, preset_id: int) -> bool:
        """Move to preset position."""
        pass
    
    @abstractmethod
    async def save_preset(self, preset_id: int) -> bool:
        """Save current position as preset."""
        pass


class ONVIFDriver(PTZDriver):
    """ONVIF-based PTZ driver."""
    
    def __init__(self):
        self._camera = None
        self._ptz_service = None
        self._media_service = None
        self._profile_token = None

    async def connect(self, host: str, port: int = 80, username: str | None = None, password: str | None = None) -> bool:
        """Connect to ONVIF camera using `onvif`/`onvif_zeep`.

        This method tries to create the ONVIF client and obtain a PTZ profile.
        If the `onvif` package is not installed, it raises a RuntimeError with
        an actionable message.
        """
        try:
            from onvif import ONVIFCamera
        except Exception as e:
            raise RuntimeError("ONVIF support not installed. Install `onvif_zeep` package.") from e

        try:
            user = username or ""
            pwd = password or ""
            cam = ONVIFCamera(host, port, user, pwd)
            # create services
            self._ptz_service = cam.create_ptz_service()
            self._media_service = cam.create_media_service()
            profiles = self._media_service.GetProfiles()
            if not profiles:
                return False
            self._profile_token = profiles[0].token
            self._camera = cam
            return True
        except Exception:
            return False

    async def disconnect(self) -> bool:
        """Disconnect from ONVIF camera.

        ONVIFCamera objects do not require an explicit close in most libs,
        but we clear local references to help GC.
        """
        try:
            self._camera = None
            self._ptz_service = None
            self._media_service = None
            self._profile_token = None
            return True
        except Exception:
            return False

    async def is_connected(self) -> bool:
        """Check ONVIF connection."""
        return self._camera is not None

    async def get_status(self) -> dict:
        """Get ONVIF camera status."""
        try:
            if not self._ptz_service or not self._profile_token:
                return {"connected": False}
            req = self._ptz_service.create_type('GetStatus')
            req.ProfileToken = self._profile_token
            status = self._ptz_service.GetStatus(req)
            return {"connected": True, "position": status.Position}
        except Exception:
            return {"connected": False}

    async def pan(self, angle: float) -> bool:
        """Pan ONVIF camera."""
        try:
            # ONVIF typically uses relative or continuous moves; we'll try RelativeMove
            req = self._ptz_service.create_type('RelativeMove')
            req.ProfileToken = self._profile_token
            req.Translation = {"PanTilt": {"x": angle, "y": 0.0}, "Zoom": {"x": 0.0}}
            self._ptz_service.RelativeMove(req)
            return True
        except Exception:
            return False

    async def tilt(self, angle: float) -> bool:
        """Tilt ONVIF camera."""
        try:
            req = self._ptz_service.create_type('RelativeMove')
            req.ProfileToken = self._profile_token
            req.Translation = {"PanTilt": {"x": 0.0, "y": angle}, "Zoom": {"x": 0.0}}
            self._ptz_service.RelativeMove(req)
            return True
        except Exception:
            return False

    async def zoom(self, level: float) -> bool:
        """Zoom ONVIF camera."""
        try:
            req = self._ptz_service.create_type('RelativeMove')
            req.ProfileToken = self._profile_token
            req.Translation = {"PanTilt": {"x": 0.0, "y": 0.0}, "Zoom": {"x": level}}
            self._ptz_service.RelativeMove(req)
            return True
        except Exception:
            return False

    async def move_to_preset(self, preset_id: int) -> bool:
        """Move ONVIF camera to preset."""
        try:
            req = self._ptz_service.create_type('GotoPreset')
            req.ProfileToken = self._profile_token
            req.PresetToken = str(preset_id)
            self._ptz_service.GotoPreset(req)
            return True
        except Exception:
            return False

    async def save_preset(self, preset_id: int) -> bool:
        """Save ONVIF preset."""
        try:
            req = self._ptz_service.create_type('SetPreset')
            req.ProfileToken = self._profile_token
            req.PresetName = f"preset_{preset_id}"
            req.PresetToken = str(preset_id)
            self._ptz_service.SetPreset(req)
            return True
        except Exception:
            return False


class PTZOpticsDriver(PTZDriver):
    """Driver for PTZOptics PT-series (G3) cameras.

    Uses VISCA-over-IP as the primary control path (movement, zoom, presets,
    and position inquiries) and the PTZOptics HTTP-CGI interface
    (``/cgi-bin/ptzctrl.cgi``) as a fallback when VISCA is unavailable.

    Pan/tilt values are treated as absolute degrees and zoom as an absolute
    percentage (0-100). Continuous "joystick" control is available via
    :meth:`drive`, :meth:`zoom_drive`, and :meth:`stop`.
    """

    def __init__(self, *, visca_port: int = 1240, use_udp: bool = False, cgi_timeout: float = 5.0):
        self._host: str | None = None
        self._http_port = 80
        self._visca_port = visca_port
        self._use_udp = use_udp
        self._cgi_timeout = cgi_timeout
        self._username: str | None = None
        self._password: str | None = None
        self._use_basic_auth = False
        self._client = None
        self._visca: ViscaOverIPClient | None = None
        self._connected = False

    # -- connection lifecycle -------------------------------------------------
    async def connect(
        self,
        host: str,
        port: int = 80,
        username: str | None = None,
        password: str | None = None,
        visca_port: int | None = None,
        use_udp: bool | None = None,
    ) -> bool:
        """Connect to the camera over VISCA and prepare the HTTP-CGI client."""
        import httpx

        self._host = host
        self._http_port = port
        self._username = username
        self._password = password
        if visca_port is not None:
            self._visca_port = visca_port
        if use_udp is not None:
            self._use_udp = use_udp

        auth = None
        if username:
            auth = httpx.DigestAuth(username, password or "")
        self._client = httpx.AsyncClient(timeout=self._cgi_timeout, auth=auth)

        self._visca = ViscaOverIPClient(host, self._visca_port, use_udp=self._use_udp)
        visca_ok = await self._visca.connect()

        # A harmless stop command doubles as an HTTP-CGI reachability probe.
        http_ok = await self._cgi("ptzcmd", "ptzstop", 1, 1)

        self._connected = bool(visca_ok or http_ok)
        if self._connected:
            logger.info(
                "PTZOptics connected",
                host=host,
                visca=visca_ok,
                http_cgi=http_ok,
                visca_port=self._visca_port,
            )
        else:
            logger.warning("PTZOptics connect failed", host=host)
        return self._connected

    async def disconnect(self) -> bool:
        try:
            if self._visca is not None:
                await self._visca.close()
            if self._client is not None:
                await self._client.aclose()
        except Exception:
            pass
        finally:
            self._visca = None
            self._client = None
            self._connected = False
        return True

    async def is_connected(self) -> bool:
        return self._connected

    # -- HTTP-CGI helper ------------------------------------------------------
    async def _cgi(self, *parts) -> bool:
        """Issue a PTZOptics HTTP-CGI command; returns True on 2xx."""
        if self._client is None or not self._host:
            return False
        import httpx

        query = "&".join(str(p) for p in parts)
        url = f"http://{self._host}:{self._http_port}/cgi-bin/ptzctrl.cgi?{query}"
        try:
            resp = await self._client.get(url)
            if resp.status_code == 401 and self._username and not self._use_basic_auth:
                # Some firmware expects Basic instead of Digest auth.
                self._use_basic_auth = True
                await self._client.aclose()
                self._client = httpx.AsyncClient(
                    timeout=self._cgi_timeout,
                    auth=httpx.BasicAuth(self._username, self._password or ""),
                )
                resp = await self._client.get(url)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.debug("PTZOptics CGI request failed", url=url, error=str(e))
            return False

    def _visca_ready(self) -> bool:
        return self._visca is not None and self._visca.connected

    # -- absolute positioning -------------------------------------------------
    async def move_absolute(
        self,
        pan_deg: Optional[float] = None,
        tilt_deg: Optional[float] = None,
        zoom_pct: Optional[float] = None,
    ) -> bool:
        """Move to an absolute pan/tilt (degrees) and/or zoom (percent)."""
        ok = True

        if pan_deg is not None or tilt_deg is not None:
            if self._visca_ready():
                current = await self._visca.get_pan_tilt()
                cur_pan, cur_tilt = current if current else (0, 0)
                pan_units = (
                    ViscaOverIPClient.pan_deg_to_units(pan_deg)
                    if pan_deg is not None
                    else cur_pan
                )
                tilt_units = (
                    ViscaOverIPClient.tilt_deg_to_units(tilt_deg)
                    if tilt_deg is not None
                    else cur_tilt
                )
                ok = ok and await self._visca.pan_tilt_absolute(pan_units, tilt_units)
            else:
                ok = False

        if zoom_pct is not None:
            if self._visca_ready():
                ok = ok and await self._visca.zoom_absolute(
                    ViscaOverIPClient.zoom_pct_to_pos(zoom_pct)
                )
            else:
                ok = False

        return ok

    async def pan(self, angle: float) -> bool:
        return await self.move_absolute(pan_deg=angle)

    async def tilt(self, angle: float) -> bool:
        return await self.move_absolute(tilt_deg=angle)

    async def zoom(self, level: float) -> bool:
        return await self.move_absolute(zoom_pct=level)

    # -- continuous ("joystick") control -------------------------------------
    async def drive(
        self, pan_dir: int, tilt_dir: int, pan_speed: int = 12, tilt_speed: int = 12
    ) -> bool:
        """Start continuous pan/tilt. Directions are -1/0/1."""
        if self._visca_ready():
            return await self._visca.pan_tilt_drive(pan_dir, tilt_dir, pan_speed, tilt_speed)
        cmd = {
            (0, 1): "up",
            (0, -1): "down",
            (-1, 0): "left",
            (1, 0): "right",
            (-1, 1): "leftup",
            (1, 1): "rightup",
            (-1, -1): "leftdown",
            (1, -1): "rightdown",
            (0, 0): "ptzstop",
        }.get((pan_dir, tilt_dir), "ptzstop")
        return await self._cgi("ptzcmd", cmd, pan_speed, tilt_speed)

    async def zoom_drive(self, direction: int, speed: int = 4) -> bool:
        """Start continuous zoom. direction: -1 wide, 0 stop, 1 tele."""
        if self._visca_ready():
            return await self._visca.zoom_drive(direction, speed)
        cmd = {1: "zoomin", -1: "zoomout", 0: "zoomstop"}.get(direction, "zoomstop")
        return await self._cgi("ptzcmd", cmd, speed)

    async def stop(self) -> bool:
        """Stop all pan/tilt and zoom motion."""
        if self._visca_ready():
            ok = await self._visca.pan_tilt_stop()
            ok = await self._visca.zoom_drive(0) and ok
            return ok
        stopped = await self._cgi("ptzcmd", "ptzstop", 1, 1)
        return await self._cgi("ptzcmd", "zoomstop", 0) and stopped

    # -- presets --------------------------------------------------------------
    async def move_to_preset(self, preset_id: int) -> bool:
        if self._visca_ready() and await self._visca.preset_recall(preset_id):
            return True
        return await self._cgi("ptzcmd", "poscall", preset_id)

    async def save_preset(self, preset_id: int) -> bool:
        if self._visca_ready() and await self._visca.preset_set(preset_id):
            return True
        return await self._cgi("ptzcmd", "posset", preset_id)

    # -- status ---------------------------------------------------------------
    async def get_status(self) -> dict:
        status: dict = {"connected": self._connected, "pan": 0.0, "tilt": 0.0, "zoom": 0.0}
        if self._visca_ready():
            pan_tilt = await self._visca.get_pan_tilt()
            if pan_tilt is not None:
                status["pan"] = ViscaOverIPClient.units_to_pan_deg(pan_tilt[0])
                status["tilt"] = ViscaOverIPClient.units_to_tilt_deg(pan_tilt[1])
            zoom_pos = await self._visca.get_zoom()
            if zoom_pos is not None:
                status["zoom"] = ViscaOverIPClient.pos_to_zoom_pct(zoom_pos)
        return status


# Backwards-compatible alias for earlier imports.
PZOpticDriver = PTZOpticsDriver
