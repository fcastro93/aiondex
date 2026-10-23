"""The Afinador's overlay summary (its macro test), shown inside the Builds module (modules/builds.py); the full
editor is the Builds window's Afinador tab."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import Module, ModuleView
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import SectionTitle, label
from ..i18n import tr

GLYPH_TUNE = ""       # equalizer / sliders


class TunerPanel(QWidget):
    def __init__(self, ctl, fonts):
        super().__init__()
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(2))

    def render(self, compact):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().hide()            # gone at once, not when the deferred delete runs: no blink
                item.widget().deleteLater()
        f, tuner = self.fonts, self.ctl.tuner
        if tuner.recording:
            s = tuner.session
            self._line(tr("Grabando prueba… {0} s · {1} lanzamientos", int(s.duration), s.fired), theme.ACCENT)
        report = tuner.last_report
        if report and not tuner.recording:
            bad = [x for x in report.findings if x.level == "bad"]
            warn = [x for x in report.findings if x.level == "warn"]
            self.content.addWidget(SectionTitle(tr("Última prueba"), f))
            self._line(tr("{0} s · {1} lanzamientos · {2} problemas, {3} avisos", int(report.duration), report.total, len(bad), len(warn)), theme.TEXT)
            for finding in (bad + warn)[:2 if compact else 5]:
                self._line(f"• {finding.text}", theme.COMBAT if finding.level == "bad" else theme.WARN)
        elif not tuner.recording:
            self._line(tr("Compara lo que deberían lanzar tus macros (del juego y externo) con lo que se lanza de "
                          "verdad, y te dice qué cambiar."), theme.MUTED)
        link = label(tr("Abrir el afinador"), f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.ctl.open_tuner
        link.setContentsMargins(f.px(4), f.px(4), 0, 0)
        self.content.addWidget(link)
        self.adjustSize()

    def _line(self, text, color):
        lbl = label(text, self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(4), 0, 0, 0)
        self.content.addWidget(lbl)


class TunerModule(Module):
    key = "tuner"
    title = tr("Afinador")

    def icon(self):
        return glyph_icon(GLYPH_TUNE, theme.ACCENT, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = TunerPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.panel.render(compact)
        tuner = self.ctl.tuner
        return ModuleView(summary="grabando" if tuner.recording else "")
