"""Pan/zoom world map with tiled background, spawn markers and where you are (QGraphicsView)."""
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsScene, QGraphicsView, QPushButton

from ..mapdata import MAP_NAMES
from . import theme
from .tiles import MAX_ZOOM

WORLD = 256.0 * 2 ** MAX_ZOOM   # scene units: normalized map coords * WORLD
BASE_ZOOM = 2                   # always-loaded low-res layer so the map never shows holes
YOU = QColor("#ff5cf0")         # your position: a colour no other marker uses
FRESH = 20                      # seconds a position counts as "now"
LOCATE_SPAN = 0.03              # how much of the map (normalized) "Dónde estoy" shows around you


class Marker(QGraphicsEllipseItem):
    def __init__(self, x, y, color, radius, tooltip, on_click=None, ring=False):
        super().__init__(-radius, -radius, radius * 2, radius * 2)
        self.setPos(x * WORLD, y * WORLD)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setBrush(QBrush(color))
        self.base_pen = QPen(QColor(255, 255, 255, 230) if ring else QColor(5, 10, 20, 220), 2 if ring else 1.2)
        self.setPen(self.base_pen)
        self.setToolTip(tooltip)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor if on_click else Qt.ArrowCursor)
        self.on_click = on_click
        self.base_radius = radius
        self.radius = radius
        self.base_z = 100
        self.group = None            # what the marker stands for (a pet, a monster of the shown pet)

    def set_emphasis(self, mode):
        """"focus": bigger with a white outline, above the rest; "dim": almost see-through; "normal"."""
        self.radius = self.base_radius * (1.35 if mode == "focus" else 1.0)
        self.setRect(-self.radius, -self.radius, 2 * self.radius, 2 * self.radius)
        self.setOpacity(0.14 if mode == "dim" else 1.0)
        self.setPen(QPen(QColor(255, 255, 255, 245), 2.6) if mode == "focus" else
                    QPen(Qt.NoPen) if mode == "dim" else self.base_pen)
        self.setZValue(self.base_z + (500 if mode == "focus" else 0))

    def hoverEnterEvent(self, _):
        r = self.radius * 1.5
        self.setRect(-r, -r, 2 * r, 2 * r)

    def hoverLeaveEvent(self, _):
        r = self.radius
        self.setRect(-r, -r, 2 * r, 2 * r)

    def mousePressEvent(self, event):
        if self.on_click and event.button() == Qt.LeftButton:
            self.on_click()
            event.accept()
        else:
            super().mousePressEvent(event)


