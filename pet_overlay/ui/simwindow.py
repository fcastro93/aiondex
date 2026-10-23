"""Simulación: a dungeon boss fight replayed from above (simulation.py has the data). Your party as circles with
their profile picture (or their class) and their name, the boss with its face, the areas it leaves on the floor
(growing rings, pools) with their name, what it casts, and a timeline with a red skull at every death (hover it to
see who) to jump to any moment. There is no picture of the dungeon's floor: the grid is 5 m."""
import math
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QToolTip,
                               QVBoxLayout, QWidget)

from .. import APP_NAME
from ..mechanics import COLORS as MECH_COLORS, LABELS as MECH_LABELS, ROOM_WIDE
from ..simulation import Replay
from . import theme
from .dps_panel import CLASS_ICON, mmss
from .dpswindow import CLASS_COLOR
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import label
from ..i18n import tr

SPEEDS = ((0.5, "×0.5"), (1.0, "×1"), (2.0, "×2"), (4.0, "×4"), (8.0, "×8"))
PERSON_R = 17           # px: a player's circle
BOSS_R = 24
ADD_R = 6
TRAIL = 3.0             # seconds of trail behind each player
GRID = 5.0              # metres between grid lines
BG = QColor("#0a0c11")
FLOOR = QColor("#0e1830")
GRID_MINOR, GRID_MAJOR = QColor(255, 255, 255, 10), QColor(255, 255, 255, 24)
SKULL_RED = QColor("#ff4d4d")
SIDE_STYLE = """
QScrollArea { background: transparent; border: none; }
QWidget#SideBody { background: transparent; }
"""


def skull_path(size):
    """A skull `size` tall centred on (0, 0) (the font's one is a white emoji)."""
    s = size
    head = QPainterPath()
    head.addEllipse(QRectF(-0.45 * s, -0.5 * s, 0.9 * s, 0.78 * s))
    jaw = QPainterPath()
    jaw.addRoundedRect(QRectF(-0.27 * s, 0.1 * s, 0.54 * s, 0.4 * s), 0.08 * s, 0.08 * s)
    holes = QPainterPath()
    holes.addEllipse(QRectF(-0.33 * s, -0.22 * s, 0.25 * s, 0.27 * s))
    holes.addEllipse(QRectF(0.08 * s, -0.22 * s, 0.25 * s, 0.27 * s))
    nose = QPainterPath(QPointF(0, 0.07 * s))
    nose.lineTo(-0.07 * s, 0.2 * s)
    nose.lineTo(0.07 * s, 0.2 * s)
    nose.closeSubpath()
    holes.addPath(nose)
    for x in (-0.13, -0.02, 0.09):
        holes.addRect(QRectF(x * s, 0.34 * s, 0.04 * s, 0.17 * s))
    return head.united(jaw).subtracted(holes)


def draw_skull(p, center, size, color):
    p.save()
    p.translate(center)
    p.setPen(QPen(QColor(0, 0, 0, 170), 1))
    p.setBrush(color)
    p.drawPath(skull_path(size))
    p.restore()


def skull_pixmap(size, color, dpr=1.0):
    pix = QPixmap(round(size * dpr), round(size * dpr))
    pix.fill(Qt.transparent)
    pix.setDevicePixelRatio(dpr)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    draw_skull(p, QPointF(size / 2, size / 2), size * 0.92, color)
    p.end()
    return pix


def _class_color(cls, me=False):
    """A new QColor (callers change its alpha: never hand out the theme's own)."""
    return QColor(theme.GOLD) if me else QColor(CLASS_COLOR.get(cls, theme.ACCENT.name()))


