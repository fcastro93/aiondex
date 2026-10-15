"""Eventos module: one small card per event running now, with what matters at a glance: its name and the time left,
and for an event with missions your week (missions collected and the coins they gave, as a bar), what is waiting to
be collected and the next mission left. Everything else is in the window (ui/eventswindow.py)."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from . import Module, ModuleView
from ..events import at
from ..status import countdown
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import ProgressBar, label

GLYPH_EVENTS = ""         # calendar (Segoe MDL2 / Fluent)
CARD_STYLE = ("QFrame#EventCard { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.09); "
              "border-radius: %dpx; }")


def week_of(ctl, event, now):
    """Your week in an event's missions: (who, collected, missions, coins got, coins in all, ready, next one left)
    or None when the event has no missions AionDex reads."""
    ms = ctl.missions
    who = ctl.missions_character(ms)
    rows = [r for b in event.get("blocks") or [] if b.get("type") == "missions" for r in b.get("rows", [])
            if len(r) > 4 and isinstance(r[4], dict) and r[4].get("id") in ms.db]
    if not rows or not who:
        return None
    done = got = ready = 0
    left = None
    for name, what, reward, amount, extra in (r[:5] for r in rows):
        p = ms.progress(who, extra["id"], now)
        if p.done:
            done += 1
            got += amount
        elif p.ready:
            ready += 1
        elif left is None or p.ratio > left[1].ratio:
            left = (name.replace("[Event] ", ""), p)
    return who, done, len(rows), got, sum(r[3] for r in rows), ready, left


class EventsPanel(QWidget):
    def __init__(self, ctl, fonts):
        super().__init__()
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(4))

    def render(self, compact):
        while self.content.count():
            widget = self.content.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        f, now = self.fonts, time.time()
        running = self.ctl.events.running(now)
        if not running:
            self.content.addWidget(label("Ningún evento en marcha.", f.small, theme.MUTED))
        for e in running if not compact else running[:3]:
            self.content.addWidget(self._card(e, now))
        link = label("Ver detalles", f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.ctl.open_events
        link.setContentsMargins(f.px(4), f.px(2), 0, 0)
        self.content.addWidget(link)
        self.adjustSize()

    def _card(self, e, now):
        f = self.fonts
        card = QFrame()
        card.setObjectName("EventCard")
        card.setStyleSheet(CARD_STYLE % f.px(8))
        card.setCursor(Qt.PointingHandCursor)
        card.on_click = self.ctl.open_events
        col = QVBoxLayout(card)
        col.setContentsMargins(f.px(8), f.px(5), f.px(8), f.px(6))
        col.setSpacing(f.px(3))
        head = QHBoxLayout()
        head.setSpacing(f.px(6))
        head.addWidget(label(e.get("title_es") or e["title"], f.name, theme.TEXT, elide=True), 1)
        end = at(e["end"])
        head.addWidget(label(countdown(end - now), f.small, theme.WARN if end - now < 2 * 86400 else theme.MUTED))
        col.addLayout(head)
        week = week_of(self.ctl, e, now)
        if week:
            who, done, total, got, coins, ready, left = week
            col.addWidget(ProgressBar(done / total if total else 0, theme.GAIN if done == total else theme.ACCENT,
                                      f.px(5)))
            line = QHBoxLayout()
            line.setSpacing(f.px(6))
            line.addWidget(label(f"{done}/{total} misiones", f.small, theme.TEXT))
            if ready:
                line.addWidget(label(f"· {ready} por cobrar", f.small, theme.GAIN))
            line.addStretch(1)
            line.addWidget(label(f"{got}/{coins} monedas", f.small, theme.GOLD))
            col.addLayout(line)
            if left and not ready:
                name, p = left
                col.addWidget(label(f"Siguiente: {name} {p.count}/{p.target}", f.small, theme.MUTED, elide=True))
        return card


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
        ready = sum((week_of(self.ctl, e, time.time()) or (0,) * 7)[5] for e in self.ctl.events.running())
        return ModuleView(summary=f"{ready} por cobrar" if ready else "")
