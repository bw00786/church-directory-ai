"""Key-spec parsing and virtual-key codes for EasyWorship control.

A key spec is a simple string like ``"pagedown"``, ``"ctrl+pagedown"`` or
``"ctrl+alt+c"``. Parsing is platform-agnostic; the Windows virtual-key codes
here are consumed by the keystroke-injection driver.
"""

from typing import List, Tuple

# Windows virtual-key codes.
VK = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}
# Function keys F1..F24.
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x70 + (_i - 1)
# Letters and digits map to their ASCII uppercase code.
for _c in range(ord("a"), ord("z") + 1):
    VK[chr(_c)] = ord(chr(_c).upper())
for _d in range(0, 10):
    VK[str(_d)] = ord(str(_d))

MODIFIERS = {
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
}


def parse_key_spec(spec: str) -> Tuple[List[int], int]:
    """Return (modifier_vks, key_vk) for a spec like "ctrl+pagedown".

    Raises ValueError if the main key is unknown.
    """
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty key spec")

    mods: List[int] = []
    key_vk = None
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
