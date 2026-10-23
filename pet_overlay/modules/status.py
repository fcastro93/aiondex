"""Status module: in the overlay, each character's energy and Nightmare, and your account's Shugo keys and daily
dungeon (shared by your characters on a server), with a red / green light; the full review and the alert bells live in
their own window."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from . import Module, ModuleView
from ..status import KIND, OVERLAY_KINDS, SHORT, amount, countdown, next_daily, next_weekly
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import LED_COLORS, Led, SectionTitle, label
from ..i18n import tr

GLYPH_STATUS = ""        # checklist
ROWS = {True: 1, False: 4}


def ago(ts, now):
    minutes = (now - ts) / 60
    if minutes < 2:
        return tr("hace un momento")
    if minutes < 90:
        return tr("hace {0:.0f} min", minutes)
    if minutes < 48 * 60:
        return tr("hace {0:.0f} h", minutes / 60)
    return tr("hace {0:.0f} días", minutes / 1440)


class StatusPanel(QWidget):
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
        f, st = self.fonts, self.ctl.status
        now = time.time()
        playing = self.ctl.character_name()
        names = st.names(now, playing)
        if not names:
            self._line(tr("Entra con cada personaje con AionDex abierto: aquí verás su energía, Nightmare, llaves "
                          "del Shugo y la mazmorra diaria, con una luz roja cuando algo esté por llenarse."), theme.MUTED)
        else:
            self.content.addWidget(SectionTitle(tr("Tus personajes"), f))
            own = [k for k in OVERLAY_KINDS if not KIND[k].account]
            shared = [k for k in OVERLAY_KINDS if KIND[k].account]
            for name in names[:ROWS[compact]]:
                self._who(name, tr("en juego") if name == playing else ago(st.read_at(name), now), own, now)
            for account, _ in st.accounts(now, playing):
                self._who(account, ago(st.read_at(account), now), shared, now)
        self._line(tr("Reinicio diario en {0} · semanal (miércoles) en {1}", countdown(next_daily(now) - now), countdown(next_weekly(now) - now)), theme.MUTED)
        link = label(tr("Abrir Status"), f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.ctl.open_status
        link.setContentsMargins(f.px(4), f.px(4), 0, 0)
        self.content.addWidget(link)
        self.adjustSize()

    def _who(self, name, when, keys, now):
        """A character (their own counters) or your account (the shared ones): light, name and values."""
        f, st = self.fonts, self.ctl.status
        values = [(key, st.value(name, key, now)) for key in keys]
        values = [(key, v) for key, v in values if v.known]
        if not values:
            return
        red = any(st.lit(name, key, now) == "red" for key, _ in values)       # the light of what it shows
        head = QWidget()
        lay = QHBoxLayout(head)
        lay.setContentsMargins(f.px(4), 0, f.px(2), 0)
        lay.setSpacing(f.px(6))
        lay.addWidget(Led("red" if red else "green", f.px(12)))
        lay.addWidget(label(name, f.name, theme.TEXT, elide=True), 1)
        lay.addWidget(label(when, f.small, theme.MUTED))
        self.content.addWidget(head)
        parts = []
        for key, v in values:
            color = LED_COLORS["red"] if st.lit(name, key, now) == "red" else theme.TEXT
            parts.append(f"<span style='color:{theme.MUTED.name()}'>{SHORT[key]}</span> "
                         f"<span style='color:{color.name()}'>{amount(v)}</span>")
        self._line(" · ".join(parts), theme.TEXT, indent=22)

    def _line(self, text, color, indent=4):
        lbl = label(text, self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(indent), 0, 0, 0)
        self.content.addWidget(lbl)


class StatusModule(Module):
    key = "status"
    title = "Status"

    def icon(self):
        return glyph_icon(GLYPH_STATUS, theme.ACCENT, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = StatusPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.panel.render(compact)
        st, now = self.ctl.status, time.time()
        own = [k for k in OVERLAY_KINDS if not KIND[k].account]
        shared = [k for k in OVERLAY_KINDS if KIND[k].account]
        red = sum(1 for n in st.names(now) if any(st.lit(n, k, now) == "red" for k in own)) + \
            sum(1 for a, _ in st.accounts(now) if any(st.lit(a, k, now) == "red" for k in shared))
        return ModuleView(summary=tr("{0} en rojo", red) if red else "")
