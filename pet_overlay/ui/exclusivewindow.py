"""Exclusive pets window: the pets that can only be farmed on one side (the monsters that drop their souls live only
there), one tab for the Asmodian side and one for the Elyos side, with your progress and where to farm each one."""
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..pets import Pet
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import PetIcon, ProgressBar, label

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
        return "Nivel máximo"
    if pet.level == 0:
        return f"Almas {pet.progress}/{pet.threshold}" if pet.progress else "Sin empezar"
    return f"Nivel {pet.level} · {pet.progress}/{pet.threshold}"


class ExclusivePetsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        own = ctl.character_faction() or ctl.crafting_faction()
        self.side = own if own in SIDES else SIDES[0]
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — Pets exclusivos de cada lado")
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
        titles.addWidget(label("Pets exclusivos", f.name_big, theme.TEXT))
        titles.addWidget(label("Solo se farmean en un lado: los monstruos que sueltan su alma no existen en el otro. "
                               "Clic en un pet para verlo en el mapa.", f.small, theme.MUTED))
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
        self.hide_max = QCheckBox("Ocultar los que tengo al máximo")
        self.hide_max.toggled.connect(lambda _: self.refresh())
        bar.addWidget(self.hide_max)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar pet, monstruo o zona…")
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
        credit = QPushButton("Pets, monstruos y zonas: metabot.gg")
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
        self.summary.setText(f"Tienes {owned} de {len(pets)} desbloqueados · {maxed} al máximo")
        query = self.search.text().strip().lower()
        shown = [p for p in pets if not (self.hide_max.isChecked() and p.is_max) and self._matches(p, query)]
        if not shown:
            empty = label("Ningún pet con ese filtro." if pets else "No hay datos de pets: actualiza la base de datos "
                          "desde la bandeja.", f.body, theme.MUTED)
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
        f, ctl = self.fonts, self.ctl
        card = PetCard(lambda pid=pet.pet_id: ctl.open_map(pid))
        card.setToolTip("Clic: ver dónde farmearlo en el mapa")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)
        lay.addWidget(PetIcon(ctl.icons_dir, pet, PORTRAIT, f), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        _, name = ctl.pet_info(pet.pet_id)
        col.addWidget(label(name, f.name, theme.GOLD if pet.is_max else theme.TEXT, elide=True))
        state = QHBoxLayout()
        state.addWidget(label(state_text(pet), f.small, theme.GOLD if pet.is_max else
                              theme.ACCENT if pet.level or pet.progress else theme.MUTED))
        bar = ProgressBar(pet.ratio, theme.GOLD if pet.is_max else theme.ACCENT, 4)
        bar.setFixedWidth(70)
        state.addStretch(1)
        state.addWidget(bar, 0, Qt.AlignVCenter)
        col.addLayout(state)
        for src, mob in ctl.mapdata.sources(pet.pet_id)[:2]:
            zones = mob.get("zones", [])
            where = (f" · {zones[0]['name']} ({zones[0].get('region', '')})" if zones else "") + \
                (f" y {len(zones) - 1} más" if len(zones) > 1 else "")
            chance = src.get("chance")
            line = label(f"{mob.get('name', src['monster'])} (Lv {src.get('level') or mob.get('level')})"
                         + (f" · {chance:g}%" if chance and chance < 100 else "") + where, f.small, theme.MUTED,
                         elide=True)
            col.addWidget(line)
        lay.addLayout(col, 1)
        return card


class PetCard(QFrame):
    """A pet's card: highlights on hover, opens the map on click."""

    def __init__(self, on_click):
        super().__init__()
        self.on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        self._paint(False)

    def _paint(self, hover):
        self.setStyleSheet(f"PetCard {{ background: {'#18264a' if hover else '#101a30'}; border-radius: 8px; "
                           f"border: 1px solid {'#2f4a80' if hover else '#1a2643'}; }}")

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
