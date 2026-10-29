"""Radar de cubos: the hidden cubes the game announces around you, on a Dragon Radar.

The device (RadarScreen) is the one from the anime: a dark hand-held body, a round pale-green screen with a square
grid, you in the centre as a small arrow, the cubes as blinking amber blips, a big round button on top that changes
the range (50 m to 1600 m; the wheel does too), and a beep that quickens as you get closer. North is up (the game's
coordinates: +x east, +y south, as the world maps draw them).

The game only announces what is about 100 m from you, so beyond that the radar draws its memory (cubes.py): a cube
that went out of view stays as a hollow blip where it was (it beeps no more), and the places where cubes appeared
before (cube_spots.json, per map) are faint rings: hidden cubes respawn at fixed places, so a ring is a place worth
walking to. The page lists them with their distance and lets you delete the wrong ones.

Two homes: a page of the AionDex window (CubeRadarWindow: the device, what it sees, the «estoy frente a un cubo»
mark and the objects to confirm as cubes, the learned ones) and its round face alone over the game's minimap
(FloatingRadar, from the HUD's menu; 2.5.14). Both read the same tracker (cubes.CubeTracker) and your position
(whereami.OwnPosition).
"""
import ctypes
import logging
import math
import time
from ctypes import wintypes

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient,
                           QRegion)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from .. import APP_NAME
from ..cubes import KNOWN
from ..mapdata import MAP_NAMES
from ..minimap import INSET, covered, fallback_geometry, overlay_geometry
from ..motion import Motion
from ..gathering import GAME_MAPS
from . import theme
from .design import Card, PageHeader, Toggle
from .mapwindow import STYLE
from .minimapoverlay import (CAMERA_FRESH, HOLD, HWND_TOPMOST, KEY_BACK, KEY_FORWARD, KEY_LEFT, KEY_RIGHT,
                             LOOK_EVERY, MOVE_MIN, SWP_NOACTIVATE, SWP_SHOWWINDOW, WDA_EXCLUDEFROMCAPTURE, _user32)
from .theme import Fonts
from .widgets import label
from ..i18n import tr

log = logging.getLogger(__name__)

RANGES = (50, 100, 200, 400, 800, 1600)    # metres from you to the edge of the screen (1600: the whole map)
BEEP_FAR_MS, BEEP_NEAR_MS = 2600, 420   # between beeps with a cube at the edge / right on you
FIX_STALE = 45                          # seconds: an older position is drawn, but doubted on screen
FLOAT_TICK_MS = 50                      # the radar over the minimap follows it and your movement this often
FACE_SHARE = 0.86                       # the face's share of the floating radar's square (its rim around)
_last_beep = [0.0]

BODY_DARK, BODY_LIGHT, BODY_EDGE = QColor("#262a2f"), QColor("#4a5058"), QColor("#0d0f12")
FACE_CENTRE, FACE_EDGE, FACE_RIM = QColor("#dff2a6"), QColor("#9ccb57"), QColor("#2f4f1d")
GRID, GRID_SOFT = QColor("#4a7a2c"), QColor(74, 122, 44, 110)
INK = QColor("#1f3a12")                  # text and «you» on the face
BLIP, BLIP_EDGE, BLIP_GLOW = QColor("#f5b31c"), QColor("#7a4e00"), QColor(245, 179, 28, 90)
LOST = QColor("#c98f14")                 # a cube out of view, where it was (hollow)
SPOT, SPOT_SOFT = QColor("#2f5a1a"), QColor(47, 90, 26, 70)    # a place where a cube appeared before (a ring)
KNOB_LIGHT, KNOB_DARK = QColor("#f2f2ef"), QColor("#9a9da3")

RADAR_STYLE = f"""
QPushButton[mark="true"] {{ background: #3a2d0b; color: {theme.GOLD.name()}; border: 1px solid {theme.GOLD_DARK.name()};
    border-radius: 8px; padding: 9px 14px; font-weight: 600; }}
QPushButton[mark="true"]:hover {{ background: #4a3a10; border-color: {theme.GOLD.name()}; }}
QPushButton[small="true"] {{ background: #1a2747; color: {theme.TEXT.name()}; border: none; border-radius: 6px;
    padding: 3px 9px; }}
QPushButton[small="true"]:hover {{ background: #22345e; }}
QPushButton[ghost="true"] {{ background: transparent; color: {theme.MUTED.name()}; border: 1px solid #2a3444;
    border-radius: 6px; padding: 3px 9px; }}
QPushButton[ghost="true"]:hover {{ color: {theme.TEXT.name()}; border-color: {theme.ACCENT.name()}; }}
"""


def map_name(game_map):
    if game_map is None:
        return tr("mapa desconocido")
    return MAP_NAMES.get(GAME_MAPS.get(game_map, ""), tr("mapa {0}", game_map))


def live_nearest(snap):
    """Metres to the nearest cube in view (the remembered ones do not beep), or None."""
    dists = [c["dist"] for c in snap.get("cubes", []) if "dist" in c and c.get("lost") is None]
    return min(dists) if dists else None


