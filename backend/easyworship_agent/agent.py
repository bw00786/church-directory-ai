"""Standalone EasyWorship keystroke agent (run on the Windows EW desktop).

FALLBACK ONLY. EasyWorship 7.3+ has a native Remote Control TCP protocol that
the backend speaks directly (EASYWORSHIP_DRIVER=remote; enable it in EasyWorship
under Edit > Options > Advanced > "Enable Remote Control"). Use this agent only
when that is unavailable.

A tiny HTTP service that injects keystrokes into the EasyWorship window, so the
church-production backend can control slides remotely when it runs on a
different machine. Self-contained: only the Python standard library + ctypes.

Run on the Windows machine that runs EasyWorship:

    python agent.py                 # listens on 0.0.0.0:8091

Then point the backend at it:

    EASYWORSHIP_AGENT_URL=http://<this-machine-ip>:8091
    EASYWORSHIP_AGENT_TOKEN=<same value as EW_AGENT_TOKEN below>
    ENABLE_MOCK_EASYWORSHIP=false

Security (this agent injects keystrokes into the EasyWorship desktop):
    EW_AGENT_TOKEN        shared secret; when set, POST /action/* must carry a
                          matching X-Agent-Token header (else 401). The backend
                          sends it from EASYWORSHIP_AGENT_TOKEN. Strongly
                          recommended whenever EW_AGENT_HOST is not 127.0.0.1.
    EW_AGENT_ALLOWED_IPS  comma-separated client IPs allowed to connect. Loopback
                          is always allowed. Without EW_AGENT_TOKEN, requests
                          from any other address are refused (403).
    EW_AGENT_HOST         bind address (default 0.0.0.0 so the backend machine
                          can reach it; set 127.0.0.1 to keep it local-only).

Endpoints:
    GET  /health          -> {"ok": true}
    POST /action/{name}   -> inject the configured key(s) for {name}

Actions: next_slide, prev_slide, next_item, prev_item, clear, logo, black, live.
Key mappings (EasyWorship 7.3+ defaults) and window title come from environment
variables (see DEFAULTS). Comma-separated specs are sent in sequence.
"""

import ctypes
import ipaddress
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Action -> default key spec. Override via EW_KEY_<ACTION> env vars.
# EW 7.3+: Down/Up = slide, Right/Left = select schedule item, Page Down = Go Live,
# Ctrl+C clear, Ctrl+L logo, Ctrl+B black. The Live pane must have focus.
DEFAULTS = {
    "next_slide": "down",
    "prev_slide": "up",
    "next_item": "right,pagedown",
    "prev_item": "left,pagedown",
    "clear": "ctrl+c",
    "logo": "ctrl+l",
    "black": "ctrl+b",
    "live": "pagedown",
}

WINDOW_TITLE = os.environ.get("EASYWORSHIP_WINDOW_TITLE", "EasyWorship")
SEND_MODE = os.environ.get("EASYWORSHIP_SEND_MODE", "foreground")
HOST = os.environ.get("EW_AGENT_HOST", "0.0.0.0")
PORT = int(os.environ.get("EW_AGENT_PORT", "8091"))
TOKEN = os.environ.get("EW_AGENT_TOKEN", "").strip()
ALLOWED_IPS = frozenset(
    ip.strip() for ip in os.environ.get("EW_AGENT_ALLOWED_IPS", "").split(",") if ip.strip()
)
TOKEN_HEADER = "X-Agent-Token"

VK = {
    "enter": 0x0D, "esc": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
}
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x70 + (_i - 1)
for _c in range(ord("a"), ord("z") + 1):
    VK[chr(_c)] = ord(chr(_c).upper())
for _d in range(0, 10):
    VK[str(_d)] = ord(str(_d))
MODIFIERS = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}