def _circle_pixmap(pix, diameter, dpr, crop=None):
    """A picture cut in a circle (crop: (x, y, w) share of the picture to keep, for full-body art)."""
    size = round(diameter * dpr)
    if crop and not pix.isNull():
        w, h = pix.width(), pix.height()
        side = crop[2] * w
        pix = pix.copy(round(crop[0] * w), round(crop[1] * h), round(side), round(side))
    pix = pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    p.setClipPath(path)
    p.drawPixmap(round((size - pix.width()) / 2), round((size - pix.height()) / 2), pix)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class ArenaView(QWidget):
    """The fight from above at time `t`."""

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.replay = window.replay
        self.fonts = window.fonts
        self.t = 0.0
        self.trails = True
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self._drag = None
        self.bounds = self.replay.bounds()
        self.setMinimumSize(560, 420)
        self.setMouseTracking(True)

    # --- world <-> screen ---
    def _scale(self):
        x0, y0, x1, y1 = self.bounds
        w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
        return min((self.width() - 60) / w, (self.height() - 60) / h) * self.zoom

    def to_screen(self, x, y):
        x0, y0, x1, y1 = self.bounds
        s = self._scale()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return QPointF(self.width() / 2 + (x - cx) * s + self.pan.x(), self.height() / 2 + (y - cy) * s + self.pan.y())

    def to_world(self, point):
        x0, y0, x1, y1 = self.bounds
        s = self._scale()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return ((point.x() - self.width() / 2 - self.pan.x()) / s + cx,
                (point.y() - self.height() / 2 - self.pan.y()) / s + cy)

    # --- mouse: drag to move, wheel to zoom, double click to fit ---
    def wheelEvent(self, event):
        before = self.to_world(event.position())
        self.zoom = max(0.4, min(8.0, self.zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        after = self.to_screen(*before)
        self.pan += event.position() - after
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.position()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.pan += event.position() - self._drag
            self._drag = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None

    def mouseDoubleClickEvent(self, event):
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self.update()

    # --- drawing ---
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), BG)
        self._dungeon_map(p)
        self._grid(p)
        r, t = self.replay, self.t
        self._zones(p, t)
        if r.mechs:
            self._mechanics(p, t)
        else:
            self._casts(p, t)                          # simulations saved before 1.48
        self._aggro(p, t)
        if self.trails:
            for person in r.people:
                self._trail(p, person, t)
        for eid in r.adds:
            self._add(p, eid, t)
        for boss in r.bosses:
            self._boss(p, boss, t)
        for person in sorted(r.people, key=lambda q: q.get("me", False)):      # you on top
            self._person(p, person, t)
        self._marks(p, t)
        self._cast_bar(p, t)
        self._scale_bar(p)
        p.end()

    def _grid(self, p):
        x0, y0 = self.to_world(QPointF(0, 0))
        x1, y1 = self.to_world(QPointF(self.width(), self.height()))
        s = self._scale()
        step = GRID if s * GRID >= 10 else GRID * 5
        gx = math.floor(x0 / step) * step
        while gx <= x1:
            sx = self.to_screen(gx, 0).x()
            p.setPen(QPen(GRID_MAJOR if round(gx / step) % 5 == 0 else GRID_MINOR, 1))
            p.drawLine(QPointF(sx, 0), QPointF(sx, self.height()))
            gx += step
        gy = math.floor(y0 / step) * step
        while gy <= y1:
            sy = self.to_screen(0, gy).y()
            p.setPen(QPen(GRID_MAJOR if round(gy / step) % 5 == 0 else GRID_MINOR, 1))
            p.drawLine(QPointF(0, sy), QPointF(self.width(), sy))
            gy += step

    def _scale_bar(self, p):
        s = self._scale()
        metres = next((m for m in (5, 10, 20, 50, 100) if m * s >= 60), 100)
        x, y = 16, self.height() - 18
        p.setPen(QPen(theme.MUTED, 2))
        p.drawLine(QPointF(x, y), QPointF(x + metres * s, y))
        p.drawLine(QPointF(x, y - 4), QPointF(x, y + 4))
        p.drawLine(QPointF(x + metres * s, y - 4), QPointF(x + metres * s, y + 4))
        p.setFont(self.fonts.small)
        p.drawText(QPointF(x + metres * s + 8, y + 4), f"{metres} m")

    def _text(self, p, center, text, font, color, background=True, above=False):
        """A label centred at `center` (its top there, or its bottom when above), on a dark pill."""
        fm = QFontMetricsF(font)
        w, h = fm.horizontalAdvance(text) + 10, fm.height() + 2
        rect = QRectF(center.x() - w / 2, center.y() - (h if above else 0), w, h)
        if background:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(6, 10, 20, 190))
            p.drawRoundedRect(rect, h / 2, h / 2)
        p.setFont(font)
        p.setPen(color)
        p.drawText(rect, Qt.AlignCenter, text)
        return rect

    def _zones(self, p, t):
        s = self._scale()
        labels = {}
        for z in self.replay.zones_at(t):
            start, end, x, y, r0, r1, kind, name, color, _npc, hits, origin = z[:12]
            life = max(0.01, end - start)
            k = (t - start) / life
            radius = (r0 + (r1 - r0) * k) * s
            fade = min(1.0, (t - start) / 0.3, (end - t) / 0.3) if life > 0.6 else 1.0
            flash = any(0 <= t - h <= 0.35 for h in hits)
            c = QColor(color)
            center = self.to_screen(*Replay.zone_center(z, t))
            if origin and t - start < 0.8:             # thrown from where the object stands
                p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 140), 1.5, Qt.DashLine))
                p.drawLine(self.to_screen(*origin), center)
            if kind == "wave":                         # travels the way it faces, with a tail behind
                h = math.radians(z[12])
                tail = z[13] * min(0.6, t - start) * s
                back = QPointF(center.x() - math.cos(h) * tail, center.y() - math.sin(h) * tail)
                p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), round(38 * fade)), max(3.0, 2 * radius),
                              Qt.SolidLine, Qt.RoundCap))
                p.drawLine(back, center)
                p.setBrush(QColor(c.red(), c.green(), c.blue(), round((190 if flash else 105) * fade)))
                p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), round(200 * fade)), 1.2))
                p.drawEllipse(center, max(3.0, radius), max(3.0, radius))
                labels.setdefault((name, color), []).append(center)
                continue
            if kind == "ring":
                p.setBrush(QColor(c.red(), c.green(), c.blue(), round(28 * fade)))
                p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), round((255 if flash else 210) * fade)),
                              4 if flash else 2.5))
            else:
                p.setBrush(QColor(c.red(), c.green(), c.blue(), round((120 if flash else 62) * fade)))
                p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), round(200 * fade)), 2 if flash else 1.2))
            p.drawEllipse(center, max(2.0, radius), max(2.0, radius))
            if kind != "ring" and radius > 10:         # hatching inside a pool so it reads as "floor"
                p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), round(70 * fade)), 1))
                for i in range(-2, 3):
                    dy = i * radius / 3
                    dx = math.sqrt(max(0.0, radius * radius - dy * dy)) * 0.75
                    p.drawLine(QPointF(center.x() - dx, center.y() + dy), QPointF(center.x() + dx, center.y() + dy))
            labels.setdefault((name, color), []).append(center)
        view = QRectF(self.rect()).adjusted(70, 30, -70, -30)
        for (name, color), points in labels.items():      # one label per kind of area, at the middle of them
            seen = [q for q in points if view.contains(q)] or points
            cx = sum(q.x() for q in seen) / len(seen)
            cy = sorted(q.y() for q in seen)[len(seen) // 2]
            text = name + (f" ×{len(points)}" if len(points) > 1 else "")
            self._text(p, QPointF(min(max(cx, view.left()), view.right()), min(max(cy, view.top()), view.bottom())),
                       text, self.fonts.small, QColor(color), above=True)

    def _casts(self, p, t):
        r = self.replay
        for c in r.casts:
            at, caster, _skill, cooldown, name, px, py, target = c
            if not at <= t <= at + 1.6:
                continue
            boss = r.position(caster, t)
            if not boss:
                continue
            origin = self.to_screen(boss[0], boss[1])
            aim = None
            if px is not None:
                aim = self.to_screen(px, py)
            elif target is not None:
                who = r.position(target, t)
                aim = self.to_screen(who[0], who[1]) if who else None
            if aim is not None and (aim - origin).manhattanLength() > 12:
                p.setPen(QPen(QColor(255, 90, 80, 170), 1.6, Qt.DashLine))
                p.drawLine(origin, aim)
                p.setPen(QPen(QColor(255, 90, 80, 220), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(aim, 7, 7)
                p.drawLine(aim + QPointF(-11, 0), aim + QPointF(11, 0))
                p.drawLine(aim + QPointF(0, -11), aim + QPointF(0, 11))

    def _trail(self, p, person, t):
        points = self.replay.trail(person["eid"], t, TRAIL)
        if len(points) < 2:
            return
        c = _class_color(person.get("class"), person.get("me"))
        for i in range(1, len(points)):
            a = self.to_screen(*points[i - 1])
            b = self.to_screen(*points[i])
            c.setAlpha(round(30 + 120 * i / len(points)))
            p.setPen(QPen(c, 2.5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(a, b)

    def _add(self, p, eid, t):
        pos = self.replay.position(eid, t)
        if not pos or self.replay.down(eid, t):
            return
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.setBrush(QColor(200, 70, 70, 220 if pos[2] else 110))
        p.drawEllipse(self.to_screen(pos[0], pos[1]), ADD_R, ADD_R)

    def _hp_ring(self, p, center, radius, frac, color=None):
        if frac is None:
            return
        rect = QRectF(center.x() - radius - 4, center.y() - radius - 4, 2 * radius + 8, 2 * radius + 8)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 150), 4))
        p.drawEllipse(rect)
        hue = color or QColor.fromHsvF(max(0.0, min(1.0, frac)) * 0.33, 0.75, 0.95)
        p.setPen(QPen(hue, 3.5, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(rect, 90 * 16, round(-360 * 16 * max(0.0, min(1.0, frac))))

    def _person(self, p, person, t):
        r = self.replay
        eid = person["eid"]
        pos = r.position(eid, t)
        if not pos:
            return
        center = self.to_screen(pos[0], pos[1])
        dead = r.down(eid, t)
        me = person.get("me")
        p.setOpacity(1.0 if pos[2] or me else 0.55)
        self._hp_ring(p, center, PERSON_R, 0.0 if dead else r.hp_at(eid, t))
        picture = self.win.picture(person)
        if picture is not None:
            p.drawPixmap(QPointF(center.x() - PERSON_R, center.y() - PERSON_R), picture)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#1b2a4a"))
            p.drawEllipse(center, PERSON_R, PERSON_R)
            p.setPen(theme.TEXT)
            p.setFont(self.fonts.name)
            p.drawText(QRectF(center.x() - PERSON_R, center.y() - PERSON_R, 2 * PERSON_R, 2 * PERSON_R),
                       Qt.AlignCenter, person["name"][:2])
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_class_color(person.get("class"), me), 2.5))
        p.drawEllipse(center, PERSON_R, PERSON_R)
        if dead:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(10, 10, 14, 170))
            p.drawEllipse(center, PERSON_R, PERSON_R)
            draw_skull(p, center, PERSON_R * 1.35, SKULL_RED)
        name = person["name"] + (tr(" (tú)") if me else "")
        self._text(p, QPointF(center.x(), center.y() + PERSON_R + 6), name, self.fonts.small,
                   SKULL_RED if dead else (theme.GOLD if me else theme.TEXT))
        p.setOpacity(1.0)

    def _boss(self, p, boss, t):
        r = self.replay
        eid = boss["eid"]
        pos = r.position(eid, t)
        if not pos:
            return
        center = self.to_screen(pos[0], pos[1])
        dead = r.down(eid, t)
        self._hp_ring(p, center, BOSS_R, 0.0 if dead else r.hp_at(eid, t), QColor("#ff5a4f"))
        face = self.win.face(boss)
        if face is not None:
            p.drawPixmap(QPointF(center.x() - BOSS_R, center.y() - BOSS_R), face)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#5a1620"))
            p.drawEllipse(center, BOSS_R, BOSS_R)
            p.setPen(QColor("#ffb3ad"))
            p.setFont(self.fonts.name_big)
            p.drawText(QRectF(center.x() - BOSS_R, center.y() - BOSS_R, 2 * BOSS_R, 2 * BOSS_R), Qt.AlignCenter,
                       boss["name"][:1])
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#ff5a4f"), 3))
        p.drawEllipse(center, BOSS_R, BOSS_R)
        if dead:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(10, 10, 14, 150))
            p.drawEllipse(center, BOSS_R, BOSS_R)
        raging = r.rage is not None and t >= r.rage and not dead
        if raging:                                     # its rage timer ran out
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 40, 40, 200), 3, Qt.DashLine))
            p.drawEllipse(center, BOSS_R + 9, BOSS_R + 9)
        gauge, broken = r.gauge(t)
        if broken and not dead:
            p.setPen(QPen(QColor("#6cb4ff"), 4))
            p.drawEllipse(center, BOSS_R + 5, BOSS_R + 5)
        rect = self._text(p, QPointF(center.x(), center.y() + BOSS_R + 6), boss["name"], self.fonts.small,
                          QColor("#ffb3ad"), background=True)
        if (gauge is not None or broken) and not dead:  # the stagger gauge under its name
            w = 64
            bar = QRectF(center.x() - w / 2, rect.bottom() + 3, w, 5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(bar, 2, 2)
            p.setBrush(QColor("#6cb4ff"))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), w * (0 if broken else gauge or 0), 5), 2, 2)
            if broken:
                self._text(p, QPointF(center.x(), bar.bottom() + 2), tr("¡ATURDIDO!"), self.fonts.small,
                           QColor("#6cb4ff"))
        if raging:
            self._text(p, QPointF(center.x(), center.y() - BOSS_R - 30), tr("FURIA"), self.fonts.name,
                       QColor("#ff4d4d"), above=True)
        cast = None if r.mechs else r.casting(eid, t)
        if cast and not dead:
            name = cast[4] or tr("Habilidad ({0:g} s)", cast[3])
            self._text(p, QPointF(center.x(), center.y() - BOSS_R - 8), name, self.fonts.name,
                       QColor("#ffd27a"), above=True)


    # --- the boss's mechanics -----------------------------------------------------------------------------
    def _mechanics(self, p, t):
        """Each cast going on: its warning on the floor filling up until it lands (a circle where it aims or around
        the boss, a cone in front of it, a line to its target, a jump), then a flash where it hit."""
        r, s = self.replay, self._scale()
        for m in r.mechs_at(t, before=0.0, after=6.0):   # chains of zones land one after the other
            start, end, caster, name, kind, cut, info = m
            if not info.get("zones") and t > end + 0.7:
                continue
            color = QColor(MECH_COLORS.get(kind, "#ff5a4f"))
            casting = t < end
            k = 1.0 if not casting else (t - start) / max(0.01, end - start)
            fade = 1.0 if casting else max(0.0, 1 - (t - end) / 0.7)
            if cut and not casting:
                continue                               # interrupted: it never landed
            boss = r.position(caster, min(t, end))
            if info.get("zones"):
                self._client_zones(p, m, t, color, boss)
                continue
            shape = info.get("shape")
            if info.get("jump"):
                x1, y1, x2, y2 = info["jump"]
                a, b = self.to_screen(x1, y1), self.to_screen(x2, y2)
                p.setPen(QPen(QColor(199, 125, 255, round(220 * fade)), 2.5, Qt.DashLine))
                p.drawLine(a, b)
                self._arrow_head(p, a, b, QColor(199, 125, 255, round(220 * fade)))
            if not shape or not boss:
                continue
            kind_s, radius, half = shape
            center = self.to_screen(boss[0], boss[1])
            if kind_s == "point" and info.get("point"):
                center = self.to_screen(*info["point"])
            if kind_s in ("point", "around"):
                rr = radius * s
                p.setBrush(QColor(color.red(), color.green(), color.blue(), round(35 * fade)))
                p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), round(220 * fade)), 2))
                p.drawEllipse(center, rr, rr)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(color.red(), color.green(), color.blue(), round((70 if casting else 150) * fade)))
                p.drawEllipse(center, rr * k, rr * k)          # fills up like the game's warning
            elif kind_s == "cone":
                rr = radius * s
                rect = QRectF(center.x() - rr, center.y() - rr, 2 * rr, 2 * rr)
                heading = info.get("heading", 0.0)
                path = QPainterPath(center)
                path.arcTo(rect, -heading - half, 2 * half)    # Qt angles go counterclockwise, y down
                path.closeSubpath()
                p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), round(220 * fade)), 2))
                p.setBrush(QColor(color.red(), color.green(), color.blue(), round(35 * fade)))
                p.drawPath(path)
                inner = QRectF(center.x() - rr * k, center.y() - rr * k, 2 * rr * k, 2 * rr * k)
                fill = QPainterPath(center)
                fill.arcTo(inner, -heading - half, 2 * half)
                fill.closeSubpath()
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(color.red(), color.green(), color.blue(), round((70 if casting else 150) * fade)))
                p.drawPath(fill)
            elif kind_s == "target":
                who = info.get("target") or (info.get("hit") or [None])[0]
                pos = r.position(who, t) if who is not None else None
                if pos:
                    end_pt = self.to_screen(pos[0], pos[1])
                    p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), round(90 * fade)), 7,
                                  Qt.SolidLine, Qt.RoundCap))
                    p.drawLine(center, end_pt)
                    p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), round(230 * fade)), 3,
                                  Qt.SolidLine, Qt.RoundCap))
                    p.drawLine(center, center + (end_pt - center) * k)
                    if not casting:
                        p.setBrush(Qt.NoBrush)
                        p.drawEllipse(end_pt, PERSON_R + 6, PERSON_R + 6)
            elif kind_s == "group" and not casting:
                for who in info.get("hit") or []:
                    pos = r.position(who, t)
                    if pos:
                        p.setBrush(Qt.NoBrush)
                        p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), round(230 * fade)), 3))
                        p.drawEllipse(self.to_screen(pos[0], pos[1]), PERSON_R + 7, PERSON_R + 7)

    def _dungeon_map(self, p):
        """The dungeon's map under the fight (only the part on screen is scaled)."""
        info = self.win.dungeon_map
        pix = self.win.map_pixmap()
        if not info or pix is None:
            return
        x0, y0, x1, y1 = info["box"]
        a, b = self.to_screen(x0, y0), self.to_screen(x1, y1)
        target = QRectF(a, b).normalized()
        shown = target.intersected(QRectF(self.rect()))
        if shown.isEmpty():
            return
        sx, sy = pix.width() / target.width(), pix.height() / target.height()
        source = QRectF((shown.left() - target.left()) * sx, (shown.top() - target.top()) * sy,
                        shown.width() * sx, shown.height() * sy)
        p.setOpacity(0.9)
        p.drawPixmap(shown, pix, source)
        p.setOpacity(1.0)

    def _client_zones(self, p, m, t, color, boss):
        """The zones the client paints for this skill: each lights up its warning time before it lands (the first
        lands as the cast ends, the next ones one after the other), filling up, then flashes."""
        r, s = self.replay, self._scale()
        start, end, caster, name, kind, cut, info = m
        if cut and t >= end:
            return
        hit, labelled = end, False
        for k, z in enumerate(info["zones"]):
            zkind, fwd, right, rot, a, b, c, d, e, notice, anchor = z
            if k:
                hit += max(0.3, notice)
            shown_from = hit - notice if notice > 0 else hit
            early = k == 0 and start <= t < shown_from      # the cast is on, the floor does not warn yet
            if not (shown_from <= t <= hit + 0.5 or early):
                continue
            if anchor == "target":
                who = info.get("target") or (info.get("hit") or [None])[0]
                pos = r.position(who, t) if who is not None else None
            elif anchor == "targetLoc":
                pos = info.get("spot")
            else:
                pos = boss
            if not pos:
                continue
            heading = math.radians((info.get("heading") or 0.0) + rot)
            fx, fy = math.cos(heading), math.sin(heading)
            rx, ry = -fy, fx
            ox, oy = pos[0] + fx * fwd + rx * right, pos[1] + fy * fwd + ry * right
            landing = t >= hit
            k_fill = 1.0 if landing or notice <= 0 else max(0.0, min(1.0, (t - shown_from) / notice))
            fade = 1.0 if not landing else max(0.0, 1 - (t - hit) / 0.5)
            if zkind in ("Circle", "Sphere") and a >= ROOM_WIDE and anchor == "caster":
                if landing:                            # the whole room: the edges of the view flash
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), round(200 * fade)), 8))
                    p.drawRect(QRectF(self.rect()).adjusted(4, 4, -4, -4))
                if not labelled:
                    labelled = True
                    self._text(p, self.to_screen(pos[0], pos[1]) + QPointF(0, -BOSS_R - 40), name, self.fonts.name,
                               QColor(color.red(), color.green(), color.blue()), above=True)
                continue
            path, fill = self._zone_path(zkind, ox, oy, fx, fy, rx, ry, heading, a, b, c, d, e, s, k_fill)
            if path is None:
                continue
            if early:                                  # what is coming: its outline, dashed, no fill
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 150), 1.5, Qt.DashLine))
                p.drawPath(path)
                if not labelled:
                    labelled = True
                    self._text(p, self.to_screen(ox, oy) + QPointF(0, -14), name, self.fonts.small,
                               QColor(color.red(), color.green(), color.blue()), above=True)
                continue
            p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), round(230 * fade)), 2))
            p.setBrush(QColor(color.red(), color.green(), color.blue(), round(30 * fade)))
            p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color.red(), color.green(), color.blue(), round((150 if landing else 75) * fade)))
            p.drawPath(fill)
            if not labelled:
                labelled = True
                self._text(p, self.to_screen(ox, oy) + QPointF(0, -14), name, self.fonts.small,
                           QColor(color.red(), color.green(), color.blue()), above=True)

    def _zone_path(self, kind, ox, oy, fx, fy, rx, ry, heading, a, b, c, d, e, s, k):
        """(outline, filled part) of one zone on screen; k: how much of the warning has filled."""
        center = self.to_screen(ox, oy)
        deg = math.degrees(heading)

        def poly(points):
            path = QPainterPath(self.to_screen(*points[0]))
            for q in points[1:]:
                path.lineTo(self.to_screen(*q))
            path.closeSubpath()
            return path

        def disc(radius):
            path = QPainterPath()
            path.addEllipse(center, radius * s, radius * s)
            return path

        def pie(radius, angle):
            rr = radius * s
            path = QPainterPath(center)
            path.arcTo(QRectF(center.x() - rr, center.y() - rr, 2 * rr, 2 * rr), -deg - angle / 2, angle)
            path.closeSubpath()
            return path

        if kind in ("Circle", "Sphere"):
            return disc(a), disc(a * k)
        if kind == "Ring":
            outline = disc(a).subtracted(disc(b))
            return outline, disc(b + (a - b) * k).subtracted(disc(b))
        if kind == "RingArc":
            outline = pie(a, c).subtracted(pie(b, c))
            return outline, pie(b + (a - b) * k, c).subtracted(pie(b, c))
        if kind == "Arc":
            return pie(a, b), pie(a * k, b)
        if kind == "Triangle":
            half = math.tan(math.radians(b / 2)) * a

            def tri(reach, width):
                return poly([(ox, oy), (ox + fx * reach + rx * width, oy + fy * reach + ry * width),
                             (ox + fx * reach - rx * width, oy + fy * reach - ry * width)])
            return tri(a, half), tri(a * k, half * k)

        def bar(length, width, along, across):
            ax, ay = along
            cx, cy = across
            return poly([(ox + ax * length + cx * width / 2, oy + ay * length + cy * width / 2),
                         (ox + ax * length - cx * width / 2, oy + ay * length - cy * width / 2),
                         (ox - cx * width / 2, oy - cy * width / 2), (ox + cx * width / 2, oy + cy * width / 2)])
        if kind == "Rectangle":
            return bar(a, b, (fx, fy), (rx, ry)), bar(a * k, b, (fx, fy), (rx, ry))
        if kind == "Cross":
            def cross(scale):
                one = poly([(ox + fx * a / 2 * scale + rx * d / 2, oy + fy * a / 2 * scale + ry * d / 2),
                            (ox + fx * a / 2 * scale - rx * d / 2, oy + fy * a / 2 * scale - ry * d / 2),
                            (ox - fx * a / 2 * scale - rx * d / 2, oy - fy * a / 2 * scale - ry * d / 2),
                            (ox - fx * a / 2 * scale + rx * d / 2, oy - fy * a / 2 * scale + ry * d / 2)])
                two = poly([(ox + rx * b / 2 * scale + fx * e / 2, oy + ry * b / 2 * scale + fy * e / 2),
                            (ox + rx * b / 2 * scale - fx * e / 2, oy + ry * b / 2 * scale - fy * e / 2),
                            (ox - rx * b / 2 * scale - fx * e / 2, oy - ry * b / 2 * scale - fy * e / 2),
                            (ox - rx * b / 2 * scale + fx * e / 2, oy - ry * b / 2 * scale + fy * e / 2)])
                return one.united(two)
            return cross(1.0), cross(max(0.01, k))
        return None, None

    def _arrow_head(self, p, a, b, color):
        angle = math.atan2(b.y() - a.y(), b.x() - a.x())
        size = 9
        left = QPointF(b.x() - size * math.cos(angle - 0.45), b.y() - size * math.sin(angle - 0.45))
        right = QPointF(b.x() - size * math.cos(angle + 0.45), b.y() - size * math.sin(angle + 0.45))
        path = QPainterPath(b)
        path.lineTo(left)
        path.lineTo(right)
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)

    def _aggro(self, p, t):
        """A faint line from the boss to whom it is after."""
        r = self.replay
        for boss in r.bosses:
            who = r.target_of(boss["eid"], t)
            a, b = r.position(boss["eid"], t), r.position(who, t) if who is not None else None
            if a and b:
                p.setPen(QPen(QColor(255, 90, 79, 70), 1.5, Qt.DotLine))
                p.drawLine(self.to_screen(a[0], a[1]), self.to_screen(b[0], b[1]))

    def _marks(self, p, t):
        """On the players: the debuffs the boss left (purple ring, its seconds left), the hits that moved them
        (an arrow to where they went), the boss's buff on itself (gold aura) and the adds it brought."""
        r, f = self.replay, self.fonts
        for m in r.mechs_at(t, before=0.0, after=25.0):
            start, end, caster, name, kind, cut, info = m
            if cut:
                continue
            for debuff in info.get("debuffs") or []:
                who, effect, seconds = debuff[:3]
                status = debuff[3] if len(debuff) > 3 else None
                if end <= t <= end + seconds:
                    pos = r.position(who, t)
                    if pos:
                        c = self.to_screen(pos[0], pos[1])
                        p.setBrush(Qt.NoBrush)
                        p.setPen(QPen(QColor(178, 141, 255, 210), 2, Qt.DashLine))
                        p.drawEllipse(c, PERSON_R + 4, PERSON_R + 4)
                        left = end + seconds - t
                        if left >= 1:
                            text = (f"{status} " if status else "") + f"{left:.0f}s"
                            self._text(p, QPointF(c.x() + PERSON_R + 10 + len(text) * 2.6, c.y() - PERSON_R), text,
                                       f.small, QColor("#b28dff"))
            if end <= t <= end + 0.8:
                for who, x1, y1, x2, y2 in info.get("knock") or []:
                    a, b = self.to_screen(x1, y1), self.to_screen(x2, y2)
                    if (b - a).manhattanLength() > 3:
                        p.setPen(QPen(QColor(255, 255, 255, 200), 2))
                        p.drawLine(a, b)
                        self._arrow_head(p, a, b, QColor(255, 255, 255, 220))
            boss = r.position(caster, t)
            if info.get("buff") and start <= t <= end + info["buff"] and boss:
                c = self.to_screen(boss[0], boss[1])
                p.setBrush(QColor(255, 224, 138, 40))
                p.setPen(QPen(QColor(255, 224, 138, 170), 2))
                p.drawEllipse(c, BOSS_R + 13, BOSS_R + 13)
            if info.get("adds") and end <= t <= end + 2.5 and boss:
                c = self.to_screen(boss[0], boss[1])
                self._text(p, QPointF(c.x(), c.y() - BOSS_R - 54), tr("Invoca adds ×{0}", info['adds']), f.name,
                           QColor(MECH_COLORS["adds"]), above=True)

    def _cast_bar(self, p, t):
        """The boss's cast bar at the top, like the game's: its name, how full, and if it was cut short."""
        got = self.replay.bar(t)
        if not got:
            return
        m, k = got
        start, end, caster, name, kind, cut, info = m
        f = self.fonts
        w, h = min(420.0, self.width() - 80.0), 16.0
        x, y = (self.width() - w) / 2, 34.0
        color = QColor(MECH_COLORS.get(kind, "#ff8a3d"))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(6, 10, 20, 210))
        p.drawRoundedRect(QRectF(x - 8, y - 26, w + 16, h + 34), 8, 8)
        p.setFont(f.name)
        done = t >= end
        p.setPen(theme.MUTED if cut and done else QColor("#ffd27a"))
        title = name + ("  · interrumpido" if cut and done else "")
        p.drawText(QRectF(x, y - 22, w, 18), Qt.AlignLeft | Qt.AlignVCenter, title)
        p.setFont(f.small)
        p.setPen(theme.MUTED)
        p.drawText(QRectF(x, y - 22, w, 18), Qt.AlignRight | Qt.AlignVCenter,
                   f"{min(t, end) - start:.1f} / {end - start:.1f} s · {MECH_LABELS.get(kind, '')}")
        p.setBrush(QColor(255, 255, 255, 25))
        p.drawRoundedRect(QRectF(x, y, w, h), 4, 4)
        fill = QColor(120, 130, 150) if cut and done else color
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(x, y, w * k, h), 4, 4)


