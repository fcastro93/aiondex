"""Pet windows: the exclusive pets (the ones only one side can farm: the monsters that drop their souls live only
there), one tab per side; and the whole collection (every pet, by state: leveling, not started, maxed), filtered by
side, by where its soul comes from (monsters of the open world, open-world bosses, the Abyss, dungeons, only
dungeons, only chests: data/pet_sources.json from aion2data) and by map and zone. Each card has your progress and
where to farm the pet; a click opens it on the map."""
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..pets import Pet
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import PetIcon, ProgressBar, label
from ..i18n import tr

SIDES = ("asmodian", "elyos")
COLUMNS = 3
PORTRAIT = 44


def exclusive_pets(ctl, side):
    """[Pet] that can only be farmed on this side: the ones you are leveling first (closest to max), then the ones
    you have not started, the maxed ones last."""
    out = []
    for pid in ctl.mapdata.db.get("pets", {}):
        if ctl.mapdata.exclusive(int(pid)) == side:
            out.append(ctl.collection.pets.get(int(pid)) or Pet(int(pid), 0, 0))
    return sorted(out, key=lambda p: (p.is_max, p.level == 0 and p.progress == 0, p.remaining_to_max,
                                      ctl.pet_info(p.pet_id)[1]))


def state_text(pet):
    if pet.is_max:
        return tr("Nivel máximo")
    if pet.level == 0:
        return tr("Almas {0}/{1}", pet.progress, pet.threshold) if pet.progress else tr("Sin empezar")
    return tr("Nivel {0} · {1}/{2}", pet.level, pet.progress, pet.threshold)


class ExclusivePetsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        own = ctl.character_faction() or ctl.crafting_faction()
        self.side = own if own in SIDES else SIDES[0]
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Pets exclusivos de cada lado", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE)
        self.resize(1080, 820)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(tr("Pets exclusivos"), f.name_big, theme.TEXT))
        titles.addWidget(label(tr("Solo se farmean en un lado: los monstruos que sueltan su alma no existen en el "
                                  "otro. Clic en un pet para verlo en el mapa."), f.small, theme.MUTED))
        head.addLayout(titles, 1)
        root.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.tab_buttons = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for side in sorted(SIDES, key=lambda s: s != self.side):        # your side first
            button = QPushButton()
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, s=side: self._show(s))
            group.addButton(button)
            bar.addWidget(button)
            self.tab_buttons[side] = button
        bar.addSpacing(12)
        self.summary = label("", f.body, theme.TEXT)
        bar.addWidget(self.summary)
        bar.addStretch(1)
        self.hide_max = QCheckBox(tr("Ocultar los que tengo al máximo"))
        self.hide_max.toggled.connect(lambda _: self.refresh())
        bar.addWidget(self.hide_max)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar pet, monstruo o zona…"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self._search_timer = QTimer(self, singleShot=True, interval=250, timeout=self.refresh)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        bar.addWidget(self.search)
        root.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setSpacing(10)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        credit = QPushButton(tr("Pets, monstruos y zonas: metabot.gg"))
        credit.setProperty("flat", True)
        credit.clicked.connect(lambda: webbrowser.open("https://metabot.gg/en/aion-2"))
        foot.addWidget(credit)
        foot.addStretch(1)
        root.addLayout(foot)
        self.refresh()

    def _show(self, side):
        self.side = side
        self.refresh()

    # --- content ---
    def refresh(self):
        _clear(self.body)
        f = self.fonts
        for side, button in self.tab_buttons.items():
            button.setText(f"{theme.FACTION_LABEL[side]} · {len(exclusive_pets(self.ctl, side))}")
            button.setChecked(side == self.side)
        pets = exclusive_pets(self.ctl, self.side)
        owned = sum(1 for p in pets if p.level >= 1)
        maxed = sum(1 for p in pets if p.is_max)
        self.summary.setText(tr("Tienes {0} de {1} desbloqueados · {2} al máximo", owned, len(pets), maxed))
        query = self.search.text().strip().lower()
        shown = [p for p in pets if not (self.hide_max.isChecked() and p.is_max) and self._matches(p, query)]
        if not shown:
            empty = label(tr("Ningún pet con ese filtro.") if pets else tr("No hay datos de pets: actualiza la base de "
                                                                       "datos desde la bandeja."), f.body, theme.MUTED)
            self.body.addWidget(empty)
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, pet in enumerate(shown):
            grid.addWidget(self._card(pet), i // COLUMNS, i % COLUMNS)
        for c in range(COLUMNS):
            grid.setColumnStretch(c, 1)
        self.body.addLayout(grid)
        self.body.addStretch(1)

    def _matches(self, pet, query):
        if not query:
            return True
        text = [self.ctl.pet_info(pet.pet_id)[1]]
        for _, mob in self.ctl.mapdata.sources(pet.pet_id):
            text.append(mob.get("name", ""))
            text += [z.get("name", "") + " " + z.get("region", "") for z in mob.get("zones", [])]
        return query in " ".join(text).lower()

    def _card(self, pet):
        return pet_card(self.ctl, self.fonts, pet)


def pet_card(ctl, f, pet, show_side=False):
    """A pet's card: icon, name, state and progress bar, the monsters that drop its soul and where."""
    card = PetCard(lambda pid=pet.pet_id: ctl.open_map(pid))
    card.setToolTip(tr("Clic: ver dónde farmearlo en el mapa"))
    lay = QHBoxLayout(card)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.setSpacing(10)
    lay.addWidget(PetIcon(ctl.icons_dir, pet, PORTRAIT, f), 0, Qt.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(2)
    _, name = ctl.pet_info(pet.pet_id)
    col.addWidget(label(name, f.name, theme.GOLD if pet.is_max else theme.TEXT, elide=True))
    side = ctl.mapdata.exclusive(pet.pet_id) if show_side else None
    if side in SIDES:
        col.addWidget(label(tr("Solo {0}", theme.FACTION_LABEL[side]), f.small, theme.faction_color(side)))
    state = QHBoxLayout()
    state.addWidget(label(state_text(pet), f.small, theme.GOLD if pet.is_max else
                          theme.ACCENT if pet.level or pet.progress else theme.MUTED))
    bar = ProgressBar(pet.ratio, theme.GOLD if pet.is_max else theme.ACCENT, 4)
    bar.setFixedWidth(70)
    state.addStretch(1)
    state.addWidget(bar, 0, Qt.AlignVCenter)
    col.addLayout(state)
    origin = ctl.pet_sources(pet.pet_id)
    kinds = [KIND_TEXT[k] for k in ORIGIN_ORDER if k in origin.get("kinds", [])]
    if kinds:
        col.addWidget(label(" · ".join(kinds), f.small, theme.ACCENT, elide=True))
    shown = 0
    for src, mob in ctl.mapdata.sources(pet.pet_id)[:2]:
        zones = mob.get("zones", [])
        where = (f" · {zones[0]['name']} ({zones[0].get('region', '')})" if zones else "") + \
            (tr(" y {0} más", len(zones) - 1) if len(zones) > 1 else "")
        chance = src.get("chance")
        line = label(f"{mob.get('name', src['monster'])} (Lv {src.get('level') or mob.get('level')})"
                     + (f" · {chance:g}%" if chance and chance < 100 else "") + where, f.small, theme.MUTED,
                     elide=True)
        col.addWidget(line)
        shown += 1
    for s in origin.get("sources", []):           # the ones metabot's maps do not have: dungeons, Morheim, Eltnen...
        if shown >= 2:
            break
        places = [m for m, _ in s["where"] if m not in WORLD_MAPS or not ctl.mapdata.sources(pet.pet_id)]
        if not places:
            continue
        area = next((a for m, a in s["where"] if m == places[0] and a), "")
        col.addWidget(label((tr("Jefe ") if s.get('boss') else '') + s['monster']
                            + (f" (Lv {s['level']})" if s.get("level") else "")
                            + (f" · {s['chance']:g}%" if s.get("chance") and s["chance"] < 100 else "")
                            + f" · {places[0]}" + (f" ({area})" if area else ""), f.small, theme.MUTED, elide=True))
        shown += 1
    if not shown:
        chests = origin.get("chests") or []
        col.addWidget(label((tr("Cofre: ") + ", ".join(chests)) if chests else tr("Sin monstruo que lo suelte: evento o "
                                                                              "tienda"), f.small, theme.MUTED, elide=True))
    lay.addLayout(col, 1)
    return card


COLLECTION_TABS = (("all", tr("Todos")), ("leveling", tr("Subiendo")), ("new", tr("Sin empezar")), ("max", tr("Al máximo")))
SIDE_FILTERS = (("any", tr("Cualquier lado")), ("asmodian", tr("Solo Asmodian")), ("elyos", tr("Solo Elyos")), ("both", tr("Ambos lados")))
# where a pet's soul comes from (data/pet_sources.json "kinds")
ORIGIN_ORDER = ("world", "boss", "abyss", "dungeon", "chest")
KIND_TEXT = {"world": tr("Mundo abierto"), "boss": tr("Jefe de mundo abierto"), "abyss": tr("Abismo"), "dungeon": tr("Mazmorra"),
             "chest": tr("Solo de cofres")}
ORIGIN_FILTERS = (("any", tr("Cualquier origen")), ("world", tr("Monstruos del mundo abierto")),
                  ("boss", tr("Jefes del mundo abierto")), ("abyss", tr("Abismo")), ("dungeon", tr("Mazmorras e instancias")),
                  ("dungeon_only", tr("Solo en mazmorras")), ("chest", tr("Solo de cofres")))
WORLD_MAPS = ("Poeta", "Verteron", "Eltnen", "Ishalgen", "Altgard", "Morheim")
ABYSS_MAPS = ("Chaotic Lower Reshanta", "Chaotic Middle Reshanta", "Chaotic Upper Reshanta")


def all_pets(ctl):
    """Every pet of the game with your progress: the ones you are leveling first (closest to max), then the ones you
    have not started, the maxed ones last."""
    out = [ctl.collection.pets.get(int(pid)) or Pet(int(pid), 0, 0) for pid in ctl.mapdata.db.get("pets", {})]
    return sorted(out, key=lambda p: (p.is_max, p.level == 0 and p.progress == 0, p.remaining_to_max,
                                      ctl.pet_info(p.pet_id)[1]))


def in_tab(pet, key):
    if key == "leveling":
        return not pet.is_max and bool(pet.level or pet.progress)
    if key == "new":
        return pet.level == 0 and pet.progress == 0
    if key == "max":
        return pet.is_max
    return True


class CollectionWindow(QWidget):
    """The whole pet collection."""

    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.tab = "all"
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Colección de pets", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE)
        self.resize(1180, 860)
        f = self.fonts
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(tr("Colección de pets"), f.name_big, theme.TEXT))
        titles.addWidget(label(tr("Todos los pets con tu progreso (se lee del juego al entrar con un personaje; la "
                                  "colección es de toda la cuenta). Clic en un pet para verlo en el mapa."), f.small,
                               theme.MUTED))
        head.addLayout(titles, 1)
        self.summary = label("", f.name, theme.TEXT)
        head.addWidget(self.summary)
        root.addLayout(head)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.tab_buttons = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, _ in COLLECTION_TABS:
            button = QPushButton()
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self._show(k))
            group.addButton(button)
            bar.addWidget(button)
            self.tab_buttons[key] = button
        filters = QHBoxLayout()                  # a row of their own: the window never gets wider than the screen
        filters.setSpacing(6)
        filters.addWidget(label(tr("Filtrar:"), f.small, theme.MUTED))
        self.side = QComboBox()
        for key, text in SIDE_FILTERS:
            self.side.addItem(text, key)
        self.side.currentIndexChanged.connect(lambda _: self.refresh())
        filters.addWidget(self.side)
        self.origin = QComboBox()
        self.origin.setToolTip(tr("De dónde sale el alma del pet (aion2data: los monstruos que la sueltan y dónde "
                                  "aparecen, o los cofres)"))
        self.origin.currentIndexChanged.connect(lambda _: self.refresh())
        filters.addWidget(self.origin)
        self.map = QComboBox()
        self.map.setToolTip(tr("El mapa: una región del mundo, una capa del Abismo o una mazmorra"))
        self.map.currentIndexChanged.connect(lambda _: self._map_changed())
        filters.addWidget(self.map)
        self.zone = QComboBox()
        self.zone.setToolTip(tr("La zona dentro del mapa elegido"))
        self.zone.currentIndexChanged.connect(lambda _: self.refresh())
        filters.addWidget(self.zone)
        filters.addStretch(1)
        for combo, chars in ((self.side, 12), (self.origin, 18), (self.map, 18), (self.zone, 16)):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)   # the list shows them whole
            combo.setMinimumContentsLength(chars)
            combo.view().setMinimumWidth(320)
        self._fill_filters()
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar pet, monstruo o zona…"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(260)
        self._search_timer = QTimer(self, singleShot=True, interval=250, timeout=self.refresh)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        bar.addWidget(self.search)
        root.addLayout(bar)
        root.addLayout(filters)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setSpacing(10)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        foot = QHBoxLayout()
        for text, url in ((tr("Pets, monstruos y zonas: metabot.gg"), "https://metabot.gg/en/aion-2"),
                          (tr("De dónde sale cada alma: aion2data.com"), "https://aion2data.com/monsters")):
            credit = QPushButton(text)
            credit.setProperty("flat", True)
            credit.clicked.connect(lambda _=False, u=url: webbrowser.open(u))
            foot.addWidget(credit)
        foot.addStretch(1)
        root.addLayout(foot)
        self.refresh()

    def _show(self, key):
        self.tab = key
        self.refresh()

    def _fill_filters(self):
        """The origin and map choices, with how many pets each has."""
        sources = self.ctl.pet_sources()
        self.origin.blockSignals(True)
        for key, text in ORIGIN_FILTERS:
            n = sum(1 for e in sources.values() if _origin_ok(e, key))
            self.origin.addItem(f"{text} ({n})" if key != "any" else text, key)
        self.origin.blockSignals(False)
        maps = {}
        for e in sources.values():
            for name in e.get("maps", {}):
                maps[name] = maps.get(name, 0) + 1
        self.map.blockSignals(True)
        self.map.addItem(tr("Todos los mapas"), None)
        for group in (WORLD_MAPS, ABYSS_MAPS, sorted(m for m in maps if m not in WORLD_MAPS + ABYSS_MAPS)):
            names = [m for m in group if m in maps]
            if names and self.map.count() > 1:
                self.map.insertSeparator(self.map.count())
            for name in names:
                tag = "" if name in WORLD_MAPS else tr(" (Abismo)") if name in ABYSS_MAPS else tr(" (mazmorra)")
                self.map.addItem(f"{name}{tag} · {maps[name]}", name)
        self.map.blockSignals(False)
        self._map_changed(refresh=False)

    def _map_changed(self, refresh=True):
        """The zones of the map you picked."""
        name = self.map.currentData()
        self.zone.blockSignals(True)
        self.zone.clear()
        self.zone.addItem(tr("Todas las zonas"), None)
        if name:
            zones = {}
            for e in self.ctl.pet_sources().values():
                for area in e.get("maps", {}).get(name, []):
                    zones[area] = zones.get(area, 0) + 1
            for area in sorted(zones):
                self.zone.addItem(f"{area} · {zones[area]}", area)
        self.zone.setEnabled(self.zone.count() > 1)
        self.zone.blockSignals(False)
        if refresh:
            self.refresh()

    def _where_ok(self, pet):
        origin = self.ctl.pet_sources(pet.pet_id)
        if not _origin_ok(origin, self.origin.currentData()):
            return False
        name, area = self.map.currentData(), self.zone.currentData()
        if name and name not in origin.get("maps", {}):
            return False
        return not area or area in origin.get("maps", {}).get(name, [])

    def _side_ok(self, pet):
        want = self.side.currentData()
        side = self.ctl.mapdata.exclusive(pet.pet_id)
        if want in SIDES:
            return side == want
        if want == "both":
            return side not in SIDES
        return True

    def refresh(self):
        _clear(self.body)
        f = self.fonts
        pets = all_pets(self.ctl)
        sided = [p for p in pets if self._side_ok(p) and self._where_ok(p)]
        for key, text in COLLECTION_TABS:
            self.tab_buttons[key].setText(f"{text} · {sum(1 for p in sided if in_tab(p, key))}")
            self.tab_buttons[key].setChecked(key == self.tab)
        owned = sum(1 for p in pets if p.level >= 1)
        maxed = sum(1 for p in pets if p.is_max)
        self.summary.setText(tr("Tienes {0} de {1} · {2} al máximo", owned, len(pets), maxed))
        query = self.search.text().strip().lower()
        shown = [p for p in sided if in_tab(p, self.tab) and _pet_matches(self.ctl, p, query)]
        if not shown:
            self.body.addWidget(label(tr("Ningún pet con ese filtro.") if pets else tr("No hay datos de pets: actualiza "
                                                                                   "la base de datos desde la "
                                                                                   "bandeja."), f.body, theme.MUTED))
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, pet in enumerate(shown):
            grid.addWidget(pet_card(self.ctl, f, pet, show_side=True), i // COLUMNS, i % COLUMNS)
        for c in range(COLUMNS):
            grid.setColumnStretch(c, 1)
        self.body.addLayout(grid)
        self.body.addStretch(1)


def _origin_ok(origin, key):
    kinds = origin.get("kinds", [])
    if key in (None, "any"):
        return True
    if key == "dungeon_only":
        return kinds == ["dungeon"]
    if key == "chest":
        return kinds == ["chest"]
    return key in kinds


def _pet_matches(ctl, pet, query):
    if not query:
        return True
    text = [ctl.pet_info(pet.pet_id)[1]]
    for _, mob in ctl.mapdata.sources(pet.pet_id):
        text.append(mob.get("name", ""))
        text += [z.get("name", "") + " " + z.get("region", "") for z in mob.get("zones", [])]
    origin = ctl.pet_sources(pet.pet_id)
    for s in origin.get("sources", []):
        text.append(s["monster"])
        text += [f"{m} {a}" for m, a in s["where"]]
    text += origin.get("chests", [])
    return query in " ".join(text).lower()


class PetCard(QFrame):
    """A pet's card: highlights on hover, opens the map on click."""

    def __init__(self, on_click):
        super().__init__()
        self.on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        self._paint(False)

    def _paint(self, hover):
        self.setStyleSheet(f"PetCard {{ background: {'#18264a' if hover else '#12161f'}; border-radius: 8px; "
                           f"border: 1px solid {'#2f4a80' if hover else '#1e2532'}; }}")

    def enterEvent(self, _):
        self._paint(True)

    def leaveEvent(self, _):
        self._paint(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.on_click()


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
