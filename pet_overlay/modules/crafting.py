"""Crafting module: in the overlay, scan the market (button or hotkey), the shots waiting to be processed and the most
profitable recipes of your profession; the tables, samples and price charts live in their own window."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from . import Module, ModuleView
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.dps_panel import short_number
from ..ui.widgets import SectionTitle, label

GLYPH_CRAFT = ""          # repair (hammer and wrench)
ROWS = {True: 3, False: 7}


def money(value, sign=False):
    """12,345 Kinah as '12.3k'; with sign, '+12.3k' / '-800'."""
    if value is None:
        return "—"
    text = short_number(abs(value)) if abs(value) >= 10_000 else f"{abs(value):,.0f}"
    return ("-" if value < 0 else "+" if sign else "") + text


class CraftingPanel(QWidget):
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
                item.widget().deleteLater()
        f, crafting = self.fonts, self.ctl.crafting
        prof = crafting.professions.get(crafting.profession, {})
        head = QWidget()
        lay = QHBoxLayout(head)
        lay.setContentsMargins(f.px(4), 0, f.px(2), 0)
        lay.addWidget(label(prof.get("name", crafting.profession), f.name, theme.TEXT), 1)
        scan = self._link(f"Escanear ({self.ctl.settings['hotkeys'].get('crafting_scan', '')})".replace(" ()", ""),
                          self.ctl.crafting_scan)
        lay.addWidget(scan)
        self.content.addWidget(head)
        shots = crafting.shots
        if shots:
            reading = sum(1 for s in shots if not s.read)
            text = f"{len(shots)} {'captura' if len(shots) == 1 else 'capturas'} por procesar"
            if reading:
                text += f" · leyendo {reading}…"
            self._line(text, theme.GOLD)
            self._line(f"Última: {shots[-1].caption()}", theme.MUTED)
            self.content.addWidget(self._link("Revisar y procesar", lambda: self.ctl.open_crafting("shots")))
        elif not crafting.samples:
            self._line(f"Abre el mercado en «{prof.get('market', '…')}», pulsa Escanear en cada página y luego "
                       f"procesa las capturas: verás qué vale la pena craftear.", theme.MUTED)
        rows = [r for r in crafting.recipe_rows(self.ctl.character_faction()) if r.profit is not None]
        if rows:
            self.content.addWidget(SectionTitle("Más rentable (por craft)", f))
            for row in rows[:ROWS[compact]]:
                line = QWidget()
                lay = QHBoxLayout(line)
                lay.setContentsMargins(f.px(4), 0, f.px(2), 0)
                lay.addWidget(label(row.product, f.body, theme.TEXT, elide=True), 1)
                lay.addWidget(label(money(row.profit, sign=True), f.name,
                                    theme.GAIN if row.profit > 0 else theme.COMBAT))
                self.content.addWidget(line)
        link = self._link("Abrir Crafting", self.ctl.open_crafting)
        link.setContentsMargins(f.px(4), f.px(4), 0, 0)
        self.content.addWidget(link)
        self.adjustSize()

    def _link(self, text, action):
        link = label(text, self.fonts.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = action
        return link

    def _line(self, text, color):
        lbl = label(text, self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(4), 0, 0, 0)
        self.content.addWidget(lbl)


class CraftingModule(Module):
    key = "crafting"
    title = "Crafting"

    def icon(self):
        return glyph_icon(GLYPH_CRAFT, theme.ACCENT, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = CraftingPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.panel.render(compact)
        shots = self.ctl.crafting.shots
        return ModuleView(summary=f"{len(shots)} {'captura' if len(shots) == 1 else 'capturas'}" if shots else "")
