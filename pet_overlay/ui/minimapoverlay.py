"""The route over the game's minimap: a click-through window right on top of the minimap (where minimap.py found
it) that draws the recommended route of the material you gather, its nodes as AionDex knows them now (available,
used up, found empty) and a line to the next one with its distance. It moves with you between the minimap reads
(smoothed), and screen captures leave it out (SetWindowDisplayAffinity), so the minimap reader keeps seeing the game
under it - and so do your screenshots and streams. Between two reads it moves with your movement keys (motion.py).

The mob route (Pets: «Ruta de mobs en el minimapa», mobs.py): the spawn points of the monsters you farm around
you, a route through the closest ones (orange), each one alive (orange), dead with the seconds until it comes back
(a pink ring: you killed it; grey: someone else did) or "ya" when it is due, and a line to the next one to kill.

It stays put: a read that fails (the minimap not recognized for a moment, the zone's name not read) keeps what was
drawn, moving with you, for up to HOLD seconds; it hides only when the game is minimized or something covers the
minimap (or you leave the map), and only after HIDE_AFTER seconds of it, so it does not blink. Everything is drawn
inside the minimap: inside your 4 calibrated corners (Ajustes > Minimapa) or its box, and when the next node is out
of the minimap, an arrow on its edge points to it."""
import ctypes
import logging
import math
import time
from ctypes import wintypes

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..gathering import GAME_MAPS
from ..minimap import covered, fallback_geometry, overlay_geometry
from ..motion import Motion
from ..whereami import to_world
from ..i18n import tr

log = logging.getLogger(__name__)
_user32 = ctypes.windll.user32
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.IsIconic.argtypes = [wintypes.HWND]

WDA_EXCLUDEFROMCAPTURE = 0x11
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
ROUTE = QColor("#2ec0f2")
UP = QColor("#3ee6b0")
GONE = QColor(225, 228, 236)
NEXT = QColor("#fff27a")
MOB = QColor("#ff8a3d")             # the mob route and the monsters alive
MOB_DEAD = QColor(60, 60, 66, 235)
MINE = QColor(255, 92, 240)
TICK_MS = 33                     # ~30 frames a second
DATA_EVERY = 1.0                 # seconds between two looks at the nodes
ROUTE_EVERY = 30.0               # seconds between two route computations (bot marks can change it)
CAMERA_FRESH = 1.5               # seconds the camera direction read from the minimap is trusted
HOLD = 60.0                      # seconds it keeps drawing without a new minimap read (moving with your keys)
HIDE_AFTER = 1.5                 # seconds something must be wrong before it hides (no blinking)
LOOK_EVERY = 0.5                 # seconds between two checks that the minimap is in sight
MOVE_MIN = 3                     # pixels the minimap's box must move before the window follows (no jitter)
KEY_FORWARD, KEY_BACK = (0x57, 0x26), (0x53, 0x28)          # W / up, S / down
KEY_LEFT, KEY_RIGHT = (0x41, 0x25), (0x44, 0x27)            # A / left, D / right


