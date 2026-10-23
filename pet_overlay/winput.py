"""Mouse input for the build macro (buildmacro.py): moves, clicks and drags in real screen pixels with SendInput,
watched by a Guard that stops everything as soon as you take the mouse, press Esc or the game leaves the front."""
import ctypes
import time
from ctypes import wintypes
from .i18n import tr

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x8000, 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
VK_ESCAPE = 0x1B
MOVED = 6                        # pixels the cursor may differ from where it was put before it counts as yours
HOVER = 0.12                     # seconds over a button before pressing it


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _Input(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MouseInput), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


_user32 = ctypes.windll.user32
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
SW_RESTORE = 9
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
MK_LBUTTON = 0x0001
INPUT, MESSAGE = "input", "message"      # how a click reaches the game: SendInput, or posted to its window


class Interrupted(Exception):
    """You took over (mouse, Esc) or the game is not in front any more: the macro stops where it is."""


def real_pixels():
    """Make this thread work in real screen pixels (as the screenshots do)."""
    _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))      # per monitor aware v2


def esc_down():
    return bool(_user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)


def _pid(hwnd):
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def game_in_front(hwnd):
    """The window in front is the game's (any of its windows: the same process)."""
    front = _user32.GetForegroundWindow()
    return bool(front) and (front == hwnd or _pid(front) == _pid(hwnd))


def bring_to_front(hwnd, wait=1.5):
    """Put the game in front (restored if minimized) -> True once it is. Windows lets the process in front do it:
    AionDex is, right after you click «Aplicar en el juego»."""
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)
    end = time.time() + wait
    while time.time() < end:
        if game_in_front(hwnd):
            return True
        time.sleep(0.05)
    return game_in_front(hwnd)


def cursor():
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _send(flags, x=0, y=0):
    left = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = max(1, _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1)
    height = max(1, _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) - 1)
    event = _Input(type=INPUT_MOUSE)
    event.mi = _MouseInput(round((x - left) * 65535 / width), round((y - top) * 65535 / height), 0,
                           flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0)
    _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input))


class Guard:
    """Every move goes through here: it checks before each one that the cursor is still where the macro left it, that
    Esc is not down and that the game is in front. client: the game's client rect on screen; a move that starts outside
    it (the cursor on another monitor, by the AionDex window) first jumps into it: the game, once in front, keeps the
    cursor inside its window, so a glide from outside would stop at its edge (2026-10-22: the user's Builds window on a
    second monitor, the game on the main one, stopped as «moviste el ratón» after the first step)."""

    def __init__(self, game_hwnd, client=None, method=INPUT):
        self.game = game_hwnd
        self.client = client
        self.method = method             # INPUT (SendInput, like a mouse) or MESSAGE (the click posted to the game
        #                                  window: what an input filter that drops injected clicks does not see)
        self.at = None

    def check(self):
        if esc_down():
            raise Interrupted("Esc")
        if self.game and not game_in_front(self.game):
            raise Interrupted(tr("el juego dejó de estar al frente"))
        if self.at is not None:
            x, y = cursor()
            if abs(x - self.at[0]) > MOVED or abs(y - self.at[1]) > MOVED:
                raise Interrupted(tr("moviste el ratón"))

    def _inside(self, x, y):
        if not self.client:
            return True
        left, top, right, bottom = self.client
        return left <= x < right and top <= y < bottom

    def move(self, x, y, steps=8, pause=0.012):
        self.check()
        x0, y0 = cursor()
        if not self._inside(x0, y0) and self._inside(x, y):         # from another monitor: jump in first
            _user32.SetCursorPos(x, y)
            _send(MOUSEEVENTF_MOVE, x, y)
            self.at = (x, y)
            time.sleep(0.05)
            self.check()
            return
        for i in range(1, steps + 1):
            px, py = round(x0 + (x - x0) * i / steps), round(y0 + (y - y0) * i / steps)
            _send(MOUSEEVENTF_MOVE, px, py)
            self.at = (px, py)
            time.sleep(pause)
        self.check()

    def click(self, x, y, button="left", hold=0.09):
        """Like a hand: onto the spot, a moment over it (the game's buttons react to a press only once the cursor
        is over them, a frame or two later), then press and release."""
        self.move(x, y)
        _send(MOUSEEVENTF_MOVE, x + 1, y)                # a small wiggle over it: the hover the game waits for
        time.sleep(0.03)
        _send(MOUSEEVENTF_MOVE, x, y)
        time.sleep(HOVER)
        self.check()
        if self.method == MESSAGE and button == "left" and self.game and self.client:
            spot = self._spot(x, y)
            _user32.PostMessageW(self.game, WM_MOUSEMOVE, 0, spot)
            time.sleep(0.03)
            _user32.PostMessageW(self.game, WM_LBUTTONDOWN, MK_LBUTTON, spot)
            time.sleep(hold)
            _user32.PostMessageW(self.game, WM_LBUTTONUP, 0, spot)
            time.sleep(0.1)
            return
        down, up = ((MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP) if button == "left" else
                    (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP))
        _send(down | MOUSEEVENTF_MOVE, x, y)
        time.sleep(hold)
        _send(up | MOUSEEVENTF_MOVE, x, y)
        time.sleep(0.1)

    def _spot(self, x, y):
        return ((y - self.client[1]) & 0xFFFF) << 16 | ((x - self.client[0]) & 0xFFFF)

    def drag(self, x0, y0, x1, y1, steps=18):
        if self.method == MESSAGE and self.game and self.client:
            self.move(x0, y0)
            _user32.PostMessageW(self.game, WM_LBUTTONDOWN, MK_LBUTTON, self._spot(x0, y0))
            time.sleep(0.12)
            for i in range(1, steps + 1):
                x, y = round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps)
                _send(MOUSEEVENTF_MOVE, x, y)
                self.at = (x, y)
                _user32.PostMessageW(self.game, WM_MOUSEMOVE, MK_LBUTTON, self._spot(x, y))
                time.sleep(0.016)
            time.sleep(0.08)
            _user32.PostMessageW(self.game, WM_LBUTTONUP, 0, self._spot(x1, y1))
            time.sleep(0.12)
            return
        self.move(x0, y0)
        _send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_MOVE, x0, y0)
        time.sleep(0.12)
        self.move(x1, y1, steps=steps, pause=0.016)
        time.sleep(0.08)
        _send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_MOVE, x1, y1)
        time.sleep(0.12)
