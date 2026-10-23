"""Builds module: the preset you have on (and which of your builds it is), your class's builds with a quick way to
use one, and the Afinador of the macro inside (the summary of its test). Everything opens the Builds window
(ui/buildswindow.py), whose second tab is the Afinador."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from . import Module, ModuleView
from ..builds import class_of_build
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import SectionTitle, label
from ..i18n import tr

GLYPH_BUILDS = ""     # equalizer / sliders (the Afinador's)
SHOWN = 3


def current_build(ctl):
    """The saved build your preset is right now (nothing to change to it), or None."""
    tuner = ctl.tuner
    if not tuner.own:
        return None
    bar = tuner.presets.get(tuner.active_preset or 1, [])
    slots = tuner.spec_slots.get(tuner.active_preset)
    for b in ctl.builds.list(my_class(ctl)):
        steps = ctl.builds.plan(b, tuner.own, bar, slots)
        if not [s for s in steps if s.kind in ("points", "place", "clear", "spec")]:
            return b
    return None


def my_class(ctl):
    own = ctl.tuner.own
    return class_of_build({"skills": {str(b): None for b in own}}) if own else None


class BuildsPanel(QWidget):
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
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()
        f, ctl = self.fonts, self.ctl
        tuner = ctl.tuner
        if tuner.own:
            now = current_build(ctl)
            preset = tuner.active_preset or 1
            self._line(tr("Preset {0} puesto", preset) + (tr(" · es «{0}»", now['name']) if now else tr(" · no es ninguna build "
                                                                                            "guardada")),
                       theme.GAIN if now else theme.TEXT)
        else:
            self._line(tr("Sin personaje: entra al juego."), theme.MUTED)
        builds = ctl.builds.list(my_class(ctl))
        if builds:
            self.content.addWidget(SectionTitle(tr("Tus builds"), f))
            for b in builds[:2 if compact else SHOWN]:
                row = QHBoxLayout()
                row.setSpacing(f.px(6))
                row.addWidget(label(b.get("name") or "Build", f.small, theme.TEXT, elide=True), 1)
                use = label(tr("Comparar"), f.small, theme.ACCENT)
                use.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                use.setCursor(Qt.PointingHandCursor)
                use.on_click = lambda i=b["id"]: ctl.open_builds(build=i, plan=True)
                row.addWidget(use)
                holder = QWidget()
                holder.setLayout(row)
                holder.setContentsMargins(f.px(4), 0, 0, 0)
                self.content.addWidget(holder)
            if len(builds) > (2 if compact else SHOWN):
                self._line(tr("y {0} más", len(builds) - (2 if compact else SHOWN)), theme.MUTED)
        link = label(tr("Abrir Builds"), f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = ctl.open_builds
        link.setContentsMargins(f.px(4), f.px(4), 0, f.px(4))
        self.content.addWidget(link)
        self._afinador(compact)
        self.adjustSize()

    def _afinador(self, compact):
        """The Afinador of the macro, part of the builds: its test going on or its last one."""
        f, tuner = self.fonts, self.ctl.tuner
        self.content.addWidget(SectionTitle(tr("Afinador del macro"), f))
        if tuner.recording:
            s = tuner.session
            self._line(tr("Grabando prueba… {0} s · {1} lanzamientos", int(s.duration), s.fired), theme.ACCENT)
        report = tuner.last_report
        if report and not tuner.recording:
            bad = [x for x in report.findings if x.level == "bad"]
            warn = [x for x in report.findings if x.level == "warn"]
            self._line(tr("Última prueba: {0} s · {1} lanzamientos · {2} problemas, {3} avisos", int(report.duration), report.total, len(bad), len(warn)), theme.TEXT)
            for finding in (bad + warn)[:2 if compact else 4]:
                self._line(f"• {finding.text}", theme.COMBAT if finding.level == "bad" else theme.WARN)
        link = label(tr("Abrir el afinador"), f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.ctl.open_tuner
        link.setContentsMargins(f.px(4), f.px(4), 0, 0)
        self.content.addWidget(link)

    def _line(self, text, color):
        lbl = label(text, self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(4), 0, 0, 0)
        self.content.addWidget(lbl)


class BuildsModule(Module):
    key = "builds"
    title = "Builds"

    def icon(self):
        return glyph_icon(GLYPH_BUILDS, theme.ACCENT, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = BuildsPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.panel.render(compact)
        tuner = self.ctl.tuner
        if tuner.recording:
            return ModuleView(summary=tr("afinador grabando"))
        now = current_build(self.ctl) if tuner.own else None
        return ModuleView(summary=now["name"] if now else "")
