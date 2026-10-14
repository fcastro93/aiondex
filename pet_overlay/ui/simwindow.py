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
from ..simulation import Replay
from . import theme
from .dps_panel import CLASS_ICON, mmss
from .dpswindow import CLASS_COLOR
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import label

SPEEDS = ((0.5, "×0.5"), (1.0, "×1"), (2.0, "×2"), (4.0, "×4"), (8.0, "×8"))
PERSON_R = 17           # px: a player's circle
BOSS_R = 24
ADD_R = 6
TRAIL = 3.0             # seconds of trail behind each player
GRID = 5.0              # metres between grid lines
BG = QColor("#0a1120")
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
        self._grid(p)
        r, t = self.replay, self.t
        self._zones(p, t)
        self._casts(p, t)
        if self.trails:
            for person in r.people:
                self._trail(p, person, t)
        for eid in r.adds:
            self._add(p, eid, t)
        for boss in r.bosses:
            self._boss(p, boss, t)
        for person in sorted(r.people, key=lambda q: q.get("me", False)):      # you on top
            self._person(p, person, t)
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
        name = person["name"] + (" (tú)" if me else "")
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
        self._text(p, QPointF(center.x(), center.y() + BOSS_R + 6), boss["name"], self.fonts.small,
                   QColor("#ffb3ad"), background=True)
        cast = r.casting(eid, t)
        if cast and not dead:
            name = cast[4] or f"Habilidad ({cast[3]:g} s)"
            self._text(p, QPointF(center.x(), center.y() - BOSS_R - 8), name, self.fonts.name,
                       QColor("#ffd27a"), above=True)


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
        p.setBrush(QColor("#0d1628"))
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
        for c in r.casts:                              # the boss's casts
            x = self._x(c[0])
            p.setPen(QPen(QColor(255, 210, 122, 150 if c[4] else 80), 1.5))
            p.drawLine(QPointF(x, bar.top() + 2), QPointF(x, bar.top() + 9))
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
                              f"{mmss(hit[0])} · murió " + ", ".join(dict.fromkeys(names)), self)
        else:
            QToolTip.hideText()

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
        self._pictures, self._faces, self._asked = {}, {}, {}
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — Simulación: {enc.main_target or 'Jefe'}")
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
        titles.addWidget(label(f"Simulación · {enc.main_target or 'Jefe'}", f.name_big, theme.TEXT))
        deaths = sum(1 for d in r.deaths if any(p["eid"] == d[1] for p in r.people))
        when = time.strftime("%d/%m %H:%M", time.localtime(enc.start))
        bits = [enc.place or "Mazmorra", when, mmss(enc.fight_time(enc.group_names(set()))),
                "derrotado" if enc.killed else "sin derrotar", f"{deaths} muertes del grupo" if deaths else "sin muertes"]
        titles.addWidget(label(" · ".join(bits), f.small, theme.MUTED))
        head.addLayout(titles, 1)
        details = QPushButton("Detalles del combate")
        details.setProperty("flat", True)
        details.clicked.connect(lambda: ctl.open_dps_details(enc.id))
        head.addWidget(details)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.view = ArenaView(self)
        frame = QFrame()
        frame.setObjectName("Card")
        frame.setStyleSheet("QFrame#Card { background: #0a1120; border: 1px solid #1c2944; border-radius: 10px; }")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        fl.addWidget(self.view)
        body.addWidget(frame, 1)
        body.addWidget(self._side(), 0)
        root.addLayout(body, 1)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.play_button = QPushButton("▶  Reproducir")
        self.play_button.setProperty("primary", True)
        self.play_button.setMinimumWidth(140)
        self.play_button.clicked.connect(self.toggle)
        controls.addWidget(self.play_button)
        stop = QPushButton("■  Detener")
        stop.setToolTip("Parar y volver al inicio")
        stop.clicked.connect(self.stop)
        controls.addWidget(stop)
        self.clock = label("0:00 / " + mmss(r.length), f.name, theme.TEXT)
        controls.addWidget(self.clock)
        controls.addStretch(1)
        self.speed = QComboBox()
        for value, text in SPEEDS:
            self.speed.addItem(text, value)
        self.speed.setCurrentIndex(1)
        self.speed.setToolTip("Velocidad")
        controls.addWidget(label("Velocidad", f.small, theme.MUTED))
        controls.addWidget(self.speed)
        trails = QCheckBox("Rastro")
        trails.setChecked(True)
        trails.setToolTip("Una estela con los últimos 3 segundos de cada jugador")
        trails.toggled.connect(self._trails)
        controls.addWidget(trails)
        root.addLayout(controls)
        self.timeline = Timeline(self)
        self.timeline.moved.connect(self.seek)
        root.addWidget(self.timeline)
        note = ("Vista desde arriba (cuadrícula de 5 m; rueda para acercar, arrastra para mover, doble clic para "
                "encuadrar). Las posiciones vienen del tráfico del juego: la tuya es aproximada (el juego no te envía "
                "tu propio movimiento). Áreas: dónde cayeron y a quién alcanzaron; su tamaño sale de esos golpes.")
        credit = getattr(ctl.boss_db, "credit", None)
        if credit:
            note += " " + credit["text"] + "."
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
        col.addWidget(label("GRUPO", f.section, theme.MUTED))
        for person in r.people:
            died = sum(1 for d in r.deaths if d[1] == person["eid"])
            row = QHBoxLayout()
            dot = label("●", f.body, _class_color(person.get("class"), person.get("me")))
            row.addWidget(dot)
            row.addWidget(label(person["name"] + (" (tú)" if person.get("me") else ""), f.body, theme.TEXT,
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
        for c in r.casts:
            named.setdefault(c[4] or f"Habilidad ({c[3]:g} s de recarga)", 0)
            named[c[4] or f"Habilidad ({c[3]:g} s de recarga)"] += 1
        if kinds:
            col.addSpacing(6)
            col.addWidget(label("ÁREAS DEL JEFE", f.section, theme.MUTED))
            for (name, color, kind), n in sorted(kinds.items(), key=lambda kv: -kv[1]):
                row = QHBoxLayout()
                row.addWidget(label("◯" if kind == "ring" else "⬤", f.body, QColor(color)))
                row.addWidget(label(name, f.body, theme.TEXT, elide=True), 1)
                row.addWidget(label(f"×{n}", f.small, theme.MUTED))
                col.addLayout(row)
        if named:
            col.addSpacing(6)
            col.addWidget(label("LO QUE LANZÓ", f.section, theme.MUTED))
            for name, n in sorted(named.items(), key=lambda kv: -kv[1])[:12]:
                row = QHBoxLayout()
                row.addWidget(label(name, f.body, QColor("#ffd27a"), elide=True), 1)
                row.addWidget(label(f"×{n}", f.small, theme.MUTED))
                col.addLayout(row)
        if r.deaths:
            col.addSpacing(6)
            col.addWidget(label("MUERTES (clic para verla)", f.section, theme.MUTED))
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

    def _pictures_check(self):
        """Pictures that arrive while the window is open (profiles, boss faces)."""
        missing = [p for p in self.replay.people if p["name"] not in self._pictures]
        missing_face = [b for b in self.replay.bosses if self._faces.get(b.get("npc")) is None]
        if not missing and not missing_face:
            self._redraw.stop()
            return
        for b in missing_face:
            self._faces.pop(b.get("npc"), None)
        self.view.update()

    # --- playing ---
    def toggle(self):
        if self._timer.isActive():
            self._timer.stop()
            self.play_button.setText("▶  Reproducir")
        else:
            if self.view.t >= self.replay.length - 0.05:
                self.seek(0.0)
            self._last = time.monotonic()
            self._timer.start()
            self.play_button.setText("⏸  Pausa")

    def stop(self):
        self._timer.stop()
        self.play_button.setText("▶  Reproducir")
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
