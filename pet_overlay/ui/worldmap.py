"""Mapa del mundo: the 9 world maps with the points of the user's own map export (data/world_db.json, built by
tools/build_world_db.py): services, places, collectables, NPCs, gathering nodes and monsters, each category on or
off from the side panel (with its colour and how many there are), the zones' names and outlines, a search with a list
of results (a click centres the map on that one and rings it), a legend, and where you are; and the Tracker's field
and Abyss bosses with their state live (alive, back in 12 min...) over their spawn points (BossLabel; the Tracker's
«Mapa» opens it on one).

The map zooms with the wheel where the mouse points, with + / − / «Ajustar» and with a double click (ui/mapview.py).
All the points of a map are one scene item (AtlasItem) drawn at a fixed size on screen; with little zoom the points of
a category that would pile up join into a numbered circle (they split as you zoom in), and the zones' names and the
bosses' labels never overlap (the ones without room wait for more zoom). Hovering a point says what it is."""
import html
import json
import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QFrame, QGraphicsItem, QGraphicsPathItem, QHBoxLayout, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QToolTip, QVBoxLayout, QWidget)

from ..gathering import MAP_SIDE, SIDES
from ..mapdata import MAP_NAMES
from ..paths import DATA_DIR
from ..tracker import countdown
from . import theme
from .design import Card, Chip, ChipGroup, Divider, GameTabs, PageHeader, SearchBox, Toggle
from .mapview import WORLD, YOU, MapView, rgba, show_you
from .theme import Fonts
from .tiles import TileCache
from .widgets import label
from ..i18n import thousands, tr

# the groups of the side panel, in order, with the categories on when you first open it
GROUPS = (("service", tr("Servicios")), ("location", tr("Lugares")), ("collectable", tr("Coleccionables")),
          ("gatherable", tr("Recolección")), ("npc", "NPCs"), ("monster", tr("Monstruos")))
FIRST_ON = {"teleport", "storage", "shop", "shop-craft", "shop-conversion", "alchemy", "blacksmith", "cook",
            "jewelcraft", "tailor", "instance", "arena", "dungeon-entrance", "portal", "teleport-artifact", "rift",
            "stronghold", "lord-monolith", "named"}
COLORS = {
    "teleport": "#3fd3ff", "storage": "#8fb2ff", "shop": "#5ee0d0", "shop-craft": "#7bd88f",
    "shop-conversion": "#c6e05a", "alchemy": "#b48cff", "blacksmith": "#ff9f5a", "cook": "#ffcf5a",
    "jewelcraft": "#ff7aa8", "tailor": "#e0a0ff", "instance": "#f0c24e", "arena": "#ff6b6b", "event": "#ffd34d",
    "companion": "#9ad1ff",
    "dungeon-entrance": "#ffb347", "portal": "#4fc3f7", "sealed-dungeon": "#c97b4f", "flight-circle": "#a7e8ff",
    "stronghold": "#ff6b6b", "lord-monolith": "#ffe27a", "teleport-artifact": "#5ee0d0", "rift": "#d36bff",
    "abyss-artifact": "#ff8a3d",
    "monolith": "#e6d3ff", "hidden-cube": "#ff5cf0",
    "od": "#6fe3ff", "vegetable": "#9bd86f", "berry": "#ff7a9a", "jewelry": "#f5f0ff", "herb": "#5fd38a",
    "metal": "#c9b38a", "shellfish": "#ffd6a0", "tree": "#b5894f",
    "citizen": "#aab4c8", "district-quest": "#ffd34d", "hero-quest": "#ffb347",
    "named": "#ff4d4d", "elite": "#ff9f5a", "special": "#ffd34d", "trans": "#c38bff", "feral": "#d9a066",
    "intellect": "#7f9cff", "nature": "#7bd88f",
}
SIZE = {"service": 9, "location": 9, "collectable": 6, "gatherable": 6, "npc": 5, "monster": 5}   # dot, px
ORDER = ("monster", "npc", "gatherable", "collectable", "location", "service")                   # drawn last on top
NAMES_AT = 0.2                   # zoom (screen px per scene unit; the whole map is ~0.1) from which the smaller
                                 # zones' names show (when they have room)
HOVER_PX = 9                     # how close the mouse must be to a point to say what it is
CLUSTER_PX = 22                  # points of a category closer than this on screen join into one numbered circle
COUNT_FROM = 4                   # a group shows its number from this many points (smaller ones: a bigger dot)
EDGE = QColor(theme.WINDOW_BG.red(), theme.WINDOW_BG.green(), theme.WINDOW_BG.blue(), 225)


def cat_color(cat):
    return QColor(COLORS.get(cat, theme.TEXT.name()))


class _Groups:
    """A category's points at one zoom level: the lone ones (drawn as dots) and the groups (numbered circles), with
    a grid of cells to find what is under the mouse."""
    __slots__ = ("cell", "singles", "multis", "cells")

    def __init__(self, cell, singles, multis, cells):
        self.cell, self.singles, self.multis, self.cells = cell, singles, multis, cells


