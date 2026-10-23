"""«Profesiones y Market» in the overlay: four modules, each with its section in the «Profesiones y Market» window
(ui/craftingwindow.py).

- Crafting: what you can craft now with what you have (bag and storage).
- Market: scan the market (button or hotkey), the shots waiting to be processed, today's sales and the most profitable
  recipes (or the materials' prices).
- Levear: what to craft to level the profession up, Kinah per point of experience.
- Gathering: the materials you looked up last, to open their map, and the search."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from . import Module, ModuleView
from ..professions import level_text
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.dps_panel import short_number
from ..ui.widgets import SectionTitle, label
from ..i18n import tr

GLYPH_CRAFT = ""          # repair (hammer and wrench)
GLYPH_MARKET = ""         # shopping cart
GLYPH_LEVEL = ""          # up
GLYPH_GATHER = ""         # map pin
ROWS = {True: 3, False: 7}


def money(value, sign=False):
    """12,345 Kinah as '12.3k'; with sign, '+12.3k' / '-800'."""
    if value is None:
        return "—"
    text = short_number(abs(value)) if abs(value) >= 10_000 else f"{abs(value):,.0f}"
    return ("-" if value < 0 else "+" if sign else "") + text


class _Panel(QWidget):
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
        self.fill(compact)
        self.adjustSize()

    def fill(self, compact):
        raise NotImplementedError

    def _head(self, title, link_text=None, action=None):
        f = self.fonts
        head = QWidget()
        lay = QHBoxLayout(head)
        lay.setContentsMargins(f.px(4), 0, f.px(2), 0)
        lay.addWidget(label(title, f.name, theme.TEXT), 1)
        if link_text:
            lay.addWidget(self._link(link_text, action))
        self.content.addWidget(head)

    def _profession(self):
        crafting = self.ctl.crafting
        prof = crafting.professions.get(crafting.profession, {}).get("name", crafting.profession)
        level = crafting.level()
        return prof + (f" · {level_text(level)}" if level else "")

    def _row(self, left, right, color=None):
        f = self.fonts
        line = QWidget()
        lay = QHBoxLayout(line)
        lay.setContentsMargins(f.px(4), 0, f.px(2), 0)
        lay.addWidget(label(left, f.body, theme.TEXT, elide=True), 1)
        lay.addWidget(label(right, f.name, color or theme.TEXT))
        self.content.addWidget(line)
        return line

    def _link(self, text, action):
        link = label(text, self.fonts.name, theme.ACCENT)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.on_click = action
        return link

    def _open(self, text, section):
        link = self._link(text, lambda: self.ctl.open_crafting(section))
        link.setContentsMargins(self.fonts.px(4), self.fonts.px(4), 0, 0)
        self.content.addWidget(link)

    def _line(self, text, color):
        lbl = label(text, self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(4), 0, 0, 0)
        self.content.addWidget(lbl)


class CraftingPanel(_Panel):
    """What you can craft now with what you have."""

    def fill(self, compact):
        crafting = self.ctl.crafting
        self._head(self._profession())
        rows = crafting.recipe_rows(self.ctl.crafting_faction(), bound=True)
        if not crafting.inventory.read_at(crafting.levels_character):
            self._line(tr("Entra al juego con tu personaje: AionDex lee su bolsa y su almacén y aquí verás lo que "
                          "puedes craftear con lo que tienes."), theme.MUTED)
        else:
            ready = []
            for row in rows:
                makes = crafting.makes(row)
                if makes and makes[0] and row.can_craft is not False:
                    ready.append((makes[0], row))
            ready.sort(key=lambda e: (-e[1].needs, -e[0]))
            if ready:
                self.content.addWidget(SectionTitle(tr("Puedes hacer ya"), self.fonts))
                for times, row in ready[:ROWS[compact]]:
                    self._row(row.product.replace(" (Bound)", ""), f"× {times}", theme.GAIN)
                if len(ready) > ROWS[compact]:
                    self._line(tr("y {0} recetas más", len(ready) - ROWS[compact]), theme.MUTED)
            else:
                self._line(tr("Con lo que tienes no te alcanza para ninguna receta: mira qué te falta en Crafting."),
                           theme.MUTED)
        self._open(tr("Abrir Crafting (recetas y materiales)"), "crafting")


class MarketPanel(_Panel):
    """Scan the market, the shots waiting, today's sales, the most profitable recipes."""

    def fill(self, compact):
        f, crafting = self.fonts, self.ctl.crafting
        prof = crafting.professions.get(crafting.profession, {})
        hotkey = self.ctl.settings['hotkeys'].get('crafting_scan', '')
        self._head(prof.get("name", crafting.profession), tr("Escanear ({0})", hotkey).replace(" ()", ""),
                   self.ctl.crafting_scan)
        shots = crafting.shots
        if shots:
            reading = sum(1 for s in shots if not s.read)
            text = tr("{0} {1} por procesar", len(shots), tr("captura") if len(shots) == 1 else tr("capturas"))
            if reading:
                text += tr(" · leyendo {0}…", reading)
            self._line(text, theme.GOLD)
            self._line(tr("Última: {0}", shots[-1].caption()), theme.MUTED)
            self.content.addWidget(self._link(tr("Revisar y procesar"), lambda: self.ctl.open_crafting("shots")))
        elif not crafting.samples:
            self._line(tr("Abre el mercado en «{0}», pulsa Escanear en cada página y luego procesa las capturas: "
                          "verás qué vale la pena craftear.", prof.get('market', '…')), theme.MUTED)
        today = self._sales_today()
        if today:
            self._line(today, theme.GAIN)
        rows = crafting.recommend(crafting.recipe_rows(self.ctl.crafting_faction()), n=ROWS[compact])
        if rows:
            level = crafting.level()
            self.content.addWidget(SectionTitle(tr("Más rentable ({0}, por intento)", level_text(level)) if level else
                                                tr("Más rentable (por intento)"), f))
            for row in rows[:ROWS[compact]]:
                self._row(row.product, money(row.profit, sign=True), theme.GAIN if row.profit > 0 else theme.COMBAT)
        self._open(tr("Abrir Market"), "market")

    def _sales_today(self):
        """'Ventas hoy: 4 · cobrado 261K · +14K' from the sales ledger, or ""."""
        ledger = self.ctl.crafting.ledger
        local = time.localtime()
        start = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
        sales = [s for s in ledger.sales if s["at"] >= start]
        if not sales:
            return ""
        t = ledger.totals(sales)
        return (tr("Ventas hoy: {0} · cobrado {1}", t['count'], money(t['net']))
                + (f" · {money(t['profit'], sign=True)}" if t["count"] > t["unknown"] else ""))


