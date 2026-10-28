"""Radar de cubos: the hidden cubes the game announces around you, on a Dragon Radar.

The device (RadarScreen) is the one from the anime: a dark hand-held body, a round pale-green screen with a square
grid, you in the centre as a small arrow, the cubes as blinking amber blips, a big round button on top that changes
the range (50 m to 800 m; the wheel does too), and a beep that quickens as you get closer. North is up (the game's
coordinates: +x east, +y south, as the world maps draw them).

Two homes: a page of the AionDex window (CubeRadarWindow: the device, what it sees, the «estoy frente a un cubo»
mark and the objects to confirm as cubes, the learned ones) and a small floating device over the game
(FloatingRadar, from the HUD's menu). Both read the same tracker (cubes.CubeTracker) and your position
(whereami.OwnPosition).
"""
import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from .. import APP_NAME
from ..cubes import KNOWN
from ..mapdata import MAP_NAMES
from ..gathering import GAME_MAPS
from . import theme
from .design import Card, PageHeader, Toggle
from .mapwindow import STYLE
from .theme import Fonts
from .widgets import label
from ..i18n import tr

RANGES = (50, 100, 200, 400, 800)      # metres from you to the edge of the screen
BEEP_FAR_MS, BEEP_NEAR_MS = 2600, 420   # between beeps with a cube at the edge / right on you
FIX_STALE = 45                          # seconds: an older position is drawn, but doubted on screen
_last_beep = [0.0]

BODY_DARK, BODY_LIGHT, BODY_EDGE = QColor("#262a2f"), QColor("#4a5058"), QColor("#0d0f12")
FACE_CENTRE, FACE_EDGE, FACE_RIM = QColor("#dff2a6"), QColor("#9ccb57"), QColor("#2f4f1d")
GRID, GRID_SOFT = QColor("#4a7a2c"), QColor(74, 122, 44, 110)
INK = QColor("#1f3a12")                  # text and «you» on the face
BLIP, BLIP_EDGE, BLIP_GLOW = QColor("#f5b31c"), QColor("#7a4e00"), QColor(245, 179, 28, 90)
OTHER = QColor(47, 79, 29, 150)
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


