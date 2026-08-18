"""Standalone EasyWorship control agent (run on the Windows EW desktop).

A tiny HTTP service that injects keystrokes into the EasyWorship window, so the
church-production backend can control slides remotely when it runs on a
different machine. Self-contained: only the Python standard library + ctypes.

Run on the Windows machine that runs EasyWorship:

    python agent.py                 # listens on 0.0.0.0:8091

Then point the backend at it:

    EASYWORSHIP_AGENT_URL=http://<this-machine-ip>:8091
    ENABLE_MOCK_EASYWORSHIP=false

Endpoints:
    GET  /health          -> {"ok": true}
    POST /action/{name}   -> inject the configured key for {name}

Actions: next_slide, prev_slide, next_item, prev_item, clear, logo, black, live.
Key mappings and window title come from environment variables (see DEFAULTS).
"""

import ctypes
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Action -> default key spec. Override via EW_KEY_<ACTION> env vars.
DEFAULTS = {
    "next_slide": "pagedown",
    "prev_slide": "pageup",
    "next_item": "ctrl+pagedown",
    "prev_item": "ctrl+pageup",
    "clear": "f5",
    "logo": "f6",
    "black": "f7",
    "live": "f9",
}

WINDOW_TITLE = os.environ.get("EASYWORSHIP_WINDOW_TITLE", "EasyWorship")
SEND_MODE = os.environ.get("EASYWORSHIP_SEND_MODE", "foreground")
HOST = os.environ.get("EW_AGENT_HOST", "0.0.0.0")
PORT = int(os.environ.get("EW_AGENT_PORT", "8091"))

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
    mods, key_vk = parse_key_spec(spec)
    hwnd = find_window()
    if hwnd is None:
        return False
    user32 = ctypes.windll.user32
    KEYUP = 0x0002
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

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
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
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"EasyWorship agent listening on http://{HOST}:{PORT} (window: {WINDOW_TITLE!r})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
