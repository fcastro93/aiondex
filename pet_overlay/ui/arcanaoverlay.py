"""«Marcar en el juego» (Arcanas › Subir, off unless you turn it on): a click-through window over the game's Arcana
window that frames, right on the game's grid, the cards AionDex picked to feed for the next level - blue the ones to
register, green the ones already registered - and in gold the card to enhance, or the one to put on (Qué hacer's
«Marcarla en el juego», 2.5.5). A thread reads the game's pixels about READ_FPS times a second
(arcanagrid: where the grid is, which part of your bag is on screen after a scroll or with a kind filter), so the
frames move with the list while you scroll; rows half in view are drawn clipped to the list's area. The window is left
out of screen captures (SetWindowDisplayAffinity), so the reads keep seeing the game under it. It never clicks nor
moves the mouse. status (and status_changed) says what it sees, for Arcanas: "off", "searching" (the game in front
but no grid), "black" (the capture comes out black: the game not in front, or exclusive fullscreen), "ok"."""
import ctypes
import logging
import threading
import time
from ctypes import wintypes

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import arcanagrid
from ..arcana import TYPES
from ..minimap import game_window, grab
from ..paths import DATA_DIR
from ..sniffer import game_pids
from ..winput import real_pixels
from ..i18n import tr

log = logging.getLogger(__name__)
_user32 = ctypes.windll.user32
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.GetForegroundWindow.restype = wintypes.HWND

WDA_EXCLUDEFROMCAPTURE = 0x11
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
READ_FPS = 30                    # looks at the grid a second
PAINT_MS = 16
HIDE_AFTER = 1.5                 # seconds without the grid before it hides (no blinking)
TOP_EVERY = 0.5                  # seconds between two "stay over the game" (the game in front can cover it)
DIAGNOSE_AFTER = 6.0             # seconds with the game in front and no grid before one capture is kept to look at
TO_ADD = QColor("#3ad1f5")
ADDED = QColor("#5fdc7c")
TARGET = QColor("#e9c45c")


