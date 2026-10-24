"""Pan/zoom world map with tiled background, spawn markers and where you are (QGraphicsView). The wheel zooms where
the mouse points, a double click zooms in there, dragging moves the map, + / − / 0 on the keyboard, and the zoom bar
in the bottom-right corner (−, the zoom, +, «Ajustar»); the tiles of the zoom shown load as you look (ui/tiles.py).
Used by the Mapa de pets (ui/mapwindow.py), the gathering map (ui/gathermap.py) and the Mapa del mundo
(ui/worldmap.py)."""
import math
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (QCheckBox, QGraphicsEllipseItem, QGraphicsItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLabel, QPushButton, QWidget)

from ..mapdata import MAP_NAMES
from . import theme
from .tiles import MAX_ZOOM
from ..i18n import tr

WORLD = 256.0 * 2 ** MAX_ZOOM   # scene units: normalized map coords * WORLD
BASE_ZOOM = 2                   # always-loaded low-res layer so the map never shows holes
YOU = QColor("#ff5cf0")         # your position: a colour no other marker uses
FRESH = 20                      # seconds a position counts as "now"
CONE_R = 58                     # pixels of the view cone around you
CONE_FRESH = 6                  # seconds the cone read from the minimap is shown
LOCATE_SPAN = 0.03              # how much of the map (normalized) "Dónde estoy" shows around you
ZOOM_STEP = 1.6                 # one press of + / − (or a double click) zooms this much
WHEEL_STEP = 1.25               # one notch of the wheel
MAX_SCALE = 4.0 * 256 * 2 ** MAX_ZOOM / WORLD      # up to 4x the sharpest tiles
KEEP_TILES = 260                # sharper tiles kept in the scene; past that, the ones far from the view go


def rgba(color, alpha):
    """A theme colour as a stylesheet rgba() (alpha 0-255)."""
    return f"rgba({color.red()},{color.green()},{color.blue()},{alpha})"


OVERLAY_BG = rgba(theme.SURFACE, 232)      # the panels floating over the map