def beat(nearest, reach, player, enabled):
    """One beep now and then while a cube is on screen: slow at the edge, fast on top of it."""
    if not enabled or nearest is None:
        return
    share = max(0.0, min(1.0, nearest / max(reach, 1)))
    gap = (BEEP_NEAR_MS + (BEEP_FAR_MS - BEEP_NEAR_MS) * share) / 1000
    now = time.monotonic()
    if now - _last_beep[0] >= gap:
        _last_beep[0] = now
        player.play("cube_radar")


def paint_face(p, face, reach, snap, fix, sweep, fonts, msg="", see_through=False, you=True):
    """The round screen: rim, pale-green face, grid, sweep, the cubes (blinking amber blips; an arrow on the rim
    with its distance for the ones out of reach), you in the centre and the range. Only cubes (2.5.14, the user:
    the other objects' dots were noise; they stay in the page's list, to tell which one a cube is). The memory under
    them: a cube out of view is a hollow blip, a place where one appeared before a faint ring. see_through:
    over the game's minimap, the face only tints it; you=False leaves the arrow to the minimap's own."""
    face_d = face.width()
    side = face_d / 0.84                  # the device's size the proportions were made for
    centre = face.center()
    radius = face_d / 2
    p.setPen(QPen(FACE_RIM, max(2.0, side * 0.028)))
    p.setBrush(Qt.NoBrush if see_through else QColor("#0a0c0f"))
    p.drawEllipse(face.adjusted(-side * 0.02, -side * 0.02, side * 0.02, side * 0.02))
    fg = QRadialGradient(centre, radius)
    inner, outer = QColor(FACE_CENTRE), QColor(FACE_EDGE)
    if see_through:
        inner.setAlpha(70)
        outer.setAlpha(120)
    fg.setColorAt(0, inner)
    fg.setColorAt(1, outer)
    p.setPen(Qt.NoPen)
    p.setBrush(fg)
    p.drawEllipse(face)
    clip = QPainterPath()
    clip.addEllipse(face)
    p.save()
    p.setClipPath(clip)
    # the square grid, 8 cells across
    step = face_d / 8
    p.setPen(QPen(GRID_SOFT, 1))
    for i in range(1, 8):
        x = face.left() + i * step
        y = face.top() + i * step
        p.drawLine(QPointF(x, face.top()), QPointF(x, face.bottom()))
        p.drawLine(QPointF(face.left(), y), QPointF(face.right(), y))
    p.setPen(QPen(GRID, 1.4))
    p.drawLine(QPointF(centre.x(), face.top()), QPointF(centre.x(), face.bottom()))
    p.drawLine(QPointF(face.left(), centre.y()), QPointF(face.right(), centre.y()))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(centre, radius / 2, radius / 2)
    # the sweep
    glow = QRadialGradient(centre, radius)
    glow.setColorAt(0, QColor(255, 255, 255, 0))
    glow.setColorAt(1, QColor(255, 255, 255, 50))
    p.setPen(Qt.NoPen)
    p.setBrush(glow)
    p.drawPie(face, int(-sweep * 16), -40 * 16)
    # what is on it
    scale = radius / reach
    blink = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(time.monotonic() * 5.0))
    if fix is not None:
        fx, fy = fix[1], fix[2]
        ring = max(3.5, side * 0.022)
        for spot in snap.get("spots", []):            # the memory: places where a cube appeared, none there now
            if spot.get("live"):
                continue
            dx, dy = (spot["x"] - fx) * scale, (spot["y"] - fy) * scale
            if math.hypot(dx, dy) <= radius - 6:
                at = QPointF(centre.x() + dx, centre.y() + dy)
                p.setPen(QPen(SPOT, 1.3))
                p.setBrush(SPOT_SOFT)
                p.drawEllipse(at, ring, ring)
                p.setPen(Qt.NoPen)
                p.setBrush(SPOT)
                p.drawEllipse(at, 1.2, 1.2)
        for cube in snap.get("cubes", []):
            lost = cube.get("lost") is not None
            dx, dy = (cube["x"] - fx) * scale, (cube["y"] - fy) * scale
            d = math.hypot(dx, dy)
            if d <= radius - 6:
                at = QPointF(centre.x() + dx, centre.y() + dy)
                size = max(4.0, side * 0.03) * (1.1 if cube.get("opened_by") is None else 0.8)
                if lost:                              # out of view: hollow, steady, where it was
                    p.setPen(QPen(LOST, 1.6))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(at, size, size)
                    p.setPen(Qt.NoPen)
                    p.setBrush(LOST)
                    p.drawEllipse(at, size * 0.35, size * 0.35)
                    continue
                halo = QColor(BLIP_GLOW)
                halo.setAlphaF(0.35 * blink)
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(at, size * 2.4, size * 2.4)
                col = QColor(BLIP)
                col.setAlphaF(0.35 + 0.65 * blink)
                p.setPen(QPen(BLIP_EDGE, 1.2))
                p.setBrush(col)
                p.drawEllipse(at, size, size)
            elif d > 0:
                # out of range: a small arrow at the rim pointing to it (hollow for a remembered one)
                ux, uy = dx / d, dy / d
                tip = QPointF(centre.x() + ux * (radius - 4), centre.y() + uy * (radius - 4))
                base = QPointF(centre.x() + ux * (radius - 14), centre.y() + uy * (radius - 14))
                px, py = -uy * 5, ux * 5
                p.setPen(QPen(LOST if lost else BLIP_EDGE, 1.2 if lost else 1))
                p.setBrush(Qt.NoBrush if lost else BLIP)
                p.drawPolygon(QPolygonF([tip, QPointF(base.x() + px, base.y() + py), QPointF(base.x() - px, base.y() - py)]))
                if "dist" in cube:               # how far, inside the rim next to its arrow
                    tag = QFont(fonts.family)
                    tag.setPointSizeF(max(6.5, side * 0.024 * 72 / 96))
                    tag.setBold(True)
                    p.setFont(tag)
                    p.setPen(INK)
                    at = QPointF(centre.x() + ux * (radius - 30), centre.y() + uy * (radius - 30))
                    p.drawText(QRectF(at.x() - 30, at.y() - 8, 60, 16), Qt.AlignCenter, tr("{0} m", int(cube["dist"])))
        if you:                          # a small arrow, north up
            arrow = QPolygonF([QPointF(centre.x(), centre.y() - 7), QPointF(centre.x() + 5, centre.y() + 6),
                               QPointF(centre.x(), centre.y() + 3), QPointF(centre.x() - 5, centre.y() + 6)])
            p.setPen(QPen(INK, 1))
            p.setBrush(INK)
            p.drawPolygon(arrow)
    # texts on the face
    font = QFont(fonts.family)
    font.setPointSizeF(max(6.5, side * 0.028 * 72 / 96))
    font.setBold(True)
    p.setFont(font)
    p.setPen(INK)
    p.drawText(QRectF(face.left(), face.bottom() - side * 0.12, face_d, side * 0.08), Qt.AlignHCenter | Qt.AlignVCenter,
               tr("{0} m", int(reach)))
    if msg:
        font.setBold(False)
        p.setFont(font)
        p.drawText(QRectF(face.left() + side * 0.08, face.top() + side * 0.08, face_d - side * 0.16, side * 0.1),
                   Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap, msg)
    p.restore()


