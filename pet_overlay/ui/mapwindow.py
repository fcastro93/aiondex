"""Pet map window: where a pet's monsters spawn, and which other pets share those zones."""
from PySide6.QtCore import QSize, Qt, QStringListModel, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QCompleter, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QTabBar, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..paths import ASSETS_DIR, DATA_DIR
from . import theme
from .mapview import MapView, show_you
from .theme import Fonts
from .tiles import TileCache
from .widgets import PetIcon, ProgressBar, label
from ..i18n import tr

OTHER_COLORS = ["#4fc3f7", "#5fdc7c", "#ff9f5a", "#b48cff", "#ff7aa8", "#5ee0d0", "#c6e05a", "#ff6b6b",
                "#8fb2ff", "#e0a0ff"]
FACTION_CHOICES = [("auto", "Auto"), ("elyos", "Elyos"), ("asmodian", "Asmodian"), ("all", tr("Todas"))]

STYLE = f"""
QWidget#MapWindow {{ background: {theme.WINDOW_BG.name()}; }}
QFrame#Side {{ background: {theme.SURFACE.name()}; border-left: 1px solid #262e3d; }}
QLineEdit, QComboBox {{ background: #0a0d14; color: {theme.TEXT.name()}; border: 1px solid #2a3444;
    border-radius: 6px; padding: 5px 8px; selection-background-color: {theme.ACCENT.name()}; }}
QComboBox QAbstractItemView {{ background: #0a0d14; color: {theme.TEXT.name()}; selection-background-color: #243041; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: #2a3444; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QTabBar::tab {{ background: #0f131b; color: {theme.MUTED.name()}; padding: 6px 14px; border: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {theme.SURFACE.name()}; color: {theme.TEXT.name()}; }}
QToolTip {{ background: #0a0d14; color: {theme.TEXT.name()}; border: 1px solid #2a3444; padding: 4px; }}
"""


