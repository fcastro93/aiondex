"""Missions module: in the overlay, the season missions you can push where you are (in the Abyss its missions, in an
instance what was played there, in Altgard or Verteron their PvP ones), with your progress and the rewards waiting in
the game's Season Mission window; all of them, by tab, live in their own window."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from . import Module, ModuleView
from ..missions import amount
from ..status import countdown, next_weekly
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import ProgressBar, SectionTitle, label

GLYPH_MISSIONS = ""        # star (Segoe MDL2 / Fluent)
ROWS = {True: 3, False: 8}
PERIOD = {"weekly": "semanal", "season": "temporada"}


class MissionsPanel(QWidget):
    def __init__(self, ctl, fonts):
        super().__init__()
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(2))

    def render(self, compact):
        while self.content.count():
            widget = self.content.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        f, ms, now = self.fonts, self.ctl.missions, time.time()
        name = self.ctl.missions_character()
        if not ms.available:
            self._line("Falta la lista de misiones (data/missions_db.json).", theme.WARN)
        elif name is None:
            self._line("Entra con tu personaje con AionDex abierto: aquí verás tus misiones de temporada y las que "
                       "puedes avanzar donde estés.", theme.MUTED)
        else:
            title, tags = self.ctl.missions_place()
            shown = ms.for_place(name, self.ctl.crafting_faction(), tags, now)
            self.content.addWidget(SectionTitle(f"Misiones · {title}", f))
            if not shown:
                self._line("Aquí no avanza ninguna misión que te falte." if tags else
                           "Cuando AionDex sepa dónde estás, aquí verás las misiones que avanzan allí.", theme.MUTED)
            for m, p in shown[:ROWS[compact]]:
                self.content.addWidget(self._row(m, p))
            if len(shown) > ROWS[compact]:
                self._line(f"y {len(shown) - ROWS[compact]} más aquí (expande o abre Misiones)", theme.MUTED)
            ready = ms.ready(name, now)
            if ready:
                self._line(f"Recompensas para recoger en el juego: {sum(p.ready for _, p in ready)} "
                           f"({', '.join(m.view(self.ctl.crafting_faction())[0] for m, _ in ready[:2])}"
                           f"{'…' if len(ready) > 2 else ''})", theme.GOLD)
            done, total = ms.week_summary(name, now)
            self._line(f"Semanales: {done}/{total} hechas · reinicio en {countdown(next_weekly(now) - now)}",
                       theme.MUTED)
        link = label("Ver todas las misiones", f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.ctl.open_missions
        link.setContentsMargins(f.px(4), f.px(4), 0, 0)
        self.content.addWidget(link)
        self.adjustSize()

    def _row(self, m, p):
        f = self.fonts
        mission_name, text, _ = m.view(self.ctl.crafting_faction())
        row = QWidget()
        row.setToolTip(f"{text}\n{self.ctl.missions.tabs.get(m.tab, m.tab)} · {PERIOD[m.period]} · paso "
                       f"{p.step}/{p.total}" + (f"\nRecompensas (todos los pasos): {m.rewards}" if m.rewards else ""))
        lay = QVBoxLayout(row)
        lay.setContentsMargins(f.px(4), f.px(1), f.px(2), f.px(1))
        lay.setSpacing(f.px(1))
        top = QHBoxLayout()
        top.setSpacing(f.px(6))
        top.addWidget(label(mission_name, f.name, theme.TEXT, elide=True), 1)
        if p.ready:
            top.addWidget(label("¡recógela!", f.small, theme.GOLD))
        top.addWidget(label(f"{amount(m, p.count)}/{amount(m, p.target)}", f.name,
                            theme.GOLD if p.ready else theme.TEXT))
        lay.addLayout(top)
        lay.addWidget(ProgressBar(p.ratio, theme.GOLD if p.ready else theme.ACCENT, f.px(4)))
        lay.addWidget(label(f"{text} · paso {p.step}/{p.total} · {PERIOD[m.period]}", f.small, theme.MUTED,
                            elide=True))
        return row

    def _line(self, text, color, indent=4):
        lbl = label(text, self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(indent), 0, 0, 0)
        self.content.addWidget(lbl)


class MissionsModule(Module):
    key = "missions"
    title = "Misiones"

    def icon(self):
        return glyph_icon(GLYPH_MISSIONS, theme.GOLD, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = MissionsPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.panel.render(compact)
        name = self.ctl.missions_character()
        ready = sum(p.ready for _, p in self.ctl.missions.ready(name)) if name else 0
        return ModuleView(summary=f"{ready} para recoger" if ready else "")