def is_loopback(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return ip.lower() == "localhost"
    mapped = getattr(addr, "ipv4_mapped", None)
    return addr.is_loopback or (mapped is not None and mapped.is_loopback)


def client_allowed(ip: str) -> bool:
    """Loopback always; otherwise the allowlist if set, else only when a token guards POSTs."""
    if is_loopback(ip):
        return True
    if ALLOWED_IPS:
        return ip in ALLOWED_IPS
    return bool(TOKEN)


def token_ok(presented) -> bool:
    """True when no token is configured, or the presented header matches it."""
    if not TOKEN:
        return True
    if not presented:
        return False
    return secrets.compare_digest(str(presented).encode("utf-8"), TOKEN.encode("utf-8"))


def key_spec_for(action: str) -> str:
    return os.environ.get(f"EW_KEY_{action.upper()}", DEFAULTS.get(action, ""))


def parse_key_spec(spec: str):
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    mods, key_vk = [], None
    for part in parts:
        if part in MODIFIERS:
            mods.append(MODIFIERS[part])
        elif part in VK:
            key_vk = VK[part]
        else:
            raise ValueError(f"unknown key: {part}")
    if key_vk is None:
        raise ValueError(f"no main key in spec: {spec}")
    return mods, key_vk


def parse_key_sequence(spec: str):
    steps = [s for s in (p.strip() for p in spec.split(",")) if s]
    if not steps:
        raise ValueError("empty key sequence")
    return [parse_key_spec(step) for step in steps]


def find_window():
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    target = WINDOW_TITLE.lower()
    found = {"hwnd": None}
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if target in buf.value.lower() and user32.IsWindowVisible(hwnd):
                found["hwnd"] = hwnd
                return False
        return True

    user32.EnumWindows(proc(_cb), 0)
    return found["hwnd"]


def inject(action: str) -> bool:
    spec = key_spec_for(action)
    if not spec:
        return False
    sequence = parse_key_sequence(spec)
    hwnd = find_window()
    if hwnd is None:
        return False
    user32 = ctypes.windll.user32
    KEYUP = 0x0002
    for mods, key_vk in sequence:
        if SEND_MODE == "postmessage":
            WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
            for m in mods:
                user32.PostMessageW(hwnd, WM_KEYDOWN, m, 0)
            user32.PostMessageW(hwnd, WM_KEYDOWN, key_vk, 0)
            user32.PostMessageW(hwnd, WM_KEYUP, key_vk, 0)
            for m in reversed(mods):
                user32.PostMessageW(hwnd, WM_KEYUP, m, 0)
        else:
            user32.SetForegroundWindow(hwnd)
            for m in mods:
                user32.keybd_event(m, 0, 0, 0)
            user32.keybd_event(key_vk, 0, 0, 0)
            user32.keybd_event(key_vk, 0, KEYUP, 0)
            for m in reversed(mods):
                user32.keybd_event(m, 0, KEYUP, 0)
    return True


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _client_ok(self) -> bool:
        if client_allowed(self.client_address[0]):
            return True
        self._send(403, {"ok": False, "error": "client address not allowed"})
        return False

    def do_GET(self):
        if not self._client_ok():
            return
        if self.path == "/health":
            self._send(200, {"ok": True, "auth": bool(TOKEN)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._client_ok():
            return
        if not token_ok(self.headers.get(TOKEN_HEADER)):
            self._send(401, {"ok": False, "error": f"missing or invalid {TOKEN_HEADER}"})
            return
        if self.path.startswith("/action/"):
            action = self.path[len("/action/"):]
            if action not in DEFAULTS:
                self._send(400, {"ok": False, "error": f"unknown action: {action}"})
                return
            try:
                ok = inject(action)
            except Exception as e:  # noqa: BLE001
                self._send(500, {"ok": False, "error": str(e)})
                return
            self._send(200 if ok else 502, {"ok": ok, "action": action})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args):  # quieter console
        pass


def main():
    if sys.platform != "win32":
        print("Warning: not on Windows; keystroke injection is a no-op.")
    if not TOKEN:
        print("=" * 72)
        print("WARNING: EW_AGENT_TOKEN is not set. Keystroke injection is UNAUTHENTICATED;")
        if ALLOWED_IPS:
            print(f"         only loopback and EW_AGENT_ALLOWED_IPS={','.join(sorted(ALLOWED_IPS))} may connect.")
        else:
            print("         only loopback clients may connect (set EW_AGENT_ALLOWED_IPS or,")
            print("         better, EW_AGENT_TOKEN to let the backend machine in).")
        print("=" * 72)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"EasyWorship agent listening on http://{HOST}:{PORT} (window: {WINDOW_TITLE!r}, token: {'set' if TOKEN else 'NONE'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