class LevelingPanel(_Panel):
    """What to craft to level the profession up."""

    def fill(self, compact):
        crafting = self.ctl.crafting
        self._head(self._profession())
        level = crafting.level()
        if level is None:
            self._line(tr("Entra al juego con tu personaje (AionDex lee el nivel de cada profesión) o escríbelo en "
                          "la ventana."), theme.MUTED)
        else:
            exp, needed = crafting.game_exp(), crafting.level_exp()
            if exp is not None and needed:
                self._line(tr("{0:,} / {1:,} de experiencia en el nivel", exp, needed), theme.MUTED)
            plan = crafting.leveling_plan(crafting.recipe_rows(self.ctl.crafting_faction(), bound=True))
            if plan:
                self.content.addWidget(SectionTitle(tr("Para subir (Kinah por exp)"), self.fonts))
                for row, exp_per, how, value, per in plan[:ROWS[compact]]:
                    right = (f"{money(per, sign=True)}/exp" if per is not None else f"{money(value, sign=True)} ?")
                    line = self._row(row.product.replace(" (Bound)", ""), right,
                                     theme.GAIN if (per if per is not None else value) >= 0 else theme.TEXT)
                    line.setToolTip(tr("{0:.0f} exp por intento ({1})", exp_per, tr(how)) if exp_per else
                                    tr("Experiencia aún no vista: craftéala una vez"))
            else:
                self._line(tr("Falta el precio de los materiales de tu nivel: escanéalos en el mercado."), theme.MUTED)
        self._open(tr("Abrir la guía para subir"), "leveling")


class GatheringPanel(_Panel):
    """The materials you looked up last, and the search."""

    def fill(self, compact):
        ctl = self.ctl
        self._head(tr("Recolección"))
        route = (ctl.settings.get("crafting") or {}).get("minimap_route")
        overlay = getattr(ctl, "minimap_overlay", None)
        if route and overlay is not None and overlay.material:
            self._line(tr("Ruta en el minimapa: {0}", overlay.material), theme.GAIN)
        recent = [n for n in ctl.crafting.config.get("recent_gather", []) if ctl.gathering.has(n)]
        if recent:
            self.content.addWidget(SectionTitle(tr("Vistos hace poco"), self.fonts))
            for name in recent[:ROWS[compact]]:
                link = self._link(f"⌖ {name}", lambda n=name: ctl.open_gather_map(n))
                link.setContentsMargins(self.fonts.px(4), 0, 0, 0)
                self.content.addWidget(link)
        else:
            self._line(tr("Busca un material por su nombre y abre su mapa: sus puntos, la mejor zona y la ruta."),
                       theme.MUTED)
        self._open(tr("Buscar material…"), "gathering")


class _ProfessionsModule(Module):
    glyph = GLYPH_CRAFT
    panel_class = CraftingPanel

    def icon(self):
        return glyph_icon(self.glyph, theme.ACCENT, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = self.panel_class(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.panel.render(compact)
        return ModuleView()


class CraftingModule(_ProfessionsModule):
    key = "crafting"
    title = "Crafting"


class MarketModule(_ProfessionsModule):
    key = "market"
    title = "Market"
    glyph = GLYPH_MARKET
    panel_class = MarketPanel

    def render(self, compact):
        self.panel.render(compact)
        shots = self.ctl.crafting.shots
        return ModuleView(summary=f"{len(shots)} {'captura' if len(shots) == 1 else 'capturas'}" if shots else "")


class LevelingModule(_ProfessionsModule):
    key = "leveling"
    title = tr("Levear")
    glyph = GLYPH_LEVEL
    panel_class = LevelingPanel


class GatheringModule(_ProfessionsModule):
    key = "gathering"
    title = "Gathering"
    glyph = GLYPH_GATHER
    panel_class = GatheringPanel


PROFESSION_MODULES = ("crafting", "market", "leveling", "gathering")