class ArcanaOverlay(QWidget):
    status_changed = Signal(str, int, int, int)     # status, cards framed, cards above the view, below it

    def __init__(self, ctl):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.ctl = ctl
        self.status = "off"
        self._lock = threading.Lock()
        self._orders = []                # the lists the grid may show: your bag, then each kind alone (a filter)
        self._picks = []                 # [Card] to feed for the next level
        self._target = None              # the Card to enhance or to put on, framed in gold
        self._mode = "feed"              # "feed" (Subir) or "wear" (the one to put on)
        self._seen = None                # (area on screen, client height, boxes, cards off screen) of the last read
        self._shown = None               # the same, as drawn
        self._lost_since = None
        self._searching_since = None
        self._diagnosed = False
        self._run = False
        self._thread = None
        self._hwnd = None
        self._placed = None
        self._topped_at = 0.0
        self._grid = arcanagrid.GridReader()   # finds where your screen has the grid's frames, and keeps it
        self._font = QFont("Segoe UI", 10, QFont.Bold)
        self._timer = QTimer(self, interval=PAINT_MS, timeout=self._tick)

    # --- on / off ---
    def set_picks(self, orders, picks, target=None, mode="feed"):
        """Frame these cards in the game's grid. orders: the lists your bag may show in the grid's order (with and
        without the card being enhanced, when it is in the bag); each kind alone is tried too (a filter). target: the
        card to enhance (when it is in the bag) or, mode "wear", the one to put on, in gold."""
        with self._lock:
            self._orders = [o for order in orders for o in [order] + [[c for c in order if c.kind == k] for k in TYPES]]
            self._picks = list(picks)
            self._target, self._mode = target, mode
        if not picks and target is None:
            self.stop()
            return
        if not self._run:
            self._run = True
            self._thread = threading.Thread(target=self._reader, name="arcana-marks", daemon=True)
            self._thread.start()
            self._timer.start()
            self._set_status("searching")

    def stop(self):
        self._run = False
        self._timer.stop()
        with self._lock:
            self._seen = None
        self._shown = None
        self.hide()
        self._set_status("off")

    def _set_status(self, status, framed=0, above=0, below=0):
        if (status, framed, above, below) != getattr(self, "_last_status", None):
            self._last_status = (status, framed, above, below)
            self.status = status
            self.status_changed.emit(status, framed, above, below)

    # --- the reads, on their own thread ---
    def _reader(self):
        real_pixels()
        pids, pids_at = None, 0.0
        while self._run:
            start = time.time()
            try:
                if start - pids_at > 5:
                    pids, pids_at = game_pids(), start
                result = self._read(pids)
            except Exception:                  # noqa: BLE001 - a bad read must not stop it
                log.exception("could not read the game's Arcana grid")
                result = ("searching", None)
            with self._lock:
                self._seen = result
            time.sleep(max(0.0, 1 / READ_FPS - (time.time() - start)))

    def _read(self, pids):
        import numpy as np
        found = game_window(pids) if pids else None
        if found is None or _user32.IsIconic(found[0]):
            return ("black", None)
        left, top, right, bottom = found[1]
        height = bottom - top
        x0, _, px, _, _, _ = arcanagrid.geometry(height)
        width = min(right - left, int(x0 + 4 * px + 0.02 * height))
        if width <= 0 or height <= 0:
            return ("black", None)
        img = grab((left, top, left + width, bottom))
        a = np.asarray(img.convert("RGB"))
        if int(a[::16, ::16].max()) < 24:                     # nothing but black: the game is not on screen
            return ("black", None)
        rows = self._grid.read(a, height)
        with self._lock:
            orders, picks, target = self._orders, self._picks, self._target
        order, first = arcanagrid.best_view(orders, rows)
        if order is None:
            self._diagnose(found[0], img)
            return ("searching", None)
        self._searching_since = None
        boxes, off = arcanagrid.cells_to_frame(order, rows, first, [c for c in picks if c in order], height,
                                               self._grid.x0)
        gold, gold_off, alike = (arcanagrid.target_cells(order, rows, first, target, height, self._grid.x0)
                                 if target is not None and target in order else ([], (0, 0), 0))
        boxes += [(x, y, w, h, None) for x, y, w, h in gold]
        off = (off[0] + gold_off[0], off[1] + gold_off[1])
        return ("ok", ((left, top, left + width, bottom), height, boxes, off, alike))

    def _diagnose(self, game, img):
        """Once a session: the game in front for a while and no grid found -> keep that capture (diagnostics), so the
        reads can be fixed for your screen."""
        if _user32.GetForegroundWindow() != game:
            self._searching_since = None
            return
        now = time.time()
        self._searching_since = self._searching_since or now
        if self._diagnosed or now - self._searching_since < DIAGNOSE_AFTER:
            return
        self._diagnosed = True
        try:
            folder = DATA_DIR / "diagnostics"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / time.strftime("arcana-marks-%Y%m%d-%H%M%S.png")
            img.save(path)
            log.info("Arcana marks: no grid found with the game in front; kept %s", path)
        except OSError:
            log.exception("could not keep the Arcana marks capture")

    # --- drawing, on the GUI thread ---
    def _tick(self):
        with self._lock:
            seen = self._seen
        if seen is None:
            return
        status, data = seen
        if status == "ok":
            self._lost_since = None
            self._shown = data
            area, _, boxes, off, _ = data
            self._set_status("ok", len(boxes), *off)
            if not self.isVisible():
                self.show()
                self._exclude_from_capture()
            if area != self._placed or time.time() - self._topped_at > TOP_EVERY:
                self._place(area)
            self.update()
            return
        self._lost_since = self._lost_since or time.time()
        if time.time() - self._lost_since > HIDE_AFTER:
            self._shown = None
            self.hide()
            self._set_status(status)

    def _place(self, area):
        hwnd = self._hwnd or int(self.winId())
        left, top, right, bottom = area
        if _user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), left, top, right - left, bottom - top,
                                SWP_NOACTIVATE | SWP_SHOWWINDOW):
            self._placed = area
        self._topped_at = time.time()

    def _exclude_from_capture(self):
        hwnd = int(self.winId())
        if hwnd != self._hwnd:
            self._hwnd = hwnd
            if not _user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
                log.warning("the Arcana marks cannot be left out of screen captures")
            self.ctl.minimap.ignore.add(hwnd)

    def paintEvent(self, _event):
        if not self._shown:
            return
        area, height, boxes, off, alike = self._shown
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dpr = self.devicePixelRatioF()
        p.scale(1 / dpr, 1 / dpr)                           # real pixels, like the reads
        top, bottom = arcanagrid.view(height)
        line = max(5.0, height / 260)                       # a thick frame (8 px at 4K)
        x0, y0, _, _, _, _ = arcanagrid.geometry(height)
        p.save()
        p.setClipRect(QRectF(0, top - line * 2, area[2] - area[0], bottom - top + line * 4))
        for x, y, w, h, registered in boxes:
            pad = line * (2 if registered is None else 1)      # the gold one a little outside, around the others
            p.setPen(QPen(TARGET if registered is None else ADDED if registered else TO_ADD, line))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(x - pad, y - pad, w + 2 * pad, h + 2 * pad), line * 1.5, line * 1.5)
        p.restore()
        above, below = off
        gold = any(r is None for *_, r in boxes)
        if self._mode == "wear":
            text = (tr("AionDex: póntela (la dorada)") if alike <= 1 else
                    tr("AionDex: póntela: es una de las {0} doradas (mira sus efectos)", alike))
        else:
            text = (tr("AionDex: la dorada es la que subes · registra las marcadas") if gold else
                    tr("AionDex: registra las marcadas"))
        text += (tr(" · {0} más arriba", above) if above else "") + (tr(" · {0} más abajo", below) if below else "")
        font = QFont(self._font)
        font.setPixelSize(max(12, int(height / 75)))
        p.setFont(font)
        tw = p.fontMetrics().horizontalAdvance(text) + font.pixelSize()
        box = QRectF(x0, y0 - height * 0.05, tw, height * 0.032)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 18, 38, 225))
        p.drawRoundedRect(box, 6, 6)
        p.setPen(TARGET if self._mode == "wear" else TO_ADD)
        p.drawText(box, Qt.AlignCenter, text)
