"""Mapa del mundo: the 9 world maps with the points of the user's own map export (data/world_db.json, built by
tools/build_world_db.py): services, places, collectables, NPCs, gathering nodes and monsters, each category on or
off, the zones' names and outlines, a search by name, and where you are; and the Tracker's field and Abyss bosses with
their state live (alive, back in 12 min...) over their spawn points (BossLabel; the Tracker's «Mapa» opens it on one).

All the points of a map are one scene item (AtlasItem) drawn with cosmetic pens (a fixed size on screen at any zoom),
so even the 20,000 of Altgard pan and zoom smoothly; hovering one says what it is."""
import json
import math

from PySide6.QtCore import QPointF, QRectF, QStringListModel, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QCompleter, QFrame, QGraphicsItem, QGraphicsPathItem,
                               QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QTabBar, QToolTip, QVBoxLayout,
                               QWidget)

from .. import APP_NAME
from ..gathering import MAP_SIDE, SIDES
from ..mapdata import MAP_NAMES
from ..paths import DATA_DIR
from ..tracker import countdown
from . import theme
from .mapview import WORLD, MapView, show_you
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tiles import TileCache
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label

# the groups of the side panel, in order, with the categories on when you first open it
GROUPS = (("service", "Servicios"), ("location", "Lugares"), ("collectable", "Coleccionables"),
          ("gatherable", "Recolección"), ("npc", "NPCs"), ("monster", "Monstruos"))
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
NAMES_AT = 0.3                   # zoom (screen px per scene unit; the whole map is ~0.1) from which every zone's
                                 # name shows
HOVER_PX = 9                     # how close the mouse must be to a point to say what it is