class ZoomBar(QWidget):
    """The map's zoom controls, over its bottom-right corner: −, the zoom (100% = the whole map in the view), +, and
    «Ajustar» (the whole map, or what the window calls home)."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(
            f"ZoomBar {{ background: {OVERLAY_BG}; border: 1px solid {theme.LINE.name()}; border-radius: 8px; }}"
            f"QPushButton {{ background: transparent; color: {theme.TEXT.name()}; border: none; border-radius: 6px;"
            f" padding: 3px 10px; font-weight: 700; font-size: 12pt; min-width: 14px; }}"
            f"QPushButton:hover {{ background: {rgba(theme.ACCENT, 40)}; color: {theme.ACCENT.name()}; }}"
            f"QPushButton:disabled {{ color: {theme.LINE.name()}; }}"
            f"QPushButton#Fit {{ font-size: 8.5pt; font-weight: 600; color: {theme.MUTED.name()}; padding: 3px 9px; }}"
            f"QPushButton#Fit:hover {{ color: {theme.TEXT.name()}; }}"
            f"QLabel {{ color: {theme.MUTED.name()}; background: transparent; min-width: 46px; font-size: 8.5pt; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)
        self.minus = QPushButton("−")
        self.minus.setToolTip(tr("Alejar (rueda del ratón hacia abajo, tecla −)"))
        self.minus.clicked.connect(lambda: view.zoom_by(1 / ZOOM_STEP, animate=True))
        lay.addWidget(self.minus)
        self.text = QLabel("")
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setToolTip(tr("Zoom respecto al mapa entero. Rueda del ratón: acercar o alejar donde apuntas; "
                                "doble clic: acercar ahí; arrastrar: mover el mapa."))
        lay.addWidget(self.text)
        self.plus = QPushButton("+")
        self.plus.setToolTip(tr("Acercar (rueda del ratón hacia arriba, tecla +, o doble clic en el mapa)"))
        self.plus.clicked.connect(lambda: view.zoom_by(ZOOM_STEP, animate=True))
        lay.addWidget(self.plus)
        fit = QPushButton(tr("Ajustar"))
        fit.setObjectName("Fit")
        fit.setToolTip(tr("Ver el mapa entero (tecla 0)"))
        fit.clicked.connect(view.fit_home)
        lay.addWidget(fit)
        for b in (self.minus, self.plus, fit):
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
        view.zoom_changed.connect(self._zoomed)

    def _zoomed(self, scale):
        fit = max(1e-9, self.view.fit_scale())
        self.text.setText(f"{round(100 * scale / fit):,}%".replace(",", "."))
        self.plus.setEnabled(scale < MAX_SCALE * 0.999)
        self.minus.setEnabled(scale > fit * 0.81)
        self.adjustSize()
        self.view._place_locate()


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
        self.cone = None                 # (direction, width) in degrees, screen angles (0 east, 90 south)
        self.font = QFont("Segoe UI", 9, QFont.Bold)

    def boundingRect(self):
        return QRectF(-CONE_R - 2, -CONE_R - 2, 2 * CONE_R + 40, 2 * CONE_R + 4)

    def paint(self, p, _option, _widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        if self.cone:                                          # where the camera looks, as in the game's minimap
            direction, width = self.cone
            fill = QRadialGradient(QPointF(0, 0), CONE_R)
            fill.setColorAt(0.0, QColor(YOU.red(), YOU.green(), YOU.blue(), 170))
            fill.setColorAt(1.0, QColor(YOU.red(), YOU.green(), YOU.blue(), 20))
            p.setPen(QPen(QColor(255, 255, 255, 170), 1.2))
            p.setBrush(fill)
            box = QRectF(-CONE_R, -CONE_R, 2 * CONE_R, 2 * CONE_R)
            p.drawPie(box, round(-(direction + width / 2) * 16), round(width * 16))
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
        path.addText(QPointF(self.R + 6, 4), self.font, tr("Tú"))
        p.setPen(QPen(QColor(5, 10, 20, 230), 3.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawPath(path)


class MapView(QGraphicsView):
    zoom_changed = Signal(float)
    show_me_changed = Signal(bool)       # «Mostrar mi ubicación» ticked or not (every map window shares it)

    def __init__(self, tiles, parent=None):
        super().__init__(parent)
        self.tiles = tiles
        self.setScene(QGraphicsScene(0, 0, WORLD, WORLD, self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
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
        self.locate = QPushButton(tr("◎  Dónde estoy"), self)
        self.locate.setCursor(Qt.PointingHandCursor)
        self.locate.setFocusPolicy(Qt.NoFocus)
        self.locate.setStyleSheet(
            f"QPushButton {{ background: {OVERLAY_BG}; color: {theme.TEXT.name()}; border: 1px solid "
            f"{YOU.name()}; border-radius: 8px; padding: 5px 12px; font-weight: 600; }} "
            f"QPushButton:hover {{ background: {rgba(YOU, 46)}; }} "
            f"QPushButton:disabled {{ color: {theme.MUTED.name()}; border-color: {theme.LINE.name()}; }}")
        self.locate.clicked.connect(self._locate_clicked)
        self.locate.setEnabled(False)
        self.locate.setToolTip(tr("Aún no sé dónde estás: lo leo del juego (sus mensajes y el minimapa) mientras "
                                  "juegas, con «Mostrar mi ubicación» marcado."))
        self.locate.adjustSize()
        self.me_box = QCheckBox(tr("Mostrar mi ubicación"), self)
        self.me_box.setChecked(True)
        self.me_box.setCursor(Qt.PointingHandCursor)
        self.me_box.setFocusPolicy(Qt.NoFocus)
        self.me_box.setToolTip(tr("Lee del juego dónde estás (sus mensajes y el minimapa) mientras el mapa está "
                                  "abierto y te pone en el mapa. Desmárcalo y no se lee (la ruta sobre el minimapa "
                                  "y el Modo Focus lo siguen leyendo si están puestos)."))
        self.me_box.setStyleSheet(
            f"QCheckBox {{ background: {OVERLAY_BG}; color: {theme.TEXT.name()}; border: 1px solid "
            f"{theme.LINE.name()}; border-radius: 8px; padding: 5px 10px; font-weight: 600; }} "
            f"QCheckBox:hover {{ border-color: {YOU.name()}; }} "
            f"QCheckBox::indicator {{ width: 12px; height: 12px; border: 1.5px solid {YOU.name()}; border-radius: 3px;"
            f" background: transparent; }} QCheckBox::indicator:checked {{ background: {YOU.name()}; }}")
        self.me_box.toggled.connect(self.show_me_changed.emit)
        self.me_box.adjustSize()
        self.me_box.move(12, 12)
        self._me_bound = False
        self.home = None                 # what «Ajustar» shows: a callable set by the window, else the whole map
        self._zoom_anim = QVariantAnimation(self)
        self._zoom_anim.setDuration(170)
        self._zoom_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._zoom_anim.valueChanged.connect(self._zoom_step)
        self._zoom_from = (1.0, 1.0, None)          # (start scale, factor, anchor point) of the running animation
        self.zoom_bar = ZoomBar(self)
        self.zoom_bar.adjustSize()
        self.setFocusPolicy(Qt.StrongFocus)

    # --- zoom ------------------------------------------------------------------------------
    def fit_scale(self):
        """The scale at which the whole map fits the view."""
        return min(self.viewport().width(), self.viewport().height()) / WORLD

    def _scale_to(self, target, at=None):
        """Set the zoom to `target` keeping the scene point under `at` (a viewport point; its centre by default)
        where it is."""
        scale = self.transform().m11()
        factor = target / scale
        if abs(factor - 1.0) < 1e-4:
            return False
        at = self.viewport().rect().center() if at is None else at
        before = self.mapToScene(at)
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.scale(factor, factor)
        self.setTransformationAnchor(anchor)
        after = self.mapToScene(at)
        centre = self.mapToScene(self.viewport().rect().center())
        self.centerOn(centre + (before - after))
        self._refresh.start()
        self.zoom_changed.emit(self.transform().m11())
        return True

    def zoom_by(self, factor, at=None, animate=False):
        """Zoom in (factor > 1) or out, around the view's centre (or `at`, a viewport point), within the map's
        limits: never smaller than the map fitting the view, never past 4x the sharpest tiles. `animate`: a short
        glide (buttons, keys, double click) instead of a jump (the wheel)."""
        if self._zoom_anim.state() == QVariantAnimation.Running:
            self._zoom_anim.stop()
            start, f, _ = self._zoom_from
            self._scale_to(start * f, self._zoom_from[2])                # finish the one under way first
        scale = self.transform().m11()
        target = max(self.fit_scale() * 0.8, min(MAX_SCALE, scale * factor))
        if abs(target / scale - 1.0) < 1e-4:
            return
        if not animate or not self.isVisible():
            self._scale_to(target, at)
            return
        self._zoom_from = (scale, target / scale, at)
        self._zoom_anim.setStartValue(0.0)
        self._zoom_anim.setEndValue(1.0)
        self._zoom_anim.start()

    def _zoom_step(self, value):
        start, factor, at = self._zoom_from
        self._scale_to(start * factor ** float(value), at)

    def fit_home(self):
        """«Ajustar»: what the window calls home (its own fit), else the whole map."""
        if callable(self.home):
            self.home()
        else:
            self.focus_points([])

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_by(ZOOM_STEP, animate=True)
        elif key in (Qt.Key_Minus, Qt.Key_Underscore):
            self.zoom_by(1 / ZOOM_STEP, animate=True)
        elif key == Qt.Key_0:
            self.fit_home()
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.zoom_by(ZOOM_STEP, at=event.position().toPoint(), animate=True)
            event.accept()
        elif event.button() == Qt.RightButton:
            self.zoom_by(1 / ZOOM_STEP, at=event.position().toPoint(), animate=True)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

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
        """The tile level whose pixels are closest to the screen's at this zoom."""
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
        self._prune_tiles(z, rect)

    def _prune_tiles(self, z, rect):
        """Drop the sharper tiles far from the view (or much sharper than the zoom needs) once there are many: a long
        pan at full zoom would otherwise keep hundreds of them in memory. The base layer always stays."""
        extra = [k for k in self.tile_items if k[0] > BASE_ZOOM]
        if len(extra) <= KEEP_TILES:
            return
        keep = rect.adjusted(-rect.width(), -rect.height(), rect.width(), rect.height())
        for key in extra:
            kz, x, y = key
            size = WORLD / 2 ** kz
            if kz > z + 1 or not QRectF(x * size, y * size, size, size).intersects(keep):
                self.scene().removeItem(self.tile_items.pop(key))

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
        if self._zoom_anim.state() == QVariantAnimation.Running:
            self._zoom_anim.stop()
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
                self.locate.setText(tr("◎  Dónde estoy"))
                self.locate.adjustSize()
                self._place_locate()
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y() * 4
        if not delta:
            return
        self.zoom_by(WHEEL_STEP ** (delta / 120), at=event.position().toPoint())
        event.accept()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._refresh.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh.start()
        self.zoom_changed.emit(self.transform().m11())
        self._place_locate()

    def center_on_norm(self, x, y):
        self.centerOn(QPointF(x * WORLD, y * WORLD))
        self._refresh.start()

    # --- you -------------------------------------------------------------------------------
    def set_you(self, point, tip="", fresh=True, away=None, cone=None, why=""):
        """Your position (normalized) on the map shown, or None. away: (map name, callback) when you are on another
        map the window has a tab for: the button then takes you there. cone: (direction, width) of the camera.
        why: when there is no position, the button's tooltip (why it is greyed)."""
        if cone != self.you.cone:
            self.you.cone = cone
            self.you.update()
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
        self.locate.setText((tr("◉  Siguiéndote") if self.follow else tr("◎  Dónde estoy")) if point else
                            tr("◎  Estás en {0}", away[0]) if away else tr("◎  Dónde estoy"))
        self.locate.setToolTip((tip + (tr("\nArrastra el mapa para dejar de seguirte.") if self.follow else
                                       tr("\nClic: centrar el mapa en ti y seguirte."))) if point else
                               tr("Ver ese mapa") if away else
                               why or tr("Aún no sé dónde estás: lo leo del juego (sus mensajes y el minimapa) "
                                         "mientras juegas, con «Mostrar mi ubicación» marcado."))
        self.locate.setEnabled(bool(point or away))
        self.locate.setVisible(True)
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
        self.locate.setText(tr("◉  Siguiéndote"))
        self.locate.adjustSize()
        self._place_locate()
        shown = self.mapToScene(self.viewport().rect()).boundingRect()
        if min(shown.width(), shown.height()) > LOCATE_SPAN * WORLD * 1.6:      # zoom in when far out
            half = LOCATE_SPAN / 2
            self.focus_points([(x - half, y - half), (x + half, y + half)])
        self.center_on_norm(x, y)

    def _place_locate(self):
        self.locate.move(self.viewport().width() - self.locate.width() - 12, 12)
        bar = getattr(self, "zoom_bar", None)
        if bar is not None:
            bar.move(self.viewport().width() - bar.width() - 12, self.viewport().height() - bar.height() - 12)
            bar.raise_()