class AtlasItem(QGraphicsItem):
    """Every shown point of a map, drawn at a fixed size; with little zoom the close ones of a category join into a
    circle with their number. Hover tells what is under the mouse."""

    def __init__(self, world_db, map_id, fonts=None):
        super().__init__()
        self.setAcceptHoverEvents(True)
        self.setZValue(50)
        self.layers = {}                 # category -> (type, [QPointF], [name])
        self.shown = set()
        self.found = []                  # [QPointF] of the search result, drawn ringed
        self._cache = {}                 # (category, level) -> _Groups
        family = fonts.family if fonts else "Segoe UI"
        self.count_font = QFont(family, 7, QFont.Bold)
        types = {c: t for c, (_, t) in world_db.get("categories", {}).items()}
        for cat, rows in world_db.get("points", {}).get(map_id, {}).items():
            points, names = [], []
            for name, pts in rows:
                for u, v in pts:
                    points.append(QPointF(u * WORLD, v * WORLD))
                    names.append(name)
            self.layers[cat] = (types.get(cat, ""), points, names)

    def boundingRect(self):
        return QRectF(0, 0, WORLD, WORLD)

    @staticmethod
    def level(scale):
        """Zoom levels in half octaves: the groups are made once per level."""
        return round(math.log2(max(scale, 1e-6)) * 2)

    def groups(self, cat, level):
        key = (cat, level)
        got = self._cache.get(key)
        if got is not None:
            return got
        cell = CLUSTER_PX / 2 ** (level / 2)
        points = self.layers[cat][1]
        buckets = {}
        for i, q in enumerate(points):
            buckets.setdefault((int(q.x() // cell), int(q.y() // cell)), []).append(i)
        singles, multis, cells = [], [], {}
        for k, idx in buckets.items():
            if len(idx) == 1:
                cells[k] = ("s", idx[0])
                singles.append(points[idx[0]])
            else:
                x = sum(points[i].x() for i in idx) / len(idx)
                y = sum(points[i].y() for i in idx) / len(idx)
                cells[k] = ("m", len(multis))
                multis.append((QPointF(x, y), idx))
        got = _Groups(cell, QPolygonF(singles), multis, cells)
        self._cache[key] = got
        return got

    @staticmethod
    def radius(size, n):
        """On-screen radius of a group of n points of a category drawn at `size`."""
        return size / 2 + 2.0 + 1.5 * math.log2(max(2, n))

    def paint(self, p, _option, _widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        transform = p.worldTransform()
        level = self.level(transform.m11())
        for kind in ORDER:
            for cat, (typ, points, _) in self.layers.items():
                if typ != kind or cat not in self.shown or not points:
                    continue
                g = self.groups(cat, level)
                size = SIZE.get(typ, 5)
                color = cat_color(cat)
                if g.singles.count():
                    edge = QPen(EDGE, size + 2.4, Qt.SolidLine, Qt.RoundCap)
                    edge.setCosmetic(True)
                    p.setPen(edge)
                    p.drawPoints(g.singles)
                    fill = QPen(color, size, Qt.SolidLine, Qt.RoundCap)
                    fill.setCosmetic(True)
                    p.setPen(fill)
                    p.drawPoints(g.singles)
                if g.multis:                                  # numbered circles, drawn in screen pixels
                    p.save()
                    p.resetTransform()
                    p.setFont(self.count_font)
                    for centre, idx in g.multis:
                        c = transform.map(centre)
                        n = len(idx)
                        r = self.radius(size, n)
                        p.setPen(QPen(EDGE, 1.4))
                        p.setBrush(color)
                        p.drawEllipse(c, r, r)
                        if n >= COUNT_FROM:
                            p.setPen(theme.WINDOW_BG)
                            p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter,
                                       str(n) if n < 1000 else f"{n // 1000}k")
                    p.restore()
        if self.found:
            p.save()
            p.resetTransform()
            p.setBrush(Qt.NoBrush)
            for q in self.found:
                c = transform.map(q)
                p.setPen(QPen(EDGE, 4.2))
                p.drawEllipse(c, 11, 11)
                p.setPen(QPen(theme.ACCENT, 2.2))
                p.drawEllipse(c, 11, 11)
                p.setPen(QPen(theme.ACCENT_GLOW, 1.0))
                p.drawEllipse(c, 15, 15)
            p.restore()

    def nearest(self, pos, scale):
        """(category, "s", index) of the lone point, or (category, "m", group index) of the group, under pos (within
        HOVER_PX on screen), or None."""
        level = self.level(scale)
        reach = HOVER_PX / max(scale, 1e-6)
        best, best_d = None, reach
        for cat in self.shown:
            layer = self.layers.get(cat)
            if not layer or not layer[1]:
                continue
            g = self.groups(cat, level)
            size = SIZE.get(layer[0], 5)
            cx, cy = int(pos.x() // g.cell), int(pos.y() // g.cell)
            n = int(reach // g.cell) + 2
            for dx in range(-n, n + 1):
                for dy in range(-n, n + 1):
                    hit = g.cells.get((cx + dx, cy + dy))
                    if hit is None:
                        continue
                    what, j = hit
                    if what == "s":
                        q = layer[1][j]
                        d = math.hypot(q.x() - pos.x(), q.y() - pos.y())
                    else:
                        q, idx = g.multis[j]
                        d = max(0.0, math.hypot(q.x() - pos.x(), q.y() - pos.y()) - self.radius(size, len(idx)) / scale)
                    if d < best_d:
                        best, best_d = (cat, what, j), d
        return best

    def describe(self, hit, scale):
        """The tooltip (rich text) of what nearest() found."""
        cat, what, j = hit
        _, points, names = self.layers[cat]
        color = cat_color(cat).name()
        name_of = html.escape(self.label_of(cat))
        if what == "s":
            return (f"<b>{html.escape(names[j])}</b><br><span style='color:{color}'>●</span> "
                    f"<span style='color:{theme.MUTED.name()}'>{name_of}</span>")
        idx = self.groups(cat, self.level(scale)).multis[j][1]
        unique = list(dict.fromkeys(names[i] for i in idx))
        shown = ", ".join(html.escape(n) for n in unique[:6])
        more = tr(" y {0} más", len(unique) - 6) if len(unique) > 6 else ""
        return (f"<b><span style='color:{color}'>●</span> {len(idx)} × {name_of}</b><br>{shown}{html.escape(more)}"
                f"<br><span style='color:{theme.MUTED.name()}'>"
                + html.escape(tr("Acerca el zoom (rueda o doble clic) para separarlos")) + "</span>")

    def hoverMoveEvent(self, event):
        view = self.scene().views()[0] if self.scene() and self.scene().views() else None
        scale = view.transform().m11() if view else 1.0
        hit = self.nearest(event.pos(), scale)
        if hit:
            QToolTip.showText(event.screenPos(), self.describe(hit, scale))
        else:
            QToolTip.hideText()

    def hoverLeaveEvent(self, _event):
        QToolTip.hideText()

    label_of = staticmethod(lambda cat: cat)


class ZoneLabel(QGraphicsItem):
    """A zone's name centred on its spot, the same size at any zoom, in the game's serif."""

    def __init__(self, text, color, about, family):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(60)
        tip = f"<b>{html.escape(text)}</b>"
        if about:
            tip += "<br>" + html.escape(about).replace("\n", "<br>")
        self.setToolTip(tip)
        self.font = QFont(family, 10, QFont.DemiBold)
        self.path = QPainterPath()
        self.path.addText(QPointF(0, 0), self.font, text)
        box = self.path.boundingRect()
        self.path.translate(-box.center())
        self.color = color

    def boundingRect(self):
        return self.path.boundingRect().adjusted(-3, -3, 3, 3)

    def paint(self, p, _option, _widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(EDGE, 3.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(self.path)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawPath(self.path)


class BossLabel(QGraphicsItem):
    """A Tracker boss over its spawn point: a pill with a dot of its state's colour, its name and its state, the same
    size at any zoom, just above the point."""
    PAD = 6

    def __init__(self, entry, family="Segoe UI"):
        super().__init__()
        self.entry = entry
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(70)
        self.font = QFont(family, 8, QFont.DemiBold)
        self.small = QFont(family, 8)
        self.name, self.state, self.color = entry.name, "", QColor(theme.MUTED)
        self._box = QRectF()
        self._layout()

    def set_state(self, text, color):
        if text != self.state or color != self.color:
            self.prepareGeometryChange()
            self.state, self.color = text, QColor(color)
            self._layout()
            self.update()

    def _layout(self):
        w = QFontMetricsF(self.font).horizontalAdvance(self.name) + \
            (QFontMetricsF(self.small).horizontalAdvance("  " + self.state) if self.state else 0)
        h = QFontMetricsF(self.font).height()
        self._box = QRectF(-(w + 2 * self.PAD + 10) / 2, -h - 18, w + 2 * self.PAD + 10, h + 6)

    def boundingRect(self):
        return self._box.adjusted(-2, -2, 2, 14)

    def paint(self, p, _option, _widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        box = self._box
        p.setPen(QPen(QColor(self.color.red(), self.color.green(), self.color.blue(), 200), 1.2))
        p.setBrush(QColor(theme.SURFACE.red(), theme.SURFACE.green(), theme.SURFACE.blue(), 235))
        p.drawRoundedRect(box, box.height() / 2, box.height() / 2)
        p.drawLine(QPointF(0, box.bottom()), QPointF(0, -4))                  # a stem down to the point
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(QPointF(box.left() + self.PAD + 3, box.center().y()), 3.2, 3.2)
        x = box.left() + self.PAD + 10
        p.setFont(self.font)
        p.setPen(theme.TEXT)
        p.drawText(QRectF(x, box.top(), box.width(), box.height()), Qt.AlignLeft | Qt.AlignVCenter, self.name)
        if self.state:
            x += QFontMetricsF(self.font).horizontalAdvance(self.name)
            p.setFont(self.small)
            p.setPen(self.color)
            p.drawText(QRectF(x, box.top(), box.width(), box.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       "  " + self.state)


def boss_state(tracker, entry, now):
    """(short text, colour) of a Tracker boss for its map label."""
    st = tracker.status(entry, now)
    if st.state in ("vivo", "en curso"):
        return tr("vivo"), theme.GAIN
    if st.state in ("debería estar", "quizá"):
        return tr("¿vivo?"), theme.WARN
    if st.start is not None and st.start > now:
        return tr("en {0}", countdown(st.start - now)), theme.GOLD
    return tr("¿?"), theme.MUTED


class _Switch(Toggle):
    """A Toggle whose setChecked also tells (the app turns the bosses on through it)."""

    def setChecked(self, on):
        if bool(on) != self.isChecked():
            super().setChecked(on)
            self.toggled.emit(bool(on))


class _CatRow(QPushButton):
    """A category in the side panel: its colour as a dot (filled when shown), its name, how many points it has."""

    def __init__(self, fonts, text, count, color, on, parent=None):
        super().__init__(parent)
        self.fonts, self.text_, self.count, self.color = fonts, text, count, QColor(color)
        self.setCheckable(True)
        self.setChecked(on)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(fonts.px(24))
        self.setToolTip(tr("Clic: mostrar u ocultar «{0}» en el mapa", text))

    def sizeHint(self):
        return QSize(self.fonts.px(200), self.fonts.px(24))

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = self.fonts
        r = QRectF(self.rect())
        if self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 12))
            p.drawRoundedRect(r, 4, 4)
        on = self.isChecked()
        d = f.px(10)
        dot = QRectF(f.px(6), (r.height() - d) / 2, d, d)
        if on:
            p.setPen(QPen(EDGE, 1))
            p.setBrush(self.color)
        else:
            p.setPen(QPen(self.color, 1.4))
            p.setBrush(Qt.NoBrush)
        p.drawEllipse(dot)
        count = thousands(f"{self.count:,}")
        p.setFont(f.small)
        cw = QFontMetricsF(f.small).horizontalAdvance(count)
        p.setPen(theme.MUTED)
        p.drawText(QRectF(r.width() - cw - f.px(6), 0, cw, r.height()), Qt.AlignVCenter | Qt.AlignRight, count)
        p.setFont(f.body)
        x = dot.right() + f.px(8)
        room = r.width() - x - cw - f.px(14)
        text = QFontMetricsF(f.body).elidedText(self.text_, Qt.ElideRight, room)
        p.setPen(theme.TEXT if on else theme.MUTED)
        p.drawText(QRectF(x, 0, room, r.height()), Qt.AlignVCenter | Qt.AlignLeft, text)


class _Swatch(QWidget):
    """A small drawing for the legend: you, a group of points, a search result, a zone's name, a zone's border."""

    def __init__(self, fonts, kind, parent=None):
        super().__init__(parent)
        self.fonts, self.kind = fonts, kind
        self.setFixedSize(fonts.px(30), fonts.px(20))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.kind == "you":
            soft = QColor(YOU)
            soft.setAlpha(70)
            p.setPen(Qt.NoPen)
            p.setBrush(soft)
            p.drawEllipse(c, 8, 8)
            p.setPen(QPen(theme.TEXT, 1.8))
            p.setBrush(YOU)
            p.drawEllipse(c, 5, 5)
        elif self.kind == "group":
            p.setPen(QPen(EDGE, 1.4))
            p.setBrush(theme.ACCENT)
            p.drawEllipse(c, 8.5, 8.5)
            p.setFont(QFont(self.fonts.family, 7, QFont.Bold))
            p.setPen(theme.WINDOW_BG)
            p.drawText(QRectF(c.x() - 9, c.y() - 9, 18, 18), Qt.AlignCenter, "12")
        elif self.kind == "found":
            p.setPen(Qt.NoPen)
            p.setBrush(theme.TEXT)
            p.drawEllipse(c, 3, 3)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(theme.ACCENT, 2))
            p.drawEllipse(c, 7, 7)
        elif self.kind in ("village", "zone"):
            p.setFont(QFont(self.fonts.display_family, 10, QFont.DemiBold))
            p.setPen(theme.TEXT if self.kind == "village" else theme.GOLD)
            p.drawText(QRectF(self.rect()), Qt.AlignCenter, "Aa")
        elif self.kind == "border":
            pen = QPen(QColor(theme.TEXT.red(), theme.TEXT.green(), theme.TEXT.blue(), 150), 1.3, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(2, c.y()), QPointF(self.width() - 2, c.y()))


class WorldMapWindow(QWidget):
    """The Mapa del mundo page of the AionDex window."""

    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        hub = getattr(ctl, "hub", None)
        self.fonts = f = getattr(hub, "fonts", None) or Fonts(1.0)
        try:
            self.db = json.loads((DATA_DIR / "world_db.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.db = {"maps": {}, "points": {}, "regions": {}, "categories": {}}
        self.cats = {c: (name, typ) for c, (name, typ) in self.db.get("categories", {}).items()}
        AtlasItem.label_of = staticmethod(lambda cat: self.cats.get(cat, (cat, ""))[0])
        saved = ctl.settings.setdefault("world_map", {})
        if not saved.get("v25"):             # 2.5, the map redone: one fresh start if almost everything was hidden
            saved["v25"] = True
            if len(saved.get("shown") or ()) <= 1:
                saved.update(shown=sorted(FIRST_ON), zones=True, bosses=True)
        self.shown = set(saved.get("shown") or FIRST_ON)
        self.side = saved.get("side")
        self.maps = []                   # map ids in the tabs
        self.map_id = None
        self.atlas = None
        self.borders = []                # the zones' outlines
        self.zone_labels = []            # (ZoneLabel, rank)
        self.boss_items = []             # BossLabel of the Tracker's bosses on this map
        self.rows = {}                   # category -> _CatRow
        self.group_counts = {}           # type -> (count label, [categories])
        self.index = {}                  # (name, category) -> [QPointF] of this map, for the search
        self.results = []                # what the result list shows: (name, category, [QPointF])
        self._home = []                  # the normalized spots «Ajustar» fits
        self.setObjectName("WorldMap")
        self.setWindowIcon(ctl.app_icon)
        self.setWindowTitle(tr("Mapa del mundo"))
        self.resize(1360, 860)

        root = QVBoxLayout(self)
        root.setContentsMargins(f.px(18), f.px(12), f.px(18), f.px(12))
        root.setSpacing(f.px(6))
        self.header = PageHeader(f, tr("Mapa del mundo"), "")
        self.sides = ChipGroup(f, [(s, theme.FACTION_LABEL[s]) for s in SIDES], None, gold=True)
        self.sides.changed.connect(lambda: self._pick_side(self.sides.selected()))
        self.header.add_right(self.sides)
        self.panel_button = QPushButton("")
        self.panel_button.setProperty("ghost", True)
        self.panel_button.setCursor(Qt.PointingHandCursor)
        self.panel_button.clicked.connect(lambda: self._show_panel(not self.side_panel.isVisible()))
        self.header.add_right(self.panel_button)
        root.addWidget(self.header)
        self.tabs = GameTabs(f, [])
        self.tabs.changed.connect(self._select_map)
        root.addWidget(self.tabs)

        body = QHBoxLayout()
        body.setSpacing(f.px(10))
        frame = QFrame()
        frame.setObjectName("MapFrame")
        frame.setStyleSheet(f"QFrame#MapFrame {{ background: {theme.WINDOW_BG.name()}; border: 1px solid "
                            f"{theme.LINE.name()}; border-radius: 6px; }}")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        self.tiles = TileCache(DATA_DIR / "tiles", self)
        self.view = MapView(self.tiles)
        self.view.zoom_changed.connect(self._zoomed)
        self.view.home = self._fit_map
        fl.addWidget(self.view)
        body.addWidget(frame, 1)

        self.side_panel = QFrame()
        self.side_panel.setObjectName("Card")
        self.side_panel.setFixedWidth(f.px(292))
        sv = QVBoxLayout(self.side_panel)
        sv.setContentsMargins(f.px(12), f.px(12), f.px(6), f.px(10))
        sv.setSpacing(f.px(6))
        self.search = SearchBox(f, tr("Buscar en este mapa: NPC, monstruo, lugar…"))
        self.search.setToolTip(tr("Escribe parte de un nombre: la lista muestra lo que hay en este mapa. Clic en uno "
                                  "(o Intro para el primero): el mapa va allí y lo marca con un anillo."))
        self.search.changed.connect(lambda _t: self._search_timer.start())
        self.search.returnPressed.connect(self._search_enter)
        sv.addWidget(self.search)
        self._search_timer = QTimer(self, singleShot=True, interval=140, timeout=self._fill_results)
        self.result_list = QListWidget()
        self.result_list.setFocusPolicy(Qt.NoFocus)
        self.result_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.result_list.setMaximumHeight(f.px(210))
        self.result_list.setStyleSheet(
            f"QListWidget {{ background: {theme.WINDOW_BG.name()}; border: 1px solid {theme.LINE.name()}; "
            f"border-radius: 4px; }} QListWidget::item {{ border: none; padding: 0px; }} "
            f"QListWidget::item:hover {{ background: {rgba(theme.TEXT, 12)}; }} "
            f"QListWidget::item:selected {{ background: {rgba(theme.ACCENT, 34)}; }}")
        self.result_list.itemClicked.connect(lambda item: self._show_result(self.result_list.row(item)))
        self.result_list.hide()
        sv.addWidget(self.result_list)
        self.found_label = label("", f.small, theme.ACCENT)
        self.found_label.setWordWrap(True)
        self.found_label.hide()
        sv.addWidget(self.found_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.panel = QVBoxLayout(holder)
        self.panel.setContentsMargins(0, f.px(4), f.px(6), 0)
        self.panel.setSpacing(f.px(1))
        scroll.setWidget(holder)
        sv.addWidget(scroll, 1)
        credit = label(tr("Puntos: tu mapa (aion2-map.json) · imágenes del mapa: metabot.gg"), f.small, theme.MUTED)
        credit.setWordWrap(True)
        sv.addWidget(credit)
        body.addWidget(self.side_panel)
        root.addLayout(body, 1)

        # the layers (zones, bosses) and the legend live in the panel's scroll, above and below the categories
        self.layers_box = QVBoxLayout()
        self.layers_box.setSpacing(f.px(6))
        self.layers_box.addWidget(label(tr("Capas").upper(), f.section, theme.MUTED))
        self.zones_check = _Switch(saved.get("zones", True))
        self.zones_check.toggled.connect(self._zones_toggled)
        self._switch_row(self.zones_check, tr("Zonas"), tr("Sus nombres y bordes; los nombres pequeños salen al "
                                                           "acercar, si caben"))
        self.bosses_check = _Switch(saved.get("bosses", True))
        self.bosses_check.setToolTip(tr("Los jefes de campo y del Abismo del Tracker sobre su punto: vivo, cuánto "
                                        "falta para que vuelva o «¿?» si aún no lo sabe (abre en el juego la lista "
                                        "de monstruos de campo, M)"))
        self.bosses_check.toggled.connect(self._bosses_toggled)
        self._switch_row(self.bosses_check, tr("Jefes del Tracker"), tr("Con su estado en vivo sobre su punto"))
        self.cats_box = QVBoxLayout()
        self.cats_box.setSpacing(f.px(1))
        self.panel.addLayout(self.layers_box)
        self.panel.addSpacing(f.px(4))
        self.panel.addWidget(Divider(plain=True))
        self.panel.addLayout(self.cats_box)
        self.panel.addSpacing(f.px(6))
        self.panel.addWidget(self._legend())
        self.panel.addStretch(1)

        self._show_panel(saved.get("panel", True), save=False)
        self._where = QTimer(self, interval=1000, timeout=self._show_you)
        self._where.start()
        self._start_side()

    # --- the panel ---
    def _switch_row(self, switch, title, note):
        f = self.fonts
        row = QHBoxLayout()
        row.setSpacing(f.px(10))
        row.addWidget(switch, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(title, f.name, theme.TEXT))
        text = label(note, f.small, theme.MUTED)
        text.setWordWrap(True)
        col.addWidget(text)
        row.addLayout(col, 1)
        self.layers_box.addLayout(row)

    def _legend(self):
        f = self.fonts
        card = Card(f, tr("Leyenda"))
        for kind, text in (("you", tr("Tú (con «Mostrar mi ubicación»)")),
                           ("group", tr("Varios puntos juntos: su número; se separan al acercar")),
                           ("found", tr("Lo que buscaste")),
                           ("village", tr("Pueblo o refugio")),
                           ("zone", tr("Zona del mapa")),
                           ("border", tr("Borde de una zona"))):
            row = QHBoxLayout()
            row.setSpacing(f.px(8))
            row.addWidget(_Swatch(f, kind))
            note = label(text, f.small, theme.TEXT)
            note.setWordWrap(True)
            row.addWidget(note, 1)
            card.body.addLayout(row)
        card.body.addWidget(label(tr("Jefes del Tracker"), f.small, theme.MUTED))
        chips = QHBoxLayout()
        chips.setSpacing(f.px(4))
        for text, color, tip in ((tr("vivo"), theme.GAIN, tr("Vivo ahora")),
                                 (tr("¿vivo?"), theme.WARN, tr("Debería haber salido ya")),
                                 (tr("en 12 min"), theme.GOLD, tr("Cuánto falta para que vuelva")),
                                 (tr("¿?"), theme.MUTED, tr("Aún no sé cuándo vuelve"))):
            chips.addWidget(Chip(text, color, f, tip=tip))
        chips.addStretch(1)
        card.body.addLayout(chips)
        tips = label(tr("Rueda: acercar o alejar donde apuntas · Arrastrar: mover · Doble clic: acercar · "
                        "+ / − / 0 en el teclado"), f.small, theme.MUTED)
        tips.setWordWrap(True)
        card.body.addWidget(tips)
        return card

    def _show_panel(self, on, save=True):
        self.side_panel.setVisible(bool(on))
        self.panel_button.setText(tr("Ocultar panel") if on else tr("Capas y búsqueda"))
        self.panel_button.setToolTip(tr("Más sitio para el mapa") if on else
                                     tr("Las categorías, la búsqueda y la leyenda"))
        if save:
            self.ctl.settings["world_map"]["panel"] = bool(on)
            self.ctl.settings.save()

    # --- maps ---
    def _start_side(self):
        from ..gathering import GAME_MAPS
        here = MAP_SIDE.get(GAME_MAPS.get(self.ctl.game_map))
        faction = self.ctl.crafting_faction() if hasattr(self.ctl, "crafting_faction") else None
        self._pick_side(here or self.side or faction or "asmodian", keep_map=GAME_MAPS.get(self.ctl.game_map))

    def _pick_side(self, side, keep_map=None):
        if side not in SIDES:
            return
        self.side = side
        self.ctl.settings["world_map"]["side"] = side
        if self.sides.selected() != side:
            self.sides.blockSignals(True)
            self.sides.set_selected(side)
            self.sides.blockSignals(False)
        order = ("world_d_a", "world_d_b", "world_d_starter", "world_l_a", "world_l_b", "world_l_starter",
                 "abyss_reshanta_a", "abyss_reshanta_c", "abyss_reshanta_d")
        self.maps = [m for m in order if MAP_SIDE.get(m) == side and m in self.db.get("maps", {})]
        current = keep_map if keep_map in self.maps else (self.maps[0] if self.maps else None)
        tabs = self.tabs
        tabs.tabs = [(m, MAP_NAMES.get(m, m).split(" (")[0]) for m in self.maps]
        tabs.labels = dict(tabs.tabs)
        tabs.enabled_keys = set(self.maps)
        tabs.current, tabs.hover = current, None
        tabs.updateGeometry()
        tabs.update()
        if current:
            self._select_map(current)

    def _go_to_index(self, index):
        if 0 <= index < len(self.maps):
            self.tabs.set_current(self.maps[index])
            self._select_map(self.maps[index])

    def _select_map(self, map_id):
        if map_id not in self.maps:
            return
        self.map_id = map_id
        self.view.set_map(map_id)
        scene = self.view.scene()
        if self.atlas is not None:
            scene.removeItem(self.atlas)
        for item in self.borders + [z for z, _ in self.zone_labels] + self.boss_items:
            scene.removeItem(item)
        self.borders, self.zone_labels, self.boss_items = [], [], []
        self.atlas = AtlasItem(self.db, map_id, self.fonts)
        self.atlas.shown = self.shown
        scene.addItem(self.atlas)
        self.index = {}
        for cat, (_, pts, names) in self.atlas.layers.items():
            for q, name in zip(pts, names):
                for n in name.split(" / "):
                    self.index.setdefault((n, cat), []).append(q)
        self._zones()
        self._bosses()
        self._fill_panel()
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._fill_results()
        spots = [(r["label"][0], r["label"][1]) for r in self.db.get("regions", {}).get(map_id, [])]
        spots += [(q.x() / WORLD, q.y() / WORLD) for _, pts, _ in self.atlas.layers.values() for q in pts[::25]]
        self._home = spots
        self._fit_map()
        self._show_you()

    def _fit_map(self):
        """«Ajustar»: the part of the map with something on it (Ishalgen sits in a corner of its image), never
        smaller than the whole map filling the view."""
        self.view.focus_points(self._home, max_zoom_scale=0.2)
        if self.view.transform().m11() < self.view.fit_scale():
            self.view.focus_points([])

    def _show_you(self):
        if self.isVisible() and self.map_id:
            show_you(self.view, self.ctl, self.map_id, self.maps, self._go_to_index)
            self._tick_bosses()

    # --- the Tracker's bosses ---
    def _bosses(self):
        """A BossLabel over each spawn point of the Tracker's bosses of this map (your map's «Named Monsters»)."""
        from ..gathering import GAME_MAPS
        scene = self.view.scene()
        for item in self.boss_items:
            scene.removeItem(item)
        self.boss_items = []
        if not self.bosses_check.isChecked():
            self._declutter()
            return
        named = {}
        for name, pts in self.db.get("points", {}).get(self.map_id, {}).get("named", []):
            for n in name.split(" / "):
                named.setdefault(n, []).extend(pts)
        for entry in self.ctl.tracker.entries:
            if entry.kind == "event" or GAME_MAPS.get(entry.map_id) != self.map_id:
                continue
            for u, v in named.get(entry.name, []):
                item = BossLabel(entry, self.fonts.family)
                item.setPos(u * WORLD, v * WORLD)
                item.setToolTip(" · ".join(x for x in (entry.name, tr("Nv. {0}", entry.level) if entry.level else "",
                                                       entry.sub) if x))
                scene.addItem(item)
                self.boss_items.append(item)
        self._tick_bosses()
        self._declutter()

    def _tick_bosses(self):
        now = time.time()
        for item in self.boss_items:
            item.set_state(*boss_state(self.ctl.tracker, item.entry, now))

    def _bosses_toggled(self, on):
        self.ctl.settings["world_map"]["bosses"] = on
        self.ctl.settings.save()
        self._bosses()

    def focus_boss(self, entry):
        """Show a Tracker boss: its map, its labels on, the search on its name (ringed, zoomed in)."""
        from ..gathering import GAME_MAPS
        map_id = GAME_MAPS.get(entry.map_id)
        if map_id is None:
            return False
        if not self.bosses_check.isChecked():
            self.bosses_check.setChecked(True)
        if map_id != self.map_id:
            self._pick_side(MAP_SIDE.get(map_id, self.side), keep_map=map_id)
        self._show_panel(True, save=False)
        self.search.blockSignals(True)
        self.search.setText(entry.name)
        self.search.blockSignals(False)
        self._fill_results()
        exact = next((i for i, (n, c, _) in enumerate(self.results) if n == entry.name and c == "named"),
                     next((i for i, (n, _, _) in enumerate(self.results) if n == entry.name), None))
        if exact is not None:
            self._show_result(exact)
        return True

    # --- zones ---
    def _zones(self):
        scene = self.view.scene()
        pen = QPen(QColor(theme.TEXT.red(), theme.TEXT.green(), theme.TEXT.blue(), 80), 1.2, Qt.DashLine)
        pen.setCosmetic(True)
        village = QColor(theme.TEXT)
        field = QColor(theme.GOLD.lighter(115))
        for r in self.db.get("regions", {}).get(self.map_id, []):
            path = QPainterPath()
            for poly in r["outline"]:
                path.addPolygon(QPolygonF([QPointF(u * WORLD, v * WORLD) for u, v in poly]))
                path.closeSubpath()
            border = QGraphicsPathItem(path)
            border.setPen(pen)
            border.setZValue(20)
            scene.addItem(border)
            self.borders.append(border)
            text = ZoneLabel(r["name"], village if r["kind"] == "village" else field, r.get("about"),
                             self.fonts.display_family)
            text.setPos(r["label"][0] * WORLD, r["label"][1] * WORLD)
            scene.addItem(text)
            self.zone_labels.append((text, r.get("rank") or 1))
        self.zone_labels.sort(key=lambda zr: zr[1])
        self._declutter()

    def _zones_toggled(self, on):
        self.ctl.settings["world_map"]["zones"] = on
        self.ctl.settings.save()
        self._declutter()

    def _zoomed(self, _scale):
        self._declutter()

    def _declutter(self):
        """Show the bosses' labels and the zones' names that have room on screen, in order of importance (bosses,
        then the big zones, then the small ones once zoomed in); the rest wait for more zoom."""
        view = self.view
        scale = view.transform().m11()
        placed = []
        if view.you.isVisible():
            c = view.mapFromScene(view.you.pos())
            placed.append(QRectF(c.x() - 30, c.y() - 14, 60, 28))

        def room(item):
            c = view.mapFromScene(item.pos())
            r = item.boundingRect().translated(c.x(), c.y())
            if any(r.intersects(q) for q in placed):
                return False
            placed.append(r.adjusted(-4, -2, 4, 2))
            return True

        for item in self.boss_items:
            item.setVisible(room(item))
        on = self.zones_check.isChecked()
        for border in self.borders:
            border.setVisible(on)
        for item, rank in self.zone_labels:
            item.setVisible(on and (rank <= 1 or scale >= NAMES_AT) and room(item))

    # --- categories ---
    def _fill_panel(self):
        while self.cats_box.count():
            item = self.cats_box.takeAt(0)
            w = item.widget()
            if w is None and item.layout() is not None:
                lay = item.layout()
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
                continue
            if w is not None:
                w.hide()
                w.deleteLater()
        self.rows, self.group_counts = {}, {}
        f = self.fonts
        counts = {c: len(pts) for c, (_, pts, _) in self.atlas.layers.items()}
        total = sum(counts.values())
        self.header.set_subtitle(thousands(tr("{0} · {1:,} puntos · rueda: zoom · arrastrar: mover · doble clic: "
                                              "acercar", self.db['maps'][self.map_id]['name'], total)))
        for typ, title in GROUPS:
            cats = sorted((c for c, (_, t) in self.cats.items() if t == typ and counts.get(c)), key=lambda c: -counts[c])
            if not cats:
                continue
            head = QWidget()
            hl = QHBoxLayout(head)
            hl.setContentsMargins(0, f.px(8), 0, f.px(2))
            hl.setSpacing(f.px(4))
            hl.addWidget(label(title.upper(), f.section, theme.MUTED))
            shown = label("", f.small, theme.MUTED)
            hl.addWidget(shown, 1)
            for text, on in ((tr("todo"), True), (tr("nada"), False)):
                button = QPushButton(text)
                button.setProperty("flat", True)
                button.setFont(f.small)
                button.setCursor(Qt.PointingHandCursor)
                button.setFocusPolicy(Qt.NoFocus)
                button.clicked.connect(lambda _=False, cs=cats, v=on: self._set_many(cs, v))
                hl.addWidget(button)
            self.cats_box.addWidget(head)
            self.group_counts[typ] = (shown, cats)
            for cat in cats:
                row = _CatRow(f, self.cats[cat][0], counts[cat], cat_color(cat), cat in self.shown)
                row.toggled.connect(lambda on, c=cat: self._set_many([c], on))
                self.cats_box.addWidget(row)
                self.rows[cat] = row
        self._count_groups()

    def _count_groups(self):
        for shown, cats in self.group_counts.values():
            shown.setText(f"{sum(1 for c in cats if c in self.shown)}/{len(cats)}")

    def _set_many(self, cats, on):
        for cat in cats:
            (self.shown.add if on else self.shown.discard)(cat)
            row = self.rows.get(cat)
            if row and row.isChecked() != on:
                row.blockSignals(True)
                row.setChecked(on)
                row.blockSignals(False)
                row.update()
        self._count_groups()
        self.ctl.settings["world_map"]["shown"] = sorted(self.shown)
        self.ctl.settings.save()
        if self.atlas:
            self.atlas.update()

    # --- search ---
    def _fill_results(self):
        """The result list: every name of this map with the text (the ones that start with it first), and whole
        categories whose name has it."""
        text = self.search.text().strip().lower()
        self.result_list.clear()
        self.results = []
        if not text or not self.atlas:
            self.result_list.hide()
            self.found_label.hide()
            if self.atlas is not None and self.atlas.found:
                self.atlas.found = []
                self.atlas.update()
            return
        hits = [(n, c, pts) for (n, c), pts in self.index.items() if text in n.lower()]
        hits.sort(key=lambda h: (not h[0].lower().startswith(text), h[0].lower() != text, len(h[0]), h[0]))
        if len(text) > 2:
            for cat, (_, pts, _) in self.atlas.layers.items():
                if pts and text in self.cats.get(cat, (cat,))[0].lower():
                    hits.insert(0, (self.cats[cat][0], cat, pts))
        self.results = hits[:80]
        f = self.fonts
        for name, cat, pts in self.results:
            item = QListWidgetItem()
            item.setSizeHint(QSize(f.px(240), f.px(26)))
            self.result_list.addItem(item)
            self.result_list.setItemWidget(item, self._result_row(name, cat, len(pts)))
        if not self.results:
            self.found_label.setText(tr("Nada con ese nombre en este mapa."))
            self.found_label.show()
            self.result_list.hide()
            return
        more = len(hits) - len(self.results)
        self.found_label.setText(tr("{0} resultados", len(hits)) + (tr(" (los primeros {0})", len(self.results))
                                                                     if more > 0 else ""))
        self.found_label.show()
        self.result_list.show()

    def _result_row(self, name, cat, count):
        f = self.fonts
        w = QWidget()
        w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(f.px(8), 0, f.px(8), 0)
        lay.setSpacing(f.px(8))
        color = cat_color(cat)
        lay.addWidget(label("●", f.small, color))
        title = label(name, f.body, theme.TEXT, elide=True)
        title.setMinimumWidth(f.px(110))
        lay.addWidget(title, 1)
        what = self.cats.get(cat, (cat,))[0]
        if what == name:                                    # a whole category
            what = tr("todos")
        side = label(what if count == 1 else f"{what} · {count}", f.small, theme.MUTED, elide=True)
        side.setMaximumWidth(f.px(120))
        side.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(side)
        return w

    def _search_enter(self):
        self._search_timer.stop()
        self._fill_results()
        if self.results:
            self._show_result(0)

    def _show_result(self, row):
        if not 0 <= row < len(self.results) or not self.atlas:
            return
        name, cat, pts = self.results[row]
        self.result_list.setCurrentRow(row)
        if cat not in self.shown:                           # what you look for shows even if its category is off
            self._set_many([cat], True)
        self.atlas.found = list(pts)
        self.atlas.update()
        what = self.cats.get(cat, (cat,))[0]
        self.found_label.setText(tr("{0}: {1} · {2}", name, what,
                                    tr("1 punto") if len(pts) == 1 else tr("{0} puntos", len(pts))))
        self.found_label.show()
        self.view.focus_points([(q.x() / WORLD, q.y() / WORLD) for q in pts], max_zoom_scale=1.2)
