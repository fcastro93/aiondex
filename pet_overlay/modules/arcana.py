"""Arcanas module (Personaje): your cards in short (arcana.py) - what is missing, the change to make, what to feed.
Everything else opens the Arcanas window (ui/arcanawindow.py)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import Module, ModuleView
from ..arcana import feed_text, short_kind
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import SectionTitle, label
from ..i18n import tr

GLYPH_ARCANA = ""       # a stack of cards: Library (Segoe MDL2 / Fluent)


class ArcanaPanel(QWidget):
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
        plan = self.ctl.arcana_plan()
        if plan is None:
            self._line(tr("Entra al juego con tu personaje (o cambia de canal): el juego manda tus arcanas con tu "
                          "inventario y aquí te digo cuáles usar, qué buscar y cuáles dar de comer."), theme.MUTED)
        else:
            short = [n for n in plan.skills if n.missing]
            self._line(tr("{0} arcanas · te faltan niveles en {1} skill{2}", len(plan.cards), len(short),
                          "s" if len(short) != 1 else "") if short else
                       tr("{0} arcanas · tus objetivos de skill están cubiertos", len(plan.cards)), theme.TEXT)
            todo = [tr("Ponte {0} ({1})", c.name, short_kind(kind)) for kind, _, c, gain in plan.changes if gain > 0]
            todo += [feed_text(fd) for fd in plan.feeds[:1 if compact else 2]]
            if todo:
                self.content.addWidget(SectionTitle(tr("Siguiente"), f))
                for n, text in enumerate(todo[:2 if compact else 3], start=1):
                    self._line(f"{n}. {text}", theme.GOLD, f.name)
            if not compact and plan.look_for:
                kind, rows = next(iter(plan.look_for.items()))
                self._line(tr("Busca: {0} con {1}", short_kind(kind), ", ".join(r[1] for r in rows[:3])), theme.MUTED)
        link = label(tr("Abrir Arcanas"), f.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = self.ctl.open_arcana
        link.setContentsMargins(f.px(4), f.px(4), 0, f.px(4))
        self.content.addWidget(link)
        self.adjustSize()

    def _line(self, text, color, font=None):
        lbl = label(text, font or self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(4), 0, 0, 0)
        self.content.addWidget(lbl)


class ArcanaModule(Module):
    key = "arcana"
    title = tr("Arcanas")

    def icon(self):
        return glyph_icon(GLYPH_ARCANA, theme.GOLD, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = ArcanaPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.panel.render(compact)
        plan = self.ctl.arcana_plan()
        if plan is None:
            return ModuleView()
        n = sum(1 for _, _, _, gain in plan.changes if gain > 0) + len(plan.feeds)
        return ModuleView(summary=tr("{0} para hacer", n) if n else "")
