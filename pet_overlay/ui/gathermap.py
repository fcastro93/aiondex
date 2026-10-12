"""Gathering map: one material's nodes on the world map, its areas with the recommended one and a route through it,
and "full of bots" marks on areas (button) and nodes (right click). The maps are picked by side (Elyos, Asmodian,
Abyss); in the Abyss, only the maps open now unless you untick it."""
import math
import time
import webbrowser

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFrame, QGraphicsItem, QGraphicsPathItem,
                               QGraphicsPolygonItem, QGraphicsSimpleTextItem, QHBoxLayout, QPushButton, QScrollArea,
                               QTabBar, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..gathering import ABYSS_OPEN, BOT_DAYS, GAME_MAPS, MAP_SIDE, SIDES, hull, zone_centers
from ..mapdata import MAP_NAMES
from ..paths import DATA_DIR
from . import theme
from .mapview import WORLD, Marker, MapView
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tiles import TileCache
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label

BEST = QColor("#7bd88f")
OTHER = QColor("#f0c24e")
BOTS = QColor("#ff6b6b")
LOOSE = QColor("#8a93a8")
AREAS_SHOWN = 8
SIDE_STYLE = """
QFrame#AreaRow { background: #101a30; border: 1px solid #1a2643; border-radius: 6px; }
QFrame#AreaRow[best="true"] { border-color: #2f7a46; }
QFrame#AreaRow[bots="true"] { border-color: #7a2f2f; }
"""


