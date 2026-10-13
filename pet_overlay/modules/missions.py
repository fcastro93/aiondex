"""Season M. and Logros modules: in the overlay, the season missions / achievements you can push where you are (in the
Abyss its own, in an instance what was played there, in Altgard or Verteron theirs), with your progress and the
rewards waiting in the game's window. With nothing to push there, the easy ones to push from anywhere (or in an
instance you enter on purpose, like Nightmare), weekly ones first. All of them, by tab, with the totals, live in their
own window."""
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
GLYPH_ACHIEVEMENTS = ""    # flag
ROWS = {True: 3, False: 8}
TIPS = {True: 2, False: 4}       # recommendations shown when nothing moves where you are
PERIOD = {"weekly": "semanal", "season": "temporada", "permanent": ""}


class ProgressPanel(QWidget):
    def __init__(self, ctl, fonts, model, title, open_window):
        super().__init__()
        self.ctl = ctl
        self.fonts = fonts
        self.model = model
        self.title = title
        self.open_window = open_window
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(2))

    def render(self, compact):
        while self.content.count():
            widget = self.content.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        f, ms, now = self.fonts, self.model, time.time()
        name = self.ctl.missions_character(ms)
        faction = self.ctl.crafting_faction()
        if not ms.available:
            self._line("Falta su lista (data/*_db.json).", theme.WARN)
        elif name is None:
            self._line("Entra con tu personaje con AionDex abierto: aquí verás tu progreso y lo que puedes avanzar "
                       "donde estés.", theme.MUTED)
        else:
            place, tags = self.ctl.missions_place(ms)
            here = ms.for_place(name, faction, tags, now) if tags else []
            self.content.addWidget(SectionTitle(f"{self.title} · {place}", f))
            expiring = ms.expiring(name, now)
            if expiring:
                ready = sum(1 for _, p, _ in expiring if p.ready)
                left = min(d for _, _, d in expiring) - now
                self._line(f"Expiran en {countdown(left)}: {len(expiring)} sin terminar"
                           + (f" ({ready} con recompensa por recoger)" if ready else ""), theme.WARN)
            for m, p in here[:ROWS[compact]]:
                self.content.addWidget(self._row(m, p, faction))
            if len(here) > ROWS[compact]:
                self._line(f"y {len(here) - ROWS[compact]} más aquí (expande o abre la ventana)", theme.MUTED)
            if not here:
                tips = ms.recommend(name, faction, now)[:TIPS[compact]]
                self._line("Aquí no avanza nada que te falte. Fáciles desde cualquier lado:" if tags else
                           "Fáciles desde cualquier lado (o en una instancia a la que entras tú):", theme.MUTED)
                for m, p in tips:
                    self.content.addWidget(self._row(m, p, faction))
            ready = ms.ready(name, now)
            if ready:
                self._line(f"Recompensas para recoger en el juego: {sum(p.ready for _, p in ready)} "
                           f"({', '.join(m.view(faction)[0] for m, _ in ready[:2])}{'…' if len(ready) > 2 else ''})",
                           theme.GOLD)
            if any(m.period == "weekly" for m in ms.db.values()):
                done, total = ms.week_summary(name, now)
                self._line(f"Semanales: {done}/{total} hechas · reinicio en {countdown(next_weekly(now) - now)}",
                           theme.MUTED)
            else:
                _, totals = ms.stats(name, now)
                self._line(f"Completos {totals.done}/{totals.count} ({totals.pct(totals.done):.0f}%) · pasos "
                           f"{totals.steps_pct:.0f}%", theme.MUTED)
        link = label("Ver todo y el resumen", f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.open_window
        link.setContentsMargins(f.px(4), f.px(4), 0, 0)
        self.content.addWidget(link)
        self.adjustSize()

    def _row(self, m, p, faction):
        f = self.fonts
        mission_name, text, _ = m.view(faction)
        tab = self.model.tabs.get(m.tab, m.tab)
        row = QWidget()
        row.setToolTip(f"{text}\n{tab}" + (f" · {PERIOD[m.period]}" if PERIOD.get(m.period) else "") +
                       f" · paso {p.step}/{p.total}" + (f"\nRecompensas (todos los pasos): {m.rewards}"
                                                        if m.rewards else ""))
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
        bits = [text, f"paso {p.step}/{p.total}", PERIOD.get(m.period) or tab]
        lay.addWidget(label(" · ".join(b for b in bits if b), f.small, theme.MUTED, elide=True))
        return row

    def _line(self, text, color, indent=4):
        lbl = label(text, self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(indent), 0, 0, 0)
        self.content.addWidget(lbl)


class MissionsModule(Module):
    key = "missions"
    title = "Season M."
    glyph, color = GLYPH_MISSIONS, "GOLD"

    def model(self):
        return self.ctl.missions

    def open_window(self):
        self.ctl.open_missions()

    def icon(self):
        return glyph_icon(self.glyph, getattr(theme, self.color), self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = ProgressPanel(self.ctl, fonts, self.model(), self.title, self.open_window)
        return self.panel

    def render(self, compact):
        self.panel.render(compact)
        name = self.ctl.missions_character(self.model())
        ready = sum(p.ready for _, p in self.model().ready(name)) if name else 0
        return ModuleView(summary=f"{ready} para recoger" if ready else "")


class AchievementsModule(MissionsModule):
    key = "achievements"
    title = "Logros"
    glyph, color = GLYPH_ACHIEVEMENTS, "ACCENT"

    def model(self):
        return self.ctl.achievements

    def open_window(self):
        self.ctl.open_achievements()
