"""Overlay panel of the Pets module.

Shows the pets farmable where the player is, closest to max level first. When there are none (zone
unknown, or everything there is maxed) it shows collection statistics: share of pets per level.
"""
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGridLayout, QMenu, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .widgets import PetRow, SectionTitle, label

NEARBY_ROWS = {True: 4, False: 10}      # compact / expanded


@dataclass
class ViewState:
    pinned: object = None                # Pet pinned by the user, shown first
    recent: list = field(default_factory=list)
    zone_name: str = ""                  # zone the player is in
    nearby: list = field(default_factory=list)   # [(pet, [monster names])], closest to max first
    zone_maxed: int = 0                  # pets of this zone already at max level
    stats: list = field(default_factory=list)    # [(label, count, color)]
    total: int = 0
    summary: str = ""
    status: str = ""
    status_color: QColor = field(default_factory=lambda: QColor(theme.MUTED))
    session_line: str = ""
    flash_ids: set = field(default_factory=set)


class StatsBar(QWidget):
    """Stacked bar: one segment per level, proportional to the number of pets."""

    def __init__(self, stats, total, height):
        super().__init__()
        self.stats, self.total = stats, max(1, total)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(theme.BAR_BG)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setClipRect(r)
        x = r.left()
        for _, count, color in self.stats:
            w = r.width() * count / self.total
            if w > 0:
                c = QColor(color)
                if color == theme.MUTED:
                    c.setAlpha(70)
                p.setBrush(c)
                p.drawRect(QRectF(x, r.top(), w, r.height()))
                x += w


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
        nearby_ids = {p.pet_id for p, _ in state.nearby}
        if state.pinned and state.pinned.pet_id not in nearby_ids:
            self._section("Fijado")
            self._row(state.pinned)
        if state.nearby:
            self._section(f"Cerca de ti · {state.zone_name}")
            limit = NEARBY_ROWS[compact]
            for pet, mobs in state.nearby[:limit]:
                self._row(pet, mobs)
            if len(state.nearby) > limit:
                self._note(f"y {len(state.nearby) - limit} más en esta zona (expande o abre el mapa)")
        else:
            self._stats(state)
        if not compact and state.recent:
            self._section("Esta sesión")
            for pet in state.recent:
                self._row(pet)
        for pid in state.flash_ids:
            if pid in self._rows:
                self._rows[pid].flash()
        link = label("Pets exclusivos de cada lado", self.fonts.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.setToolTip("Los pets que solo se farmean en el lado Asmodian o en el Elyos, con tu progreso")
        link.on_click = self.ctl.open_exclusive_pets      # the shell calls it on a plain click
        link.setContentsMargins(self.fonts.px(4), self.fonts.px(4), 0, 0)
        self.content.addWidget(link)
        self.adjustSize()

    @staticmethod
    def footer(state, compact):
        if compact and state.session_line:
            return state.session_line
        return "\n".join(t for t in (state.session_line, state.status) if t)

    # --- pieces -------------------------------------------------------------------------------
    def _section(self, title):
        self.content.addWidget(SectionTitle(title, self.fonts))

    def _note(self, text, color=None):
        note = label(text, self.fonts.small, color or theme.MUTED)
        note.setWordWrap(True)
        note.setContentsMargins(self.fonts.px(4), self.fonts.px(2), 0, self.fonts.px(2))
        self.content.addWidget(note)

    def _row(self, pet, local_mobs=None):
        info, name = self.ctl.pet_info(pet.pet_id)
        if local_mobs:
            info = {**info, "mob": "Mata: " + ", ".join(local_mobs)}
        row = PetRow(pet, info, name, self.fonts, self.ctl.icons_dir,
                     self.ctl.session.per_pet.get(pet.pet_id, 0), side=self.ctl.pet_exclusive(pet.pet_id))
        row.on_click = lambda pid=pet.pet_id: self.ctl.open_map(pid)   # shell calls this on a plain click
        row.setContextMenuPolicy(Qt.CustomContextMenu)
        row.customContextMenuRequested.connect(lambda pos, w=row, pid=pet.pet_id: self._pet_menu(w, pid, pos))
        self._rows.setdefault(pet.pet_id, row)
        self.content.addWidget(row)

    def _stats(self, state):
        f = self.fonts
        owned = sum(count for lbl, count, _ in state.stats[:3])
        pct = round(100 * owned / state.total) if state.total else 0
        self._section("Tu colección")
        headline = label(f"{owned} de {state.total} pets desbloqueados ({pct}%)", f.name, theme.TEXT)
        headline.setContentsMargins(f.px(4), 0, 0, f.px(3))
        self.content.addWidget(headline)
        bar = StatsBar(state.stats, state.total, f.px(8))
        bar.setContentsMargins(f.px(4), 0, f.px(4), 0)
        self.content.addWidget(bar)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(f.px(4), f.px(5), f.px(4), f.px(2))
        grid.setHorizontalSpacing(f.px(8))
        grid.setVerticalSpacing(f.px(2))
        for i, (text, count, color) in enumerate(state.stats):
            share = 100 * count / state.total if state.total else 0
            grid.addWidget(label("●", f.small, color), i, 0)
            grid.addWidget(label(text, f.body, theme.TEXT), i, 1)
            grid.addWidget(label(str(count), f.name, theme.TEXT), i, 2, Qt.AlignRight)
            grid.addWidget(label(f"{share:.0f}%", f.body, theme.MUTED), i, 3, Qt.AlignRight)
        grid.setColumnStretch(1, 1)
        self.content.addWidget(grid_host)
        if state.zone_name and state.zone_maxed:
            self._note(f"En {state.zone_name} ya tienes al máximo todos los pets ({state.zone_maxed}).",
                       theme.GAIN)
        elif not state.zone_name:
            self._note("Cuando AionDex detecte la zona en la que estás, aquí verás los pets que puedes farmear.")

    def _pet_menu(self, widget, pet_id, pos):
        menu = QMenu(self)
        menu.addAction("Ver en el mapa", lambda: self.ctl.open_map(pet_id))
        if self.ctl.settings["pinned"] == pet_id:
            menu.addAction("Quitar fijado", lambda: self.ctl.pin(None))
        else:
            menu.addAction("Fijar arriba", lambda: self.ctl.pin(pet_id))
        menu.exec(widget.mapToGlobal(pos))


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()            # gone at once, not when the deferred delete runs
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