class Clickable(QFrame):
    """Row that highlights on hover and runs a callback on click."""

    def __init__(self, on_click, selected=False, parent=None):
        super().__init__(parent)
        self.on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        self._selected = selected
        self._paint(False)

    def _paint(self, hover):
        bg = "#243041" if self._selected else ("#18264a" if hover else "transparent")
        border = theme.GOLD.name() if self._selected else "transparent"
        self.setStyleSheet(f"Clickable {{ background: {bg}; border-radius: 6px; border: 1px solid {border}; }}")

    def enterEvent(self, _):
        self._paint(True)

    def leaveEvent(self, _):
        self._paint(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.on_click()


def badge_label(size, dpr):
    """The app logo as a crisp QLabel of size x size logical pixels."""
    px = QPixmap(str(ASSETS_DIR / "badge.png")).scaled(QSize(size, size) * dpr, Qt.KeepAspectRatio,
                                                       Qt.SmoothTransformation)
    px.setDevicePixelRatio(dpr)
    lbl = QLabel()
    lbl.setPixmap(px)
    lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
    return lbl


def chip(text, color, fonts):
    lbl = QLabel(text)
    lbl.setFont(fonts.badge)
    c = QColor(color)
    lbl.setStyleSheet(f"color: {c.name()}; border: 1px solid {c.name()}; border-radius: 7px; padding: 1px 6px;")
    return lbl


class MapWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.data = ctl.mapdata
        self.fonts = Fonts(1.0)
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Mapa de pets", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE)
        self.resize(1320, 800)
        self.pet_id = None
        self.source = None          # monster slug selected in "Dónde farmear"
        self.map_id = None
        self.colors = {}
        self.highlight = None            # a pet of the zone whose dots stand out (clicked in the list or the map)
        self._other_rows = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = QVBoxLayout()
        left.setContentsMargins(8, 8, 0, 0)
        left.setSpacing(0)
        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        left.addWidget(self.tabs)
        self.tiles = TileCache(DATA_DIR / "tiles", self)
        self.view = MapView(self.tiles)
        left.addWidget(self.view, 1)
        root.addLayout(left, 1)
        self._tab_maps = []
        self._where = QTimer(self, interval=1000, timeout=self._show_you)
        self._where.start()

        side = QFrame()
        side.setObjectName("Side")
        side.setFixedWidth(380)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 14, 10, 12)
        sv.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(badge_label(34, self.devicePixelRatioF()))
        title = QVBoxLayout()
        title.setSpacing(0)
        title.addWidget(label(tr("Mapa de pets"), self.fonts.name_big, theme.TEXT))
        self.subtitle = label("", self.fonts.small, theme.MUTED)
        title.addWidget(self.subtitle)
        head.addLayout(title, 1)
        sv.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar pet…"))
        names = sorted({self.ctl.pet_info(int(pid))[1] for pid in self.ctl.db})
        self._name_to_id = {self.ctl.pet_info(int(pid))[1].lower(): int(pid) for pid in self.ctl.db}
        completer = QCompleter(QStringListModel(names, self), self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.activated.connect(self._search_pick)
        completer.popup().setStyleSheet(STYLE)
        self.search.setCompleter(completer)
        self.search.returnPressed.connect(lambda: self._search_pick(self.search.text()))
        sv.addWidget(self.search)

        frow = QHBoxLayout()
        frow.addWidget(label(tr("Facción"), self.fonts.small, theme.MUTED))
        self.faction_box = QComboBox()
        for key, text in FACTION_CHOICES:
            self.faction_box.addItem(text, key)
        self.faction_box.setCurrentIndex(
            max(0, [k for k, _ in FACTION_CHOICES].index(ctl.settings.get("map_faction", "auto"))))
        self.faction_box.currentIndexChanged.connect(self._faction_changed)
        frow.addWidget(self.faction_box, 1)
        sv.addLayout(frow)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.panel = QVBoxLayout(holder)
        self.panel.setContentsMargins(0, 0, 6, 0)
        self.panel.setSpacing(4)
        scroll.setWidget(holder)
        sv.addWidget(scroll, 1)
        self.footer = label(tr("Datos y mapa: metabot.gg"), self.fonts.small, theme.MUTED)
        sv.addWidget(self.footer)
        root.addWidget(side)
        self._update_faction_label()

    # --- public ------------------------------------------------------------------------------
    def show_pet(self, pet_id, source=None):
        self.pet_id, self.source = pet_id, source
        self.highlight = None            # another pet of the zone whose dots stand out
        self._rebuild_tabs()
        self._rebuild_panel()
        self._plot()

    def refresh(self):
        """Collection or faction changed: redraw without moving the map."""
        if self.pet_id is not None:
            self._update_faction_label()
            self._rebuild_panel()
            self._plot(refocus=False)

    # --- state helpers -------------------------------------------------------------------------
    def faction(self):
        return self.ctl.map_faction()

    def _update_faction_label(self):
        here = self.ctl.location_faction()
        own = self.ctl.character_faction()
        if here:
            auto = tr("Auto (donde estás: {0})", theme.FACTION_LABEL[here])
        elif own:
            auto = f"Auto ({theme.FACTION_LABEL[own]})"
        else:
            auto = tr("Auto (sin detectar)")
        self.faction_box.setItemText(0, auto)
        bits = [self.ctl.character_name() or tr("Personaje sin detectar")]
        zone = self.data.zones.get(self.ctl.current_zone or "")
        if zone:
            bits.append(tr("en {0}", zone['name']))
        self.subtitle.setText(" · ".join(bits))

    def _faction_changed(self):
        self.ctl.set_map_faction(self.faction_box.currentData())
        self.show_pet(self.pet_id, self.source)

    def _search_pick(self, text):
        pid = self._name_to_id.get(str(text).strip().lower())
        if pid is not None:
            self.show_pet(pid)

    def _sources(self):
        """[(src, monster)] visible under the current faction filter."""
        faction = self.faction()
        out = []
        for src, mob in self.data.sources(self.pet_id):
            sides = self.data.monster_faction(mob)
            if not faction or not sides or faction in sides or "abyss" in sides:
                out.append((src, mob))
        return out

    # --- map tabs & markers ----------------------------------------------------------------
    def _rebuild_tabs(self):
        maps = self.data.spawn_maps(self.pet_id, self.faction())
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self._tab_maps = []
        preferred = None
        here = set(self.data.zones.get(self.ctl.current_zone or "", {}).get("maps", []))
        for map_id, entries in sorted(maps.items(), key=lambda kv: -sum(len(p) for _, p in kv[1])):
            self.tabs.addTab(self.data.map_label(map_id))
            self._tab_maps.append(map_id)
            if self.source and any(slug == self.source for slug, _ in entries):
                preferred = len(self._tab_maps) - 1
            elif preferred is None and map_id in here:
                preferred = len(self._tab_maps) - 1
        self.tabs.blockSignals(False)
        if self._tab_maps:
            self.tabs.setCurrentIndex(preferred if preferred is not None else 0)
            self.map_id = self._tab_maps[self.tabs.currentIndex()]
            self.view.set_map(self.map_id)
        else:
            self.map_id = None

    def _tab_changed(self, index):
        if 0 <= index < len(getattr(self, "_tab_maps", [])):
            self.map_id = self._tab_maps[index]
            self.view.set_map(self.map_id)
            self._plot()
            self._show_you()

    def _show_you(self):
        if self.isVisible():
            show_you(self.view, self.ctl, self.map_id, self._tab_maps, self.tabs.setCurrentIndex)

    def _plot(self, refocus=True):
        self.view.clear_markers()
        if not self.map_id:
            self.view.focus_points([])
            return
        zones = self._zones()
        others = self.data.pets_in_zones(zones, exclude={self.pet_id})
        for pid, mob_slugs in others.items():
            pet = self.ctl.collection.pets.get(pid)
            if pet and pet.is_max:
                continue
            color = QColor(self._color(pid))
            for slug in mob_slugs:
                mob = self.data.monsters[slug]
                tip = f"{mob['name']} (Lv{mob['level']})\n→ {self.ctl.pet_info(pid)[1]} · {self._progress(pid)}"
                for m in mob["maps"]:
                    if m["map"] == self.map_id:
                        for x, y in m["spawns"]:
                            self.view.add_marker(x, y, color, 4.5, tip + tr("\n(clic: resaltar sus puntos)"),
                                                 on_click=lambda p=pid: self._toggle_highlight(p), group=pid)

        focus_pts = []
        for src, mob in self._sources():
            selected = self.source in (None, src["monster"])
            tip = f"{mob['name']} (Lv{mob['level']}, {mob['rank']}) · {src['chance']:g}%\n→ {self.ctl.pet_info(self.pet_id)[1]}"
            for m in mob["maps"]:
                if m["map"] != self.map_id:
                    continue
                for x, y in m["spawns"]:
                    self.view.add_marker(x, y, theme.GOLD, 6.5 if selected else 5, tip,
                                         on_click=lambda s=src["monster"]: self.show_pet(self.pet_id, s),
                                         ring=selected and self.source is not None, z=200 if selected else 150,
                                         group=("main", src["monster"]))
                    if selected:
                        focus_pts.append((x, y))
        if refocus:
            self.view.focus_points(focus_pts)
        self._apply_emphasis()

    def _apply_emphasis(self):
        """A pet of the zone picked: its dots outlined, the rest faded. One monster of the shown pet picked: the
        same with its dots. Nothing picked: all as usual."""
        if getattr(self, "highlight", None) is not None:
            self.view.emphasize(lambda g: g == self.highlight)
        elif self.source is not None:
            self.view.emphasize(lambda g: g == ("main", self.source))
        else:
            self.view.emphasize(None)

    def _toggle_highlight(self, pid):
        self.highlight = None if self.highlight == pid else pid
        self._apply_emphasis()
        for opid, rows in self._other_rows.items():
            for row in rows:
                row._selected = opid == self.highlight
                row._paint(False)

    def _zones(self):
        zones = set()
        for src, mob in self._sources():
            if self.source in (None, src["monster"]):
                zones.update(z["slug"] for z in mob["zones"])
        return zones

    def _color(self, pid):
        if pid not in self.colors:
            self.colors[pid] = OTHER_COLORS[len(self.colors) % len(OTHER_COLORS)]
        return self.colors[pid]

    def _progress(self, pid):
        pet = self.ctl.collection.pets.get(pid)
        if pet is None:
            return tr("no lo tienes")
        if pet.is_max:
            return "MAX"
        if pet.level == 0:
            return tr("almas {0}/{1}", pet.progress, pet.threshold)
        return f"N{pet.level} {pet.progress}/{pet.threshold}"

    # --- side panel ----------------------------------------------------------------------------
    def _clear_panel(self):
        while self.panel.count():
            item = self.panel.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout():
                lay = item.layout()
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

    def _section(self, text):
        lbl = label(text.upper(), self.fonts.section, theme.MUTED)
        lbl.setContentsMargins(2, 12, 0, 2)
        self.panel.addWidget(lbl)

    def _rebuild_panel(self):
        self._clear_panel()
        f = self.fonts
        pid = self.pet_id
        info, name = self.ctl.pet_info(pid)
        pet = self.ctl.collection.pets.get(pid)

        card = QFrame()
        card.setStyleSheet("QFrame { background: #0f131b; border-radius: 10px; }")
        row = QHBoxLayout(card)
        row.setContentsMargins(10, 10, 12, 10)
        row.setSpacing(12)
        row.addWidget(PetIcon(self.ctl.icons_dir, pet or _Ghost(pid), 56, f), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(label(name, f.name_big, theme.TEXT, elide=True))
        col.addWidget(label(self._progress(pid), f.body, theme.GOLD if pet and pet.is_max else theme.TEXT))
        if pet and not pet.is_max and pet.threshold:
            col.addWidget(ProgressBar(pet.ratio, theme.ACCENT, 6))
        chips = QHBoxLayout()
        chips.setSpacing(6)
        excl = self.data.exclusive(pid)
        if excl:
            chips.addWidget(chip(tr("Solo {0}", theme.FACTION_LABEL[excl]), theme.faction_color(excl), f))
        else:
            for fac in sorted(self.data.factions(pid)):
                chips.addWidget(chip(theme.FACTION_LABEL.get(fac, fac), theme.faction_color(fac), f))
        chips.addStretch(1)
        col.addLayout(chips)
        row.addLayout(col, 1)
        self.panel.addWidget(card)

        faction = self.faction()
        if faction and not self.data.can_farm(pid, faction):
            warn = label(tr("No se farmea en el lado {0}.", theme.FACTION_LABEL[faction]), f.small, theme.WARN)
            warn.setWordWrap(True)
            self.panel.addWidget(warn)

        self._section(tr("Dónde farmear"))
        sources = self._sources()
        if not sources:
            self.panel.addWidget(label(tr("Sin datos de aparición para esta facción."), f.small, theme.MUTED))
        for src, mob in sources:
            selected = self.source == src["monster"]
            item = Clickable(lambda s=src["monster"]: self.show_pet(pid, None if self.source == s else s), selected)
            lay = QVBoxLayout(item)
            lay.setContentsMargins(8, 6, 8, 6)
            lay.setSpacing(1)
            top = QHBoxLayout()
            dot = label("●", f.small, theme.GOLD)
            top.addWidget(dot)
            top.addWidget(label(mob["name"], f.name, theme.TEXT, elide=True), 1)
            top.addWidget(label(f"{src['chance']:g}%" if src.get("chance") is not None else "", f.small, theme.MUTED))
            lay.addLayout(top)
            where = ", ".join(f"{z['name']} ({z['region']})" for z in mob["zones"]) or tr("zona desconocida")
            meta = label(f"Lv{mob['level']} · {mob['rank']} · {where}", f.small, theme.MUTED)
            meta.setWordWrap(True)
            lay.addWidget(meta)
            self.panel.addWidget(item)

        zones = self._zones()
        self._other_rows = {}
        if zones:
            self._section(tr("En esta zona también"))
            hint = label(tr("Clic en un pet: resalta sus puntos en el mapa (otro clic lo quita). «Ver»: abrir ese "
                            "pet."),
                         f.small, theme.MUTED)
            hint.setWordWrap(True)
            self.panel.addWidget(hint)
            for zslug in sorted(zones, key=lambda s: self.data.zones[s]["name"]):
                zone = self.data.zones[zslug]
                found = self.data.pets_in_zones([zslug], exclude={pid})
                entries = []
                for opid, mobs in found.items():
                    opet = self.ctl.collection.pets.get(opid)
                    if opet and opet.is_max:
                        continue
                    missing = opet is None or opet.level == 0
                    entries.append((not missing, opet.ratio if opet else 0, opid, mobs))
                if not entries:
                    continue
                zl = label(f"{zone['name']} · {zone['region']}", f.small, theme.TEXT)
                zl.setContentsMargins(2, 6, 0, 0)
                self.panel.addWidget(zl)
                for _, _, opid, mobs in sorted(entries, key=lambda e: (e[0], -e[1])):
                    self.panel.addWidget(self._other_row(opid, mobs))
        self.panel.addStretch(1)

    def _other_row(self, pid, mob_slugs):
        f = self.fonts
        pet = self.ctl.collection.pets.get(pid)
        item = Clickable(lambda: self._toggle_highlight(pid), selected=pid == getattr(self, "highlight", None))
        item.setToolTip(tr("Clic: resaltar sus puntos en el mapa"))
        self._other_rows.setdefault(pid, []).append(item)
        lay = QHBoxLayout(item)
        lay.setContentsMargins(6, 4, 8, 4)
        lay.setSpacing(8)
        lay.addWidget(label("●", f.small, QColor(self._color(pid))))
        lay.addWidget(PetIcon(self.ctl.icons_dir, pet or _Ghost(pid), 28, f))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(self.ctl.pet_info(pid)[1], f.name, theme.TEXT, elide=True))
        mobs = ", ".join(self.data.monsters[s]["name"] for s in mob_slugs)
        col.addWidget(label(mobs, f.small, theme.MUTED, elide=True))
        lay.addLayout(col, 1)
        missing = pet is None or pet.level == 0
        lay.addWidget(label(tr("Te falta") if missing else self._progress(pid), f.small,
                            theme.WARN if missing else theme.TEXT))
        see = QPushButton(tr("Ver"))
        see.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {theme.ACCENT.name()}; "
                          f"padding: 2px 6px; }} QPushButton:hover {{ color: #6fdcff; }}")
        see.setCursor(Qt.PointingHandCursor)
        see.setToolTip(tr("Abrir este pet en el mapa"))
        see.clicked.connect(lambda _=False: self.show_pet(pid))
        lay.addWidget(see)
        return item


class _Ghost:
    """Stand-in for a pet the player does not own yet (renders a muted badge)."""

    def __init__(self, pet_id):
        self.pet_id = pet_id
        self.level = 0
        self.is_max = False