class Timeline(QWidget):
    """The fight's time: the boss's HP behind, a red skull at each death (hover: who), ticks at the boss's casts;
    click or drag to jump."""
    moved = Signal(float)

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.replay = window.replay
        self.fonts = window.fonts
        self.t = 0.0
        self.setMinimumHeight(64)
        self.setMouseTracking(True)
        self._pressed = False

    def _x(self, t):
        return 12 + (self.width() - 24) * (t / max(1.0, self.replay.length))

    def _t(self, x):
        return max(0.0, min(self.replay.length, (x - 12) / max(1.0, self.width() - 24) * self.replay.length))

    def _skulls(self):
        """[(x, death)] of every death shown."""
        return [(self._x(d[0]), d) for d in self.replay.deaths]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.replay
        top, bottom = 26, self.height() - 14
        bar = QRectF(12, top, self.width() - 24, bottom - top)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#10131b"))
        p.drawRoundedRect(bar, 6, 6)
        boss = r.bosses[0]["eid"] if r.bosses else None
        rows = r.hp.get(boss) if boss is not None else None
        if rows:                                       # the boss's HP over the fight
            path = QPainterPath(QPointF(bar.left(), bar.bottom()))
            last = rows[0][1]
            path.lineTo(bar.left(), bar.bottom() - bar.height() * last)
            for at, frac in rows:
                x = self._x(at)
                path.lineTo(x, bar.bottom() - bar.height() * last)
                path.lineTo(x, bar.bottom() - bar.height() * frac)
                last = frac
            path.lineTo(bar.right(), bar.bottom() - bar.height() * last)
            path.lineTo(bar.right(), bar.bottom())
            path.closeSubpath()
            p.setBrush(QColor(255, 90, 79, 55))
            p.drawPath(path)
        if r.mechs:                                    # the boss's casts: one bar each, its colour its kind
            for m in r.mechs:
                x0, x1 = self._x(m[0]), max(self._x(m[1]), self._x(m[0]) + 2)
                c = QColor(MECH_COLORS.get(m[4], "#8a93a6"))
                c.setAlpha(110 if m[5] else 230)
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawRect(QRectF(x0, bar.top() + 2, x1 - x0, 7))
        else:
            for c in r.casts:                          # simulations saved before 1.48
                x = self._x(c[0])
                p.setPen(QPen(QColor(255, 210, 122, 150 if c[4] else 80), 1.5))
                p.drawLine(QPointF(x, bar.top() + 2), QPointF(x, bar.top() + 9))
        for b in r.breaks:                             # stagger broken
            x = self._x(b)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#6cb4ff"))
            p.drawPolygon([QPointF(x, bar.bottom() - 9), QPointF(x + 4, bar.bottom() - 5), QPointF(x, bar.bottom() - 1),
                           QPointF(x - 4, bar.bottom() - 5)])
        if r.rage is not None and r.rage <= r.length:  # the rage timer
            x = self._x(r.rage)
            p.setPen(QPen(QColor(255, 60, 60, 220), 2, Qt.DashLine))
            p.drawLine(QPointF(x, bar.top()), QPointF(x, bar.bottom()))
        x = self._x(self.t)                            # where the simulation is
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(46, 192, 242, 60))
        p.drawRect(QRectF(bar.left(), bar.top(), x - bar.left(), bar.height()))
        p.setPen(QPen(theme.ACCENT, 2))
        p.drawLine(QPointF(x, top - 4), QPointF(x, bottom + 4))
        for sx, d in self._skulls():                   # a red skull per death (gold: the boss)
            boss_death = any(b["eid"] == d[1] for b in r.bosses)
            draw_skull(p, QPointF(sx, top / 2 - 1), 16, theme.GOLD if boss_death else SKULL_RED)
        p.setFont(self.fonts.small)
        p.setPen(theme.MUTED)
        p.drawText(QRectF(12, bottom, 60, 14), Qt.AlignLeft | Qt.AlignVCenter, "0:00")
        p.drawText(QRectF(self.width() - 72, bottom, 60, 14), Qt.AlignRight | Qt.AlignVCenter, mmss(r.length))
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            hit = self._skull_at(event.position())
            self._pressed = True
            self.moved.emit(max(0.0, hit[0] - 2.0) if hit and event.position().y() < 26 else
                            self._t(event.position().x()))

    def mouseMoveEvent(self, event):
        if self._pressed:
            self.moved.emit(self._t(event.position().x()))
            return
        hit = self._skull_at(event.position())
        if hit:
            names = [self.replay.name_of(d[1]) for d in self.replay.deaths if abs(self._x(d[0]) - self._x(hit[0])) < 6]
            QToolTip.showText(event.globalPosition().toPoint(),
                              tr("{0} · murió ", mmss(hit[0])) + ", ".join(dict.fromkeys(names)), self)
            return
        tip = self._lane_tip(event.position())
        if tip:
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()

    def _lane_tip(self, pos):
        """What the cast bars, the stagger breaks and the rage line under the mouse are."""
        r, top = self.replay, 26
        bottom = self.height() - 14
        if not top <= pos.y() <= bottom:
            return None
        t = self._t(pos.x())
        slack = r.length / max(1.0, self.width()) * 4
        if pos.y() <= top + 11:
            here = [m for m in r.mechs if m[0] - slack <= t <= m[1] + slack]
            if here:
                return "\n".join(f"{mmss(m[0])} · {m[3]} ({m[1] - m[0]:.1f} s){' · interrumpido' if m[5] else ''}"
                                 for m in here[:4])
        if any(abs(b - t) <= slack * 2 for b in r.breaks):
            return tr("El jefe quedó aturdido (barra de aturdimiento rota)")
        if r.rage is not None and abs(r.rage - t) <= slack * 2:
            return tr("{0} · el jefe entra en furia (se acabó su tiempo)", mmss(r.rage))
        return None

    def mouseReleaseEvent(self, event):
        self._pressed = False

    def _skull_at(self, pos):
        if pos.y() > 30:
            return None
        best = min(self._skulls(), key=lambda s: abs(s[0] - pos.x()), default=None)
        return best[1] if best and abs(best[0] - pos.x()) <= 9 else None


