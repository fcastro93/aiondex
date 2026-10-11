"""Win32 bits Qt does not expose: click-through toggling and global hotkeys."""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEYS = {**{f"f{i}": 0x6F + i for i in range(1, 13)}, "space": 0x20, "tab": 0x09, "home": 0x24, "end": 0x23,
         "insert": 0x2D, "delete": 0x2E, "pageup": 0x21, "pagedown": 0x22}


def set_click_through(hwnd, enabled):
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_NOACTIVATE
    style = style | WS_EX_TRANSPARENT if enabled else style & ~WS_EX_TRANSPARENT
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)


def parse_hotkey(text):
    """'Alt+Shift+P' -> (modifiers, virtual key) or None."""
    mods, vk = MOD_NOREPEAT, None
    for part in (p.strip().lower() for p in text.split("+")):
        if part in _MODS:
            mods |= _MODS[part]
        elif part in _KEYS:
            vk = _KEYS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            return None
    return (mods, vk) if vk else None


def register_hotkey(hwnd, hotkey_id, text):
    parsed = parse_hotkey(text)
    return bool(parsed) and bool(user32.RegisterHotKey(hwnd, hotkey_id, *parsed))


def unregister_hotkey(hwnd, hotkey_id):
    user32.UnregisterHotKey(hwnd, hotkey_id)


def hotkey_id_from_native(event_type, message):
    """Return the hotkey id if this native event is WM_HOTKEY, else None."""
    if event_type != b"windows_generic_MSG":
        return None
    msg = wintypes.MSG.from_address(int(message))
    return int(msg.wParam) if msg.message == WM_HOTKEY else None
