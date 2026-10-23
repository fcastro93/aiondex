"""Mejoras module (Personaje): what to do next with your gear (upgrades.py) - your Kina and stones, the next steps you
can do now. Everything else opens the Mejoras window (ui/upgradeswindow.py)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import Module, ModuleView
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import SectionTitle, label
from ..upgrades import kinah_text, next_steps
from ..i18n import tr

GLYPH_UPGRADES = ""       # an arrow up
SHOWN = 3


class UpgradesPanel(QWidget):
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
        f = self.fonts
        plan = self.ctl.upgrade_plan()
        if plan is None:
            self._line(tr("Entra al juego con tu personaje: leo tu equipo, tu bolsa y tu almacén y te digo qué "
                          "mejorar primero."), theme.MUTED)
        else:
            me = plan.me
            bits = [tr("Alt · {0} Kinah ligada", kinah_text(plan.have('kina_bound'))) if me and me.alt else
                    f"{kinah_text(plan.have('kina'))} Kinah" + (tr(" + {0} ligada", kinah_text(plan.have('kina_bound')))
                                                                 if plan.have("kina_bound") else ""),
                    f"{plan.have('enhance_stone'):,} Enhance Stones"]
            if me and me.item_level:
                bits.insert(0, tr("nivel de objeto {0:,}", me.item_level))
            bits += [f"{n} vouchers" for n in plan.vouchers.values()][:1]
            self._line(" · ".join(bits), theme.TEXT)
            self.content.addWidget(SectionTitle(tr("Siguiente"), f))
            for n, step in enumerate(next_steps(plan, 2 if compact else SHOWN), start=1):
                self._line(f"{n}. {step.title}", theme.GOLD if step.ready else theme.TEXT, f.name)
                if not compact:
                    detail = label(step.summary or step.detail, f.small, theme.MUTED, elide=True)
                    detail.setContentsMargins(f.px(14), 0, 0, 0)
                    self.content.addWidget(detail)
        link = label(tr("Abrir Mejoras"), f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.ctl.open_upgrades
        link.setContentsMargins(f.px(4), f.px(4), 0, f.px(4))
        self.content.addWidget(link)
        self.adjustSize()

    def _line(self, text, color, font=None):
        lbl = label(text, font or self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(4), 0, 0, 0)
        self.content.addWidget(lbl)


class UpgradesModule(Module):
    key = "upgrades"
    title = tr("Mejoras")

    def icon(self):
        return glyph_icon(GLYPH_UPGRADES, theme.ACCENT, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = UpgradesPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.panel.render(compact)
        plan = self.ctl.upgrade_plan()
        if plan is None:
            return ModuleView()
        ready = sum(1 for s in plan.steps if s.ready and s.kind not in ("farm", "boss", "content", "goal", "spare",
                                                                        "theostone"))
        return ModuleView(summary=tr("{0} para hacer ya", ready) if ready else "")