class SimulationWindow(QWidget):
    def __init__(self, ctl, enc):
        super().__init__(None, Qt.Window)
        self.ctl, self.enc = ctl, enc
        self.replay = Replay(enc.replay)
        self.fonts = Fonts(1.0)
        skills = getattr(ctl, "boss_skills", None)
        self.dungeon_map = skills.map_for([b.get("npc") for b in self.replay.bosses]) if skills else None
        self._map_pix = None
        self._pictures, self._faces, self._asked = {}, {}, {}
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Simulación: {1}", APP_NAME, enc.main_target or tr("Jefe")))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + SIDE_STYLE)
        self.resize(1280, 860)
        f, r = self.fonts, self.replay

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(34, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(tr("Simulación · {0}", enc.main_target or tr("Jefe")), f.name_big, theme.TEXT))
        deaths = sum(1 for d in r.deaths if any(p["eid"] == d[1] for p in r.people))
        when = time.strftime("%d/%m %H:%M", time.localtime(enc.start))
        bits = [tr(enc.place or "Mazmorra"), when, mmss(enc.fight_time(enc.group_names(set()))),
                tr("derrotado") if enc.killed else tr("sin derrotar"), tr("{0} muertes del grupo", deaths) if deaths else tr("sin muertes")]
        titles.addWidget(label(" · ".join(bits), f.small, theme.MUTED))
        head.addLayout(titles, 1)
        details = QPushButton(tr("Detalles del combate"))
        details.setProperty("flat", True)
        details.clicked.connect(lambda: ctl.open_dps_details(enc.id))
        head.addWidget(details)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.view = ArenaView(self)
        frame = QFrame()
        frame.setObjectName("Card")
        frame.setStyleSheet("QFrame#Card { background: #0a0c11; border: 1px solid #20262f; border-radius: 10px; }")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        fl.addWidget(self.view)
        body.addWidget(frame, 1)
        body.addWidget(self._side(), 0)
        root.addLayout(body, 1)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.play_button = QPushButton(tr("▶  Reproducir"))
        self.play_button.setProperty("primary", True)
        self.play_button.setMinimumWidth(140)
        self.play_button.clicked.connect(self.toggle)
        controls.addWidget(self.play_button)
        stop = QPushButton(tr("■  Detener"))
        stop.setToolTip(tr("Parar y volver al inicio"))
        stop.clicked.connect(self.stop)
        controls.addWidget(stop)
        self.clock = label("0:00 / " + mmss(r.length), f.name, theme.TEXT)
        controls.addWidget(self.clock)
        controls.addStretch(1)
        self.speed = QComboBox()
        for value, text in SPEEDS:
            self.speed.addItem(text, value)
        self.speed.setCurrentIndex(1)
        self.speed.setToolTip(tr("Velocidad"))
        controls.addWidget(label(tr("Velocidad"), f.small, theme.MUTED))
        controls.addWidget(self.speed)
        trails = QCheckBox(tr("Rastro"))
        trails.setChecked(True)
        trails.setToolTip(tr("Una estela con los últimos 3 segundos de cada jugador"))
        trails.toggled.connect(self._trails)
        controls.addWidget(trails)
        root.addLayout(controls)
        self.timeline = Timeline(self)
        self.timeline.moved.connect(self.seek)
        root.addWidget(self.timeline)
        note = (tr("Vista desde arriba (cuadrícula de 5 m; rueda para acercar, arrastra para mover, doble clic para "
                   "encuadrar). Las posiciones vienen del tráfico del juego: la tuya es aproximada (el juego no te "
                   "envía tu propio movimiento). Áreas: dónde cayeron y a quién alcanzaron; su tamaño sale de esos "
                   "golpes."))
        for source in (getattr(ctl.boss_db, "credit", None), getattr(getattr(ctl, "boss_skills", None), "credit", None)):
            if source:
                note += " " + source["text"] + "."
        foot = label(note, f.small, theme.MUTED)
        foot.setWordWrap(True)
        root.addWidget(foot)

        self._timer = QTimer(self, interval=33, timeout=self._tick)
        self._last = None
        self._redraw = QTimer(self, interval=1500, timeout=self._pictures_check)
        self._redraw.start()
        self.seek(0.0)

    # --- side panel: who, the areas and the deaths ---
    def _side(self):
        f, r = self.fonts, self.replay
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(270)
        inner = QWidget()
        inner.setObjectName("SideBody")
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(6)
        col.addWidget(label(tr("GRUPO"), f.section, theme.MUTED))
        for person in r.people:
            died = sum(1 for d in r.deaths if d[1] == person["eid"])
            row = QHBoxLayout()
            dot = label("●", f.body, _class_color(person.get("class"), person.get("me")))
            row.addWidget(dot)
            row.addWidget(label(person["name"] + (tr(" (tú)") if person.get("me") else ""), f.body, theme.TEXT,
                                elide=True), 1)
            if died:
                icon = QLabel()
                icon.setPixmap(skull_pixmap(14, SKULL_RED, self.devicePixelRatioF()))
                row.addWidget(icon)
            row.addWidget(label(str(died) if died else "—", f.small, SKULL_RED if died else theme.MUTED))
            col.addLayout(row)
        kinds = {}
        for z in r.zones:
            kinds.setdefault((z[7], z[8], z[6]), 0)
            kinds[(z[7], z[8], z[6])] += 1
        named = {}
        if r.mechs:
            for m in r.mechs:
                key = (m[3], m[4])
                named.setdefault(key, []).append(m[0])
        else:
            for c in r.casts:
                key = (c[4] or tr("Habilidad ({0:g} s de recarga)", c[3]), "cast")
                named.setdefault(key, []).append(c[0])
        if kinds:
            col.addSpacing(6)
            col.addWidget(label(tr("ÁREAS DEL JEFE"), f.section, theme.MUTED))
            for (name, color, kind), n in sorted(kinds.items(), key=lambda kv: -kv[1]):
                row = QHBoxLayout()
                row.addWidget(label("◯" if kind == "ring" else "⬤", f.body, QColor(color)))
                row.addWidget(label(name, f.body, theme.TEXT, elide=True), 1)
                row.addWidget(label(f"×{n}", f.small, theme.MUTED))
                col.addLayout(row)
        if named:
            col.addSpacing(6)
            col.addWidget(label(tr("LO QUE LANZÓ (clic: la siguiente)"), f.section, theme.MUTED))
            for (name, kind), times in sorted(named.items(), key=lambda kv: -len(kv[1]))[:16]:
                button = QPushButton(f"■ {name}  ×{len(times)}")
                button.setProperty("flat", True)
                button.setToolTip(MECH_LABELS.get(kind, ""))
                button.setStyleSheet(f"text-align: left; color: {MECH_COLORS.get(kind, '#ffd27a')};")
                button.clicked.connect(lambda _=False, ts=sorted(times): self._next_of(ts))
                col.addWidget(button)
        if r.fails:
            col.addSpacing(6)
            col.addWidget(label(tr("ERRORES AL SEPARARSE"), f.section, theme.MUTED))
            for at, owner, victim in r.fails[:20]:
                button = QPushButton(tr("{0}  la marca de {1} le pegó a {2}", mmss(at), owner, victim))
                button.setProperty("flat", True)
                button.setStyleSheet("text-align: left; color: #ffb347;")
                button.clicked.connect(lambda _=False, at=at: self.seek(max(0.0, at - 3.0)))
                col.addWidget(button)
        if r.deaths:
            col.addSpacing(6)
            col.addWidget(label(tr("MUERTES (clic para verla)"), f.section, theme.MUTED))
            for d in r.deaths:
                boss = any(b["eid"] == d[1] for b in r.bosses)
                button = QPushButton(f" {mmss(d[0])}  {r.name_of(d[1])}")
                button.setIcon(QIcon(skull_pixmap(16, theme.GOLD if boss else SKULL_RED, self.devicePixelRatioF())))
                button.setProperty("flat", True)
                button.setStyleSheet(f"text-align: left; color: {(theme.GOLD if boss else SKULL_RED).name()};")
                button.clicked.connect(lambda _=False, at=d[0]: self.seek(max(0.0, at - 3.0)))
                col.addWidget(button)
        col.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _next_of(self, times):
        """Jump to the next time a mechanic is cast (from where the simulation is; after the last, the first)."""
        now = self.view.t
        nxt = next((at for at in times if at > now + 0.5), times[0])
        self.seek(max(0.0, nxt - 1.0))

    # --- pictures ---
    def picture(self, person):
        """The player's profile picture (or class icon) cut in a circle, or None."""
        name = person["name"]
        if name in self._pictures:
            return self._pictures[name]
        ctl, pix, crop = self.ctl, None, None
        server = (getattr(ctl, "_roster_info", {}).get(name) or (None,))[0] or self._own_server()
        profile = ctl.armory.cached(name, server) if server else None
        path = ctl.armory.image(profile) if profile else None
        if path:
            pix = QPixmap(str(path))
        elif server and not self._asked.get(name):
            self._asked[name] = True
            ctl.armory.want(name, server)
        if pix is None or pix.isNull():
            slug = CLASS_ICON.get(person.get("class") or "")
            icon = Path(ctl.data_dir) / "class_icons" / f"{slug}.png" if slug else None
            pix = QPixmap(str(icon)) if icon and icon.exists() else None
            crop = None
        if pix is None or pix.isNull():
            return None
        out = _circle_pixmap(pix, 2 * PERSON_R, self.devicePixelRatioF(), crop)
        if path:
            self._pictures[name] = out                 # the class icon is kept until the picture arrives
        return out

    def _own_server(self):
        character = getattr(self.ctl, "character", None)
        return character.server if character else 2102

    def face(self, boss):
        npc = boss.get("npc")
        if npc in self._faces:
            return self._faces[npc]
        url = self.ctl.boss_db.face(npc) if npc else None
        path = self.ctl.armory.icon(url) if url else None
        if not path:
            return None
        img = QImage(str(path))
        if img.isNull():
            try:                                       # no WebP reader in Qt: decode it with Pillow
                from PIL import Image
                from PIL.ImageQt import ImageQt
                img = QImage(ImageQt(Image.open(path).convert("RGBA")))
            except Exception:                          # noqa: BLE001
                self._faces[npc] = None
                return None
        crop = (0.33, 0.04, 0.34) if "/art/" in url else None       # full-body art: the head
        self._faces[npc] = _circle_pixmap(QPixmap.fromImage(img), 2 * BOSS_R, self.devicePixelRatioF(), crop)
        return self._faces[npc]

    def map_pixmap(self):
        """The dungeon map's picture once downloaded (from the Waffle meter project, MIT), else None."""
        if self._map_pix is not None or not self.dungeon_map:
            return self._map_pix
        url = self.ctl.boss_skills.map_url + self.dungeon_map["image"]
        path = self.ctl.armory.icon(url)
        if path:
            pix = QPixmap(str(path))
            self._map_pix = pix if not pix.isNull() else None
        return self._map_pix

    def _pictures_check(self):
        """Pictures that arrive while the window is open (profiles, boss faces)."""
        missing = [p for p in self.replay.people if p["name"] not in self._pictures]
        missing_face = [b for b in self.replay.bosses if self._faces.get(b.get("npc")) is None]
        missing_map = bool(self.dungeon_map) and self.map_pixmap() is None
        if not missing and not missing_face and not missing_map:
            self._redraw.stop()
            return
        for b in missing_face:
            self._faces.pop(b.get("npc"), None)
        self.view.update()

    # --- playing ---
    def toggle(self):
        if self._timer.isActive():
            self._timer.stop()
            self.play_button.setText(tr("▶  Reproducir"))
        else:
            if self.view.t >= self.replay.length - 0.05:
                self.seek(0.0)
            self._last = time.monotonic()
            self._timer.start()
            self.play_button.setText(tr("⏸  Pausa"))

    def stop(self):
        self._timer.stop()
        self.play_button.setText(tr("▶  Reproducir"))
        self.seek(0.0)

    def seek(self, t):
        t = max(0.0, min(self.replay.length, t))
        self.view.t = self.timeline.t = t
        self.clock.setText(f"{mmss(t)} / {mmss(self.replay.length)}")
        self.view.update()
        self.timeline.update()

    def _tick(self):
        now = time.monotonic()
        dt = now - (self._last or now)
        self._last = now
        t = self.view.t + dt * (self.speed.currentData() or 1.0)
        if t >= self.replay.length:
            self.seek(self.replay.length)
            self.toggle()
            return
        self.seek(t)

    def _trails(self, on):
        self.view.trails = on
        self.view.update()

    def closeEvent(self, event):
        self._timer.stop()
        self._redraw.stop()
        super().closeEvent(event)
