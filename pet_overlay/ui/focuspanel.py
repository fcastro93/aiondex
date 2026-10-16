"""Modo Focus of the pets: the pets you are farming listed right under the game's quest list (under the minimap),
drawn like the game's quests so it looks part of it: the dark band with the gold title (the pet, its count), the
lighter line under it with a key-like chip (what you got this session) and what to kill. Above them, in the Abyss,
the world bosses and siege cores around you (bigfoes.py): their name, HP % and a red HP bar, how far, or how long
since they went out of sight.

Where: minimap.py reads where the quest list ends (quest_area, every 2 s; it grows and shrinks with your quests).
The window lets clicks through and screen captures leave it out (SetWindowDisplayAffinity): the quest reader does
not read it as a quest, and it is not in your screenshots or streams. It hides when the game is minimized or
something covers the minimap."""
import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..minimap import covered
from .widgets import pet_pixmap

log = logging.getLogger(__name__)
_user32 = ctypes.windll.user32
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.IsIconic.argtypes = [wintypes.HWND]

ROWS = 5                         # pets listed
REFERENCE = 497.0                # the minimap's width at 4K: the quest list's sizes below are for it
TITLE_H, SUB_H, GAP = 50, 34, 8  # a pet's band, its line under it and the space to the next (4K pixels)
GOLD = QColor(255, 211, 95)      # the game's quest title (measured on the user's screenshot)
FOE = QColor(255, 128, 112)      # a boss's name
HP = QColor(229, 57, 80)         # its HP bar
WHITE = QColor(242, 242, 240)
BAND = QColor(10, 10, 10, 160)   # the title's band: the world behind at ~37%
SUB_BAND = QColor(10, 10, 10, 85)
FLASH = QColor(120, 92, 20, 190)  # a pet that just got a kill
FLASH_FOR = 1.6
HIDE_AFTER = 1.5
TICK_MS = 250
FRESH = 12.0                     # seconds the quest list's end is trusted without a new read
FONT = "Candara"                 # closest to the game's (flared sans); its old-style numbers are switched off


@dataclass
class FocusRow:
    pet_id: int
    title: str                   # the pet
    count: str                   # "72/75", "MAX"
    ratio: float
    sub: str                     # "Mata: ..."
    chip: str = ""               # "+32" this session
    maxed: bool = False
    flash_at: float = 0.0
    kind: str = "pet"            # "pet" | "boss" (a boss / siege core: red, a thicker HP bar, no portrait)
    faded: bool = False          # out of sight or dead


def _font(px, bold=True):
    font = QFont(FONT)
    font.setPixelSize(max(8, round(px)))
    font.setBold(bold)
    try:
        font.setFeature(QFont.Tag("lnum"), 1)                  # lining numbers, like the game's
    except (AttributeError, TypeError):
        pass
    return font