class MinimapOverlay(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.ctl = ctl
        self.material = None
        self.mobs_on = False             # the mob route (pets)
        self._mobs = None                # {"route": [...], "points": [...]} from the Controller
        self._map = None
        self._route = []                 # [(x m, y m)] of the recommended area's loop
        self._points = []                # [(x m, y m)] every point of the material on the map
        self._nodes = []                 # [(x m, y m, state)] live
        self._route_at = 0.0
        self._data_at = 0.0
        self._box = None                 # the minimap on screen (real pixels)
        self._poly = None                # your calibrated corners (real pixels) or None
        self._ppm = None                 # minimap pixels per metre
        self._geo = None                 # (box, ppm, corners) last read from the minimap, and when
        self._geo_at = 0.0
        self._wrong_since = None         # since when it should hide
        self._hidden_game = False        # the game minimized or the minimap covered (checked every LOOK_EVERY)
        self._looked_at = 0.0
        self._shown = None               # (x, y) metres drawn at the minimap's centre
        self._motion = Motion()          # where you are between two reads (keys, camera, speed)
        self._hwnd = None
        self._font = QFont("Segoe UI", 9, QFont.Bold)
        self._timer = QTimer(self, interval=TICK_MS, timeout=self._tick)

    # --- on / off ---
    def reload(self):
        """The route changed (another area chosen): load it at the next tick."""
        self._route_at = 0.0

    def set_material(self, material):
        """Draw this material's route over the minimap (None: stop)."""
        self.material = material
        self._route, self._points, self._nodes = [], [], []
        self._route_at = self._data_at = 0.0
        self._switch()

    def set_mobs(self, on):
        """Draw the mob route (the monsters you farm) over the minimap, or stop."""
        self.mobs_on = bool(on)
        self._mobs = None
        self._data_at = 0.0
        self._switch()

    def _switch(self):
        if self.material or self.mobs_on:
            self._timer.start()
            self._tick()
        else:
            self._timer.stop()
            self.ctl.minimap.fast = False
            self.hide()

    # --- every tick ---
    def _tick(self):
        ctl = self.ctl
        ctl.minimap_wanted_until = time.time() + 3
        ctl.minimap.fast = True
        now = time.time()
        geo = overlay_geometry(ctl.minimap, now)
        if geo is not None:
            self._geo, self._geo_at = geo, now
        elif self._geo is not None and now - self._geo_at > HOLD:
            self._geo = None
        if self._geo is None:                   # never read yet, or lost for long: where the minimap is, at its
            self._geo = fallback_geometry(ctl.minimap)       # usual zoom, centred on the game's own position
            self._geo_at = now
        position = ctl.bridge.position
        map_id = GAME_MAPS.get(position.game_map)
        fix = position.fix
        if self._geo is not None and now - self._looked_at > LOOK_EVERY:
            self._looked_at = now
            self._hidden_game = self._game_hidden(self._geo[0])
        if self._geo is None or map_id is None or fix is None or now - fix[0] > HOLD or self._hidden_game:
            self._wrong_since = self._wrong_since or now
            if now - self._wrong_since > HIDE_AFTER or map_id is None or self._geo is None:
                self.hide()
            return
        self._wrong_since = None
        box, ppm, poly = self._geo
        if self.material and (map_id != self._map or now - self._route_at > ROUTE_EVERY):
            self._load_route(map_id, position.game_map)
            self._route_at = now
        if now - self._data_at > DATA_EVERY:
            self._nodes = [(*to_world(position.game_map, r[0], r[1]), r[2])
                           for r in ctl.live_nodes(map_id, self.material)] if self.material else []
            self._mobs = ctl.mob_route_data(position.game_map) if self.mobs_on else None
            self._data_at = now
        elif self._mobs:                          # the seconds left tick every frame
            self.update()
        cone = position.cone
        camera = cone[1] if cone and now - cone[0] < CAMERA_FRESH else None
        self._shown = self._motion.update(now, fix, self._keys(), camera)
        self._ppm = ppm
        if not self.isVisible():
            self.show()
            self._exclude_from_capture()
        if self._box is None or max(abs(a - b) for a, b in zip(box, self._box)) >= MOVE_MIN or poly != self._poly:
            self._box, self._poly = box, poly
            self._place(box)
        self.update()

    def _game_hidden(self, box):
        """The game minimized, or another window over the minimap."""
        game = self.ctl.minimap.game_hwnd
        if not game:
            return True
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

    def _load_route(self, map_id, game_map):
        g = self.ctl.gathering
        to_m = lambda p: to_world(game_map, p[0], p[1])                 # noqa: E731
        areas, loose = g.areas(self.material, map_id)
        best = g.farm_area(self.material, map_id, areas)            # the one you chose, else the recommended
        self._route = [to_m(p) for p in best.route] if best else []
        self._points = [to_m(p) for a in areas for p in a.nodes] + [to_m(p) for p in loose]
        self._map = map_id

    # --- the window over the minimap ---
    def _place(self, box):
        """Over the minimap, in real screen pixels (Qt's own geometry is in scaled ones)."""
        hwnd = self._hwnd or int(self.winId())
        left, top, right, bottom = box
        if not _user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), left, top, right - left, bottom - top,
                                    SWP_NOACTIVATE | SWP_SHOWWINDOW):
            log.warning("could not put the route over the minimap (error %s)", ctypes.GetLastError())

    def _exclude_from_capture(self):
        hwnd = int(self.winId())
        if hwnd != self._hwnd:
            self._hwnd = hwnd
            if not _user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
                log.warning("the route over the minimap cannot be left out of screen captures")
            self.ctl.minimap.ignore.add(hwnd)

    # --- drawing ---
    def paintEvent(self, _event):
        if self._box is None or self._shown is None or not self._ppm:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dpr = self.devicePixelRatioF()
        p.scale(1 / dpr, 1 / dpr)                           # draw in real pixels, like the minimap
        w, h = self._box[2] - self._box[0], self._box[3] - self._box[1]
        if self._poly:                                      # inside your corners exactly
            outline = QPolygonF([QPointF(x - self._box[0], y - self._box[1]) for x, y in self._poly])
            clip = QPainterPath()
            clip.addPolygon(outline)
            clip.closeSubpath()
            p.setClipPath(clip)
            inner = outline.boundingRect()
        else:
            p.setClipRect(QRectF(0, 0, w, h))
            inner = QRectF(0, 0, w, h)
        sx, sy = self._shown
        k = self._ppm
        unit = h / 330                                      # the minimap's 4K size is the reference

        def at(x, y):
            return QPointF(w / 2 + (x - sx) * k, h / 2 + (y - sy) * k)

        if len(self._route) > 1:
            path = QPainterPath(at(*self._route[0]))
            for q in self._route[1:] + self._route[:1]:
                path.lineTo(at(*q))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(5, 10, 20, 170), 5 * unit))
            p.drawPath(path)
            p.setPen(QPen(ROUTE, 2.6 * unit, Qt.DashLine))
            p.drawPath(path)
        states = {}
        for x, y, state in self._nodes:
            states[(round(x), round(y))] = state
        drawn = set()
        free = []
        for x, y, state in self._nodes:
            self._node(p, at(x, y), state, unit)
            drawn.add((round(x / 3), round(y / 3)))
            if state == "up":
                free.append((x, y))
        for x, y in self._points:
            if (round(x / 3), round(y / 3)) in drawn:
                continue
            gone = any(math.dist((x, y), (a, b)) < 4 for a, b, s in self._nodes if s in ("used", "empty"))
            self._node(p, at(x, y), "gone" if gone else "point", unit)
            if not gone:
                free.append((x, y))
        if free:                                            # the next one: the closest node not known to be taken
            nx, ny = min(free, key=lambda q: math.dist(q, (sx, sy)))
            target, centre = at(nx, ny), QPointF(w / 2, h / 2)
            margin = 14 * unit
            room = inner.adjusted(margin, margin, -margin, -margin)
            text = f"{math.dist((nx, ny), (sx, sy)):.0f} m"
            p.setPen(QPen(NEXT, 2.2 * unit))
            p.setBrush(Qt.NoBrush)
            if room.contains(target):
                p.drawLine(centre, target)
                p.drawEllipse(target, 9 * unit, 9 * unit)
                spot = target + QPointF(11 * unit, -8 * unit)
            else:                                           # out of the minimap: an arrow on its edge
                edge = _to_edge(centre, target, room)
                p.drawLine(centre, edge)
                self._arrow(p, centre, edge, unit)
                spot = edge + QPointF(-30 * unit if edge.x() > centre.x() else 8 * unit,
                                      16 * unit if edge.y() < centre.y() else -8 * unit)
            spot.setX(min(max(spot.x(), room.left()), room.right() - 34 * unit))
            spot.setY(min(max(spot.y(), room.top() + 12 * unit), room.bottom()))
            self._label(p, spot, text, unit)
        if self._mobs:
            self._draw_mobs(p, at, inner, unit, (sx, sy), QPointF(w / 2, h / 2))
        p.end()

    def _draw_mobs(self, p, at, inner, unit, me, centre):
        """The mob route: the next kills planned (from you, numbered), each spawn point's state, the next one."""
        plan = self._mobs.get("plan") or []
        if plan:
            path = QPainterPath(centre)
            for x, y, *_ in plan:
                path.lineTo(at(x, y))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(5, 10, 20, 160), 4.6 * unit))
            p.drawPath(path)
            p.setPen(QPen(MOB, 2.2 * unit, Qt.DashLine))
            p.drawPath(path)
        now_left = time.time() - self._data_at          # the seconds left were computed this long ago
        r = 4.8 * unit
        for x, y, state, left, mine, _name in self._mobs.get("points") or []:
            c = at(x, y)
            if state == "up":
                p.setPen(QPen(QColor(255, 255, 255, 235), 1.5 * unit))
                p.setBrush(MOB)
                p.drawEllipse(c, r, r)
            elif state in ("dead", "soon"):
                p.setPen(QPen(MINE if mine else QColor(170, 170, 178), 1.8 * unit))
                p.setBrush(MOB_DEAD)
                p.drawEllipse(c, r, r)
                seconds = None if left is None else left - now_left
                text = "?" if seconds is None else tr("ya") if seconds <= 0 else \
                    f"{int(seconds)} s" if seconds < 60 else f"{int(seconds) // 60}:{int(seconds) % 60:02d}"
                self._label(p, c + QPointF(r + 3 * unit, 4 * unit), text, unit * 0.85,
                            MOB if seconds is not None and seconds <= 0 else NEXT)
            else:                                       # not seen yet / out of sight
                p.setPen(QPen(MOB, 1.4 * unit))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, r * 0.8, r * 0.8)
        room = inner.adjusted(14 * unit, 14 * unit, -14 * unit, -14 * unit)
        for i, (x, y, _name, _start, wait) in enumerate(plan):          # 1, 2, 3...: the order of the kills
            c = at(x, y)
            if not room.contains(c):
                if i == 0:                                  # the next one is off the minimap: an arrow to it
                    edge = _to_edge(centre, c, room)
                    p.setPen(QPen(MOB, 2 * unit))
                    self._arrow(p, centre, edge, unit)
                continue
            if i == 0:
                p.setPen(QPen(MOB, 2.2 * unit))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, 9 * unit, 9 * unit)
                if wait > 1:
                    self._label(p, c + QPointF(-10 * unit, -12 * unit), tr("espera {0} s", int(wait)), unit * 0.85, MOB)
            self._label(p, c + QPointF(-4 * unit, -7 * unit), str(i + 1), unit * 0.8, QColor(255, 255, 255))

    def _arrow(self, p, start, tip, unit):
        angle = math.atan2(tip.y() - start.y(), tip.x() - start.x())
        size = 16 * unit
        head = QPolygonF([tip, tip - QPointF(math.cos(angle - 0.45) * size, math.sin(angle - 0.45) * size),
                          tip - QPointF(math.cos(angle + 0.45) * size, math.sin(angle + 0.45) * size)])
        p.setPen(QPen(QColor(5, 10, 20, 220), 1.2 * unit))
        p.setBrush(NEXT)
        p.drawPolygon(head)

    def _node(self, p, c, state, unit):
        r = 4.5 * unit
        if state == "up":
            p.setPen(QPen(QColor(255, 255, 255, 240), 1.6 * unit))
            p.setBrush(UP)
            p.drawEllipse(c, r + unit, r + unit)
        elif state in ("used", "empty", "gone"):
            for pen in (QPen(QColor(5, 10, 20, 210), 4.4 * unit), QPen(GONE, 2 * unit)):
                p.setPen(pen)
                p.drawLine(c + QPointF(-r, -r), c + QPointF(r, r))
                p.drawLine(c + QPointF(-r, r), c + QPointF(r, -r))
        else:                                               # a known point, nothing seen there yet
            p.setPen(QPen(QColor(5, 10, 20, 200), 1.2 * unit))
            p.setBrush(QColor(ROUTE.red(), ROUTE.green(), ROUTE.blue(), 210))
            p.drawEllipse(c, r * 0.8, r * 0.8)

    def _label(self, p, at, text, unit, color=NEXT):
        font = QFont(self._font)
        font.setPixelSize(max(9, round(15 * unit)))
        path = QPainterPath()
        path.addText(at, font, text)
        p.setPen(QPen(QColor(5, 10, 20, 230), 3 * unit))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)


def _to_edge(centre, target, room):
    """Where the line from the centre to the target leaves the room (a rectangle around the centre)."""
    dx, dy = target.x() - centre.x(), target.y() - centre.y()
    t = 1.0
    if dx:
        t = min(t, ((room.right() if dx > 0 else room.left()) - centre.x()) / dx)
    if dy:
        t = min(t, ((room.bottom() if dy > 0 else room.top()) - centre.y()) / dy)
    t = max(0.0, t)
    return QPointF(centre.x() + dx * t, centre.y() + dy * t)
