"""Diagnóstico module: in the overlay, the problems AionDex sees right now; the full tool lives in its own window."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import Module, ModuleView
from .. import diagnostics
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import SectionTitle, label

GLYPH_HEALTH = ""       # medical / health check
LEVEL_COLOR = {"warn": theme.WARN, "bad": theme.COMBAT}


def open_problems(ctl):
    return [c for c in diagnostics.status(ctl) if c.level in ("warn", "bad")]


class DiagnosticsPanel(QWidget):
    def __init__(self, ctl, fonts):
        super().__init__()
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(2))

    def render(self, compact, problems):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().hide()            # gone at once, not when the deferred delete runs: no blink
                item.widget().deleteLater()
        f = self.fonts
        if problems:
            self.content.addWidget(SectionTitle("Problemas detectados", f))
            for check in problems[:2 if compact else 5]:
                self._line(f"● {check.title}: {check.detail}", LEVEL_COLOR[check.level])
        else:
            self._line("Todo funciona.", theme.GAIN)
            self._line("Si algo no va bien, abre el diagnóstico y elige el problema: AionDex busca la causa y lo "
                       "arregla si puede.", theme.MUTED)
        link = label("Abrir diagnóstico", f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.ctl.open_diagnostics
        link.setContentsMargins(f.px(4), f.px(4), 0, 0)
        self.content.addWidget(link)
        self.adjustSize()

    def _line(self, text, color):
        lbl = label(text, self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(4), 0, 0, 0)
        self.content.addWidget(lbl)


class DiagnosticsModule(Module):
    key = "diagnostics"
    title = "Diagnóstico"

    def icon(self):
        return glyph_icon(GLYPH_HEALTH, theme.ACCENT, self.ctl.fonts.icons_family)

    def menu_label(self):
        n = len(open_problems(self.ctl))
        return f"{self.title} ({n})" if n else self.title

    def create_panel(self, fonts):
        self.panel = DiagnosticsPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        problems = open_problems(self.ctl)
        self.panel.render(compact, problems)
        return ModuleView(summary=f"{len(problems)} aviso{'s' if len(problems) != 1 else ''}" if problems else "")