def show_you(view, ctl, map_id, tab_maps, go_to_tab):
    """Your position on a map window: on the map shown when you are there; else, when one of its tabs is the map you
    are on, a button to it (go_to_tab(index)). Called about once a second. Nothing, and no reading, with «Mostrar mi
    ubicación» off (one setting for every map window)."""
    on = bool(ctl.settings.get("show_me", True)) if hasattr(ctl, "settings") else True
    if view.me_box.isChecked() != on:
        view.me_box.blockSignals(True)
        view.me_box.setChecked(on)
        view.me_box.blockSignals(False)
    if not view._me_bound and hasattr(ctl, "set_show_me"):
        view.show_me_changed.connect(ctl.set_show_me)
        view._me_bound = True
    if not on:
        view.set_you(None, why=tr("Marca «Mostrar mi ubicación» y el mapa te encuentra."))
        return
    here = ctl.where_am_i() if hasattr(ctl, "where_am_i") else None
    if not here:
        view.set_you(None)
        return
    spot, u, v, age, exact, source = here
    when = tr("ahora") if age < FRESH else tr("hace {0:.0f} s", age) if age < 90 else tr("hace {0:.0f} min", age / 60)
    how = tr("del minimapa") if source == "minimapa" else "exacta" if exact else "aprox."
    tip = tr("Tú estás aquí ({0}, {1})", when, how)
    status = getattr(ctl, "minimap_status", lambda: "")()
    if status:
        tip += "\n" + status
    view_cone = getattr(ctl, "view_cone", lambda: None)()
    if spot == map_id:
        view.set_you((u, v), tip, fresh=age < FRESH, cone=view_cone)
    elif spot in tab_maps:
        index = tab_maps.index(spot)
        view.set_you(None, tip, away=(MAP_NAMES.get(spot, spot).split(" (")[0], lambda: go_to_tab(index)))
    else:
        view.set_you(None, why=tr("Estás en {0}, un mapa que esta ventana no muestra.",
                                  MAP_NAMES.get(spot, spot).split(" (")[0]))