class RadarScreen(QWidget):
    """The Dragon Radar itself. set_data() feeds it; the knob (or the wheel) changes the range."""
    range_changed = Signal(int)
    knob_pressed = Signal()

    def __init__(self, fonts, parent=None, compact=False):
        super().__init__(parent)
        self.fonts = fonts
        self.compact = compact
        self.range_index = 2
        self.show_others = True
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
        dists = [c["dist"] for c in self.snap.get("cubes", []) if "dist" in c]
        return min(dists) if dists else None

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
        centre = face.center()
        radius = face_d / 2
        p.setPen(QPen(FACE_RIM, side * 0.028))
        p.setBrush(QColor("#0a0c0f"))
        p.drawEllipse(face.adjusted(-side * 0.02, -side * 0.02, side * 0.02, side * 0.02))
        fg = QRadialGradient(centre, radius)
        fg.setColorAt(0, FACE_CENTRE)
        fg.setColorAt(1, FACE_EDGE)
        p.setPen(Qt.NoPen)
        p.setBrush(fg)
        p.drawEllipse(face)
        clip = QPainterPath()
        clip.addEllipse(face)
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
        sweep = QRadialGradient(centre, radius)
        sweep.setColorAt(0, QColor(255, 255, 255, 0))
        sweep.setColorAt(1, QColor(255, 255, 255, 50))
        p.setPen(Qt.NoPen)
        p.setBrush(sweep)
        p.drawPie(face, int(-self._sweep * 16), -40 * 16)
        # what is on it
        reach = self.reach
        scale = radius / reach
        fix = self.fix
        blink = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(time.monotonic() * 5.0))
        if fix is not None:
            fx, fy = fix[1], fix[2]
            if self.show_others:
                p.setBrush(OTHER)
                for obj in self.snap.get("objects", []):
                    dx, dy = (obj["x"] - fx) * scale, (obj["y"] - fy) * scale
                    if math.hypot(dx, dy) <= radius - 3:
                        p.drawEllipse(QPointF(centre.x() + dx, centre.y() + dy), 2.6, 2.6)
            for cube in self.snap.get("cubes", []):
                dx, dy = (cube["x"] - fx) * scale, (cube["y"] - fy) * scale
                d = math.hypot(dx, dy)
                if d <= radius - 6:
                    at = QPointF(centre.x() + dx, centre.y() + dy)
                    size = max(4.0, side * 0.03) * (1.1 if cube.get("opened_by") is None else 0.8)
                    glow = QColor(BLIP_GLOW)
                    glow.setAlphaF(0.35 * blink)
                    p.setPen(Qt.NoPen)
                    p.setBrush(glow)
                    p.drawEllipse(at, size * 2.4, size * 2.4)
                    col = QColor(BLIP)
                    col.setAlphaF(0.35 + 0.65 * blink)
                    p.setPen(QPen(BLIP_EDGE, 1.2))
                    p.setBrush(col)
                    p.drawEllipse(at, size, size)
                elif d > 0:
                    # out of range: a small arrow at the rim pointing to it
                    ux, uy = dx / d, dy / d
                    tip = QPointF(centre.x() + ux * (radius - 4), centre.y() + uy * (radius - 4))
                    base = QPointF(centre.x() + ux * (radius - 14), centre.y() + uy * (radius - 14))
                    px, py = -uy * 5, ux * 5
                    p.setPen(QPen(BLIP_EDGE, 1))
                    p.setBrush(BLIP)
                    p.drawPolygon(QPolygonF([tip, QPointF(base.x() + px, base.y() + py), QPointF(base.x() - px, base.y() - py)]))
            # you: a small arrow, north up
            you = QPolygonF([QPointF(centre.x(), centre.y() - 7), QPointF(centre.x() + 5, centre.y() + 6),
                             QPointF(centre.x(), centre.y() + 3), QPointF(centre.x() - 5, centre.y() + 6)])
            p.setPen(QPen(INK, 1))
            p.setBrush(INK)
            p.drawPolygon(you)
        # texts on the face
        font = QFont(self.fonts.family)
        font.setPointSizeF(max(6.5, side * 0.028 * 72 / 96))
        font.setBold(True)
        p.setFont(font)
        p.setPen(INK)
        p.drawText(QRectF(face.left(), face.bottom() - side * 0.12, face_d, side * 0.08), Qt.AlignHCenter | Qt.AlignVCenter,
                   tr("{0} m", reach))
        cubes = self.snap.get("cubes", [])
        if fix is None:
            msg = tr("Sin tu posición: entra a un mapa o muévete") if not self.status else self.status
        elif self.now - fix[0] > FIX_STALE:
            msg = tr("Tu posición es de hace {0} s", int(self.now - fix[0]))
        else:
            msg = ""
        if msg:
            font.setBold(False)
            p.setFont(font)
            p.drawText(QRectF(face.left() + side * 0.08, face.top() + side * 0.08, face_d - side * 0.16, side * 0.1),
                       Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap, msg)
        p.setClipping(False)
        # under the screen: how many cubes, the nearest
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#c8ccd2"))
        bottom = QRectF(body.left(), face.bottom() + side * 0.028, body.width(), side * 0.06)
        if cubes:
            near = self.nearest()
            text = tr("{0} cubo(s) a la vista", len(cubes)) + (tr(" · el más cerca a {0} m", int(near)) if near is not None else "")
        else:
            text = tr("ningún cubo a la vista") if fix is not None else tr("esperando al juego…") if not self.status else ""
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
        opts.addWidget(label(tr("Pitidos"), f.body, theme.TEXT))
        opts.addSpacing(f.px(12))
        self.others_toggle = Toggle(bool(ctl.settings.get("cube_radar_others", True)))
        self.others_toggle.toggled.connect(self._others_toggled)
        self.screen.show_others = self.others_toggle.isChecked()
        opts.addWidget(self.others_toggle)
        opts.addWidget(label(tr("Mostrar los demás objetos"), f.body, theme.TEXT), 1)
        self.state_card.body.addLayout(opts)
        col.addWidget(self.state_card)
        # cubes in view
        self.cubes_card = Card(f, tr("Cubos a la vista"))
        self.cubes_box = QVBoxLayout()
        self.cubes_box.setSpacing(f.px(4))
        self.cubes_card.body.addLayout(self.cubes_box)
        col.addWidget(self.cubes_card)
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

    def _others_toggled(self, on):
        self.ctl.settings["cube_radar_others"] = bool(on)
        self.ctl.settings.save()
        self.screen.show_others = bool(on)
        self.screen.update()

    def refresh(self):
        ctl = self.ctl
        now = time.time()
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
        lines.append(tr("Captura: {0}", ctl.capture_status))
        self.state_lines.setText("\n".join(lines))
        # cubes
        key = tuple((c["eid"], c.get("opened_by") is not None, int(c.get("dist", -1) // 5)) for c in snap["cubes"])
        if key != self._rows_key:
            self._rows_key = key
            _clear(self.cubes_box)
            if not snap["cubes"]:
                self.cubes_box.addWidget(label(tr("Ninguno ahora. El juego avisa los objetos a unos 100 m: camina y mira el radar."),
                                              self.fonts.body, theme.MUTED))
            for c in snap["cubes"][:12]:
                where = tr("a {0} m al {1}", int(c["dist"]), c["point"]) if "dist" in c else tr("en ({0}, {1})", int(c["x"]), int(c["y"]))
                opened = tr(" · abierto por ti") if c.get("opened_by") == "me" else tr(" · alguien lo está abriendo") if c.get("opened_by") else ""
                self.cubes_box.addWidget(_Row(self.fonts, f"◆ {c['name']} {where}{opened}", theme.GOLD))
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
    """The device alone, small, over the game (from the HUD's menu). Drag it anywhere; right click for more."""

    def __init__(self, ctl):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(tr("Radar de cubos"))
        self.setStyleSheet(RADAR_STYLE)
        size = int(ctl.settings.get("cube_radar_size") or self.fonts.px(240))
        self.resize(size, int(size * 1.15))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.screen = RadarScreen(self.fonts, compact=True)
        self.screen.show_others = bool(ctl.settings.get("cube_radar_others", True))
        lay.addWidget(self.screen)
        self._drag = None
        pos = ctl.settings.get("cube_radar_pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.move(*pos)
        self._timer = QTimer(self, interval=500, timeout=self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()
        self.ctl.settings["cube_radar_pos"] = [self.x(), self.y()]
        self.ctl.settings.save()

    def refresh(self):
        ctl = self.ctl
        fix = ctl.bridge.position.fix
        snap = ctl.bridge.cubes.snapshot(fix)
        self.screen.set_data(snap, fix, "")
        beat(self.screen.nearest(), self.screen.reach, ctl.sounds, not ctl.sounds.config("cube_radar")["muted"])

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif event.button() == Qt.RightButton:
            self._menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:        # Ctrl + wheel: bigger or smaller device
            size = max(self.fonts.px(160), min(self.fonts.px(520), self.width() + (20 if event.angleDelta().y() > 0 else -20)))
            self.resize(size, int(size * 1.15))
            self.ctl.settings["cube_radar_size"] = size
            event.accept()
            return
        self.screen.wheelEvent(event)

    def _menu(self, at):
        from .appmenu import styled_menu
        ctl = self.ctl
        menu = styled_menu(self)
        menu.addAction(tr("Estoy frente a un cubo"), lambda: ctl.mark_cube("radar flotante"))
        menu.addAction(tr("Abrir la página del radar"), lambda: ctl.open_cube_radar())
        menu.addAction(tr("Alcance: {0} m (cambiar)", self.screen.reach), self.screen.cycle_range)
        muted = ctl.sounds.config("cube_radar")["muted"]
        menu.addAction(tr("Activar los pitidos") if muted else tr("Silenciar los pitidos"),
                       lambda: ctl.sounds.toggle_mute("cube_radar"))
        others = menu.addAction(tr("Mostrar los demás objetos"))
        others.setCheckable(True)
        others.setChecked(self.screen.show_others)
        others.toggled.connect(self._others)
        menu.addSeparator()
        menu.addAction(tr("Cerrar el radar"), self.hide)
        menu.exec(at)

    def _others(self, on):
        self.screen.show_others = bool(on)
        self.ctl.settings["cube_radar_others"] = bool(on)
        self.ctl.settings.save()
        self.screen.update()