class FocusPanel(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.ctl = ctl
        self.rows = []
        self.pet_rows = []               # the pets (Modo Focus on), set on each overlay refresh
        self.boss_rows = lambda: []      # the bosses around you (asked every tick: their HP moves)
        self._flash = {}                 # pet id -> when it got a kill
        self._rect = None                # where it is (real pixels)
        self._k = 1.0
        self._hwnd = None
        self._wrong_since = None
        self._hidden_game = False
        self._looked_at = 0.0
        self._icons = {}
        self._timer = QTimer(self, interval=TICK_MS, timeout=self._tick)

    # --- on / off ---
    def start(self):
        self._timer.start()
        self._tick()

    def stop(self):
        self._timer.stop()
        self.hide()

    def set_rows(self, rows, flashed=()):
        now = time.time()
        for pid in flashed:
            self._flash[pid] = now
        self.pet_rows = rows[:ROWS]
        self._compose()

    def _compose(self):
        rows = self.boss_rows() + self.pet_rows
        if rows != self.rows:
            resize = len(rows) != len(self.rows)
            self.rows = rows
            if resize:
                self._fit()
            self.update()

    # --- where ---
    def _tick(self):
        ctl = self.ctl
        now = time.time()
        self._compose()
        if not self.rows:
            self.hide()
            return
        ctl.minimap_wanted_until = max(getattr(ctl, "minimap_wanted_until", 0), now + 3)
        area = ctl.minimap.quest_area
        if area and now - self._looked_at > 0.5:
            self._looked_at = now
            self._hidden_game = self._game_hidden(area)
        if not self.rows or area is None or now - area[3] > FRESH or self._hidden_game:
            self._wrong_since = self._wrong_since or now
            if now - self._wrong_since > HIDE_AFTER or not self.rows or area is None:
                self.hide()
            return
        self._wrong_since = None
        left, top, right, _ = area
        self._k = (right - left) / REFERENCE
        k = self._k
        rect = (round(left - 2 * k), round(top + 6 * k), round(right + 2 * k), 0)
        self._place(rect)
        if not self.isVisible():
            self.show()
            self._exclude_from_capture()
            self._place(rect, force=True)
        if any(now - t < FLASH_FOR for t in self._flash.values()):
            self.update()

    def _fit(self):
        if self._rect:
            self._place(self._rect, force=True)

    def _place(self, rect, force=False):
        k = self._k
        height = round(len(self.rows) * (TITLE_H + SUB_H + GAP) * k)
        rect = (rect[0], rect[1], rect[2], rect[1] + height)
        if not force and self._rect and max(abs(a - b) for a, b in zip(rect, self._rect)) < 2:
            return
        self._rect = rect
        left, top, right, bottom = rect
        if right - left > 0 and bottom - top > 0:
            _user32.SetWindowPos(int(self.winId()), wintypes.HWND(-1), left, top, right - left, bottom - top,
                                 0x0010 | 0x0040)
        self.update()

    def _game_hidden(self, area):
        game = self.ctl.minimap.game_hwnd
        if not game or _user32.IsIconic(game):
            return True
        client = self.ctl.minimap.client
        box = self.ctl.minimap.minimap_box(client) if client else None
        try:
            return bool(box) and covered(game, box, self.ctl.minimap.ignore)
        except OSError:
            return False

    def _exclude_from_capture(self):
        hwnd = int(self.winId())
        if hwnd != self._hwnd:
            self._hwnd = hwnd
            if not _user32.SetWindowDisplayAffinity(hwnd, 0x11):
                log.warning("the pets' Modo Focus cannot be left out of screen captures")
            self.ctl.minimap.ignore.add(hwnd)

    # --- drawing ---
    def paintEvent(self, _event):
        if not self.rows or not self._rect:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        dpr = self.devicePixelRatioF()
        p.scale(1 / dpr, 1 / dpr)                           # real pixels, like the game
        k = self._k
        width = self._rect[2] - self._rect[0]
        title_font, count_font, sub_font, chip_font = _font(31 * k), _font(27 * k), _font(23 * k), _font(18 * k)
        now = time.time()
        y = 0.0
        for row in self.rows:
            boss = row.kind == "boss"
            p.setOpacity(0.55 if row.faded else 1.0)
            flash = not boss and now - self._flash.get(row.pet_id, 0) < FLASH_FOR
            band = QRectF(0, y, width, TITLE_H * k)
            p.fillRect(band, FLASH if flash else BAND)
            thick = (7 if boss else 3) * k                                 # progress (HP), along the band
            bar = QRectF(0, band.bottom() - thick, width, thick)
            p.fillRect(bar, QColor(255, 255, 255, 40 if boss else 28))
            fill = HP if boss else QColor(GOLD.red(), GOLD.green(), GOLD.blue(), 200)
            p.fillRect(QRectF(bar.left(), bar.top(), bar.width() * max(0.0, min(1.0, row.ratio)), bar.height()), fill)
            if boss:
                self._diamond(p, QPointF(32 * k, y + TITLE_H * k / 2 - 2 * k), 15 * k)
            else:
                self._icon(p, row, QPointF(32 * k, y + TITLE_H * k / 2), 20 * k)
            count_w = QFontMetricsF(count_font).horizontalAdvance(row.count) + 12 * k
            self._text(p, QRectF(72 * k, y, width - 72 * k - count_w, TITLE_H * k - (thick if boss else 0)),
                       row.title, title_font, FOE if boss else GOLD)
            self._text(p, QRectF(width - count_w, y, count_w - 10 * k, TITLE_H * k - (thick if boss else 0)),
                       row.count, count_font, GOLD if row.maxed else WHITE, Qt.AlignRight)
            sub = QRectF(0, band.bottom(), width, SUB_H * k)
            p.fillRect(sub, SUB_BAND)
            if row.chip:                                        # like the game's key hint ("Alt + 1")
                metrics = QFontMetricsF(chip_font)
                chip = QRectF(6 * k, sub.top() + 6 * k, metrics.horizontalAdvance(row.chip) + 10 * k, 22 * k)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(235, 235, 235))
                p.drawRoundedRect(chip, 3 * k, 3 * k)
                p.setFont(chip_font)
                p.setPen(QColor(20, 20, 20))
                p.drawText(chip, Qt.AlignCenter, row.chip)
            self._text(p, QRectF(72 * k, sub.top(), width - 80 * k, SUB_H * k), row.sub, sub_font, WHITE)
            p.setOpacity(1.0)
            y += (TITLE_H + SUB_H + GAP) * k
        p.end()

    def _diamond(self, p, centre, r):
        """A boss's mark, like the game's quest diamond, in red."""
        shape = QPainterPath()
        shape.moveTo(centre + QPointF(0, -r))
        shape.lineTo(centre + QPointF(r * 0.8, 0))
        shape.lineTo(centre + QPointF(0, r))
        shape.lineTo(centre + QPointF(-r * 0.8, 0))
        shape.closeSubpath()
        p.setPen(QPen(QColor(255, 220, 200, 230), 1.4 * self._k))
        p.setBrush(HP)
        p.drawPath(shape)

    def _text(self, p, rect, text, font, color, align=Qt.AlignLeft):
        metrics = QFontMetricsF(font)
        text = metrics.elidedText(text, Qt.ElideRight, rect.width())
        x = rect.left() if align == Qt.AlignLeft else rect.right() - metrics.horizontalAdvance(text)
        base = rect.top() + (rect.height() + metrics.ascent() - metrics.descent()) / 2
        path = QPainterPath()
        path.addText(QPointF(x, base), font, text)
        p.setPen(QPen(QColor(0, 0, 0, 150), max(1.5, 2.2 * self._k)))       # the game's soft dark edge
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)

    def _icon(self, p, row, centre, r):
        size = round(2 * r)
        key = (row.pet_id, size)
        if key not in self._icons:
            self._icons[key] = pet_pixmap(self.ctl.icons_dir, row.pet_id, size, 1.0)
        pix = self._icons[key]
        circle = QRectF(centre.x() - r, centre.y() - r, 2 * r, 2 * r)
        path = QPainterPath()
        path.addEllipse(circle)
        p.fillPath(path, QColor(0, 0, 0, 120))
        if pix:
            p.save()
            p.setClipPath(path)
            p.drawPixmap(circle.toRect(), pix)
            p.restore()
        p.setPen(QPen(GOLD if row.maxed else QColor(220, 196, 130, 220), 1.6 * self._k))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(circle)