def fix_message(fix, now, status=""):
    """What the face says about your position: none yet, or how old it is."""
    if fix is None:
        return status or tr("Sin tu posición: entra a un mapa o muévete")
    if now - fix[0] > FIX_STALE:
        return tr("Tu posición es de hace {0} s", int(now - fix[0]))
    return ""


class RadarScreen(QWidget):
    """The Dragon Radar itself. set_data() feeds it; the knob (or the wheel) changes the range."""
    range_changed = Signal(int)
    knob_pressed = Signal()

    def __init__(self, fonts, parent=None, compact=False):
        super().__init__(parent)
        self.fonts = fonts
        self.compact = compact
        self.range_index = 2
        self.snap = {"cubes": [], "objects": [], "map": None}
        self.fix = None
        self.status = ""
        self.now = time.time()
        self._sweep = 0.0
        self.setMinimumSize(fonts.px(180), fonts.px(210))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setToolTip(tr("El botón de arriba (o la rueda) cambia el alcance. Tú estás en el centro; el norte, arriba."))
        self._anim = QTimer(self, interval=90, timeout=self._tick)

    # --- data ---
    def set_data(self, snap, fix, status=""):
        self.snap, self.fix, self.status = snap, fix, status
        self.now = time.time()
        self.update()

    @property
    def reach(self):
        return RANGES[self.range_index]

    def set_range_index(self, index):
        self.range_index = max(0, min(len(RANGES) - 1, index))
        self.range_changed.emit(self.reach)
        self.update()

    def cycle_range(self):
        self.set_range_index((self.range_index + 1) % len(RANGES))

    def nearest(self):
        return live_nearest(self.snap)

    # --- life ---
    def showEvent(self, event):
        super().showEvent(event)
        self._anim.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._anim.stop()

    def _tick(self):
        self._sweep = (self._sweep + 4.5) % 360
        self.update()

    # --- input ---
    def _knob_rect(self):
        r = self.rect()
        side = min(r.width(), r.height() * 0.86)
        k = side * 0.17
        return QRectF(r.center().x() - k / 2, r.top() + side * 0.01, k, k)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._knob_rect().adjusted(-6, -6, 6, 6).contains(event.position()):
            self.knob_pressed.emit()
            self.cycle_range()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        over = self._knob_rect().adjusted(-6, -6, 6, 6).contains(event.position())
        self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        step = -1 if event.angleDelta().y() > 0 else 1       # wheel up: closer look
        self.set_range_index(self.range_index + step)
        event.accept()

    # --- paint ---
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        r = self.rect()
        side = min(r.width(), r.height() * 0.86)
        knob = self._knob_rect()
        body = QRectF(r.center().x() - side / 2, knob.center().y(), side, side * 1.0 + side * 0.08)
        if body.bottom() > r.bottom():
            body.moveBottom(r.bottom() - 1)
        # the body: a dark rounded slab with a soft bevel
        grad = QLinearGradient(body.topLeft(), body.bottomRight())
        grad.setColorAt(0, BODY_LIGHT)
        grad.setColorAt(0.25, BODY_DARK)
        grad.setColorAt(1, QColor("#15181c"))
        p.setPen(QPen(BODY_EDGE, 2))
        p.setBrush(grad)
        p.drawRoundedRect(body, side * 0.11, side * 0.11)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(body.adjusted(3, 3, -3, -3), side * 0.1, side * 0.1)
        # the knob on top
        kg = QRadialGradient(knob.center() - QPointF(knob.width() * 0.2, knob.height() * 0.25), knob.width() * 0.8)
        kg.setColorAt(0, KNOB_LIGHT)
        kg.setColorAt(1, KNOB_DARK)
        p.setPen(QPen(QColor("#5b5f66"), 2))
        p.setBrush(kg)
        p.drawEllipse(knob)
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        p.drawEllipse(knob.adjusted(knob.width() * 0.28, knob.height() * 0.28, -knob.width() * 0.28, -knob.height() * 0.28))
        # the screen
        face_d = side * 0.84
        face = QRectF(body.center().x() - face_d / 2, knob.bottom() + side * 0.05, face_d, face_d)
        fix = self.fix
        cubes = self.snap.get("cubes", [])
        paint_face(p, face, self.reach, self.snap, fix, self._sweep, self.fonts,
                   fix_message(fix, self.now, self.status))
        font = QFont(self.fonts.family)
        font.setPointSizeF(max(6.5, side * 0.028 * 72 / 96))
        # under the screen: how many cubes, the nearest
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#c8ccd2"))
        bottom = QRectF(body.left(), face.bottom() + side * 0.028, body.width(), side * 0.06)
        live = [c for c in cubes if c.get("lost") is None]
        lost = len(cubes) - len(live)
        if live:
            near = self.nearest()
            text = tr("{0} cubo(s) a la vista", len(live)) + (tr(" · el más cerca a {0} m", int(near)) if near is not None else "")
        else:
            text = tr("ningún cubo a la vista") if fix is not None else tr("esperando al juego…") if not self.status else ""
        if lost:
            text += tr(" · {0} recordado(s)", lost)
        p.drawText(bottom, Qt.AlignHCenter | Qt.AlignVCenter, text)


