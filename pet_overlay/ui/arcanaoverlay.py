"""«Marcar en el juego» (Arcanas › Subir, off unless you turn it on): a click-through window over the game's Arcana
window that frames, right on the game's grid, the cards AionDex picked to feed for the next level - blue the ones to
register, green the ones already registered. arcanagrid reads where the grid is and which part of your bag is on
screen (after you scroll, or with a kind filter) from the game's pixels, every READ_EVERY; the window is left out of
screen captures (SetWindowDisplayAffinity), so those reads keep seeing the game under it. It draws only while the grid
is in sight; it never clicks nor moves the mouse."""
import ctypes
import logging
import time
from ctypes import wintypes

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import arcanagrid
from ..arcana import TYPES
from ..minimap import game_window, grab
from ..sniffer import game_pids
from ..winput import real_pixels
from ..i18n import tr

log = logging.getLogger(__name__)
_user32 = ctypes.windll.user32
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.IsIconic.argtypes = [wintypes.HWND]

WDA_EXCLUDEFROMCAPTURE = 0x11
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
READ_EVERY = 400                 # ms between two looks at the grid
HIDE_AFTER = 1.5                 # seconds without the grid before it hides (no blinking)
TO_ADD = QColor("#2ec0f2")
ADDED = QColor("#7bd88f")


class ArcanaOverlay(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.ctl = ctl
        self._orders = []                # the lists the grid may show: your bag, then each kind alone (a filter)
        self._picks = []                 # [Card] to feed for the next level
        self._boxes = []                 # [(x, y, w, h, registered)] in this window's real pixels
        self._off = 0                    # picks not on screen (scroll)
        self._area = None                # (left, top, right, bottom) on screen, real pixels
        self._height = 0
        self._lost_since = None
        self._hwnd = None
        self._font = QFont("Segoe UI", 10, QFont.Bold)
        self._timer = QTimer(self, interval=READ_EVERY, timeout=self._tick)

    def set_picks(self, orders, picks):
        """Frame these cards in the game's grid. orders: the lists your bag may show in the grid's order (with and
        without the card being enhanced, when it is in the bag); each kind alone is tried too (a filter)."""
        self._orders = [o for order in orders for o in [order] + [[c for c in order if c.kind == k] for k in TYPES]]
        self._picks = list(picks)
        if self._picks:
            if not self._timer.isActive():
                self._timer.start()
            self._tick()
        else:
            self.stop()

    def stop(self):
        self._timer.stop()
        self._boxes = []
        self.hide()

    def _tick(self):
        try:
            self._read()
        except Exception:                      # noqa: BLE001 - a bad read must not stop the app
            log.exception("could not read the game's Arcana grid")
            self._boxes = []
        if self._boxes or self._off:
            self._lost_since = None
            if not self.isVisible():
                self.show()
                self._exclude_from_capture()
            self._place()
            self.update()
        else:
            self._lost_since = self._lost_since or time.time()
            if time.time() - self._lost_since > HIDE_AFTER:
                self.hide()

    def _read(self):
        real_pixels()
        found = game_window(game_pids())
        self._boxes, self._off = [], 0
        if found is None or _user32.IsIconic(found[0]):
            return
        left, top, right, bottom = found[1]
        height = bottom - top
        x0, _, px, _, _, _ = arcanagrid.geometry(height)
        width = min(right - left, int(x0 + 4 * px + 0.02 * height))
        if width <= 0 or height <= 0:
            return
        img = grab((left, top, left + width, bottom)).convert("RGB")
        pixels = img.load()
        rows = arcanagrid.read_grid(lambda x, y: pixels[x, y], width, height)
        order, first = arcanagrid.best_view(self._orders, rows)
        if order is None:
            return
        boxes = arcanagrid.cells_to_frame(order, rows, first, [c for c in self._picks if c in order], height)
        self._boxes = boxes
        self._off = max(0, len(self._picks) - len(boxes))
        self._area, self._height = (left, top, left + width, bottom), height

    def _place(self):
        if self._area is None:
            return
        hwnd = self._hwnd or int(self.winId())
        left, top, right, bottom = self._area
        _user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), left, top, right - left, bottom - top,
                             SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _exclude_from_capture(self):
        hwnd = int(self.winId())
        if hwnd != self._hwnd:
            self._hwnd = hwnd
            if not _user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
                log.warning("the Arcana marks cannot be left out of screen captures")
            self.ctl.minimap.ignore.add(hwnd)

    def paintEvent(self, _event):
        if self._area is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dpr = self.devicePixelRatioF()
        p.scale(1 / dpr, 1 / dpr)                           # real pixels, like the reads
        line = max(3.0, self._height / 360)
        for x, y, w, h, registered in self._boxes:
            color = ADDED if registered else TO_ADD
            p.setPen(QPen(color, line))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(x - line, y - line, w + 2 * line, h + 2 * line), line * 2, line * 2)
        text = tr("AionDex: registra las marcadas") + (tr(" · {0} más abajo en la lista", self._off) if self._off else "")
        font = QFont(self._font)
        font.setPixelSize(max(12, int(self._height / 75)))
        p.setFont(font)
        x0, y0, _, _, _, _ = arcanagrid.geometry(self._height)
        box = QRectF(x0, y0 - self._height * 0.045, self._height * 0.6, self._height * 0.035)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 18, 38, 220))
        width = p.fontMetrics().horizontalAdvance(text) + font.pixelSize()
        p.drawRoundedRect(QRectF(box.x(), box.y(), width, box.height()), 6, 6)
        p.setPen(TO_ADD)
        p.drawText(QRectF(box.x(), box.y(), width, box.height()), Qt.AlignCenter, text)