class NodeMarker(Marker):
    """A node: left click centres on it, right click marks it (or not) as full of bots."""

    def __init__(self, *args, on_right=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_right = on_right

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton and self.on_right:
            self.on_right()
            event.accept()
        else:
            super().mousePressEvent(event)


class GatherMapWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.gathering = ctl.gathering
        self.fonts = Fonts(1.0)
        self.material = None
        self.map_id = None
        self.side = None                  # "elyos" | "asmodian" | "abyss": the maps shown in the tabs
        self._all_maps = []               # [(map id, has points)] of the material, every side
        self._maps = []                   # [(map id, has points)] in the tabs
        self._items = []                  # scene items other than the markers (areas, route, labels)
        self.setObjectName("MapWindow")
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + SIDE_STYLE)
        self.resize(1320, 820)

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
        group.setExclusive(True)
        for side in SIDES:
            button = QPushButton(theme.FACTION_LABEL[side])
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, s=side: self._pick_side(s))
            group.addButton(button)
            sides.addWidget(button)
            self.side_buttons[side] = button
        open_names = " · ".join(sorted(MAP_NAMES.get(m, m).split(" (")[0] for m in ABYSS_OPEN))
        self.open_only = QCheckBox(f"Solo lo abierto ahora ({open_names})")
        self.open_only.setChecked(self.ctl.settings.get("crafting", {}).get("abyss_open_only", True))
        self.open_only.toggled.connect(self._open_only_toggled)
        sides.addSpacing(10)
        sides.addWidget(self.open_only)
        sides.addStretch(1)
        left.addLayout(sides)
        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        left.addWidget(self.tabs)
        self.tiles = TileCache(DATA_DIR / "tiles", self)
        self.view = MapView(self.tiles)
        left.addWidget(self.view, 1)
        root.addLayout(left, 1)

        side = QFrame()
        side.setObjectName("Side")
        side.setFixedWidth(390)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 14, 10, 12)
        sv.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(badge_label(34, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = label("", self.fonts.name_big, theme.TEXT)
        titles.addWidget(self.title)
        self.subtitle = label("", self.fonts.small, theme.MUTED)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        sv.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.panel = QVBoxLayout(holder)
        self.panel.setContentsMargins(0, 0, 6, 0)
        self.panel.setSpacing(6)
        scroll.setWidget(holder)
        sv.addWidget(scroll, 1)
        credit = self.gathering.credit or {"text": "Puntos de recolección: metabot.gg",
                                           "url": "https://metabot.gg/en/aion-2/gathering"}
        link = QPushButton(credit["text"] + " · mapa: metabot.gg")
        link.setProperty("flat", True)
        link.clicked.connect(lambda: webbrowser.open(credit["url"]))
        sv.addWidget(link)
        root.addWidget(side)

    # --- public ---
    def show_material(self, name):
        self.material = name
        verb = "conseguir" if self.gathering.is_drop(name) else "recolectar"
        self.setWindowTitle(f"{APP_NAME} — Dónde {verb} {name}")
        faction = self.ctl.crafting_faction()
        maps = [(m, True) for m in self.gathering.maps(name)]
        for _, map_id, _, _ in self.gathering.side_regions(name, None):
            if map_id and map_id not in [m for m, _ in maps]:
                maps.append((map_id, False))
        self._all_maps = maps
        counts = {s: sum(1 for m, _ in maps if MAP_SIDE.get(m) == s) for s in SIDES}
        for side, button in self.side_buttons.items():
            button.setText(f"{theme.FACTION_LABEL[side]} · {counts[side]}")
            button.setEnabled(counts[side] > 0)
            button.setToolTip(f"{counts[side]} {'mapa' if counts[side] == 1 else 'mapas'} con {name}")
        # the side of the map you are on, else yours, else the first one where it is
        here = MAP_SIDE.get(GAME_MAPS.get(self.ctl.game_map))
        self.side = next((s for s in (here, faction, *SIDES) if s and counts.get(s)), None)
        if self.side is None:              # dropped by monsters with no spawn map: just the list
            for button in self.side_buttons.values():
                button.setChecked(False)
            self.open_only.setVisible(False)
            self._set_tabs([])
            self.title.setText(f"Dónde {verb} {name}")
            self._panel([], [], [])
            return
        self._fill_tabs()

    def _pick_side(self, side):
        self.side = side
        self._fill_tabs()

    def _open_only_toggled(self, on):
        self.ctl.settings.setdefault("crafting", {})["abyss_open_only"] = on
        self.ctl.settings.save()
        if self.material and self.side == "abyss":
            self._fill_tabs()

    def _fill_tabs(self):
        """The tabs of the side picked: the map you are on first, then the ones with points."""
        self.side_buttons[self.side].setChecked(True)
        abyss = self.side == "abyss"
        self.open_only.setVisible(abyss)
        open_only = abyss and self.open_only.isChecked()
        here = GAME_MAPS.get(self.ctl.game_map)
        maps = [m for m in self._all_maps if MAP_SIDE.get(m[0]) == self.side and (not open_only or m[0] in ABYSS_OPEN)]
        maps.sort(key=lambda m: (m[0] != here, m[0] not in ABYSS_OPEN if abyss else False, not m[1]))
        self._set_tabs(maps)
        if not maps:                      # all of it is in Abyss maps that are not open yet
            self.title.setText(f"Dónde {'conseguir' if self.gathering.is_drop(self.material) else 'recolectar'} "
                               f"{self.material}")
            self._panel([], [], [], closed=True)
            return
        self.tabs.setCurrentIndex(0)
        self._tab_changed(0)

    def _set_tabs(self, maps):
        self._maps = maps
        self.map_id = None
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for map_id, has_points in maps:
            text = (MAP_NAMES.get(map_id) or self.ctl.mapdata.map_label(map_id)).split(" (")[0]
            if MAP_SIDE.get(map_id) == "abyss" and map_id not in ABYSS_OPEN:
                text += " · cerrado"
            self.tabs.addTab(text + ("" if has_points else " · zonas"))
        self.tabs.blockSignals(False)
        if not maps:
            self._clear_scene()

    def _tab_changed(self, index):
        if not 0 <= index < len(self._maps):
            return
        self.map_id = self._maps[index][0]
        self.view.set_map(self.map_id)
        self.refresh(refocus=True)

    # --- drawing ---
    def refresh(self, refocus=False):
        name, map_id = self.material, self.map_id
        if not name or not map_id:
            return
        g = self.gathering
        info = g.info(name)
        drop = g.is_drop(name)
        self.title.setText(f"Dónde {'conseguir' if drop else 'recolectar'} {name}")
        if drop:
            mobs = info.get("monsters", [])
            self.subtitle.setText(f"Lo sueltan {len(mobs)} monstruos" +
                                  (f" · el mejor: {mobs[0]['name']} ({mobs[0]['chance']:g}%)" if mobs else ""))
        else:
            node = info.get("node", name)
            level = info.get("level")
            self.subtitle.setText(f"Nodo: {node}" + (f" · nivel de recolección {level}" if level else ""))
        self._clear_scene()
        areas, loose = g.areas(name, map_id)
        focus = []
        for area in areas:
            best = area is areas[0] and not area.bots
            color = BOTS if area.bots else BEST if best else OTHER
            self._area_shape(area, color, best)
            if best and len(area.route) > 1:
                self._route(area.route)
            for p in area.nodes:
                bots = g.node_has_bots(name, map_id, p)
                mob = g.monster_at(name, map_id, p)
                what = f"{mob['name']} (Lv {mob['level']}) · {mob['chance']:g}% de {name}" if mob else name
                tip = (f"{what} · zona {area.index}" + (" · ¡con bots!" if bots or area.bots else "") +
                       "\nClic derecho: marcar / quitar «con bots» en este punto")
                self._node(p, BOTS if bots else color, 6.5 if best else 5, tip)
                if best:
                    focus.append(p)
        for p in loose:
            bots = g.node_has_bots(name, map_id, p)
            self._node(p, BOTS if bots else LOOSE, 4.5, f"{name} (punto suelto)\nClic derecho: marcar «con bots»")
        zones = []
        own = next((r for r in g.side_regions(name, None) if r[1] == map_id), None)
        if own:
            zones = g.zone_positions(own[3], map_id) or zone_centers(self.ctl.mapdata, own[3], map_id)
            for zname, x, y in zones:
                self._zone_label(zname, x, y, faded=bool(areas))
        if refocus:
            pts = focus or [p for a in areas for p in a.nodes] or [(x, y) for _, x, y in zones]
            self.view.focus_points(pts)
        self._panel(areas, loose, zones)

    def _clear_scene(self):
        self.view.clear_markers()
        for item in self._items:
            self.view.scene().removeItem(item)
        self._items = []

    def _node(self, p, color, radius, tip):
        m = NodeMarker(p[0], p[1], color, radius, tip, on_click=lambda q=p: self.view.center_on_norm(*q),
                       on_right=lambda q=p: self._toggle_node(q))
        m.setZValue(200)
        self.view.scene().addItem(m)
        self.view.markers.append(m)

    def _area_shape(self, area, color, best):
        pts = hull(area.nodes)
        if len(pts) < 3:
            return
        cx, cy = area.center
        poly = QPolygonF([QPointF(cx * WORLD + (x - cx) * WORLD * 1.25, cy * WORLD + (y - cy) * WORLD * 1.25)
                          for x, y in pts])
        item = QGraphicsPolygonItem(poly)
        fill = QColor(color)
        fill.setAlpha(55 if best else 30)
        item.setBrush(QBrush(fill))
        pen = QPen(color, 2 if best else 1.4)
        pen.setCosmetic(True)
        if area.bots:
            pen.setStyle(Qt.DashLine)
        item.setPen(pen)
        item.setZValue(120)
        item.setToolTip(f"Zona {area.index}: {len(area.nodes)} nodos" + (" · llena de bots" if area.bots else ""))
        self.view.scene().addItem(item)
        self._items.append(item)

    def _route(self, route):
        path = QPainterPath(QPointF(route[0][0] * WORLD, route[0][1] * WORLD))
        for x, y in route[1:] + route[:1]:
            path.lineTo(QPointF(x * WORLD, y * WORLD))
        item = QGraphicsPathItem(path)
        pen = QPen(QColor("#2ec0f2"), 2.2, Qt.DashLine)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setZValue(150)
        item.setToolTip(f"Ruta recomendada: {len(route)} nodos en vuelta")
        self.view.scene().addItem(item)
        self._items.append(item)
        start = QGraphicsSimpleTextItem("▶ inicio")
        start.setBrush(QBrush(QColor("#2ec0f2")))
        start.setFont(self.fonts.name)
        start.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        start.setPos(route[0][0] * WORLD, route[0][1] * WORLD)
        start.setZValue(260)
        self.view.scene().addItem(start)
        self._items.append(start)

    def _zone_label(self, name, x, y, faded):
        text = QGraphicsSimpleTextItem(name)
        text.setBrush(QBrush(QColor(255, 255, 255, 120 if faded else 235)))
        text.setFont(self.fonts.name if not faded else self.fonts.small)
        text.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        text.setPos(x * WORLD, y * WORLD)
        text.setZValue(250)
        self.view.scene().addItem(text)
        self._items.append(text)
        if not faded:                     # a zone with the material but no known points: a soft halo
            halo = Marker(x, y, QColor(46, 192, 242, 70), 26, f"{name}: aquí hay {self.material}")
            halo.setPen(QPen(QColor(46, 192, 242, 180), 1.5))
            halo.setZValue(110)
            self.view.scene().addItem(halo)
            self.view.markers.append(halo)

    # --- side panel ---
    def _panel(self, areas, loose, zones, closed=False):
        while self.panel.count():
            widget = self.panel.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        f, g = self.fonts, self.gathering
        faction = self.ctl.crafting_faction()
        mobs = g.info(self.material).get("monsters", [])
        if mobs:
            self.panel.addWidget(SectionTitle("Quién lo suelta", f))
            for m in mobs:
                p = m["chance"] / 100
                kills = math.ceil(math.log(0.1) / math.log(1 - p)) if 0 < p < 1 else 1
                line = label(f"{m['name']} · Lv {m['level']} · {m['chance']:g}% · ~{kills} kills para un 90%"
                             + (f"\n{m['zone']}" if m.get("zone") else ""), f.small, theme.TEXT)
                line.setWordWrap(True)
                self.panel.addWidget(line)
            if not g.maps(self.material):
                self.panel.addWidget(self._hint("metabot no dibuja dónde aparecen estos monstruos (suelen ser "
                                                "jefes o de mazmorra)."))
                self.panel.addStretch(1)
                return
        if closed:
            self.panel.addWidget(self._hint(
                f"En lo que está abierto ahora del Abismo no hay {self.material}: solo en mapas que aún no abren. "
                f"Desmarca «Solo lo abierto ahora» para verlos."))
            self.panel.addStretch(1)
            return
        for region, map_id, nodes, zone_names in g.side_regions(self.material, faction):
            line = label(f"{region}: {nodes} nodos · {', '.join(zone_names)}", f.small, theme.TEXT)
            line.setWordWrap(True)
            self.panel.addWidget(line)
        if not areas:
            self.panel.addWidget(self._hint(
                f"De este mapa metabot no publica los puntos de {self.material} (en su página solo dibuja una región, "
                f"hasta 40 puntos): se marcan las zonas donde está. Ahí, busca los nodos de "
                f"{g.info(self.material).get('node', self.material)}."))
            self.panel.addStretch(1)
            return
        self.panel.addWidget(SectionTitle("Zonas", f))
        legend = label("Verde: la recomendada (más nodos sin bots), con la ruta en azul. Amarillo: otras. Rojo: con bots. "
                       f"Las marcas de bots se borran solas a los {BOT_DAYS} días (los bots se mueven).", f.small,
                       theme.MUTED)
        legend.setWordWrap(True)
        self.panel.addWidget(legend)
        for area in areas[:AREAS_SHOWN]:
            self.panel.addWidget(self._area_row(area, area is areas[0] and not area.bots, zones))
        if len(areas) > AREAS_SHOWN:
            self.panel.addWidget(label(f"y {len(areas) - AREAS_SHOWN} zonas más pequeñas en el mapa.", f.small,
                                       theme.MUTED))
        if loose:
            self.panel.addWidget(label(f"{len(loose)} puntos sueltos (grises), lejos de las zonas.", f.small, theme.MUTED))
        self.panel.addWidget(self._hint("Clic derecho en un punto del mapa para marcarlo «con bots»: sale de la ruta."))
        self.panel.addStretch(1)

    def _area_row(self, area, best, zones):
        f = self.fonts
        row = QFrame()
        row.setObjectName("AreaRow")
        row.setProperty("best", best)
        row.setProperty("bots", area.bots)
        lay = QVBoxLayout(row)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)
        cx, cy = area.center
        near = min(zones, key=lambda z: (z[1] - cx) ** 2 + (z[2] - cy) ** 2)[0] if zones else ""
        head = QHBoxLayout()
        title = f"{'★ Recomendada · ' if best else ''}Zona {area.index}" + (f" · {near}" if near else "")
        head.addWidget(label(title, f.name, BOTS if area.bots else BEST if best else theme.TEXT, elide=True), 1)
        see = QPushButton("Ver")
        see.setProperty("flat", True)
        see.clicked.connect(lambda _=False, a=area: self.view.focus_points(a.nodes))
        head.addWidget(see)
        lay.addLayout(head)
        free = len(area.route)
        mobs = self.gathering.info(self.material).get("monsters", [])
        if mobs and area.mobs:
            best_mob = max((mobs[i] for i in area.mobs if i < len(mobs)), key=lambda m: m["chance"])
            sub = f"{len(area.nodes)} spawns · {best_mob['name']} {best_mob['chance']:g}%"
        else:
            sub = f"{len(area.nodes)} nodos"
        if free != len(area.nodes):
            sub += f" · {free} sin bots en la ruta"
        if area.bots:
            sub += f" · llena de bots (marcada {_ago(area.bot_at)})"
        lay.addWidget(label(sub, f.small, BOTS if area.bots else theme.MUTED))
        mark = QPushButton("Quitar «llena de bots»" if area.bots else "Marcar: llena de bots")
        mark.setProperty("flat", True)
        mark.clicked.connect(lambda _=False, a=area: self._toggle_area(a))
        lay.addWidget(mark, 0, Qt.AlignLeft)
        return row

    def _hint(self, text):
        hint = label(text, self.fonts.small, theme.MUTED)
        hint.setWordWrap(True)
        return hint

    # --- bots ---
    def _toggle_area(self, area):
        self.gathering.toggle_area(self.material, self.map_id, area)
        self.refresh()

    def _toggle_node(self, point):
        self.gathering.toggle_node(self.material, self.map_id, point)
        self.refresh()


def _ago(ts):
    if not ts:
        return ""
    hours = (time.time() - ts) / 3600
    return "hace un momento" if hours < 1 else f"hace {hours:.0f} h" if hours < 48 else f"hace {hours / 24:.0f} días"