class _Row(QFrame):
    """One line of the side lists: a text and, when given, a button."""

    def __init__(self, fonts, text, color=None, button=None, on_click=None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(fonts.px(8))
        self.text = label(text, fonts.body, color or theme.TEXT)
        self.text.setWordWrap(True)
        lay.addWidget(self.text, 1)
        if button:
            b = QPushButton(button)
            b.setProperty("small", True)
            b.setCursor(Qt.PointingHandCursor)
            if on_click:
                b.clicked.connect(on_click)
            lay.addWidget(b, 0, Qt.AlignTop)


def _ago(seconds):
    """'40 s', '3 min', '2 h'."""
    seconds = max(0, int(seconds))
    if seconds < 90:
        return tr("{0} s", seconds)
    if seconds < 5400:
        return tr("{0} min", seconds // 60)
    if seconds < 172800:
        return tr("{0} h", seconds // 3600)
    return tr("{0} días", seconds // 86400)


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


class CubeRadarWindow(QWidget):
    """Mundo › Radar de cubos: the device and, beside it, what it sees and the marks to teach it."""

    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        f = self.fonts
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Radar de cubos", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + RADAR_STYLE)
        self.resize(1040, 720)
        self._rows_key = None
        self._spots_key = None
        self._mark_key = None
        self._learned_key = None
        root = QVBoxLayout(self)
        root.setContentsMargins(f.px(18), f.px(14), f.px(18), f.px(14))
        root.setSpacing(f.px(10))
        self.header = PageHeader(f, tr("Radar de cubos"),
                                 tr("Los hidden cubes que el juego anuncia cerca de ti, en un radar. Pita cuando hay uno a la vista."))
        mark = QPushButton(tr("Estoy frente a un cubo"))
        mark.setProperty("mark", True)
        mark.setCursor(Qt.PointingHandCursor)
        mark.setToolTip(tr("Guarda la hora, dónde estás y los objetos cerca, para confirmar cuál es el cubo"))
        mark.clicked.connect(lambda: ctl.mark_cube("botón"))
        self.mark_button = mark
        self.header.add_right(mark)
        root.addWidget(self.header)
        body = QHBoxLayout()
        body.setSpacing(f.px(16))
        self.screen = RadarScreen(f)
        self.screen.range_changed.connect(lambda _r: self.refresh())
        body.addWidget(self.screen, 5)
        side = QScrollArea()
        side.setWidgetResizable(True)
        side.setFrameShape(QFrame.NoFrame)
        side.setMinimumWidth(f.px(360))
        side.setMaximumWidth(f.px(440))
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, f.px(6), 0)
        col.setSpacing(f.px(10))
        # state
        self.state_card = Card(f, tr("Ahora"))
        self.state_lines = label("", f.body, theme.TEXT)
        self.state_lines.setWordWrap(True)
        self.state_card.body.addWidget(self.state_lines)
        opts = QHBoxLayout()
        opts.setSpacing(f.px(8))
        self.sound_toggle = Toggle(not ctl.sounds.config("cube_radar")["muted"])
        self.sound_toggle.toggled.connect(self._sound_toggled)
        opts.addWidget(self.sound_toggle)
        opts.addWidget(label(tr("Pitidos"), f.body, theme.TEXT), 1)
        self.state_card.body.addLayout(opts)
        col.addWidget(self.state_card)
        # cubes in view (and the remembered ones)
        self.cubes_card = Card(f, tr("Cubos a la vista"))
        self.cubes_box = QVBoxLayout()
        self.cubes_box.setSpacing(f.px(4))
        self.cubes_card.body.addLayout(self.cubes_box)
        col.addWidget(self.cubes_card)
        # the places where cubes appeared before (this map)
        self.spots_card = Card(f, tr("Lugares donde hubo cubos"))
        self.spots_box = QVBoxLayout()
        self.spots_box.setSpacing(f.px(4))
        self.spots_card.body.addLayout(self.spots_box)
        col.addWidget(self.spots_card)
        # the mark and the objects to confirm
        self.mark_card = Card(f, tr("Enseñarle cuál es el cubo"))
        self.mark_box = QVBoxLayout()
        self.mark_box.setSpacing(f.px(4))
        self.mark_card.body.addLayout(self.mark_box)
        col.addWidget(self.mark_card)
        # learned
        self.learned_card = Card(f, tr("Cubos que conoce"))
        self.learned_box = QVBoxLayout()
        self.learned_box.setSpacing(f.px(4))
        self.learned_card.body.addLayout(self.learned_box)
        col.addWidget(self.learned_card)
        col.addStretch(1)
        side.setWidget(holder)
        body.addWidget(side, 0)
        root.addLayout(body, 1)
        self._timer = QTimer(self, interval=500, timeout=self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _sound_toggled(self, on):
        conf = self.ctl.sounds.config("cube_radar")
        if conf["muted"] == on:
            self.ctl.sounds.toggle_mute("cube_radar")

    def refresh(self):
        ctl = self.ctl
        now = time.time()
        ctl.minimap_wanted_until = now + 3       # the minimap tells where you are while you only run (2.5.14)
        fix = ctl.bridge.position.fix
        snap = ctl.bridge.cubes.snapshot(fix, now)
        status = "" if ctl.sniffer is None or getattr(ctl.health, "sniffing", True) else ctl.capture_status
        self.screen.set_data(snap, fix, status)
        beat(self.screen.nearest(), self.screen.reach, ctl.sounds, not ctl.sounds.config("cube_radar")["muted"])
        hk = ctl.settings["hotkeys"].get("mark_cube", "")
        self.mark_button.setText(tr("Estoy frente a un cubo") + (f"  ({hk})" if hk else ""))
        # state
        lines = [tr("Mapa: {0}", map_name(snap["map"] if snap["map"] is not None else ctl.bridge.position.game_map))]
        if fix:
            lines.append(tr("Tú: ({0}, {1}), posición de hace {2} s{3}", int(fix[1]), int(fix[2]), int(now - fix[0]),
                            "" if fix[3] else tr(" (aproximada)")))
        else:
            lines.append(tr("Tu posición: aún no la sé (llega al entrar a un mapa, al moverte o al lanzar skills)"))
        lines.append(tr("Alcance del radar: {0} m · objetos vistos: {1} · cubos vistos: {2}", self.screen.reach,
                        snap["counts"]["objects"], snap["counts"]["cubes"]))
        reach = snap["counts"].get("reach") or 0
        lines.append(tr("El juego avisa los objetos hasta ~{0} m de ti (medido); más lejos, el radar muestra lo que "
                        "recuerda: cubos que salieron de vista (huecos) y lugares donde hubo cubos (anillos).",
                        reach) if reach else
                     tr("El juego avisa los objetos a unos 100 m de ti; más lejos, el radar muestra lo que recuerda: "
                        "cubos que salieron de vista (huecos) y lugares donde hubo cubos (anillos)."))
        lines.append(tr("Captura: {0}", ctl.capture_status))
        self.state_lines.setText("\n".join(lines))
        # cubes
        key = tuple((c["eid"], c.get("opened_by") is not None, c.get("lost") is not None, int(c.get("dist", -1) // 5),
                     int(c.get("lost_ago", 0) // 60)) for c in snap["cubes"])
        if key != self._rows_key:
            self._rows_key = key
            _clear(self.cubes_box)
            if not snap["cubes"]:
                self.cubes_box.addWidget(label(tr("Ninguno ahora. El juego avisa los objetos a unos 100 m: camina y mira el radar."),
                                              self.fonts.body, theme.MUTED))
            for c in snap["cubes"][:12]:
                where = tr("a {0} m al {1}", int(c["dist"]), c["point"]) if "dist" in c else tr("en ({0}, {1})", int(c["x"]), int(c["y"]))
                opened = tr(" · abierto por ti") if c.get("opened_by") == "me" else tr(" · alguien lo está abriendo") if c.get("opened_by") else ""
                if c.get("lost") is not None:
                    self.cubes_box.addWidget(_Row(self.fonts, tr("◇ {0} {1} · fuera de vista desde hace {2}", c["name"], where,
                                                                 _ago(c["lost_ago"])), theme.MUTED))
                else:
                    self.cubes_box.addWidget(_Row(self.fonts, f"◆ {c['name']} {where}{opened}", theme.GOLD))
        # the spots
        spots = snap.get("spots", [])
        skey = tuple((s["id"], s.get("live", False), int(s.get("dist", -1) // 10), int(s.get("last_ago", 0) // 300)) for s in spots)
        if skey != self._spots_key:
            self._spots_key = skey
            _clear(self.spots_box)
            if not spots:
                lbl = label(tr("Ninguno en este mapa todavía. Cada cubo que el radar vea queda apuntado aquí: los hidden "
                               "cubes reaparecen en los mismos lugares, así que estos anillos son adónde ir a mirar."),
                            self.fonts.body, theme.MUTED)
                lbl.setWordWrap(True)
                self.spots_box.addWidget(lbl)
            for s in spots[:12]:
                where = tr("a {0} m al {1}", int(s["dist"]), s["point"]) if "dist" in s else tr("en ({0}, {1})", int(s["x"]), int(s["y"]))
                times = s.get("times", 1)
                seen = (tr("ahí ahora") if s.get("live") else tr("visto una vez, hace {0}", _ago(s["last_ago"])) if times == 1
                        else tr("visto {0} veces, la última hace {1}", times, _ago(s["last_ago"])))
                self.spots_box.addWidget(_Row(self.fonts, f"○ {s.get('name') or 'Hidden Cube'} {where} · {seen}",
                                              theme.GOLD if s.get("live") else theme.TEXT, tr("Borrar"),
                                              lambda _=False, sid=s["id"], m=snap["map"]: ctl.forget_spot(m, sid)))
            if len(spots) > 12:
                self.spots_box.addWidget(label(tr("… y {0} más lejos", len(spots) - 12), self.fonts.body, theme.MUTED))
        # the mark
        mark = ctl.bridge.cubes.last_mark(now)
        mkey = (mark["ts"] if mark else None, len(snap["objects"]) > 0, tuple(sorted(ctl.bridge.cubes.learned)))
        if mkey != self._mark_key:
            self._mark_key = mkey
            _clear(self.mark_box)
            f = self.fonts
            if mark is None:
                text = tr("Cuando veas un hidden cube con tus ojos, párate frente a él y pulsa «Estoy frente a un cubo» "
                          "(o {0}). Aquí salen los objetos que había cerca para que digas cuál era: desde entonces el radar "
                          "lo marca como cubo.", hk or tr("el atajo que pongas en settings.json › hotkeys › mark_cube"))
                lbl = label(text, f.body, theme.MUTED)
                lbl.setWordWrap(True)
                self.mark_box.addWidget(lbl)
            else:
                when = time.strftime("%H:%M:%S", time.localtime(mark["ts"]))
                head = label(tr("Marca de las {0}: {1} objeto(s) cerca. ¿Cuál era el cubo?", when, len(mark["near"])),
                             f.body, theme.TEXT)
                head.setWordWrap(True)
                self.mark_box.addWidget(head)
                shown = 0
                for obj in mark["near"]:
                    if obj.get("cube"):
                        continue
                    where = tr("a {0} m al {1}", int(obj["dist"]), obj["point"]) if "dist" in obj else tr("en ({0}, {1})", int(obj["x"]), int(obj["y"]))
                    fresh = tr(" · recién aparecido") if obj.get("fresh") else ""
                    self.mark_box.addWidget(_Row(f, tr("objeto {0} (tipo {1}) {2}{3}", obj["oid"], obj["kind"], where, fresh),
                                                 theme.TEXT, tr("Es un cubo"),
                                                 lambda _=False, oid=obj["oid"]: ctl.confirm_cube(oid)))
                    shown += 1
                    if shown >= 10:
                        break
                if not shown:
                    lbl = label(tr("Ningún objeto desconocido cerca de esa marca: el radar ya los conoce todos, o el "
                                   "cubo llega con otro mensaje (la marca quedó guardada en diagnostics/cube_sightings.jsonl)."),
                                f.body, theme.MUTED)
                    lbl.setWordWrap(True)
                    self.mark_box.addWidget(lbl)
        # learned
        learned = ctl.bridge.cubes.learned
        lkey = tuple(sorted(learned))
        if lkey != self._learned_key:
            self._learned_key = lkey
            _clear(self.learned_box)
            f = self.fonts
            for oid, (name, where, sure) in KNOWN.items():
                sure_text = tr("verificado por la comunidad") if sure == "verified" else tr("hipótesis de la comunidad")
                self.learned_box.addWidget(_Row(f, f"{oid} · {name} · {where} · {sure_text}", theme.MUTED))
            for oid, info in sorted(learned.items()):
                when = time.strftime("%d/%m %H:%M", time.localtime(info.get("at", 0)))
                self.learned_box.addWidget(_Row(f, tr("{0} · {1} · confirmado por ti el {2}", oid, info.get("name", "Hidden Cube"), when),
                                                theme.GOLD, tr("Olvidar"), lambda _=False, o=oid: ctl.forget_cube(int(o))))


class FloatingRadar(QWidget):
    """The radar over the game's minimap (from the HUD's menu): only its round face, see-through so the minimap shows
    under it, and an ✕ to close it (2.5.14, the user's ask: the device with its body and knob was in the way). It sits
    on the minimap where minimap.py finds it and follows it, at the minimap's own scale: a blip lands where the object
    is on the minimap, the farther cubes are arrows on the rim. Screen captures leave it out (SetWindowDisplayAffinity),
    so the minimap reader keeps reading your position under it - that is what moves the blips while you only run (the
    game never sends your own movement; before, the radar did not ask for the minimap and its blips stood still).
    Clicks off the circle go to the game (a window mask); right click for more. While the minimap is not found it
    waits where you put it, at the range you chose (the wheel changes it, Ctrl + wheel its size; drag it)."""

    def __init__(self, ctl):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle(tr("Radar de cubos"))
        self.range_index = 2
        self.reach = RANGES[self.range_index]       # metres from you to the rim
        self.snap = {"cubes": [], "objects": [], "map": None}
        self.fix = None                  # (time, x, y, exact) as drawn: smoothed between the minimap's reads
        self.docked = False              # on the minimap (its place and scale) or where you put it
        self._sweep = 0.0
        self._geo, self._geo_at = None, 0.0          # (box in real pixels, minimap pixels per metre, corners)
        self._placed = None              # the rectangle (real pixels) it was last put at
        self._hwnd = None
        self._motion = Motion()
        self._looked_at = 0.0
        self._game_away = False          # the game minimized or the minimap covered
        self._parked = False             # hidden for that, the timer still running
        self._data_at = 0.0
        self._drag = None
        size = int(ctl.settings.get("cube_radar_size") or self.fonts.px(200))
        self.resize(size, size)
        pos = ctl.settings.get("cube_radar_pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.move(*pos)
        self._timer = QTimer(self, interval=FLOAT_TICK_MS, timeout=self._tick)

    # --- on / off ---
    def showEvent(self, event):
        super().showEvent(event)
        self._exclude_from_capture()
        if not self._timer.isActive():
            self._timer.start()
            self._tick()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._parked:                 # only out of sight while the game is away: it keeps watching
            return
        self._timer.stop()
        overlay = self.ctl.minimap_overlay
        if overlay is None or not (overlay.material or overlay.mobs_on):
            self.ctl.minimap.fast = False
        if not self.docked:
            self.ctl.settings["cube_radar_pos"] = [self.x(), self.y()]
            self.ctl.settings.save()

    def _park(self, away):
        if away and self.isVisible():
            self._parked = True
            self.hide()
        elif not away and self._parked:
            self._parked = False
            self.show()

    # --- every tick ---
    def _tick(self):
        ctl = self.ctl
        now = time.time()
        ctl.minimap_wanted_until = now + 3           # keep the minimap read: your position while you only run
        ctl.minimap.fast = True
        geo = overlay_geometry(ctl.minimap, now)
        if geo is not None:
            self._geo, self._geo_at = geo, now
        elif self._geo is not None and now - self._geo_at > HOLD:
            self._geo = None
        if self._geo is None:
            self._geo = fallback_geometry(ctl.minimap)
            self._geo_at = now
        if self._geo is not None and now - self._looked_at > LOOK_EVERY:
            self._looked_at = now
            self._game_away = self._game_hidden(self._geo[0])
            self._park(self._game_away)
        if self._parked:
            return
        self.docked = self._geo is not None
        if self.docked:
            self._dock(*self._geo[:2])
        else:
            self.reach = RANGES[self.range_index]
        fix = ctl.bridge.position.fix
        if fix is not None:
            cone = ctl.bridge.position.cone
            camera = cone[1] if cone and now - cone[0] < CAMERA_FRESH else None
            shown = self._motion.update(now, fix, self._keys(), camera)
            self.fix = (fix[0], shown[0], shown[1], fix[3]) if shown else fix
        else:
            self.fix = None
        self._sweep = (self._sweep + 2.5) % 360
        if now - self._data_at >= 0.5:
            self._data_at = now
            self.snap = ctl.bridge.cubes.snapshot(self.fix, now)
            beat(live_nearest(self.snap), self.reach, ctl.sounds, not ctl.sounds.config("cube_radar")["muted"])
        self.update()

    def _dock(self, box, ppm):
        """Over the minimap's round map: its inner square (the box less its frame), in real pixels; the range is the
        minimap's own scale out to the rim."""
        left, top, right, bottom = box
        inset = (right - left) * INSET
        side = min(right - left, bottom - top) - 2 * inset
        cx, cy = (left + right) / 2, (top + bottom) / 2
        rect = (int(cx - side / 2), int(cy - side / 2), int(side), int(side))
        if self._placed is None or max(abs(a - b) for a, b in zip(rect, self._placed)) >= MOVE_MIN:
            self._placed = rect
            hwnd = self._hwnd or int(self.winId())
            if not _user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), *rect, SWP_NOACTIVATE | SWP_SHOWWINDOW):
                log.warning("could not put the cube radar over the minimap (error %s)", ctypes.GetLastError())
        if ppm:
            self.reach = max(10.0, side * FACE_SHARE / 2 / ppm)

    def _game_hidden(self, box):
        """The game minimized, or another window over the minimap."""
        game = self.ctl.minimap.game_hwnd
        if not game:
            return False
        if _user32.IsIconic(game):
            return True
        try:
            return covered(game, box, self.ctl.minimap.ignore)
        except OSError:
            return False

    def _keys(self):
        """(forward, right) from the movement keys held while the game window is in front, else None."""
        game = self.ctl.minimap.game_hwnd
        if not game or _user32.GetForegroundWindow() != game:
            return None
        held = lambda codes: any(_user32.GetAsyncKeyState(c) & 0x8000 for c in codes)    # noqa: E731
        return (held(KEY_FORWARD) - held(KEY_BACK), held(KEY_RIGHT) - held(KEY_LEFT))

    def _exclude_from_capture(self):
        """Screen captures (the minimap reader's included) see the game under it."""
        hwnd = int(self.winId())
        if hwnd != self._hwnd:
            self._hwnd = hwnd
            if not _user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
                log.warning("the cube radar cannot be left out of screen captures")
            self.ctl.minimap.ignore.add(hwnd)

    # --- the circle and its ✕ ---
    def _face(self):
        r = self.rect()
        d = min(r.width(), r.height()) * FACE_SHARE
        return QRectF(r.center().x() - d / 2 + 0.5, r.center().y() - d / 2 + 0.5, d, d)

    def _close_rect(self):
        face = self._face()
        k = max(14.0, face.width() * 0.11)
        at = face.center() + QPointF(face.width() / 2 * 0.74, -face.height() / 2 * 0.74)
        return QRectF(at.x() - k / 2, at.y() - k / 2, k, k)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        face = self._face()
        grow = face.width() * 0.06               # the rim around the face
        region = QRegion(face.adjusted(-grow, -grow, grow, grow).toRect(), QRegion.Ellipse)
        self.setMask(region.united(QRegion(self._close_rect().adjusted(-2, -2, 2, 2).toRect(), QRegion.Ellipse)))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        msg = fix_message(self.fix, time.time())
        paint_face(p, self._face(), self.reach, self.snap, self.fix, self._sweep, self.fonts, msg,
                   see_through=True, you=not self.docked)
        x = self._close_rect()
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(QColor(18, 22, 27, 225))
        p.drawEllipse(x)
        p.setPen(QPen(QColor("#e8eaed"), max(1.4, x.width() * 0.11)))
        k = x.width() * 0.27
        c = x.center()
        p.drawLine(QPointF(c.x() - k, c.y() - k), QPointF(c.x() + k, c.y() + k))
        p.drawLine(QPointF(c.x() - k, c.y() + k), QPointF(c.x() + k, c.y() - k))

    # --- input ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._close_rect().adjusted(-3, -3, 3, 3).contains(event.position()):
                self.hide()
            elif not self.docked:
                self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif event.button() == Qt.RightButton:
            self._menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        over = self._close_rect().adjusted(-3, -3, 3, 3).contains(event.position())
        self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def wheelEvent(self, event):
        if self.docked:                          # on the minimap its scale is the minimap's
            event.accept()
            return
        if event.modifiers() & Qt.ControlModifier:        # Ctrl + wheel: bigger or smaller
            size = max(self.fonts.px(140), min(self.fonts.px(480), self.width() + (20 if event.angleDelta().y() > 0 else -20)))
            self.resize(size, size)
            self.ctl.settings["cube_radar_size"] = size
        else:                                             # wheel up: closer look
            self.range_index = max(0, min(len(RANGES) - 1, self.range_index + (-1 if event.angleDelta().y() > 0 else 1)))
            self.reach = RANGES[self.range_index]
        self.update()
        event.accept()

    def _menu(self, at):
        from .appmenu import styled_menu
        ctl = self.ctl
        menu = styled_menu(self)
        menu.addAction(tr("Estoy frente a un cubo"), lambda: ctl.mark_cube("radar flotante"))
        menu.addAction(tr("Abrir la página del radar"), lambda: ctl.open_cube_radar())
        if not self.docked:
            menu.addAction(tr("Alcance: {0} m (cambiar)", int(self.reach)), self._next_range)
        muted = ctl.sounds.config("cube_radar")["muted"]
        menu.addAction(tr("Activar los pitidos") if muted else tr("Silenciar los pitidos"),
                       lambda: ctl.sounds.toggle_mute("cube_radar"))
        menu.addSeparator()
        menu.addAction(tr("Cerrar el radar"), self.hide)
        menu.exec(at)

    def _next_range(self):
        self.range_index = (self.range_index + 1) % len(RANGES)
        self.reach = RANGES[self.range_index]
        self.update()