class YouMarker(QGraphicsItem):
    """You: a dot with a pulsing halo and "Tú", the same size at any zoom; faded when the position is old."""
    R = 7

    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(900)
        self.setAcceptHoverEvents(True)
        self.fresh = True
        self.font = QFont("Segoe UI", 9, QFont.Bold)

    def boundingRect(self):
        return QRectF(-30, -30, 70, 60)

    def paint(self, p, _option, _widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        if self.fresh:
            k = (time.time() % 1.6) / 1.6                       # the halo grows and fades every 1.6 s
            halo = QColor(YOU)
            halo.setAlpha(round(150 * (1 - k)))
            p.setPen(QPen(halo, 2))
            p.setBrush(Qt.NoBrush)
            r = self.R + 4 + 16 * k
            p.drawEllipse(QPointF(0, 0), r, r)
        soft = QColor(YOU)
        soft.setAlpha(70)
        p.setPen(Qt.NoPen)
        p.setBrush(soft)
        p.drawEllipse(QPointF(0, 0), self.R + 4, self.R + 4)
        p.setPen(QPen(QColor(255, 255, 255, 240), 2.4))
        p.setBrush(YOU if self.fresh else QColor(YOU.red(), YOU.green(), YOU.blue(), 120))
        p.drawEllipse(QPointF(0, 0), self.R, self.R)
        path = QPainterPath()
        path.addText(QPointF(self.R + 6, 4), self.font, "Tú")
        p.setPen(QPen(QColor(5, 10, 20, 230), 3.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawPath(path)


class MapView(QGraphicsView):
    zoom_changed = Signal(float)

    def __init__(self, tiles, parent=None):
        super().__init__(parent)
        self.tiles = tiles
        self.setScene(QGraphicsScene(0, 0, WORLD, WORLD, self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(theme.WINDOW_BG)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.map_id = None
        self.tile_items = {}
        self.markers = []
        self._refresh = QTimer(self, singleShot=True, interval=60, timeout=self._update_tiles)
        tiles.tile_ready.connect(self._on_tile)
        self.you = YouMarker()
        self.you.hide()
        self.scene().addItem(self.you)
        self.you_point = None
        self.follow = False              # "Dónde estoy" keeps the map on you until you drag it
        self._away = None                # (map name, callback): you are on another map the window can show
        self._pulse = QTimer(self, interval=50, timeout=self._pulse_tick)
        self.locate = QPushButton("◎  Dónde estoy", self)
        self.locate.setCursor(Qt.PointingHandCursor)
        self.locate.setStyleSheet(
            f"QPushButton {{ background: rgba(11,18,34,225); color: {theme.TEXT.name()}; border: 1px solid "
            f"{YOU.name()}; border-radius: 8px; padding: 5px 12px; font-weight: 600; }} "
            f"QPushButton:hover {{ background: rgba(40,20,48,235); }}")
        self.locate.clicked.connect(self._locate_clicked)
        self.locate.hide()

    # --- map & tiles ---------------------------------------------------------------------
    def set_map(self, map_id):
        if map_id == self.map_id:
            return
        for item in self.tile_items.values():
            self.scene().removeItem(item)
        self.tile_items.clear()
        self.tiles.cancel_pending()
        self.map_id = map_id
        n = 2 ** BASE_ZOOM
        for x in range(n):
            for y in range(n):
                self._place(BASE_ZOOM, x, y)
        self._refresh.start()

    def _place(self, z, x, y):
        key = (z, x, y)
        if key in self.tile_items or self.map_id is None:
            return
        px = self.tiles.pixmap(self.map_id, z, x, y)
        if px is None:
            return
        size = WORLD / 2 ** z
        item = self.scene().addPixmap(px)
        item.setTransformationMode(Qt.SmoothTransformation)
        # a hair of overlap hides the seams between neighbouring tiles when scaled
        item.setScale((size + size / px.width()) / px.width())
        item.setPos(x * size, y * size)
        item.setZValue(z)
        self.tile_items[key] = item

    def _on_tile(self, map_id, z, x, y):
        if map_id == self.map_id:
            self._place(z, x, y)

    def _current_zoom(self):
        scale = self.transform().m11()
        return max(BASE_ZOOM, min(MAX_ZOOM, round(math.log2(max(scale * WORLD / 256 * 1.0, 1e-6)) + 0.3)))

    def _update_tiles(self):
        if self.map_id is None:
            return
        z = self._current_zoom()
        n = 2 ** z
        size = WORLD / n
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        x0, x1 = max(0, int(rect.left() // size)), min(n - 1, int(rect.right() // size))
        y0, y1 = max(0, int(rect.top() // size)), min(n - 1, int(rect.bottom() // size))
        # request the centre first so the area being looked at fills in first
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        cells = sorted(((x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)),
                       key=lambda c: -((c[0] - cx) ** 2 + (c[1] - cy) ** 2))
        for x, y in cells:
            self._place(z, x, y)

    # --- markers ---------------------------------------------------------------------------
    def clear_markers(self):
        for m in self.markers:
            self.scene().removeItem(m)
        self.markers = []

    def add_marker(self, x, y, color, radius, tooltip, on_click=None, ring=False, z=100, group=None):
        m = Marker(x, y, color, radius, tooltip, on_click, ring)
        m.setZValue(z)
        m.base_z, m.group = z, group
        self.scene().addItem(m)
        self.markers.append(m)
        return m

    def emphasize(self, wanted):
        """Outline the markers for which wanted(group) is true and fade the others; wanted None = all normal."""
        for m in self.markers:
            m.set_emphasis("normal" if wanted is None else "focus" if wanted(m.group) else "dim")

    def focus_points(self, points, max_zoom_scale=1.6):
        """Fit the view around normalized points (or the whole map when empty)."""
        if points:
            xs = [p[0] * WORLD for p in points]
            ys = [p[1] * WORLD for p in points]
            rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
            pad = max(rect.width(), rect.height(), WORLD / 64) * 0.35
            rect = rect.adjusted(-pad, -pad, pad, pad)
        else:
            rect = QRectF(0, 0, WORLD, WORLD)
        self.fitInView(rect, Qt.KeepAspectRatio)
        limit = max_zoom_scale * 256 * 2 ** MAX_ZOOM / WORLD
        if self.transform().m11() > limit:
            self.resetTransform()
            self.scale(limit, limit)
            self.centerOn(rect.center())
        self._refresh.start()
        self.zoom_changed.emit(self.transform().m11())

    # --- interaction -----------------------------------------------------------------------
    def mousePressEvent(self, event):
        if self.follow and event.button() == Qt.LeftButton:     # dragging the map: stop following
            self.follow = False
            if self.you_point is not None:
                self.locate.setText("◎  Dónde estoy")
                self.locate.adjustSize()
                self._place_locate()
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        factor = 1.25 ** (event.angleDelta().y() / 120)
        scale = self.transform().m11() * factor
        fit = min(self.viewport().width(), self.viewport().height()) / WORLD
        if scale < fit * 0.8 or scale > 4.0 * 256 * 2 ** MAX_ZOOM / WORLD:
            return
        self.scale(factor, factor)
        self._refresh.start()
        self.zoom_changed.emit(self.transform().m11())

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._refresh.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh.start()
        self._place_locate()

    def center_on_norm(self, x, y):
        self.centerOn(QPointF(x * WORLD, y * WORLD))
        self._refresh.start()

    # --- you -------------------------------------------------------------------------------
    def set_you(self, point, tip="", fresh=True, away=None):
        """Your position (normalized) on the map shown, or None. away: (map name, callback) when you are on another
        map the window has a tab for: the button then takes you there."""
        moved = point is not None and point != self.you_point
        self.you_point, self._away = point, away
        if point is None:
            self.follow = False
        elif moved and self.follow:
            self.center_on_norm(*point)
        if point is None:
            self.you.hide()
            self._pulse.stop()
        else:
            self.you.setPos(point[0] * WORLD, point[1] * WORLD)
            self.you.setToolTip(tip)
            if self.you.fresh != fresh:
                self.you.fresh = fresh
                self.you.update()
            self.you.show()
            if fresh and not self._pulse.isActive():
                self._pulse.start()
            elif not fresh:
                self._pulse.stop()
        self.locate.setText(("◉  Siguiéndote" if self.follow else "◎  Dónde estoy") if point else
                            f"◎  Estás en {away[0]}" if away else "")
        self.locate.setToolTip((tip + ("\nArrastra el mapa para dejar de seguirte." if self.follow else
                                       "\nClic: centrar el mapa en ti y seguirte.")) if point else
                               "Ver ese mapa" if away else "")
        self.locate.setVisible(bool(point or away))
        self.locate.adjustSize()
        self._place_locate()

    def _pulse_tick(self):
        if self.isVisible() and self.you.isVisible():
            self.you.update()

    def _locate_clicked(self):
        if self.you_point is None:
            if self._away:
                self._away[1]()
            return
        x, y = self.you_point
        self.follow = True
        self.locate.setText("◉  Siguiéndote")
        self.locate.adjustSize()
        self._place_locate()
        shown = self.mapToScene(self.viewport().rect()).boundingRect()
        if min(shown.width(), shown.height()) > LOCATE_SPAN * WORLD * 1.6:      # zoom in when far out
            half = LOCATE_SPAN / 2
            self.focus_points([(x - half, y - half), (x + half, y + half)])
        self.center_on_norm(x, y)

    def _place_locate(self):
        self.locate.move(self.viewport().width() - self.locate.width() - 12, 12)


def show_you(view, ctl, map_id, tab_maps, go_to_tab):
    """Your position on a map window: on the map shown when you are there; else, when one of its tabs is the map you
    are on, a button to it (go_to_tab(index)). Called about once a second."""
    here = ctl.where_am_i() if hasattr(ctl, "where_am_i") else None
    if not here:
        view.set_you(None)
        return
    spot, u, v, age, exact, source = here
    when = "ahora" if age < FRESH else f"hace {age:.0f} s" if age < 90 else f"hace {age / 60:.0f} min"
    how = "del minimapa" if source == "minimapa" else "exacta" if exact else "aprox."
    tip = f"Tú estás aquí ({when}, {how})"
    status = getattr(ctl, "minimap_status", lambda: "")()
    if status:
        tip += "\n" + status
    if spot == map_id:
        view.set_you((u, v), tip, fresh=age < FRESH)
    elif spot in tab_maps:
        index = tab_maps.index(spot)
        view.set_you(None, tip, away=(MAP_NAMES.get(spot, spot).split(" (")[0], lambda: go_to_tab(index)))
    else:
        view.set_you(None)
