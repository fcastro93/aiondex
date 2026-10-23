"""Real screen pixels for the threads that read the game's window (the minimap's calibration, the Macro window reader):
the game's window and the screenshots are in real pixels, whatever the scaling of each monitor."""
import ctypes

_user32 = ctypes.windll.user32
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]


def real_pixels():
    """Make this thread work in real screen pixels (as the screenshots do)."""
    _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))      # per monitor aware v2
