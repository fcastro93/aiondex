"""Eventos module: the events running now in short (when each ends, what pays most, when the weekly missions start
over) with a link to the whole of each one (ui/eventswindow.py)."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import Module, ModuleView
from ..events import at, weeks_left
from ..status import countdown, next_weekly
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import SectionTitle, label

GLYPH_EVENTS = ""         # calendar (Segoe MDL2 / Fluent)
LINES = {True: 2, False: 4}     # headline lines per event (compact / expanded)


class EventsPanel(QWidget):
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
        f, now = self.fonts, time.time()
        running = self.ctl.events.running(now)
        if not running:
            self._line("No hay eventos en marcha (o aún no se leyó la lista oficial).", theme.MUTED)
        for e in running if not compact else running[:3]:
            self.content.addWidget(SectionTitle(e.get("title_es") or e["title"], f))
            end = at(e["end"])
            self._line(f"Termina en {countdown(end - now)} ({time.strftime('%d/%m %H:%M', time.localtime(end))})",
                       theme.WARN if end - now < 2 * 86400 else theme.MUTED)
            for text in (e.get("headline") or [])[:LINES[compact]]:
                self._line("• " + text, theme.TEXT)
            ids = [row[4]["id"] for b in e.get("blocks") or [] if b.get("type") == "missions"
                   for row in b.get("rows", []) if len(row) > 4 and isinstance(row[4], dict) and row[4].get("id")]
            ms = self.ctl.missions
            who = self.ctl.missions_character(ms)
            if ids and who:
                progress = [ms.progress(who, mid, now) for mid in ids if mid in ms.db]
                done, ready = sum(p.done for p in progress), sum(p.ready for p in progress)
                self._line(f"{who}: {done}/{len(progress)} misiones del evento cobradas"
                           + (f" · {ready} por cobrar" if ready else ""), theme.GAIN if ready else theme.TEXT)
            if any(b.get("type") == "missions" for b in e.get("blocks") or []):
                left = weeks_left(e, now)
                self._line(f"Misiones semanales: se renuevan en {countdown(next_weekly(now) - now)} · "
                           f"{left} {'semana' if left == 1 else 'semanas'} de misiones hasta el final", theme.MUTED)
            if not e.get("curated"):
                self._line("Sin detalles en AionDex todavía: el aviso oficial está en la ventana.", theme.MUTED)
        link = label("Ver misiones, recompensas y tienda", f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.ctl.open_events
        link.setContentsMargins(f.px(4), f.px(4), 0, 0)
        self.content.addWidget(link)
        self.adjustSize()

    def _line(self, text, color, indent=4):
        lbl = label(text, self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(indent), 0, 0, 0)
        self.content.addWidget(lbl)


class EventsModule(Module):
    key = "events"
    title = "Eventos"

    def icon(self):
        return glyph_icon(GLYPH_EVENTS, theme.GOLD, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = EventsPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.ctl.events.refresh_live()
        self.panel.render(compact)
        n = len(self.ctl.events.running())
        return ModuleView(summary=f"{n} en marcha" if n else "")
