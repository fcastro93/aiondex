"""Dashboard: the module to keep open while playing. One small card per thing that needs you, each hidden when there
is nothing to show:

- the local clock and the server's (the Tracker's region clock);
- the next activity you marked in the Tracker and how long until it;
- how many pets you can push to max level where you are;
- an event mission, a weekly season mission and an achievement: one you can push where you are, else the ones left,
  one after the other every few seconds;
- an alert when Status needs you (a click opens Status).

"Editar" lists every card with a box to tick: all on by default, the ones you untick never show."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from . import Module, ModuleView
from ..status import ALERTS
from ..tracker import REGIONS, utc_offset
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.dps_panel import Pill
from ..ui.widgets import label
from .tracker import when_text

GLYPH_HOME = ""
ROTATE = 6                  # seconds each mission / achievement left stays before the next one
ROTATING = 12               # at most this many are rotated (achievements: the closest to done)
CARDS = (("clock", "Reloj local y del servidor", ""),
         ("activity", "Siguiente actividad del Tracker", ""),
         ("pets", "Pets por subir cerca de ti", ""),
         ("event", "Misión del evento", ""),
         ("season", "Misión semanal de temporada", ""),
         ("achievement", "Logro", ""),
         ("status", "Aviso de Status", ""))
CARD_STYLE = ("QFrame#DashCard { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.09); "
              "border-radius: %dpx; }")


def _short_region(region):
    return REGIONS.get(region, REGIONS["global-nae"])[0].split(" (")[0]


class DashboardPanel(QWidget):
    def __init__(self, ctl, fonts):
        super().__init__()
        self.ctl = ctl
        self.fonts = fonts
        self.editing = False
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(4))
        self._clock_labels = None           # (local label, server label) updated every second
        self._clock_timer = QTimer(self, interval=1000, timeout=self._tick)
        self._clock_timer.start()

    # --- settings ---
    @property
    def hidden(self):
        return set(self.ctl.settings.get("dashboard", {}).get("hidden", []))

    def _toggle(self, key):
        conf = self.ctl.settings.setdefault("dashboard", {})
        hidden = set(conf.get("hidden", []))
        hidden ^= {key}
        conf["hidden"] = sorted(hidden)
        self.ctl.settings.save()
        self.ctl.refresh()

    def _edit(self, on):
        self.editing = on
        self.ctl.refresh()

    # --- drawing ---
    def render(self, compact):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._clock_labels = None
        f = self.fonts
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(f.px(2), 0, 0, 0)
        hl.addWidget(label("EDITAR DASHBOARD" if self.editing else "DASHBOARD", f.section, theme.MUTED), 1)
        hl.addWidget(Pill("Listo" if self.editing else "Editar", f, lambda: self._edit(not self.editing),
                          selected=self.editing, tip="Elegir qué recuadros salen en el Dashboard"))
        self.content.addWidget(head)
        if self.editing:
            hidden = self.hidden
            for key, title, glyph in CARDS:
                on = key not in hidden
                card = self._card(glyph, theme.ACCENT if on else theme.MUTED, "", title,
                                  "☑" if on else "☐", theme.GAIN if on else theme.MUTED)
                card.on_click = lambda k=key: self._toggle(k)
                card.setToolTip("Clic para mostrarlo u ocultarlo")
                self.content.addWidget(card)
            note = label("Los marcados salen en el Dashboard cuando tienen algo que mostrar.", f.small, theme.MUTED)
            note.setWordWrap(True)
            self.content.addWidget(note)
            self.adjustSize()
            return
        shown = 0
        for key, _, glyph in CARDS:
            if key in self.hidden:
                continue
            card = getattr(self, f"_{key}")(glyph)
            if card is not None:
                self.content.addWidget(card)
                shown += 1
        if not shown:
            self.content.addWidget(label("Nada pendiente ahora mismo.", f.small, theme.MUTED))
        self.adjustSize()

    def _card(self, glyph, glyph_color, title, text, value="", value_color=None, sub=""):
        f = self.fonts
        card = QFrame()
        card.setObjectName("DashCard")
        card.setStyleSheet(CARD_STYLE % f.px(8))
        lay = QHBoxLayout(card)
        lay.setContentsMargins(f.px(8), f.px(5), f.px(8), f.px(5))
        lay.setSpacing(f.px(8))
        icon = label(glyph, f.glyph, glyph_color)
        icon.setFixedWidth(f.px(16))
        lay.addWidget(icon, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(0)
        if title:
            col.addWidget(label(title, f.section, theme.MUTED, elide=True))
        col.addWidget(label(text, f.body, theme.TEXT, elide=True))
        if sub:
            col.addWidget(label(sub, f.small, theme.MUTED, elide=True))
        lay.addLayout(col, 1)
        if value:
            lay.addWidget(label(value, f.name, value_color or theme.TEXT), 0, Qt.AlignVCenter)
        card.setCursor(Qt.PointingHandCursor)
        return card

    # --- the cards (None: nothing to show) ---
    def _clock(self, glyph):
        """The clocks: the local one big, the server's (the Tracker's region) under it; both tick every second."""
        f = self.fonts
        card = QFrame()
        card.setObjectName("DashCard")
        card.setStyleSheet(CARD_STYLE % f.px(8))
        lay = QHBoxLayout(card)
        lay.setContentsMargins(f.px(8), f.px(4), f.px(8), f.px(4))
        lay.setSpacing(f.px(8))
        icon = label(glyph, f.glyph, theme.ACCENT)
        icon.setFixedWidth(f.px(16))
        lay.addWidget(icon, 0, Qt.AlignVCenter)
        local = label("", f.count_big, theme.TEXT)
        lay.addWidget(local, 0, Qt.AlignVCenter)
        server = label("", f.small, theme.MUTED)
        lay.addWidget(server, 1, Qt.AlignRight | Qt.AlignVCenter)
        self._clock_labels = (local, server)
        self._tick()
        return card

    def _tick(self):
        if self._clock_labels is None or not self.isVisible():
            return
        local, server = self._clock_labels
        now = time.time()
        local.setText(time.strftime("%H:%M:%S", time.localtime(now)))
        region = self.ctl.tracker.region
        server.setText("Servidor " + time.strftime("%H:%M:%S", time.gmtime(now + utc_offset(region, now) * 3600))
                       + f" · {_short_region(region)}")

    def _rotating(self, items):
        """The item to show now from a list that rotates every ROTATE seconds: (item, "i/n")."""
        i = int(time.time() // ROTATE) % len(items)
        return items[i], f"{i + 1}/{len(items)}"

    def _mission(self, glyph, model, title, pick, open_module):
        ms = self.ctl.missions if model == "missions" else self.ctl.achievements
        name = self.ctl.missions_character(ms)
        if not ms.available or not name:
            return None
        faction = self.ctl.crafting_faction()
        now = time.time()
        pool = [m for m in ms.db.values() if pick(m)]
        if not pool:
            return None
        progress = {m.id: ms.progress(name, m.id, now) for m in pool}
        ready = [m for m in pool if progress[m.id].ready]
        place, tags = self.ctl.missions_place(ms)
        here = [m for m, _ in ms.for_place(name, faction, tags, now) if pick(m)] if tags else []
        if ready:
            m, where, color = ready[0], "¡recompensa por cobrar!" + (f" (+{len(ready) - 1})" if len(ready) > 1 else ""), \
                theme.GAIN
        elif here:
            m, where, color = here[0], f"{place.lower()}", theme.ACCENT
        else:
            left = [m for m in pool if not progress[m.id].done]
            if not left:
                return None
            if model != "missions":           # achievements: the closest to done (or the easy ones)
                started = sorted((m for m in left if progress[m.id].count), key=lambda m: -progress[m.id].ratio)
                left = started[:ROTATING] or [m for m, _ in ms.recommend(name, faction, now)[:ROTATING]] \
                    or left[:ROTATING]
            m, position = self._rotating(left[:ROTATING] if model != "missions" else left)
            where, color = f"por hacer {position}", theme.MUTED
        p = progress[m.id]
        shown_name, _, _ = m.view(faction)
        value = "✓" if p.done else f"{p.count:,}/{p.target:,}".replace(",", ".")
        card = self._card(glyph, theme.GOLD if p.ready else theme.ACCENT, f"{title} · {where}".upper(), shown_name,
                          value, theme.GAIN if p.ready else theme.TEXT, sub=m.view(faction)[1])
        card.on_click = lambda: self.ctl.switch_module(open_module)
        return card

    def _event(self, glyph):
        return self._mission(glyph, "missions", "Evento", lambda m: m.tab == "event", "events")

    def _season(self, glyph):
        return self._mission(glyph, "missions", "Season semanal",
                             lambda m: m.tab != "event" and m.period == "weekly", "missions")

    def _achievement(self, glyph):
        return self._mission(glyph, "achievements", "Logro", lambda m: True, "achievements")

    def _activity(self, glyph):
        rows = self.ctl.tracker.upcoming()
        if not rows:
            return None
        entry, st = rows[0]
        text, color = when_text(st, time.time())
        after = ""
        if len(rows) > 1:
            nxt_entry, nxt = rows[1]
            nxt_text, _ = when_text(nxt, time.time())
            after = f"después: {nxt_entry.name} {nxt_text}"
        card = self._card(glyph, theme.ACCENT, "SIGUIENTE ACTIVIDAD", entry.name, text, color, sub=after)
        card.on_click = lambda: self.ctl.switch_module("tracker")
        return card

    def _pets(self, glyph):
        zone, nearby, _ = self.ctl.nearby_pets()
        left = [p for p, _ in nearby if not p.is_max]
        if not zone or not left:
            return None
        card = self._card(glyph, theme.GOLD, f"PETS CERCA · {zone.get('name', '')}".upper(),
                          f"{len(left)} {'pet' if len(left) == 1 else 'pets'} por subir a nivel máximo aquí",
                          str(len(left)), theme.GOLD)
        card.on_click = lambda: self.ctl.switch_module("pets")
        return card

    def _status(self, glyph):
        st, now = self.ctl.status, time.time()
        alerts = [(key, st.targets(key, now)) for key in ALERTS]
        alerts = [(key, who) for key, who in alerts if who]
        if not alerts:
            return None
        key, who = alerts[0]
        more = sum(len(w) for _, w in alerts)
        card = self._card(glyph, theme.COMBAT, "STATUS REQUIERE ATENCIÓN", ALERTS[key][0],
                          f"{more} {'aviso' if more == 1 else 'avisos'}", theme.COMBAT,
                          sub=", ".join(who[:3]) + (" y más" if len(who) > 3 else ""))
        card.setToolTip("Clic para abrir Status y ver qué es")
        card.on_click = lambda: self.ctl.switch_module("status")
        return card


class DashboardModule(Module):
    key = "dashboard"
    title = "Dashboard"

    def icon(self):
        return glyph_icon(GLYPH_HOME, theme.GOLD, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = DashboardPanel(self.ctl, fonts)
        self._timer = QTimer(self.panel, interval=ROTATE * 1000, timeout=self._again)
        self._timer.start()
        return self.panel

    def _again(self):
        """Every few seconds: the next mission of the ones rotating, and the countdowns."""
        if self.ctl.active_module is self and self.panel.isVisible() and not self.panel.editing:
            self.ctl.refresh()

    def render(self, compact):
        self.panel.render(compact)
        rows = self.ctl.tracker.upcoming() if "activity" not in self.panel.hidden else []
        summary = ""
        if rows:
            entry, st = rows[0]
            text, _ = when_text(st, time.time())
            summary = f"{entry.name.split()[-1]} {text}"
        return ModuleView(summary=summary)
