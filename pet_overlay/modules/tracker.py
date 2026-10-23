"""Tracker module: in the overlay, how long until the bosses and events you marked; the full list is a window."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from . import Module, ModuleView
from ..tracker import countdown
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import SectionTitle, label
from ..i18n import tr

GLYPH_ALARM = ""        # calendar / timer
ROWS = {True: 3, False: 8}


def when_text(st, now):
    if st.state in ("vivo", "en curso"):
        return tr("ahora"), theme.GAIN
    if st.state == "quizá":
        return tr("¿ahora?"), theme.WARN
    if st.state == "debería estar":
        return tr("¿ya?"), theme.WARN
    if st.start is None:
        return "—", theme.MUTED
    return tr("en {0}", countdown(st.start - now)), theme.TEXT


class TrackerPanel(QWidget):
    def __init__(self, ctl, fonts):
        super().__init__()
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(2))

    def render(self, compact, rows):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().hide()            # gone at once, not when the deferred delete runs: no blink
                item.widget().deleteLater()
        f = self.fonts
        now = time.time()
        if not rows:
            note = label(tr("Marca con la campana los jefes y eventos que te interesan: aquí verás cuánto falta y "
                            "te avisará antes de que salgan."), f.small, theme.MUTED)
            note.setWordWrap(True)
            note.setContentsMargins(f.px(4), 0, 0, 0)
            self.content.addWidget(note)
        else:
            self.content.addWidget(SectionTitle(tr("Próximos"), f))
            for entry, st in rows[:ROWS[compact]]:
                line = QWidget()
                lay = QHBoxLayout(line)
                lay.setContentsMargins(f.px(4), 0, f.px(2), 0)
                lay.addWidget(label(entry.name, f.name if st.state in ("vivo", "en curso") else f.body,
                                    theme.GOLD if st.state in ("vivo", "en curso") else theme.TEXT, elide=True), 1)
                text, color = when_text(st, now)
                lay.addWidget(label(text, f.name, color))
                self.content.addWidget(line)
        link = label(tr("Abrir el tracker"), f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.ctl.open_tracker
        link.setContentsMargins(f.px(4), f.px(4), 0, 0)
        self.content.addWidget(link)
        self.adjustSize()


class TrackerModule(Module):
    key = "tracker"
    title = "Tracker"

    def icon(self):
        return glyph_icon(GLYPH_ALARM, theme.ACCENT, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = TrackerPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        rows = self.ctl.tracker.upcoming()
        self.panel.render(compact, rows)
        summary = ""
        if rows:
            entry, st = rows[0]
            text, _ = when_text(st, time.time())
            summary = f"{entry.name.split()[-1]} {text}"
        return ModuleView(summary=summary)
