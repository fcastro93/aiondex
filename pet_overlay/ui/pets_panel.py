"""Overlay panel of the Pets module: focus card, session, zone suggestions, closest to level-up."""
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from . import theme
from .widgets import FocusCard, PetRow, SectionTitle, label


@dataclass
class ViewState:
    focus: object = None                 # Pet shown in the focus card
    focus_pinned: bool = False
    recent: list = field(default_factory=list)
    closest: list = field(default_factory=list)
    zone_name: str = ""                  # zone the player seems to be farming in
    nearby: list = field(default_factory=list)
    summary: str = ""
    status: str = ""
    status_color: QColor = field(default_factory=lambda: QColor(theme.MUTED))
    session_line: str = ""
    flash_ids: set = field(default_factory=set)


class PetsPanel(QWidget):
    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(1))
        self._rows = {}

    def render(self, state, compact):
        _clear(self.content)
        self._rows = {}
        if state.focus is None:
            self.content.addWidget(label("Sin datos todavía", self.fonts.body, theme.MUTED))
        else:
            card = FocusCard(state.focus, *self._pet_args(state.focus), self.ctl.session, state.focus_pinned,
                             side=self.ctl.pet_exclusive(state.focus.pet_id))
            self._register(card, state.focus.pet_id)
            self.content.addWidget(card)
            if not compact:
                self._section("Esta sesión", state.recent)
                if state.zone_name:
                    self._section(f"En tu zona también · {state.zone_name}", state.nearby)
                self._section("Más cerca de subir", state.closest)
        for pid in state.flash_ids:
            if pid in self._rows:
                self._rows[pid].flash()
        self.adjustSize()

    @staticmethod
    def footer(state, compact):
        if compact and state.session_line:
            return state.session_line
        return "\n".join(t for t in (state.session_line, state.status) if t)

    def _section(self, title, pets):
        if not pets:
            return
        self.content.addWidget(SectionTitle(title, self.fonts))
        for pet in pets:
            row = PetRow(pet, *self._pet_args(pet), self.ctl.session.per_pet.get(pet.pet_id, 0),
                         side=self.ctl.pet_exclusive(pet.pet_id))
            self._register(row, pet.pet_id)
            self.content.addWidget(row)

    def _pet_args(self, pet):
        info, name = self.ctl.pet_info(pet.pet_id)
        return info, name, self.fonts, self.ctl.icons_dir

    def _register(self, widget, pet_id):
        widget.on_click = lambda pid=pet_id: self.ctl.open_map(pid)   # shell calls this on a plain click
        widget.setContextMenuPolicy(Qt.CustomContextMenu)
        widget.customContextMenuRequested.connect(lambda pos, w=widget, pid=pet_id: self._pet_menu(w, pid, pos))
        self._rows.setdefault(pet_id, widget)

    def _pet_menu(self, widget, pet_id, pos):
        menu = QMenu(self)
        menu.addAction("Ver en el mapa", lambda: self.ctl.open_map(pet_id))
        if self.ctl.settings["pinned"] == pet_id:
            menu.addAction("Quitar fijado", lambda: self.ctl.pin(None))
        else:
            menu.addAction("Fijar como objetivo", lambda: self.ctl.pin(pet_id))
        menu.exec(widget.mapToGlobal(pos))


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
