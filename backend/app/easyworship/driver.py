"""EasyWorship control drivers.

Preferred: EasyWorship 7.3+'s native Remote Control TCP protocol
(``RemoteProtocolDriver`` in :mod:`app.easyworship.remote_protocol`), which
needs no window focus, supports absolute jumps and reports the live slide
position back. Fallbacks inject keystrokes into the EasyWorship window, either
locally (``KeyboardDriver``) or through a small HTTP agent on the EW machine
(``HttpAgentDriver``). ``MockDriver`` is used for development and tests.
"""

import sys
from abc import ABC, abstractmethod
from typing import Optional

from app.config import settings
from app.logging_config import get_logger

from .keys import parse_key_sequence

logger = get_logger(__name__)


def key_spec_for(action: str) -> str:
    """Return the configured key spec for a named action."""
    mapping = {
        "next_slide": settings.ew_key_next_slide,
        "prev_slide": settings.ew_key_prev_slide,
        "next_item": settings.ew_key_next_item,
        "prev_item": settings.ew_key_prev_item,
        "clear": settings.ew_key_clear,
        "logo": settings.ew_key_logo,
        "black": settings.ew_key_black,
        "live": settings.ew_key_live,
    }
    return mapping.get(action, "")


class EasyWorshipDriver(ABC):
    """Abstract EasyWorship control driver."""

    @abstractmethod
    async def connect(self) -> bool: ...

    @abstractmethod
    async def is_connected(self) -> bool: ...

    @abstractmethod
    async def send_action(self, action: str) -> bool:
        """Send a named action (e.g. ``next_slide``). Returns success."""

    @property
    def supports_confirmation(self) -> bool:
        """True when the driver reads EasyWorship state back (see remote_protocol)."""
        return False


class MockDriver(EasyWorshipDriver):
    """No-op driver that records the last action (dev / non-Windows)."""

    def __init__(self):
        self.last_action: Optional[str] = None

    async def connect(self) -> bool:
        logger.info("EasyWorship mock driver connected")
        return True

    async def is_connected(self) -> bool:
        return True

    async def send_action(self, action: str) -> bool:
        self.last_action = action
        logger.info("EasyWorship (mock) action", action=action)
        return True


class KeyboardDriver(EasyWorshipDriver):
    """Windows driver that injects keystrokes into the EasyWorship window."""
    def __init__(self, window_title: Optional[str] = None, send_mode: Optional[str] = None):
        self.window_title = window_title or settings.easyworship_window_title
        self.send_mode = send_mode or settings.easyworship_send_mode
        self._hwnd = None

    async def connect(self) -> bool:
        self._hwnd = self._find_window()
        if self._hwnd is None:
            logger.warning("EasyWorship window not found", title=self.window_title)
            return False
        logger.info("EasyWorship window found", title=self.window_title)
        return True

    async def is_connected(self) -> bool:
        return self._hwnd is not None

    async def send_action(self, action: str) -> bool:
        spec = key_spec_for(action)
        if not spec:
            logger.warning("No key mapping for EasyWorship action", action=action)
            return False
        try:
            sequence = parse_key_sequence(spec)
        except ValueError as e:
            logger.warning("Bad EasyWorship key spec", action=action, error=str(e))
            return False

        # Re-resolve the window in case it was reopened.
        if self._hwnd is None:
            self._hwnd = self._find_window()
        if self._hwnd is None:
            return False

        try:
            for mods, key_vk in sequence:
                if self.send_mode == "postmessage":
                    self._post_keys(self._hwnd, mods, key_vk)
                else:
                    self._foreground_send(self._hwnd, mods, key_vk)
            logger.info("EasyWorship action sent", action=action, spec=spec)
            return True
        except Exception as e:
            logger.warning("EasyWorship keystroke failed", action=action, error=str(e))
            return False

    # -- Windows internals ----------------------------------------------------
    def _find_window(self):
        if sys.platform != "win32":
            return None
        import ctypes

        user32 = ctypes.windll.user32
        target = self.window_title.lower()
        found = {"hwnd": None}

        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
        )

        def _cb(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if target in buf.value.lower() and user32.IsWindowVisible(hwnd):
                    found["hwnd"] = hwnd
                    return False
            return True

        user32.EnumWindows(EnumWindowsProc(_cb), 0)
        return found["hwnd"]

    def _foreground_send(self, hwnd, mods, key_vk):
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetForegroundWindow(hwnd)

        KEYEVENTF_KEYUP = 0x0002
        for m in mods:
            user32.keybd_event(m, 0, 0, 0)
        user32.keybd_event(key_vk, 0, 0, 0)
        user32.keybd_event(key_vk, 0, KEYEVENTF_KEYUP, 0)
        for m in reversed(mods):
            user32.keybd_event(m, 0, KEYEVENTF_KEYUP, 0)

    def _post_keys(self, hwnd, mods, key_vk):
        import ctypes

        user32 = ctypes.windll.user32
        WM_KEYDOWN = 0x0100
        WM_KEYUP = 0x0101
        # Modifiers via PostMessage are unreliable; send the main key only.
        for m in mods:
            user32.PostMessageW(hwnd, WM_KEYDOWN, m, 0)
        user32.PostMessageW(hwnd, WM_KEYDOWN, key_vk, 0)
        user32.PostMessageW(hwnd, WM_KEYUP, key_vk, 0)
        for m in reversed(mods):
            user32.PostMessageW(hwnd, WM_KEYUP, m, 0)


class HttpAgentDriver(EasyWorshipDriver):
    """Controls EasyWorship via a remote agent running on the EW machine."""

    def __init__(self, agent_url: Optional[str] = None):
        self.agent_url = (agent_url or settings.easyworship_agent_url or "").rstrip("/")
        self._connected = False

    async def connect(self) -> bool:
        if not self.agent_url:
            return False
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.agent_url}/health")
                self._connected = resp.status_code == 200
        except Exception as e:
            logger.warning("EasyWorship agent unreachable", url=self.agent_url, error=str(e))
            self._connected = False
        return self._connected

    async def is_connected(self) -> bool:
        return self._connected

    async def send_action(self, action: str) -> bool:
        if not self.agent_url:
            return False
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"{self.agent_url}/action/{action}")
            ok = resp.status_code in (200, 204)
            logger.info("EasyWorship agent action", action=action, ok=ok)
            return ok
        except Exception as e:
            logger.warning("EasyWorship agent action failed", action=action, error=str(e))
            return False


def _zeroconf_available() -> bool:
    try:
        import zeroconf  # noqa: F401
    except ImportError:
        return False
    return True


def build_driver(mock: Optional[bool] = None) -> EasyWorshipDriver:
    """Select a driver per ``settings.easyworship_driver`` (see config.py)."""
    use_mock = settings.enable_mock_easyworship if mock is None else mock
    if use_mock:
        return MockDriver()

    from .remote_protocol import RemoteProtocolDriver

    mode = (settings.easyworship_driver or "auto").lower()
    if mode == "mock":
        return MockDriver()
    if mode == "remote":
        return RemoteProtocolDriver()
    if mode == "agent":
        return HttpAgentDriver()
    if mode == "keyboard":
        return KeyboardDriver() if sys.platform == "win32" else MockDriver()
    if mode != "auto":
        logger.warning("Unknown EASYWORSHIP_DRIVER; using auto", value=mode)

    if settings.easyworship_remote_host:
        return RemoteProtocolDriver()
    if settings.easyworship_agent_url:
        return HttpAgentDriver()
    if _zeroconf_available():
        return RemoteProtocolDriver()
    if sys.platform == "win32":
        return KeyboardDriver()
    return MockDriver()
