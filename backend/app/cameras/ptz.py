"""PTZ camera driver abstraction."""

from abc import ABC, abstractmethod
from typing import Optional


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


class PZOpticDriver(PTZDriver):
    """PZ Optic-specific PTZ driver.

    Current implementation delegates to ONVIF when available. If your
    PZOptics model supports ONVIF (many do), this will provide basic PTZ
    and preset functionality. Vendor-specific HTTP/SDK extensions can be
    added later if needed.
    """

    def __init__(self, http_ptz_path: str | None = None, http_preset_path: str | None = None):
        self._onvif = ONVIFDriver()
        self._http_driver = None
        self._http_ptz_path = http_ptz_path
        self._http_preset_path = http_preset_path

    async def connect(self, host: str, port: int = 80, username: str | None = None, password: str | None = None) -> bool:
        """Attempt to connect using ONVIF first; if that fails, try HTTP fallback."""
        # Try ONVIF first
        try:
            ok = await self._onvif.connect(host, port, username, password)
            if ok:
                return True
        except RuntimeError:
            # ONVIF lib not installed — fall through to HTTP
            pass

        # HTTP fallback driver
        from httpx import AsyncClient

        class _HttpDriver:
            def __init__(self, base_url: str, auth: tuple | None = None, ptz_path: str | None = None, preset_path: str | None = None):
                self.base_url = base_url.rstrip('/')
                self.auth = auth
                self.ptz_path = ptz_path or '/'
                self.preset_path = preset_path or '/'
                self._client = AsyncClient(timeout=5.0)

            async def connect(self):
                try:
                    r = await self._client.get(self.base_url, auth=self.auth)
                    return r.status_code == 200
                except Exception:
                    return False

            async def get_status(self):
                try:
                    r = await self._client.get(self.base_url, auth=self.auth)
                    return {'connected': r.status_code == 200, 'status_code': r.status_code}
                except Exception:
                    return {'connected': False}

            async def goto_preset(self, preset_id: int):
                # best-effort: try common query param names used by some cameras
                try:
                    url = f"{self.base_url}{self.preset_path}"
                    params = {'call': preset_id, 'goto': preset_id, 'preset': preset_id}
                    # try each param until one succeeds
                    for k, v in params.items():
                        r = await self._client.get(url, params={k: v}, auth=self.auth)
                        if r.status_code in (200, 204):
                            return True
                    return False
                except Exception:
                    return False

            async def move(self, pan: float | None = None, tilt: float | None = None, zoom: float | None = None):
                try:
                    url = f"{self.base_url}{self.ptz_path}"
                    params = {}
                    if pan is not None:
                        params.update({'pan': pan})
                    if tilt is not None:
                        params.update({'tilt': tilt})
                    if zoom is not None:
                        params.update({'zoom': zoom})
                    if not params:
                        return False
                    r = await self._client.get(url, params=params, auth=self.auth)
                    return r.status_code in (200, 204)
                except Exception:
                    return False

        base = f"http://{host}:{port}"
        auth = (username, password) if username and password else None
        httpd = _HttpDriver(base, auth=auth, ptz_path=self._http_ptz_path, preset_path=self._http_preset_path)
        ok = await httpd.connect()
        if ok:
            self._http_driver = httpd
            return True
        return False

    async def disconnect(self) -> bool:
        if await self._onvif.is_connected():
            return await self._onvif.disconnect()
        if self._http_driver:
            try:
                await self._http_driver._client.aclose()
            except Exception:
                pass
            self._http_driver = None
            return True
        return False

    async def is_connected(self) -> bool:
        if await self._onvif.is_connected():
            return True
        return self._http_driver is not None

    async def get_status(self) -> dict:
        if await self._onvif.is_connected():
            return await self._onvif.get_status()
        if self._http_driver:
            return await self._http_driver.get_status()
        return {"connected": False}

    async def pan(self, angle: float) -> bool:
        if await self._onvif.is_connected():
            return await self._onvif.pan(angle)
        if self._http_driver:
            return await self._http_driver.move(pan=angle)
        return False

    async def tilt(self, angle: float) -> bool:
        if await self._onvif.is_connected():
            return await self._onvif.tilt(angle)
        if self._http_driver:
            return await self._http_driver.move(tilt=angle)
        return False

    async def zoom(self, level: float) -> bool:
        if await self._onvif.is_connected():
            return await self._onvif.zoom(level)
        if self._http_driver:
            return await self._http_driver.move(zoom=level)
        return False

    async def move_to_preset(self, preset_id: int) -> bool:
        if await self._onvif.is_connected():
            return await self._onvif.move_to_preset(preset_id)
        if self._http_driver:
            return await self._http_driver.goto_preset(preset_id)
        return False

    async def save_preset(self, preset_id: int) -> bool:
        if await self._onvif.is_connected():
            return await self._onvif.save_preset(preset_id)
        # Many HTTP endpoints do not support setting presets via simple CGI; skip
        return False