class AtlasItem(QGraphicsItem):
    """Every shown point of a map, drawn at a fixed size; hover tells the nearest one's name."""
    CELL = WORLD / 128

    def __init__(self, world_db, map_id):
        super().__init__()
        self.setAcceptHoverEvents(True)
        self.setZValue(50)
        self.layers = {}                 # category -> (type, [QPointF], [name])
        self.shown = set()
        self.found = []                  # [QPointF] of the search, drawn ringed
        self.grid = {}                   # cell -> [(category, index)]
        types = {c: t for c, (_, t) in world_db.get("categories", {}).items()}
        for cat, rows in world_db.get("points", {}).get(map_id, {}).items():
            points, names = [], []
            for name, pts in rows:
                for u, v in pts:
                    points.append(QPointF(u * WORLD, v * WORLD))
                    names.append(name)
            self.layers[cat] = (types.get(cat, ""), points, names)
            for i, q in enumerate(points):
                self.grid.setdefault((int(q.x() // self.CELL), int(q.y() // self.CELL)), []).append((cat, i))

    def boundingRect(self):
        return QRectF(0, 0, WORLD, WORLD)

    def paint(self, p, _option, _widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        for kind in ORDER:
            for cat, (typ, points, _) in self.layers.items():
                if typ != kind or cat not in self.shown or not points:
                    continue
                size = SIZE.get(typ, 5)
                dots = QPolygonF(points)
                edge = QPen(QColor(5, 10, 20, 220), size + 2.4, Qt.SolidLine, Qt.RoundCap)
                edge.setCosmetic(True)
                p.setPen(edge)
                p.drawPoints(dots)
                fill = QPen(QColor(COLORS.get(cat, "#ffffff")), size, Qt.SolidLine, Qt.RoundCap)
                fill.setCosmetic(True)
                p.setPen(fill)
                p.drawPoints(dots)
        if self.found:
            ring = QPen(QColor(255, 255, 255, 240), 2.2)
            ring.setCosmetic(True)
            p.setPen(ring)
            p.setBrush(Qt.NoBrush)
            r = 9 / max(p.worldTransform().m11(), 1e-6)
            for q in self.found:
                p.drawEllipse(q, r, r)

    def nearest(self, pos, scale):
        """(category, name) of the shown point closest to pos within HOVER_PX on screen, or None."""
        reach = HOVER_PX / max(scale, 1e-6)
        cx, cy = int(pos.x() // self.CELL), int(pos.y() // self.CELL)
        n = int(reach // self.CELL) + 1
        best, best_d = None, reach
        for dx in range(-n, n + 1):
            for dy in range(-n, n + 1):
                for cat, i in self.grid.get((cx + dx, cy + dy), ()):
                    if cat in self.shown:
                        q = self.layers[cat][1][i]
                        d = math.hypot(q.x() - pos.x(), q.y() - pos.y())
                        if d < best_d:
                            best, best_d = (cat, self.layers[cat][2][i]), d
        return best

    def hoverMoveEvent(self, event):
        view = self.scene().views()[0] if self.scene() and self.scene().views() else None
        hit = self.nearest(event.pos(), view.transform().m11() if view else 1.0)
        if hit:
            cat, name = hit
            QToolTip.showText(event.screenPos(), f"{name}\n{self.label_of(cat)}")
        else:
            QToolTip.hideText()

    def hoverLeaveEvent(self, _event):
        QToolTip.hideText()

    label_of = staticmethod(lambda cat: cat)


class ZoneLabel(QGraphicsItem):
    """A zone's name centred on its spot, the same size at any zoom."""

    def __init__(self, text, color, about):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(60)
        self.setToolTip(about or text)
        self.font = QFont("Segoe UI", 9, QFont.Bold)
        self.path = QPainterPath()
        self.path.addText(QPointF(0, 0), self.font, text)
        box = self.path.boundingRect()
        self.path.translate(-box.center())
        self.color = color

    def boundingRect(self):
        return self.path.boundingRect().adjusted(-3, -3, 3, 3)

    def paint(self, p, _option, _widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(5, 10, 20, 225), 3.4))
        p.setBrush(Qt.NoBrush)
        p.drawPath(self.path)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawPath(self.path)


class BossLabel(QGraphicsItem):
    """A Tracker boss over its spawn point: a pill with a dot of its state's colour, its name and its state, the same
    size at any zoom, just above the point."""
    PAD = 6

    def __init__(self, entry):
        super().__init__()
        self.entry = entry
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(70)
        self.font = QFont("Segoe UI", 8, QFont.DemiBold)
        self.small = QFont("Segoe UI", 8)
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
        from PySide6.QtGui import QFontMetricsF
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
        p.setBrush(QColor(10, 16, 30, 225))
        p.drawRoundedRect(box, box.height() / 2, box.height() / 2)
        p.drawLine(QPointF(0, box.bottom()), QPointF(0, -4))                  # a stem down to the point
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(QPointF(box.left() + self.PAD + 3, box.center().y()), 3.2, 3.2)
        x = box.left() + self.PAD + 10
        p.setFont(self.font)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(x, box.top(), box.width(), box.height()), Qt.AlignLeft | Qt.AlignVCenter, self.name)
        if self.state:
            from PySide6.QtGui import QFontMetricsF
            x += QFontMetricsF(self.font).horizontalAdvance(self.name)
            p.setFont(self.small)
            p.setPen(self.color)
            p.drawText(QRectF(x, box.top(), box.width(), box.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       "  " + self.state)


def boss_state(tracker, entry, now):
    """(short text, colour) of a Tracker boss for its map label."""
    st = tracker.status(entry, now)
    if st.state in ("vivo", "en curso"):
        return "vivo", theme.GAIN
    if st.state in ("debería estar", "quizá"):
        return "¿vivo?", theme.WARN
    if st.start is not None and st.start > now:
        return f"en {countdown(st.start - now)}", theme.GOLD
    return "¿?", theme.MUTED


class WorldMapWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        try:
            self.db = json.loads((DATA_DIR / "world_db.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.db = {"maps": {}, "points": {}, "regions": {}, "categories": {}}
        self.cats = {c: (name, typ) for c, (name, typ) in self.db.get("categories", {}).items()}
        AtlasItem.label_of = staticmethod(lambda cat: self.cats.get(cat, (cat, ""))[0])
        saved = ctl.settings.setdefault("world_map", {})
        self.shown = set(saved.get("shown") or FIRST_ON)
        self.side = saved.get("side")
        self.maps = []                   # map ids in the tabs
        self.map_id = None
        self.atlas = None
        self.zone_items = []             # (item, rank)
        self.boss_items = []             # BossLabel of the Tracker's bosses on this map
        self.checks = {}
        self.setObjectName("MapWindow")
        self.setWindowIcon(ctl.app_icon)
        self.setWindowTitle(f"{APP_NAME} — Mapa del mundo")
        self.setStyleSheet(STYLE + EXTRA_STYLE)
        self.resize(1360, 860)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        left = QVBoxLayout()
        left.setContentsMargins(8, 8, 0, 0)
        left.setSpacing(0)
        sides = QHBoxLayout()
        sides.setContentsMargins(0, 0, 8, 8)
        sides.setSpacing(6)
        self.side_buttons = {}
        group = QButtonGroup(self)
        for side in SIDES:
            button = QPushButton(theme.FACTION_LABEL[side])
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, s=side: self._pick_side(s))
            group.addButton(button)
            sides.addWidget(button)
            self.side_buttons[side] = button
        sides.addStretch(1)
        left.addLayout(sides)
        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        left.addWidget(self.tabs)
        self.tiles = TileCache(DATA_DIR / "tiles", self)
        self.view = MapView(self.tiles)
        self.view.zoom_changed.connect(self._zoomed)
        left.addWidget(self.view, 1)
        root.addLayout(left, 1)

        side = QFrame()
        side.setObjectName("Side")
        side.setFixedWidth(360)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 14, 10, 12)
        sv.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(badge_label(34, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = label("Mapa del mundo", self.fonts.name_big, theme.TEXT)
        titles.addWidget(self.title)
        self.subtitle = label("", self.fonts.small, theme.MUTED)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        sv.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar en este mapa (NPC, monstruo, lugar…)")
        self.search.setClearButtonEnabled(True)
        self.completer = QCompleter(self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.activated.connect(self._find)
        self.search.setCompleter(self.completer)
        self.search.returnPressed.connect(lambda: self._find(self.search.text()))
        self.search.textChanged.connect(lambda t: self._find("") if not t else None)
        sv.addWidget(self.search)
        self.found_label = label("", self.fonts.small, theme.ACCENT)
        self.found_label.setWordWrap(True)
        self.found_label.hide()
        sv.addWidget(self.found_label)
        self.zones_check = QCheckBox("Nombres y bordes de las zonas")
        self.zones_check.setChecked(saved.get("zones", True))
        self.zones_check.toggled.connect(self._zones_toggled)
        sv.addWidget(self.zones_check)
        self.bosses_check = QCheckBox("Jefes del Tracker con su estado en vivo")
        self.bosses_check.setToolTip("Los jefes de campo y del Abismo del Tracker sobre su punto: vivo, cuánto falta para "
                                     "que vuelva o «¿?» si aún no lo sabe (abre en el juego la lista de monstruos de "
                                     "campo, M)")
        self.bosses_check.setChecked(saved.get("bosses", True))
        self.bosses_check.toggled.connect(self._bosses_toggled)
        sv.addWidget(self.bosses_check)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.panel = QVBoxLayout(holder)
        self.panel.setContentsMargins(0, 0, 6, 0)
        self.panel.setSpacing(2)
        scroll.setWidget(holder)
        sv.addWidget(scroll, 1)
        credit = label("Puntos: tu mapa (aion2-map.json) · imágenes del mapa: metabot.gg", self.fonts.small,
                       theme.MUTED)
        credit.setWordWrap(True)
        sv.addWidget(credit)
        root.addWidget(side)
        self._where = QTimer(self, interval=1000, timeout=self._show_you)
        self._where.start()
        self._start_side()

    # --- maps ---
    def _start_side(self):
        from ..gathering import GAME_MAPS
        here = MAP_SIDE.get(GAME_MAPS.get(self.ctl.game_map))
        faction = self.ctl.crafting_faction() if hasattr(self.ctl, "crafting_faction") else None
        self._pick_side(here or self.side or faction or "asmodian", keep_map=GAME_MAPS.get(self.ctl.game_map))

    def _pick_side(self, side, keep_map=None):
        self.side = side
        self.ctl.settings["world_map"]["side"] = side
        self.side_buttons[side].setChecked(True)
        order = ("world_d_a", "world_d_b", "world_d_starter", "world_l_a", "world_l_b", "world_l_starter",
                 "abyss_reshanta_a", "abyss_reshanta_c", "abyss_reshanta_d")
        self.maps = [m for m in order if MAP_SIDE.get(m) == side and m in self.db.get("maps", {})]
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for m in self.maps:
            self.tabs.addTab(MAP_NAMES.get(m, m).split(" (")[0])
        self.tabs.blockSignals(False)
        index = self.maps.index(keep_map) if keep_map in self.maps else 0
        self.tabs.setCurrentIndex(index)
        self._tab_changed(index)

    def _tab_changed(self, index):
        if not 0 <= index < len(self.maps):
            return
        self.map_id = self.maps[index]
        self.view.set_map(self.map_id)
        scene = self.view.scene()
        if self.atlas is not None:
            scene.removeItem(self.atlas)
        for item, _ in self.zone_items:
            scene.removeItem(item)
        self.zone_items = []
        for item in self.boss_items:
            scene.removeItem(item)
        self.boss_items = []
        self.atlas = AtlasItem(self.db, self.map_id)
        self.atlas.shown = self.shown
        scene.addItem(self.atlas)
        self._zones()
        self._bosses()
        self._fill_panel()
        names = sorted({n for _, _, ns in self.atlas.layers.values() for name in ns for n in name.split(" / ")} |
                       {self.cats.get(c, (c,))[0] for c in self.atlas.layers})
        self.completer.setModel(QStringListModel(names, self.completer))
        self.search.clear()
        spots = [(r["label"][0], r["label"][1]) for r in self.db.get("regions", {}).get(self.map_id, [])]
        spots += [(q.x() / WORLD, q.y() / WORLD) for _, pts, _ in self.atlas.layers.values() for q in pts[::25]]
        self.view.focus_points(spots, max_zoom_scale=0.2)             # where the map is (Ishalgen: a corner)
        self._zoomed(self.view.transform().m11())
        self._show_you()

    def _show_you(self):
        if self.isVisible() and self.map_id:
            show_you(self.view, self.ctl, self.map_id, self.maps, self.tabs.setCurrentIndex)
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
            return
        named = {}
        for name, pts in self.db.get("points", {}).get(self.map_id, {}).get("named", []):
            for n in name.split(" / "):
                named.setdefault(n, []).extend(pts)
        for entry in self.ctl.tracker.entries:
            if entry.kind == "event" or GAME_MAPS.get(entry.map_id) != self.map_id:
                continue
            for u, v in named.get(entry.name, []):
                item = BossLabel(entry)
                item.setPos(u * WORLD, v * WORLD)
                item.setToolTip(" · ".join(x for x in (entry.name, f"Nv. {entry.level}" if entry.level else "",
                                                       entry.sub) if x))
                scene.addItem(item)
                self.boss_items.append(item)
        self._tick_bosses()

    def _tick_bosses(self):
        import time
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
        self.search.blockSignals(True)
        self.search.setText(entry.name)
        self.search.blockSignals(False)
        self._find(entry.name)
        return True

    # --- zones ---
    def _zones(self):
        scene = self.view.scene()
        for r in self.db.get("regions", {}).get(self.map_id, []):
            path = QPainterPath()
            for poly in r["outline"]:
                path.addPolygon(QPolygonF([QPointF(u * WORLD, v * WORLD) for u, v in poly]))
                path.closeSubpath()
            border = QGraphicsPathItem(path)
            pen = QPen(QColor(255, 255, 255, 70), 1.2, Qt.DashLine)
            pen.setCosmetic(True)
            border.setPen(pen)
            border.setZValue(20)
            scene.addItem(border)
            self.zone_items.append((border, 0))
            text = ZoneLabel(r["name"], QColor(255, 255, 255, 235) if r["kind"] == "village" else
                             QColor(255, 226, 150, 230), r.get("about"))
            text.setPos(r["label"][0] * WORLD, r["label"][1] * WORLD)
            scene.addItem(text)
            self.zone_items.append((text, r.get("rank") or 1))
        self._zones_toggled(self.zones_check.isChecked(), save=False)

    def _zones_toggled(self, on, save=True):
        if save:
            self.ctl.settings["world_map"]["zones"] = on
            self.ctl.settings.save()
        self._zoomed(self.view.transform().m11())

    def _zoomed(self, scale):
        """Zone names: the big ones always, the rest once zoomed in (they would pile up on the whole map)."""
        on = self.zones_check.isChecked()
        close = scale >= NAMES_AT
        for item, rank in self.zone_items:
            item.setVisible(on and (rank <= 1 or close or isinstance(item, QGraphicsPathItem)))

    # --- categories ---
    def _fill_panel(self):
        while self.panel.count():
            item = self.panel.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.checks = {}
        counts = {c: len(pts) for c, (_, pts, _) in self.atlas.layers.items()}
        total = sum(counts.values())
        self.subtitle.setText(f"{self.db['maps'][self.map_id]['name']} · {total:,} puntos".replace(",", "."))
        for typ, title in GROUPS:
            cats = [c for c, (_, t) in self.cats.items() if t == typ and counts.get(c)]
            if not cats:
                continue
            head = QHBoxLayout()
            head.addWidget(SectionTitle(title, self.fonts), 1)
            for text, on in (("todo", True), ("nada", False)):
                button = QPushButton(text)
                button.setProperty("flat", True)
                button.clicked.connect(lambda _=False, cs=cats, v=on: self._set_many(cs, v))
                head.addWidget(button)
            box = QWidget()
            box.setLayout(head)
            self.panel.addWidget(box)
            for cat in sorted(cats, key=lambda c: -counts[c]):
                check = QCheckBox(f"{self.cats[cat][0]}  ·  {counts[cat]:,}".replace(",", "."))
                check.setChecked(cat in self.shown)
                color = COLORS.get(cat, "#ffffff")
                check.setStyleSheet(f"QCheckBox {{ color: {theme.TEXT.name()}; }} QCheckBox::indicator {{ width: 12px; "
                                    f"height: 12px; border: 1.5px solid {color}; border-radius: 4px; background: "
                                    f"transparent; }} QCheckBox::indicator:checked {{ background: {color}; }}")
                check.toggled.connect(lambda on, c=cat: self._set_many([c], on))
                self.panel.addWidget(check)
                self.checks[cat] = check
        self.panel.addStretch(1)

    def _set_many(self, cats, on):
        for cat in cats:
            (self.shown.add if on else self.shown.discard)(cat)
            check = self.checks.get(cat)
            if check and check.isChecked() != on:
                check.blockSignals(True)
                check.setChecked(on)
                check.blockSignals(False)
        self.ctl.settings["world_map"]["shown"] = sorted(self.shown)
        self.ctl.settings.save()
        if self.atlas:
            self.atlas.update()

    # --- search ---
    def _find(self, text):
        text = text.strip().lower()
        if not self.atlas:
            return
        if not text:
            self.atlas.found = []
            self.found_label.hide()
            self.atlas.update()
            return
        found, cats = [], set()
        for cat, (_, points, names) in self.atlas.layers.items():
            whole = len(text) > 3 and text in self.cats.get(cat, (cat,))[0].lower()      # a category: all of it
            for q, name in zip(points, names):
                if whole or any(text == n.lower() for n in name.split(" / ")) or (len(text) > 3 and
                                                                                   text in name.lower()):
                    found.append(q)
                    cats.add(cat)
        self.atlas.found = found
        for cat in cats - self.shown:                       # what you look for shows even if its category is off
            self._set_many([cat], True)
        self.atlas.update()
        self.found_label.setText(f"{len(found)} {'punto' if len(found) == 1 else 'puntos'}: "
                                 + ", ".join(sorted(self.cats.get(c, (c,))[0] for c in cats)) if found else
                                 "Nada con ese nombre en este mapa.")
        self.found_label.show()
        if found:
            self.view.focus_points([(q.x() / WORLD, q.y() / WORLD) for q in found], max_zoom_scale=1.2)
