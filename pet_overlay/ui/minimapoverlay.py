"""The route over the game's minimap: a click-through window right on top of the minimap (where minimap.py found
it) that draws the recommended route of the material you gather, its nodes as AionDex knows them now (available,
used up, found empty) and a line to the next one with its distance. It moves with you between the minimap reads
(smoothed), and screen captures leave it out (SetWindowDisplayAffinity), so the minimap reader keeps seeing the game
under it - and so do your screenshots and streams."""
import ctypes
import logging
import math
import time
from ctypes import wintypes

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..gathering import GAME_MAPS
from ..minimap import overlay_geometry
from ..whereami import SPAN

log = logging.getLogger(__name__)
_user32 = ctypes.windll.user32
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]

WDA_EXCLUDEFROMCAPTURE = 0x11
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
ROUTE = QColor("#2ec0f2")
UP = QColor("#3ee6b0")
GONE = QColor(225, 228, 236)
NEXT = QColor("#fff27a")
TICK_MS = 33                     # ~30 frames a second
DATA_EVERY = 1.0                 # seconds between two looks at the nodes
ROUTE_EVERY = 30.0               # seconds between two route computations (bot marks can change it)
FOLLOW = 0.5                     # how much of the way to the predicted position the drawing moves per frame
AHEAD = 0.5                      # seconds at most that the drawing runs ahead of the last read
STILL = 1.2                      # m/s under which you count as standing (the reads' own wobble)


class MinimapOverlay(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.ctl = ctl
        self.material = None
        self._map = None
        self._route = []                 # [(x m, y m)] of the recommended area's loop
        self._points = []                # [(x m, y m)] every point of the material on the map
        self._nodes = []                 # [(x m, y m, state)] live
        self._route_at = 0.0
        self._data_at = 0.0
        self._box = None                 # the minimap on screen (real pixels)
        self._ppm = None                 # minimap pixels per metre
        self._shown = None               # (x, y) metres drawn at the minimap's centre (smoothed)
        self._track = []                 # [(time, x, y)] the last reads, for your speed
        self._hwnd = None
        self._font = QFont("Segoe UI", 9, QFont.Bold)
        self._timer = QTimer(self, interval=TICK_MS, timeout=self._tick)

    # --- on / off ---
    def set_material(self, material):
        """Draw this material's route over the minimap (None: stop)."""
        self.material = material
        self._route_at = self._data_at = 0.0
        if material:
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
        position = ctl.bridge.position
        map_id = GAME_MAPS.get(position.game_map)
        fix = position.fix
        if geo is None or map_id is None or fix is None or now - fix[0] > 10:
            self.hide()
            return
        box, ppm = geo
        if map_id != self._map or now - self._route_at > ROUTE_EVERY:
            self._load_route(map_id, position.game_map)
            self._route_at = now
        if now - self._data_at > DATA_EVERY:
            span = SPAN[position.game_map]
            self._nodes = [((r[0] - 0.5) * span, (r[1] - 0.5) * span, r[2])
                           for r in ctl.live_nodes(map_id, self.material)]
            self._data_at = now
        target = self._predict(fix, now)
        if self._shown is None or math.dist(self._shown, target) > 60:
            self._shown = target
        else:
            self._shown = (self._shown[0] + (target[0] - self._shown[0]) * FOLLOW,
                           self._shown[1] + (target[1] - self._shown[1]) * FOLLOW)
        self._ppm = ppm
        if not self.isVisible():
            self.show()
            self._exclude_from_capture()
        if box != self._box:
            self._box = box
            self._place(box)
        self.update()

    def _predict(self, fix, now):
        """Where you are now: the last read moved on by your speed over the last second (the reads arrive a little
        late and a few times a second; drawing the last one as it is trails behind you)."""
        if not self._track or self._track[-1][0] != fix[0]:
            if self._track and math.dist(self._track[-1][1:], fix[1:3]) > 40:        # a teleport: start again
                self._track = []
            self._track.append((fix[0], fix[1], fix[2]))
            self._track = [p for p in self._track if fix[0] - p[0] <= 1.0][-12:]
        pts = self._track
        if len(pts) < 3 or pts[-1][0] - pts[0][0] < 0.25:
            return fix[1], fix[2]
        t0 = sum(p[0] for p in pts) / len(pts)
        den = sum((p[0] - t0) ** 2 for p in pts)
        vx = sum((p[0] - t0) * p[1] for p in pts) / den
        vy = sum((p[0] - t0) * p[2] for p in pts) / den
        speed = math.hypot(vx, vy)
        if speed < STILL or speed > 40:
            return fix[1], fix[2]
        # the line through the reads (less wobble than the last read alone), carried to now
        cx = sum(p[1] for p in pts) / len(pts) + vx * (fix[0] - t0)
        cy = sum(p[2] for p in pts) / len(pts) + vy * (fix[0] - t0)
        ahead = min(AHEAD, max(0.0, now - fix[0]))
        return cx + vx * ahead, cy + vy * ahead

    def _load_route(self, map_id, game_map):
        span = SPAN[game_map]
        g = self.ctl.gathering
        to_m = lambda p: ((p[0] - 0.5) * span, (p[1] - 0.5) * span)     # noqa: E731
        areas, loose = g.areas(self.material, map_id)
        best = next((a for a in areas if not a.bots), None)
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
            self.ctl.minimap.ignore = {hwnd}

    # --- drawing ---
    def paintEvent(self, _event):
        if self._box is None or self._shown is None or not self._ppm:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dpr = self.devicePixelRatioF()
        p.scale(1 / dpr, 1 / dpr)                           # draw in real pixels, like the minimap
        w, h = self._box[2] - self._box[0], self._box[3] - self._box[1]
        p.setClipRect(QRectF(0, 0, w, h))
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
            p.setPen(QPen(NEXT, 2.2 * unit))
            p.drawLine(centre, target)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(target, 9 * unit, 9 * unit)
            self._label(p, target + QPointF(11 * unit, -8 * unit), f"{math.dist((nx, ny), (sx, sy)):.0f} m", unit)
        p.end()

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

    def _label(self, p, at, text, unit):
        font = QFont(self._font)
        font.setPixelSize(max(9, round(15 * unit)))
        path = QPainterPath()
        path.addText(at, font, text)
        p.setPen(QPen(QColor(5, 10, 20, 230), 3 * unit))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(NEXT)
        p.drawPath(path)
