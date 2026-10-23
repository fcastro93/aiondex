"""Profesiones y Market: one window, four sections (the overlay's «Profesiones y Market» menu has one module each).

- Crafting: your profession's recipes with how many of each material you have (bag and storage together) and how many
  times you can make each one; a click on a material shows its market price (and, if it is gathered, where).
- Market: which recipes are worth crafting (sorted by profit after the market's tax, with what each one needs and
  where it comes from), the materials' prices, the market shots waiting to be processed, the samples taken, sales.
- Levear: what to craft to level the profession up at your level: Kinah per point of experience, with the experience
  each recipe gave you (or an estimate from the ones near its level) and what each level asks for once seen.
- Gathering: find a gatherable material by name and open its map."""
import os
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPainterPath, QPen, QPixmap, QValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QToolTip, QVBoxLayout, QWidget,
                               QWidgetAction)

from .. import APP_NAME
from ..crafting import DEFAULT_PROFESSION, MATERIALS, SORT_KEYS, RecipeRow, span_text
from ..gathering import kills_for, map_open
from ..professions import NOVICE_MAX, RANKS, level_text, parse_level
from ..mapdata import MAP_NAMES
from . import theme
from .appmenu import styled_menu
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label
from ..i18n import tr

SECTIONS = [("crafting", "Crafting"), ("market", "Market"), ("leveling", tr("Levear")), ("gathering", "Gathering")]
TABS = [("profit", tr("Productos")), ("materials", tr("Materiales")), ("gear", tr("Equipo")), ("shots", tr("Capturas")),
        ("samples", tr("Muestreos")), ("sales", tr("Ventas"))]          # the Market's own tabs
VERDICT_TEXT = {"upgrade": (tr("Te sirve"), "#7bd88f"), "base": (tr("Mejor base"), "#f0c24e"), "no": ("No", "#8a93a8"),
                "class": (tr("Otra clase"), "#8a93a8"), "level": (tr("Nivel"), "#8a93a8"), "faction": (tr("Otra facción"), "#8a93a8")}
SEARCH_HINT = {"crafting": tr("Buscar receta o material…"), "market": tr("Buscar objeto o material…"),
               "leveling": tr("Buscar receta…"), "gathering": tr("Buscar material para recolectar (nombre o nodo)…")}
PROFESSION_SECTIONS = {"crafting", "leveling"}                 # plus the Market's Productos: they use the profession
# aion2data's grades in the game's colours (its "Epic" is the game's blue Legend tier)
GRADE_COLOR = {"Common": "#C3C3C3", "Rare": "#19E048", "Epic": "#379AFF", "Unique": "#FFD02B", "Special": "#00FFD8"}
SOURCE = {   # how a material is priced -> (chip text, colour)
    "npc": ("NPC", "#2ec0f2"), "gather": (tr("Recolectas"), "#7bd88f"), "craft": (tr("Lo fabricas"), "#c38bff"),
    "market": (tr("Mercado"), "#f0c24e"), "manual": (tr("Tu precio"), "#e0a050"), "unknown": (tr("Sin precio"), "#ff7a59"),
    "drop": (tr("Mobs / mercado"), "#ff9f6e"), "dungeon": (tr("Mazmorra / mercado"), "#8fb3ff"),
    "chest": (tr("Cofre / mercado"), "#c9a46b"), "combo": (tr("Combo / mercado"), "#c38bff"),
}
FARMED = {"drop", "dungeon", "chest"}       # not sold, gathered or crafted, but they drop (1.63.1: were «Mercado»)
PROFESSION_ES = {"Cooking": tr("Cocina"), "Alchemy": tr("Alquimia"), "Blacksmithing": tr("Herrería"),
                 "Armorsmithing": tr("Armería"), "Handicrafting": tr("Artesanía")}
VERDICT_COLOR = {"good": theme.GAIN, "bad": theme.COMBAT, "none": theme.MUTED}
MAT_KINDS = [("all", tr("Todos")), ("gather", tr("Recolectados")), ("farm", tr("De mobs, mazmorras o cofres")),
             ("combo", tr("Combo de craft")), ("market", tr("Solo del mercado")), ("craft", tr("Procesados"))]
MAT_KIND_CHIP = {"gather": (tr("Recolectado"), "#7bd88f"), "market": (tr("Solo mercado"), "#f0c24e"),
                 "craft": (tr("Procesado"), "#c38bff"), "drop": ("Mobs", "#ff9f6e"), "dungeon": (tr("Mazmorra"), "#8fb3ff"),
                 "chest": (tr("Cofre"), "#c9a46b"), "combo": (tr("Combo de craft"), "#c38bff")}
COLUMNS = ((tr("Precio"), 74), (tr("Recibes"), 74), (tr("Fallo"), 96), (tr("Recolectando"), 108), (tr("Comprando"), 108),
           (tr("¿Se vende?"), 146))
SORTABLE = {tr("Objeto"): "name", tr("Precio"): "price", tr("Recibes"): "net", tr("Fallo"): "fail",
            tr("Recolectando"): "gather", tr("Comprando"): "buy", tr("¿Se vende?"): "sales"}             # click a column title to sort by it
DETAIL_W = 170
THUMB_W = 340
CARD_STYLE = """
QFrame#Card { background: #151923; border: 1px solid #2b3242; border-radius: 8px; }
QFrame#Row { background: #12161f; border: 1px solid #262c3a; border-radius: 6px; }
QFrame#Row[open="true"] { border-color: #4a5368; }
QFrame#Detail { background: #10131b; border: 1px solid #262c3a; border-radius: 6px; }
QFrame#Tile { background: #151923; border: 1px solid #2b3242; border-radius: 6px; }
QPushButton[small="true"] { padding: 3px 10px; font-weight: 600; }
"""


def _clock(ts):
    return time.strftime("%d/%m %H:%M", time.localtime(ts))


def _ago(ts):
    mins = max(0, (time.time() - ts) / 60)
    if mins < 1:
        return tr("hace un momento")
    if mins < 60:
        return tr("hace {0:.0f} min", mins)
    if mins < 48 * 60:
        return tr("hace {0:.0f} h", mins / 60)
    return tr("hace {0:.0f} días", mins / 1440)


def _kinah(value, sign=False):
    return "—" if value is None else (f"{value:+,.0f}" if sign else f"{value:,.0f}")


def _axis(value):
    """Price labels of the chart: 26,500 / 1.25M (short forms like 27K repeat on a narrow range)."""
    return f"{value / 1_000_000:,.2f}M" if value >= 1_000_000 else f"{value:,.0f}"


SHARE_TIP = (tr("Comparte tus muestreos del mercado con los demás usuarios de AionDex de tu servidor y lado, y usa "
                "los suyos junto con los tuyos (precios más al día, más ventas vistas). Solo viaja lo que se ve en "
                "la lista y las ofertas del mercado; nunca tus ventas, tu inventario ni tu personaje. Un precio de "
                "otro usuario muy distinto de los demás del mismo objeto no se usa."))


class CraftingWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.crafting = ctl.crafting
        self.fonts = Fonts(1.0)
        self.tab = "shots" if self.crafting.shots else "profit"
        self.section = "market" if self.crafting.shots else "crafting"
        if self.crafting.profession == MATERIALS:                # 1.61: the materials are the Market's own tab
            self.crafting.set_option("profession", DEFAULT_PROFESSION)
            self.section, self.tab = "market", "materials"
        self._open_recipes = set()        # recipes of Crafting whose materials are shown
        self._open = set()                # products whose materials are shown
        self._editing = set()             # materials whose price box is open
        self._thumbs = {}                 # shot file -> QPixmap thumbnail
        self._sample = None               # the sample shown in Muestreos
        self.sales_period = self.crafting.config.get("sales_period", "week")     # Ventas: hoy / 7 / 30 días / todo
        self.chart_window = None
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Profesiones y Market", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + CARD_STYLE)
        self.resize(1180, 860)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = label(tr("Profesiones y Market"), f.name_big, theme.TEXT)
        title.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        title.setToolTip(tr("Tus recetas con lo que tienes, el mercado (qué vale la pena craftear y vender), cómo "
                            "subir de nivel y dónde recolectar."))
        titles.addWidget(title)
        head.addLayout(titles, 1)
        head.addWidget(label(tr("Personaje:"), f.small, theme.MUTED))
        self.who = QComboBox()
        self.who.setToolTip(tr("Con los niveles de qué personaje se calcula (AionDex los lee del juego al entrar "
                               "con cada uno)"))
        self.who.currentIndexChanged.connect(self._who_changed)
        head.addWidget(self.who)
        head.addWidget(label(tr("Profesión:"), f.small, theme.MUTED))
        self.profession = QComboBox()
        for key, info in self.crafting.professions.items():
            if key != MATERIALS:
                self.profession.addItem(info["name"], key)
        self.profession.setCurrentIndex(max(0, self.profession.findData(self.crafting.profession)))
        self.profession.currentIndexChanged.connect(self._profession_changed)
        head.addWidget(self.profession)
        self.level_label = label(tr("Tu nivel:"), f.small, theme.MUTED)
        head.addWidget(self.level_label)
        self.level = LevelBox()
        self.level.setToolTip(tr("Tu nivel en esta profesión: decide tu probabilidad de éxito y qué recetas puedes "
                                 "hacer"))
        self.level.setValue(self.crafting.level() or 0)
        self._level_timer = QTimer(self, singleShot=True, interval=500, timeout=self._level_changed)
        self.level.valueChanged.connect(lambda _: self._level_timer.start())
        head.addWidget(self.level)
        self.level_back = QPushButton("")
        self.level_back.setProperty("flat", True)
        self.level_back.clicked.connect(self._level_back)
        head.addWidget(self.level_back)
        self.report_button = QPushButton(tr("Informe"))
        self.report_button.setProperty("flat", True)
        self.report_button.setToolTip(tr("Crea una imagen con lo importante para compartir (se guarda en "
                                         "Imágenes\\AionDex y se copia al portapapeles)"))
        self.report_button.clicked.connect(self._report)
        self.scan_button = QPushButton(tr("Escanear"))
        self.scan_button.clicked.connect(self._scan)
        head.addWidget(self.scan_button)
        root.addLayout(head)

        sections = QHBoxLayout()
        sections.setSpacing(6)
        self.section_buttons = {}
        for key, text in SECTIONS:
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.setMinimumWidth(120)
            button.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            sections.addWidget(button)
            self.section_buttons[key] = button
        sections.addStretch(1)
        root.addLayout(sections)

        tabs = QHBoxLayout()
        self.tab_buttons = {}
        for key, text in TABS:
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            tabs.addWidget(button)
            self.tab_buttons[key] = button
        tabs.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar objeto o material…"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self._search_timer = QTimer(self, singleShot=True, interval=250, timeout=self.refresh)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        tabs.addWidget(self.report_button)
        tabs.addWidget(self.search)
        root.addLayout(tabs)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setSpacing(8)
        scroll.setWidget(holder)
        self.scroll = scroll
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        credit = self.crafting.credit or {"text": tr("Recetas, tiendas y objetos: aion2data.com"),
                                          "url": "https://aion2data.com/recipes"}
        link = QPushButton(credit["text"])
        link.setProperty("flat", True)
        link.clicked.connect(lambda: webbrowser.open(credit["url"]))
        foot.addWidget(link)
        foot.addStretch(1)
        self.share_box = QCheckBox(tr("Compartir muestreos"))
        self.share_box.setToolTip(SHARE_TIP)
        self.share_box.toggled.connect(self._share_toggled)
        foot.addWidget(self.share_box)
        self.share_status = label("", f.small, theme.MUTED)
        self.share_status.setToolTip(SHARE_TIP)
        foot.addWidget(self.share_status)
        foot.addSpacing(12)
        foot.addWidget(label(tr("El texto del mercado se lee con el OCR de Windows, en tu PC: las capturas no salen "
                                "de ella."), f.small, theme.MUTED))
        root.addLayout(foot)
        self.refresh()

    # --- actions -----------------------------------------------------------------------------------------
    def _profession_changed(self, _):
        self.crafting.set_option("profession", self.profession.currentData())
        self._open.clear()
        self.level.blockSignals(True)
        self.level.setValue(self.crafting.level() or 0)
        self.level.blockSignals(False)
        self.refresh()
        self.ctl.refresh()

    def _show_level(self):
        """The level box: the game's level (read-only) when AionDex has read it for this character, else yours."""
        crafting = self.crafting
        materials = crafting.profession == MATERIALS
        for widget in (self.level_label, self.level):
            widget.setVisible(not materials)                 # materials have no level
        if materials:
            self.level_back.setVisible(False)
            return
        source = crafting.level_source()
        self.level.blockSignals(True)
        self.level.setValue(crafting.level() or 0)
        self.level.blockSignals(False)
        game = crafting.game_level()
        self.level_back.setVisible(source == "try")
        if source == "try":
            self.level_back.setText(tr("↺ usar el del juego ({0})", level_text(game)))
            self.level.setToolTip(tr("Estás probando con {0}: los números son como si lo tuvieras. Tu nivel real "
                                     "(del juego) es {1}.", level_text(crafting.level()), level_text(game)))
        elif source == "game":
            exp = crafting.game_exp()
            self.level.setToolTip(tr("Leído del juego para {0}: {1}", crafting.levels_character, level_text(game))
                                  + (tr(", {0} de experiencia en el nivel", exp) if exp is not None else "")
                                  + tr(". Se actualiza al entrar y cada vez que crafteas. Cámbialo para ver los "
                                       "números con otro nivel (por ejemplo, el que tendrás)."))
        else:
            self.level.setToolTip(tr("Tu nivel en esta profesión: decide tu probabilidad de éxito y qué recetas "
                                     "puedes hacer. AionDex lo lee del juego al entrar con el personaje (y lo "
                                     "aprende de cada profesión la primera vez que crafteas algo de ella)."))

    def _level_changed(self):
        if self.crafting.game_level() is not None:
            self.crafting.try_level(self.level.value())          # over the game's level: "what if I were N"
        else:
            self.crafting.set_level(self.level.value())
        self.refresh()
        self.ctl.refresh()

    def _fill_who(self):
        crafting = self.crafting
        playing = crafting.character
        others = [n for n in crafting.known_characters() if n != playing]
        chosen = crafting.config.get("view_character")
        self.who.blockSignals(True)
        self.who.clear()
        self.who.addItem(tr("El que juegas ({0})", playing) if playing else tr("El que juegas"), None)
        for name in others:
            self.who.addItem(name, name)
        self.who.setCurrentIndex(max(0, self.who.findData(chosen)) if chosen in others else 0)
        self.who.blockSignals(False)

    def _who_changed(self, _):
        self.crafting.set_option("view_character", self.who.currentData())
        self.refresh()
        self.ctl.refresh()

    def _report(self):
        """The shareable picture of what is on screen: saved, copied to the clipboard and opened."""
        from pathlib import Path
        from PySide6.QtWidgets import QApplication
        from . import crafting_report
        crafting = self.crafting
        prof = crafting.professions.get(crafting.profession, {}).get("name", crafting.profession)
        who = crafting.levels_character
        level = crafting.level()
        last = crafting.samples[-1]["at"] if crafting.samples else None
        bits = [who or "", level_text(level) + (tr(" (probando)") if crafting.level_source() == "try" else "")
                if level else tr("sin nivel"), tr("precios de tus muestreos del {0}", _clock(last)) if last else tr("sin muestreos")]
        image = crafting_report.render(getattr(self, "_shown", []), getattr(self, "_shown_picks", []),
                                       f"Crafting · {prof}", " · ".join(b for b in bits if b), GRADE_COLOR,
                                       level_pick=getattr(self, "_level_pick", None))
        folder = Path.home() / "Pictures" / "AionDex"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / time.strftime(f"crafting-{crafting.profession.lower()}-%Y%m%d-%H%M%S.jpg")
        if not image.save(str(path), "JPG", 92):
            self.ctl.notify("warn", tr("No se pudo guardar el informe"), str(path))
            return
        QApplication.clipboard().setImage(image)
        self.ctl.notify("info", tr("Informe de Crafting listo"),
                        tr("Copiado al portapapeles (pégalo en Discord o WhatsApp) y guardado en Imágenes\\AionDex."))
        os.startfile(str(path))

    def _level_back(self):
        self.crafting.try_level(None)
        self.refresh()
        self.ctl.refresh()

    def _scan(self):
        self.ctl.crafting_scan(from_window=True)

    def ask_share(self):
        """Once: share your market samples and use the other users'?"""
        if self.ctl.market_share.asked:
            return
        box = QMessageBox(self)
        box.setWindowTitle(tr("{0} — Compartir muestreos", APP_NAME))
        box.setIcon(QMessageBox.Question)
        box.setText(tr("¿Compartes tus muestreos del mercado con los demás usuarios de AionDex de tu servidor y "
                       "lado?"))
        box.setInformativeText(
            tr("Con más gente escaneando, los precios están más al día y se ve mejor qué se vende: usarás sus "
               "muestreos junto con los tuyos.\n\nSolo viaja lo que se ve en la lista y en las ofertas del mercado "
               "(objetos, precios y ofertas). Nunca tus ventas, tu inventario ni tu personaje, y las capturas no "
               "salen de tu PC.\n\nPuedes cambiarlo cuando quieras con «Compartir muestreos», abajo en esta ventana."))
        yes = box.addButton(tr("Compartir"), QMessageBox.AcceptRole)
        box.addButton(tr("Ahora no"), QMessageBox.RejectRole)
        box.exec()
        self.ctl.set_market_share(box.clickedButton() is yes)
        self.refresh()

    def _share_toggled(self, on):
        if on != self.ctl.market_share.enabled:
            self.ctl.set_market_share(on)
            self.refresh()

    def _share_text(self):
        share, crafting = self.ctl.market_share, self.crafting
        if not share.enabled:
            return "desactivado"
        market = self.ctl.market_key()
        if not market:
            return tr("entra con un personaje para saber tu mercado")
        server, side = market.split("-")
        bits = [tr("{0} de otros ({1} · {2})", crafting.shared_count, server, 'Asmodian' if side == 'asmodian' else 'Elyos')]
        if share.sent:
            bits.append(tr("{0} tuyos enviados", share.sent))
        if share.pending:
            bits.append(tr("{0} por enviar", len(share.pending)))
        if share.error:
            bits.append(share.error)
        elif share.synced_at:
            bits.append(tr("al día {0}", time.strftime('%H:%M', time.localtime(share.synced_at))))
        return " · ".join(bits)

    def show_tab(self, key):
        """A section (crafting / market / leveling / gathering) or one of the Market's tabs (profit, shots...)."""
        if key in dict(SECTIONS):
            self.section = key
        else:
            self.section, self.tab = "market", key
        self.refresh()
        self.scroll.verticalScrollBar().setValue(0)
        if self.section == "gathering":
            self.search.setFocus()

    def _process(self):
        sample = self.crafting.process()
        if sample is None:
            return
        self.ctl.crafting_processed(sample)
        self._sample = None
        self.show_tab("profit")

    def _remove(self, shot):
        self.crafting.remove_shot(shot)
        self._thumbs.pop(shot.file, None)
        self.refresh()
        self.ctl.refresh()

    def _remove_unchecked(self):
        for shot in [s for s in self.crafting.shots if not s.checked]:
            self._remove(shot)

    def _toggle(self, product):
        self._open.symmetric_difference_update({product})
        self.refresh()

    def _set_price(self, name, edit):
        text = edit.text().replace(",", "").replace(".", "").strip()
        value = int(text) if text.isdigit() else None
        if value != self.crafting.manual_price(name):
            self.crafting.set_manual_price(name, value)
            QTimer.singleShot(0, self.refresh)
            self.ctl.refresh()

    def open_chart(self, name):
        if self.chart_window is None:
            self.chart_window = PriceChartWindow(self.ctl)
        self.chart_window.show_item(name)
        self.chart_window.show()
        self.chart_window.raise_()
        self.chart_window.activateWindow()

    # --- content -----------------------------------------------------------------------------------------
    def refresh(self):
        counts = {"shots": len(self.crafting.shots), "samples": len(self.crafting.samples),
                  "sales": len(self.crafting.ledger.sales), "gear": len(self.ctl.gear_prices())}
        market = self.section == "market"
        for key, button in self.section_buttons.items():
            button.setChecked(key == self.section)
            if key == "market" and counts["shots"]:
                button.setText(f"Market ({counts['shots']})")
            elif key == "market":
                button.setText("Market")
        for key, button in self.tab_buttons.items():
            text = dict(TABS)[key]
            button.setText(f"{text} ({counts[key]})" if counts.get(key) else text)
            button.setChecked(key == self.tab)
            button.setVisible(market)
        self.search.setVisible(not (market and self.tab == "shots"))
        self.search.setPlaceholderText(SEARCH_HINT[self.section])
        uses_profession = self.section in PROFESSION_SECTIONS or (market and self.tab == "profit")
        for widget in (self.profession, self.who):
            widget.setEnabled(uses_profession or self.section == "crafting")
        self.report_button.setVisible(market and self.tab == "profit")
        hk = self.ctl.settings["hotkeys"].get("crafting_scan")
        self.scan_button.setToolTip(tr("Captura la ventana del juego (con el mercado abierto)")
                                    + (tr(". En el juego: {0}", hk) if hk else ""))
        self._fill_who()
        self._show_level()
        self.share_box.blockSignals(True)
        self.share_box.setChecked(self.ctl.market_share.enabled)
        self.share_box.blockSignals(False)
        self.share_status.setText(self._share_text())
        if not uses_profession:
            for widget in (self.level_label, self.level, self.level_back):
                widget.setVisible(False)
        _clear(self.body)
        if market:
            {"profit": self._profit_tab, "materials": self._market_materials, "gear": self._gear_tab,
             "shots": self._shots_tab, "samples": self._samples_tab, "sales": self._sales_tab}[self.tab]()
        else:
            {"crafting": self._recipes_tab, "leveling": self._leveling_tab,
             "gathering": self._gathering_tab}[self.section]()
        self.body.addStretch(1)
        if self.chart_window and self.chart_window.isVisible():
            self.chart_window.refresh()

    # --- Crafting: the recipes and what you have ---
    def _recipes_tab(self):
        f, crafting = self.fonts, self.crafting
        query = self.search.text().strip().lower()
        prof = crafting.professions.get(crafting.profession, {}).get("name", crafting.profession)
        who = crafting.levels_character or tr("tu personaje")
        read = crafting.inventory.read_at(crafting.levels_character)
        level = crafting.level()
        if read:
            self._hint(tr("Recetas de {0} con lo que tiene {1} (bolsa y almacén juntos, leídos del juego {2}; se "
                          "actualiza al craftear, recoger o comprar). Clic en una receta para ver sus materiales; "
                          "clic en un material para ver su precio en el mercado y dónde recolectarlo.", prof, who, _ago(read)))
        else:
            self._hint(tr("Entra al juego con {0} con AionDex abierto: lee su bolsa y su almacén y aquí verás "
                          "cuánto tienes de cada material y cuántas veces puedes hacer cada receta.", who), theme.WARN)
        conf = crafting.config.setdefault("recipes_view", {})
        options = QHBoxLayout()
        for key, text, tip in (("ready", tr("Solo las que puedo hacer ya"), tr("Tienes todos sus materiales para 1 "
                                                                           "intento")),
                               ("level", tr("Solo con mi nivel ({0})", level_text(level)) if level else tr("Solo con mi nivel"),
                                tr("Las recetas de tu nivel de la profesión o menos"))):
            box = QCheckBox(text)
            box.setToolTip(tip)
            box.setChecked(bool(conf.get(key)))
            box.setEnabled(key != "level" or level is not None)
            box.toggled.connect(lambda on, k=key: (conf.__setitem__(k, on), crafting.settings.save(),
                                                   QTimer.singleShot(0, self.refresh)))
            options.addWidget(box)
        options.addStretch(1)
        self.body.addLayout(options)
        rows = crafting.recipe_rows(self.ctl.crafting_faction(), bound=True)
        if query:
            rows = [r for r in rows if query in r.product.lower() or any(query in m.name.lower() for m in r.materials)]
        makes = {r.product: crafting.makes(r) for r in rows}
        if conf.get("ready"):
            rows = [r for r in rows if (makes[r.product] or (0,))[0] > 0]
        if conf.get("level") and level is not None:
            rows = [r for r in rows if r.can_craft]
        rows.sort(key=lambda r: (r.can_craft is False, -(makes[r.product] or (0,))[0], -r.needs))   # yours, makeable
        if not rows:
            self._hint(tr("Nada coincide con «{0}».", self.search.text().strip()) if query else
                       tr("Ninguna receta con esos filtros."))
            return
        for row in rows:
            self.body.addWidget(self._recipe_card(row, makes[row.product]))
            if row.product in self._open_recipes:
                self.body.addWidget(self._recipe_materials(row, makes[row.product]))

    def _recipe_card(self, row, makes):
        f = self.fonts
        card = QFrame()
        card.setObjectName("Row")
        card.setProperty("open", row.product in self._open_recipes)
        card.setCursor(Qt.PointingHandCursor)
        card.mousePressEvent = lambda e, p=row.product: (self._open_recipes.symmetric_difference_update({p}),
                                                         QTimer.singleShot(0, self.refresh))
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(1)
        head = QHBoxLayout()
        color = QColor(GRADE_COLOR.get(row.recipe.get("grade"), "#D6DAE3"))
        arrow = "▾ " if row.product in self._open_recipes else "▸ "
        name = row.product.replace(" (Bound)", "") + (tr(" · ligado") if "(Bound)" in row.product else "")
        head.addWidget(label(arrow + name + (f"  × {row.count}" if row.count > 1 else ""), f.name, color, elide=True),
                       1)
        need = label(level_text(row.needs), f.small, theme.COMBAT if row.can_craft is False else theme.MUTED)
        need.setToolTip(tr("El nivel de la profesión que pide") +
                        (tr(" (aún no lo tienes)") if row.can_craft is False else ""))
        head.addWidget(need)
        if makes is None:
            head.addWidget(label("—", f.name, theme.MUTED))
        else:
            times, limit = makes
            text = tr("Puedes hacer {0}", times) if times else tr("Te faltan materiales")
            badge = label(text, f.name, theme.GAIN if times else theme.MUTED)
            if times and len(row.materials) > 1:
                badge.setToolTip(tr("Te limita {0}", limit.replace(' (Bound)', '')))
            head.addWidget(badge)
        lay.addLayout(head)
        bits = []
        for m in row.materials:
            owned = self.crafting.owned(m.name)
            have = owned[0] if owned else None
            short = m.name.replace(" (Bound)", "")
            if have is None:
                bits.append(f"{m.count}× {short}")
            else:
                ok = have >= m.count
                bits.append(f"<span style='color:{(theme.GAIN if ok else theme.COMBAT).name()}'>{m.count}× {short} "
                            f"({have:,})</span>")
        lay.addWidget(label(" · ".join(bits), f.small, theme.MUTED))
        return card

    def _recipe_materials(self, row, makes):
        """One recipe's materials: per attempt, what you have (bag + storage), the market price, what is missing."""
        f = self.fonts
        frame = QFrame()
        frame.setObjectName("Detail")
        col = QVBoxLayout(frame)
        col.setContentsMargins(16, 10, 16, 10)
        col.setSpacing(6)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(3)
        for c, text in enumerate(("Material", tr("Por intento"), tr("Tienes"), tr("Precio mercado"), tr("Para 10 intentos"))):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignLeft if c == 0 else Qt.AlignRight)
        prices = self.crafting.latest_prices()
        for r, m in enumerate(row.materials, start=1):
            grid.addWidget(self._material_name(m.name, m.name.replace(" (Bound)", "")), r, 0)
            grid.addWidget(label(f"{m.count}", f.body, theme.TEXT), r, 1, Qt.AlignRight)
            grid.addWidget(self._have_cell(m.name, m.count), r, 2, Qt.AlignRight)
            price = prices.get(m.name)
            cell = label(_kinah(price[0]) if price else ("NPC " + _kinah(m.unit) if m.how == "npc" else "—"), f.body,
                         theme.TEXT if price or m.how == "npc" else theme.MUTED)
            if price:
                cell.setToolTip(tr("El más bajo, {0}", _ago(price[1])))
            grid.addWidget(cell, r, 3, Qt.AlignRight)
            owned = self.crafting.owned(m.name)
            missing = max(0, m.count * 10 - (owned[0] if owned else 0))
            grid.addWidget(label(tr("te faltan {0:,}", missing) if missing else "✓", f.small,
                                 theme.WARN if missing else theme.GAIN), r, 4, Qt.AlignRight)
        grid.setColumnStretch(0, 1)
        col.addLayout(grid)
        text, tip = self._makes_text(row)
        note = label(text, f.small, theme.MUTED)
        note.setToolTip(tip)
        col.addWidget(note)
        market = QPushButton(tr("Ver en Market: ¿vale la pena venderlo?"))
        market.setProperty("flat", True)
        market.clicked.connect(lambda _=False, p=row.product: self._find_in_market(p))
        col.addWidget(market, 0, Qt.AlignLeft)
        return frame

    # --- Levear ---
    def _leveling_tab(self):
        f, crafting = self.fonts, self.crafting
        query = self.search.text().strip().lower()
        prof = crafting.professions.get(crafting.profession, {}).get("name", crafting.profession)
        level = crafting.level()
        if level is None:
            self._hint(tr("Falta tu nivel de {0}: entra al juego con el personaje (AionDex lo lee) o escríbelo "
                          "arriba.", prof), theme.WARN)
            return
        rows = crafting.recipe_rows(self.ctl.crafting_faction(), bound=True)
        plan = crafting.leveling_plan(rows)
        if query:
            plan = [e for e in plan if query in e[0].product.lower()]
        card = QFrame()
        card.setObjectName("Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(3)
        exp_now = crafting.game_exp()
        needed = crafting.level_exp()
        line = f"{prof}: {level_text(level)}"
        if exp_now is not None:
            line += tr(" · {0:,} de experiencia en el nivel", exp_now)
            if needed:
                line += tr(" de {0:,} (faltan {1:,})", needed, max(0, needed - exp_now))
        col.addWidget(label(line, f.name, theme.TEXT))
        best = next((e for e in plan if e[1]), None)
        wrap = lambda w: (w.setWordWrap(True), w)[1]          # noqa: E731 - long lines wrap in the card
        if best:
            row, exp, how, value, per = best
            makes = crafting.makes(row)
            left = max(0, needed - exp_now) if needed and exp_now is not None else None
            tries = tr(" · unos {0:.0f} intentos para subir", left / exp) if left and exp else ""
            col.addWidget(wrap(label(tr("Para subir ahora: {0}  —  {1:.0f} exp por intento ({2}) · {3} por intento "
                                        "· {4} por punto de exp{5}", row.product.replace(' (Bound)', ''), exp, tr(how), _kinah(value, sign=True), _kinah(per, sign=True), tries), f.body, theme.GAIN)))
            if makes is not None:
                col.addWidget(label(tr("Con lo que tienes: {0} intentos", makes[0]) if makes[0] else
                                    tr("Con lo que tienes: ninguno (mira sus materiales en Crafting)"), f.small,
                                    theme.TEXT if makes[0] else theme.WARN))
        col.addWidget(wrap(label(tr("Cuenta lo que te cuesta cada intento (o lo que ganas vendiéndolo) entre la "
                                    "experiencia que da: las recetas muy por debajo de tu nivel dan poca. La "
                                    "experiencia de cada receta la aprende AionDex cuando la crafteas; las que aún "
                                    "no hiciste se estiman con las de nivel parecido. Lo que pide cada nivel se "
                                    "aprende al subirlo."),
                                 f.small, theme.MUTED)))
        self.body.addWidget(card)
        if not plan:
            self._hint(tr("No hay recetas de tu nivel con precio de materiales: escanea el mercado (Market) o mira "
                          "sus materiales en Crafting."))
            return
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        heads = (tr("Receta"), tr("Pide"), tr("Éxito"), tr("Exp por intento"), tr("Por intento"), tr("Kinah por exp"), tr("Puedes hacer"))
        for c, text in enumerate(heads):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignLeft if c == 0 else Qt.AlignRight)
        for r, (row, exp, how, value, per) in enumerate(plan[:30], start=1):
            name = label(row.product.replace(" (Bound)", ""), f.body,
                         QColor(GRADE_COLOR.get(row.recipe.get("grade"), "#D6DAE3")))
            grid.addWidget(name, r, 0)
            grid.addWidget(label(level_text(row.needs), f.small, theme.MUTED), r, 1, Qt.AlignRight)
            grid.addWidget(label("—" if row.success is None else f"{row.success:.0%}", f.body, theme.TEXT), r, 2,
                           Qt.AlignRight)
            cell = label("?" if not exp else f"{exp:.0f}" if how == "visto" else f"~{exp:.0f}", f.name,
                         theme.TEXT if how == "visto" else theme.MUTED)
            cell.setToolTip(tr("Lo que te dio") if how == "visto" else
                            tr("Estimada con las recetas de nivel parecido que ya crafteaste") if how else
                            tr("Aún no se sabe: craftéala una vez y AionDex lo aprende"))
            grid.addWidget(cell, r, 3, Qt.AlignRight)
            grid.addWidget(label(_kinah(value, sign=True), f.body, theme.GAIN if value >= 0 else theme.COMBAT), r, 4,
                           Qt.AlignRight)
            grid.addWidget(label("—" if per is None else _kinah(per, sign=True), f.name,
                                 theme.GAIN if per is not None and per >= 0 else theme.TEXT), r, 5, Qt.AlignRight)
            makes = self.crafting.makes(row)
            grid.addWidget(label("—" if makes is None else str(makes[0]), f.body,
                                 theme.GAIN if makes and makes[0] else theme.MUTED), r, 6, Qt.AlignRight)
        grid.setColumnStretch(0, 1)
        self.body.addLayout(grid)

    # --- Gathering ---
    def _gathering_tab(self):
        f, g, crafting = self.fonts, self.ctl.gathering, self.crafting
        query = self.search.text().strip().lower()
        recent = [n for n in crafting.config.get("recent_gather", []) if g.has(n)]
        if recent and not query:
            self.body.addWidget(SectionTitle(tr("Vistos hace poco"), f))
            row = QHBoxLayout()
            row.setSpacing(6)
            for name in recent[:8]:
                button = QPushButton(f"⌖ {name}")
                button.setProperty("flat", True)
                button.clicked.connect(lambda _=False, n=name: self.ctl.open_gather_map(n))
                row.addWidget(button)
            row.addStretch(1)
            self.body.addLayout(row)
        names = sorted(n for n in g.materials if g.has(n))
        if query:
            names = [n for n in names if query in n.lower() or query in (g.info(n).get("node") or "").lower()]
        self._hint(tr("{0} materiales que se recolectan o sueltan monstruos", len(names)) +
                   (tr(" con «{0}»", self.search.text().strip()) if query else "") +
                   tr(". Clic en uno para abrir su mapa: sus puntos, la mejor zona y una ruta (también en el "
                      "minimapa)."))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for c, text in enumerate(("Material", tr("De"), tr("Mapas"), tr("Tienes"), tr("Mercado"))):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignRight if c >= 3 else Qt.AlignLeft)
        prices = crafting.latest_prices()
        for r, name in enumerate(names[:200], start=1):
            info = g.info(name)
            link = label(f"⌖ {name}", f.body, theme.ACCENT)
            link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            link.setCursor(Qt.PointingHandCursor)
            link.mousePressEvent = lambda e, n=name: (self.ctl.open_gather_map(n)
                                                      if e.button() == Qt.LeftButton else None)
            grid.addWidget(link, r, 0)
            if g.is_drop(name):
                best = g.best_drop(name)
                where = tr("lo sueltan monstruos · {0} {1:g}%", best[0]['name'], best[0]['chance']) if best else \
                    tr("lo sueltan monstruos")
            else:
                where = tr("nodo {0}", info.get('node') or '—') + (tr(" · nivel {0}", info['level']) if info.get("level")
                                                               else "")
            where_label = label(where, f.small, theme.MUTED, elide=True)
            where_label.setMaximumWidth(330)
            grid.addWidget(where_label, r, 1)
            maps = [(MAP_NAMES.get(m["map"], m["map"]).split(" (")[0], len(m["points"]))
                    for m in info.get("maps", []) if m["points"]]
            grid.addWidget(label(" · ".join(f"{n} {c}" for n, c in maps[:3]) or "—", f.small, theme.TEXT,
                                 elide=True), r, 2)
            grid.addWidget(self._have_cell(name), r, 3, Qt.AlignRight)
            price = prices.get(name)
            grid.addWidget(label(_kinah(price[0]) if price else "—", f.body, theme.TEXT if price else theme.MUTED),
                           r, 4, Qt.AlignRight)
        if len(names) > 200:
            grid.addWidget(label(tr("y {0} más: escribe para buscar", len(names) - 200), f.small, theme.MUTED), 201, 0)
        grid.setColumnStretch(2, 1)
        self.body.addLayout(grid)

    def _hint(self, text, color=None):
        hint = label(text, self.fonts.body, color or theme.MUTED)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        return hint

    def _how_to(self):
        prof = self.crafting.professions.get(self.crafting.profession, {})
        hk = self.ctl.settings["hotkeys"].get("crafting_scan")
        return (tr("En el juego, abre el mercado (Exchange) en «{0}» y pulsa Escanear", prof.get('market', '…'))
                + (tr(" o {0}", hk) if hk else "") + tr(" en cada página. Escanea también la pantalla de ofertas de los "
                                                  "objetos que te interesen (así se ve cuánto se vende entre "
                                                  "muestreos) y la de los materiales que no da un NPC. Luego, en "
                                                  "Capturas, marca las buenas y pulsa Procesar. Las de tus ventas "
                                                  "(Settlement) y de lo que tienes a la venta (List) van solas a "
                                                  "Ventas."))

    # --- Rentabilidad ---
    def _profit_tab(self):
        f, crafting = self.fonts, self.crafting
        query = self.search.text().strip().lower()
        prof = crafting.professions.get(crafting.profession, {}).get("name", crafting.profession)
        level = crafting.level()
        if not crafting.pool:
            self._hint(tr("Aún no hay muestreos. ") + self._how_to())
        else:
            last = crafting.pool[-1]
            others = crafting.shared_count
            whose = tr("tus muestreos y {0} de otros usuarios", others) if others else tr("tus muestreos")
            hint = self._hint(tr("Precios de {0} (el último: {1}, {2}). Ganancia media por intento, con tu % de "
                                 "éxito y el {3:.0%} del mercado.  ⓘ", whose, _clock(last['at']), _ago(last['at']), crafting.fee))
            hint.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            hint.setToolTip(tr("Ganancia por intento = lo que te paga el mercado (precio − {0:.0%}) × tu "
                               "probabilidad de éxito − los materiales (si el craft falla, se "
                               "pierden).<br><b>Recolectando</b>: lo que se recolecta no te cuesta y lo que se "
                               "fabrica, a su coste.<br><b>Comprando</b>: todos los materiales al precio del "
                               "mercado (lo de NPC a su precio; lo procesado, por el camino más barato).<br>Clic en "
                               "una receta para ver sus materiales.", crafting.fee))
        if level is None:
            self._hint(tr("Falta tu nivel de {0}: escríbelo arriba (o craftea algo de {1} y AionDex lo aprende). "
                          "Mientras, se usa el % de éxito más bajo.", prof, prof), theme.WARN)
        rows = crafting.recipe_rows(self.ctl.crafting_faction())
        if query:
            rows = [r for r in rows if query in r.product.lower() or any(query in m.name.lower() for m in r.materials)]
        picks = crafting.recommend(rows)
        self._level_pick = crafting.leveling_pick(rows)
        if picks and not query:
            self.body.addWidget(self._picks(picks, level, prof))
        elif level is not None and not query:
            ahead = sorted((r for r in rows if r.can_craft is False and r.profit is not None and r.profit > 0),
                           key=lambda r: (r.needs, -r.profit))
            text = (tr("Con tu nivel de {0} ({1}) ninguna receta deja ganancia con los precios de tus muestreos.", prof, level_text(level)))
            if ahead:
                first = ahead[0]
                text += (tr(" La primera que sí: {0} ({1}), {2} por intento al llegar.", first.product, level_text(first.needs), _kinah(first.profit, sign=True)))
            pick = self._level_pick
            if pick is not None:
                cost = pick.profit if pick.profit is not None else -pick.cost
                text += (tr(" Mientras subes, lo que cuenta es la experiencia: la marcada con EXP, {0} ({1} por "
                            "intento).", pick.product, _kinah(cost, sign=True)))
            self._hint(text, theme.WARN)
        options = QHBoxLayout()
        only = QCheckBox(tr("Solo lo que puedo craftear con mi nivel ({0})", level_text(level)) if level is not None else
                         tr("Solo lo que puedo craftear con mi nivel"))
        only.setToolTip(tr("Marcado: solo las recetas de tu nivel o menos. Desmarcado: todas; las de más nivel "
                           "salen en gris con el nivel que piden."))
        if level is None:
            only.setEnabled(False)
            only.setToolTip(tr("Aún no se sabe tu nivel: entra con el personaje (AionDex lo lee del juego) o "
                               "escríbelo arriba."))
        only.setChecked(level is not None and crafting.config.get("only_craftable", True))
        only.toggled.connect(lambda on: (crafting.set_option("only_craftable", on), self.refresh()))
        options.addWidget(only)
        everything = QCheckBox(tr("Mostrar también los que no tienen precio"))
        everything.setChecked(bool(crafting.config.get("show_unpriced")))
        everything.toggled.connect(lambda on: (crafting.set_option("show_unpriced", on), self.refresh()))
        options.addWidget(everything)
        options.addStretch(1)
        self.body.addLayout(options)

        if only.isChecked():
            rows = [r for r in rows if r.can_craft]
        priced = [r for r in rows if r.sell is not None]
        shown = rows if everything.isChecked() else priced
        self._shown, self._shown_picks = [r for r in shown if r.sell is not None], picks
        if not shown:
            if rows and not priced and crafting.samples:
                self._hint(tr("Ningún producto de esta profesión aparece en los muestreos: escanea su categoría del "
                              "mercado o marca «Mostrar también los que no tienen precio»."))
            elif query:
                self._hint(tr("Nada coincide con «{0}».", self.search.text().strip()))
            return
        self.body.addWidget(self._header_row())
        for row in shown:
            self.body.addWidget(self._product_row(row))
            if row.product in self._open:
                self.body.addWidget(self._detail(row))
        self._materials(query)

    def _market_materials(self):
        """Market > Materiales: every material's price (and its week), the ones not priced yet to scan."""
        crafting = self.crafting
        query = self.search.text().strip().lower()
        if not crafting.pool:
            self._hint(tr("Aún no hay muestreos. ") + self._how_to())
        else:
            last = crafting.pool[-1]
            others = crafting.shared_count
            whose = tr("tus muestreos y {0} de otros usuarios", others) if others else tr("tus muestreos")
            self._hint(tr("Precios de {0} (el último: {1}, {2}): el más bajo de cada material y su media de los "
                          "últimos 7 días (en verde, más barato que su media). Clic en un material para ver su "
                          "precio y dónde recolectarlo; «Gráfica de precio» lo muestra en el tiempo.", whose, _clock(last['at']), _ago(last['at'])))
        self._materials_tab(query)

    def _gear_tab(self):
        """Market > Equipo: every gear piece with a price in the samples, and whether it is an upgrade for the
        character (gear.verdict against the gear AionDex read), for Mejoras."""
        from ..gear import POSITIONS, SLOTS, verdict
        f, crafting, ctl = self.fonts, self.crafting, self.ctl
        conf = crafting.config.setdefault("gear_view", {})
        query = self.search.text().strip().lower()
        prices = ctl.gear_prices()
        who = crafting.levels_character or ctl.character_name()
        gear = ctl.settings.get("gear", {}).get(who) if who else None
        hk = ctl.settings["hotkeys"].get("crafting_scan")
        self._hint(tr("Precios del equipo para Mejoras. En el juego, abre el mercado en una categoría de equipo "
                      "(Collar, Aretes, Anillo, Grebas…) y pulsa Escanear") + (tr(" o {0}", hk) if hk else "") + tr(" en cada página; en "
                                                                                                 "Capturas, Procesar. AionDex "
                                                                                                 "lee el nombre, las ofertas "
                                                                                                 "y el precio más bajo de "
                                                                                                 "cada pieza y te dice si te "
                                                                                                 "sirve") + (tr(" a {0}", who) if who else "") + ".")
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(label(tr("Pieza"), f.small, theme.MUTED))
        kind = QComboBox()
        kind.addItem(tr("Todas"), "all")
        for key in dict.fromkeys(SLOTS[p][0] for p in sorted(SLOTS)):
            kind.addItem(SLOTS[POSITIONS[key][0]][1], key)
        kind.setCurrentIndex(max(0, kind.findData(conf.get("slot", "all"))))
        kind.currentIndexChanged.connect(lambda _: self._gear_option("slot", kind.currentData()))
        bar.addWidget(kind)
        useful = QCheckBox(tr("Solo las que le sirven a {0}", who) if who else tr("Solo las que me sirven"))
        useful.setEnabled(gear is not None)
        useful.setChecked(bool(conf.get("useful")) and gear is not None)
        useful.toggled.connect(lambda on: self._gear_option("useful", on))
        bar.addWidget(useful)
        bar.addStretch(1)
        rows = []
        for name, (lowest, at) in prices.items():
            item = ctl.gear_db.named(name, (gear or {}).get("faction"))
            if item is None or query and query not in name.lower():
                continue
            if conf.get("slot", "all") != "all" and item.slot != conf["slot"]:
                continue
            v = verdict(item, gear, ctl.gear_db) if gear else None
            if conf.get("useful") and gear and (v is None or v.kind not in ("upgrade", "base")):
                continue
            rows.append((item, lowest, at, v))
        bar.addWidget(label(tr("{0} piezas con precio", len(rows)), f.small, theme.MUTED))
        self.body.addLayout(bar)
        if not prices:
            self._hint(tr("Aún no hay precios de equipo: escanea una lista de equipo del mercado."), theme.WARN)
            return
        if not rows:
            self._hint(tr("Nada con esos filtros."))
            return
        order = {key: i for i, key in enumerate(dict.fromkeys(SLOTS[p][0] for p in sorted(SLOTS)))}
        rows.sort(key=lambda r: (order.get(r[0].slot, 99), -r[0].level, r[1]))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for c, text in enumerate((tr("Equipo"), tr("Pieza"), tr("Nivel"), tr("Más bajo"), tr("Media 7 días"), tr("Ofertas"),
                                  tr("Para {0}", who) if who else tr("Para ti"))):
            grid.addWidget(label(text, f.small, theme.ACCENT), 0, c, Qt.AlignRight if 1 < c < 6 else Qt.AlignLeft)
        for r, (item, lowest, at, v) in enumerate(rows, start=1):
            grid.addWidget(label(item.name, f.name, QColor(GRADE_COLOR.get(item.grade, "#D6DAE3"))), r, 0)
            positions = POSITIONS.get(item.slot) or [0]
            grid.addWidget(label(SLOTS.get(positions[0], ("", item.category))[1], f.body, theme.MUTED), r, 1)
            grid.addWidget(label(str(item.level), f.body, theme.TEXT), r, 2, Qt.AlignRight)
            price = label(_kinah(lowest), f.name, theme.TEXT)
            price.setToolTip(tr("precio {0}", _ago(at)) + (tr(" (de otro usuario)") if crafting.price_is_shared(item.name) else ""))
            price.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            grid.addWidget(price, r, 3, Qt.AlignRight)
            avg, n = crafting.week_average(item.name)
            if avg and n >= 2:
                diff = (lowest - avg) / avg
                color = theme.GAIN if diff <= -0.05 else theme.COMBAT if diff >= 0.05 else theme.MUTED
                grid.addWidget(label(f"{_kinah(avg)} ({diff:+.0%})", f.body, color), r, 4, Qt.AlignRight)
            else:
                grid.addWidget(label("—", f.body, theme.MUTED), r, 4, Qt.AlignRight)
            listings = crafting._latest_field(item.name, "listings")
            grid.addWidget(label("—" if listings is None else str(listings), f.body, theme.TEXT), r, 5, Qt.AlignRight)
            if v is not None:
                text, color = VERDICT_TEXT.get(v.kind, (v.kind, "#8a93a8"))
                cell = label(text, f.small, QColor(color))
                cell.setToolTip(v.text)
                cell.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            else:
                cell = label("—", f.small, theme.MUTED)
            grid.addWidget(cell, r, 6)
            grid.addWidget(self._chart_button(item.name), r, 7)
        grid.setColumnStretch(6, 1)
        self.body.addLayout(grid)
        link = QPushButton(tr("Abrir Mejoras"))
        link.clicked.connect(ctl.open_upgrades)
        self.body.addWidget(link, 0, Qt.AlignLeft)

    def _gear_option(self, key, value):
        self.crafting.config.setdefault("gear_view", {})[key] = value
        self.crafting.settings.save()
        QTimer.singleShot(0, self.refresh)

    def _materials_tab(self, query):
        """«Materiales»: every material the recipes use, with filters (kind, the profession that uses it, only
        priced, only selling) and every column sortable with a click on its title (another click reverses it).
        Materials with no price yet stay at the end (or out, with «Solo con precio») so you know what to scan."""
        f, crafting = self.fonts, self.crafting
        conf = crafting.config.setdefault("materials", {})
        used_by = {}
        for r in crafting.recipes:
            for m in r["materials"]:
                used_by.setdefault(m["name"], set()).add(r["profession"])
        rows = crafting.material_rows(MATERIALS, with_unpriced=True)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(label(tr("Tipo"), f.small, theme.MUTED))
        kind = QComboBox()
        for key, text in MAT_KINDS:
            kind.addItem(text, key)
        kind.setCurrentIndex(max(0, kind.findData(conf.get("kind", "all"))))
        kind.currentIndexChanged.connect(lambda _: self._mat_option("kind", kind.currentData()))
        bar.addWidget(kind)
        bar.addWidget(label(tr("Lo usa"), f.small, theme.MUTED))
        prof = QComboBox()
        prof.addItem(tr("Cualquier profesión"), "all")
        for key, info in crafting.professions.items():
            if key != MATERIALS:
                prof.addItem(info["name"], key)
        prof.setCurrentIndex(max(0, prof.findData(conf.get("prof", "all"))))
        prof.currentIndexChanged.connect(lambda _: self._mat_option("prof", prof.currentData()))
        bar.addWidget(prof)
        for key, text, tip in (("priced", tr("Solo con precio"), tr("Ocultar los que aún no escaneaste")),
                               ("selling", tr("Solo los que se venden"), tr("Los que se vieron venderse entre muestreos"))):
            box = QCheckBox(text)
            box.setToolTip(tip)
            box.setChecked(bool(conf.get(key)))
            box.toggled.connect(lambda on, k=key: self._mat_option(k, on))
            bar.addWidget(box)
        bar.addStretch(1)
        if conf.get("kind", "all") == "farm":
            rows = [m for m in rows if m[1] in FARMED]
        elif conf.get("kind", "all") != "all":
            rows = [m for m in rows if m[1] == conf["kind"]]
        if conf.get("prof", "all") != "all":
            rows = [m for m in rows if conf["prof"] in used_by.get(m[0], set())]
        if conf.get("priced"):
            rows = [m for m in rows if m[2] is not None]
        if conf.get("selling"):
            rows = [m for m in rows if m[5].verdict()[1] == "good"]
        if query:
            rows = [m for m in rows if query in m[0].lower() or any(query in p.lower() for p in m[7])]
        priced = sum(1 for m in rows if m[2] is not None)
        bar.addWidget(label(tr("{0} materiales · {1} con precio", len(rows), priced), f.small, theme.MUTED))
        self.body.addLayout(bar)
        if not rows:
            self._hint(tr("Nada coincide con «{0}».", self.search.text().strip()) if query else
                       tr("Ningún material con esos filtros."))
            return
        key, flip = conf.get("sort", "kind"), conf.get("flip", False)
        value, descending = self._mat_sort_keys().get(key, self._mat_sort_keys()["kind"])
        known = [m for m in rows if value(m) is not None]
        unknown = [m for m in rows if value(m) is None]
        known.sort(key=lambda m: (value(m), m[0].lower()), reverse=descending != flip)
        self.body.addLayout(self._material_grid(known + sorted(unknown, key=lambda m: m[0].lower()), key, flip))

    def _mat_option(self, key, value):
        self.crafting.config.setdefault("materials", {})[key] = value
        self.crafting.settings.save()
        QTimer.singleShot(0, self.refresh)

    def _mat_sort(self, key):
        conf = self.crafting.config.setdefault("materials", {})
        if conf.get("sort", "kind") == key:
            conf["flip"] = not conf.get("flip", False)
        else:
            conf["sort"], conf["flip"] = key, False
        self.crafting.settings.save()
        QTimer.singleShot(0, self.refresh)

    def _mat_sort_keys(self):
        """column -> (value of a material row, highest first by default); None values always go last."""
        crafting = self.crafting

        def owned(m):
            o = crafting.owned(m[0])
            return o[0] if o else None

        def change(m):
            avg, n = crafting.week_average(m[0])
            return (m[2] - avg) / avg if m[2] is not None and avg and n >= 2 else None

        def sales(m):
            s = m[5]
            return s.per_day if s.per_day is not None else (s.sold if s.observations >= 2 else None)

        order = {k: i for i, (k, _) in enumerate(MAT_KINDS)}
        return {"name": (lambda m: m[0].lower(), False),
                "kind": (lambda m: (order.get("farm" if m[1] in FARMED else m[1], 9), m[0].lower()), False),
                "have": (owned, True),
                "price": (lambda m: m[2], True),
                "avg": (change, False),
                "listings": (lambda m: m[4], True),
                "supply": (lambda m: m[6][0] if m[6] else None, True),
                "sales": (sales, True),
                "used": (lambda m: len(m[7]), True)}

    def _material_grid(self, mats, current, flip):
        f, crafting = self.fonts, self.crafting
        keys = self._mat_sort_keys()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        heads = (("Material", "name"), (tr("Tipo"), "kind"), (tr("Tienes"), "have"), (tr("Más bajo"), "price"),
                 (tr("Media 7 días"), "avg"), (tr("Ofertas"), "listings"), (tr("A la venta"), "supply"), (tr("¿Se vende?"), "sales"),
                 (tr("Lo usan"), "used"))
        for c, (text, key) in enumerate(heads):
            active = key == current
            arrow = (" ▾" if keys[key][1] != flip else " ▴") if active else ""
            head = label(text + arrow, f.small, theme.TEXT if active else theme.ACCENT)
            head.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            head.setCursor(Qt.PointingHandCursor)
            head.setToolTip(tr("Ordenar por esta columna (otro clic invierte el orden)"))
            head.mousePressEvent = lambda e, k=key: self._mat_sort(k)
            grid.addWidget(head, 0, c, Qt.AlignRight if 1 < c < 8 else Qt.AlignLeft)
        for r, (name, source, lowest, at, listings, sales, supply, products) in enumerate(mats, start=1):
            grid.addWidget(self._material_name(name, name, QColor(GRADE_COLOR.get(self._grade(name), "#D6DAE3"))),
                           r, 0)
            text, color = MAT_KIND_CHIP.get(source, ("—", "#8a93a6"))
            chip = _chip(text, color, f)
            where = crafting.source_text(name)
            if where:
                chip.setToolTip(where)
                chip.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            grid.addWidget(chip, r, 1)
            grid.addWidget(self._have_cell(name), r, 2, Qt.AlignRight)
            if lowest is None:
                grid.addWidget(label(tr("sin precio"), f.small, theme.WARN), r, 3, Qt.AlignRight)
            else:
                price = label(_kinah(lowest), f.name, theme.TEXT)
                shared = crafting.price_is_shared(name)
                price.setToolTip(tr("precio {0}", _ago(at)) + (tr(" (de otro usuario)") if shared else ""))
                grid.addWidget(price, r, 3, Qt.AlignRight)
            avg, n = crafting.week_average(name)
            if lowest is not None and avg is not None and n >= 2:
                diff = (lowest - avg) / avg if avg else 0
                color = theme.GAIN if diff <= -0.05 else theme.COMBAT if diff >= 0.05 else theme.MUTED
                cell = label(f"{_kinah(avg)} ({diff:+.0%})", f.body, color)
                cell.setToolTip(tr("Media de {0} lecturas de los últimos 7 días; el precio de ahora está {1:+.0%} "
                                   "respecto a ella ({2} para comprar)", n, diff, 'barato' if diff < 0 else 'caro'))
            else:
                cell = label("—", f.body, theme.MUTED)
            grid.addWidget(cell, r, 4, Qt.AlignRight)
            grid.addWidget(label("—" if listings is None else str(listings), f.body, theme.TEXT), r, 5, Qt.AlignRight)
            units = tr("{0}{1} uds.", supply[0], '+' if supply[2] else '') if supply else "—"
            grid.addWidget(label(units, f.body, theme.MUTED), r, 6, Qt.AlignRight)
            verdict, kind = sales.verdict()
            grid.addWidget(label(verdict, f.small, VERDICT_COLOR[kind]), r, 7, Qt.AlignRight)
            used = label(f"{len(products)} {'receta' if len(products) == 1 else 'recetas'}", f.small, theme.MUTED)
            used.setToolTip(tr("Lo usan: ") + ", ".join(products))
            used.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            grid.addWidget(used, r, 8)
            if lowest is not None:
                grid.addWidget(self._chart_button(name), r, 9)
        grid.setColumnStretch(8, 1)
        return grid

    def _materials(self, query):
        """The profession's materials with a price in your samples: what you scanned that no recipe makes (Lesser
        Manastone...) shows here too, not only inside the recipes that use it."""
        f, crafting = self.fonts, self.crafting
        mats = [m for m in crafting.material_rows() if not query or query in m[0].lower()]
        if not mats:
            return
        prof = crafting.professions.get(crafting.profession, {}).get("name", crafting.profession)
        self.body.addWidget(SectionTitle(tr("Materiales de {0} en tus muestreos", prof), f))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for c, text in enumerate(("Material", "", tr("Tienes"), tr("Más bajo"), tr("Ofertas"), tr("A la venta"), tr("¿Se vende?"),
                                  tr("Lo usan"), "")):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignRight if 1 < c < 7 else Qt.AlignLeft)
        for r, (name, source, lowest, at, listings, sales, supply, products) in enumerate(mats, start=1):
            grid.addWidget(self._material_name(name, name, QColor(GRADE_COLOR.get(self._grade(name), "#D6DAE3"))),
                           r, 0)
            text, color = SOURCE.get(source if source != "market" else "market", SOURCE["unknown"])
            chip = _chip(text if source != "market" else tr("Del juego / mercado"), color, f)
            where = crafting.source_text(name)
            if where:
                chip.setToolTip(where)
                chip.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            grid.addWidget(chip, r, 1)
            grid.addWidget(self._have_cell(name), r, 2, Qt.AlignRight)
            price = label(_kinah(lowest), f.name, theme.TEXT)
            price.setToolTip(tr("precio {0}", _ago(at)))
            grid.addWidget(price, r, 3, Qt.AlignRight)
            grid.addWidget(label("—" if listings is None else str(listings), f.body, theme.TEXT), r, 4, Qt.AlignRight)
            units = tr("{0}{1} uds.", supply[0], '+' if supply[2] else '') if supply else "—"
            grid.addWidget(label(units, f.body, theme.MUTED), r, 5, Qt.AlignRight)
            verdict, kind = sales.verdict()
            grid.addWidget(label(verdict, f.small, VERDICT_COLOR[kind]), r, 6, Qt.AlignRight)
            used = label(", ".join(products[:2]) + (tr(" y {0} más", len(products) - 2) if len(products) > 2 else ""),
                         f.small, theme.MUTED, elide=True)
            used.setToolTip(tr("Lo usan: ") + ", ".join(products))
            used.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            grid.addWidget(used, r, 7)
            grid.addWidget(self._chart_button(name), r, 8)
        grid.setColumnStretch(7, 1)
        self.body.addLayout(grid)

    def _picks(self, picks, level, prof):
        """The recommendation: what to make at your level (seen selling first), with why."""
        f = self.fonts
        card = QFrame()
        card.setObjectName("Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(3)
        if self.crafting.level_source() == "try":
            who = tr("Si fueras {0} en {1} (eres {2})", level_text(level), prof, level_text(self.crafting.game_level()))
        else:
            who = (tr("Con tu nivel de {0} ({1})", prof, level_text(level)) if level is not None else
                   tr("Al nivel justo de cada receta"))
        col.addWidget(label(tr("{0}, lo que más deja recolectando:", who), f.name, theme.GOLD))
        for i, r in enumerate(picks, start=1):
            sold, kind = r.sales.verdict()
            parts = [tr("{0} por intento", _kinah(r.profit, sign=True)),
                     tr("{0:.0%} de fallo", 1 - r.success) if r.success is not None else "",
                     sold if kind == "good" else tr("aún sin ventas vistas")]
            line = label(f"{i}. {r.product}  —  " + " · ".join(x for x in parts if x), f.body,
                         theme.TEXT if kind == "good" else theme.MUTED)
            col.addWidget(line)
        pick = getattr(self, "_level_pick", None)
        if pick is not None:
            cost = pick.profit if pick.profit is not None else -pick.cost
            col.addWidget(label(tr("Para subir de nivel:  {0} ({1})  —  {2} por intento", pick.product, level_text(pick.needs), _kinah(cost, sign=True)), f.body, theme.GAIN))
        col.addWidget(label(tr("Solo cuenta el precio más bajo de cada material: si necesitas muchos, mira cuántos "
                               "hay a ese precio en su pantalla de ofertas."), f.small, theme.MUTED))
        return card

    def _header_row(self):
        f = self.fonts
        line = QWidget()
        lay = QHBoxLayout(line)
        lay.setContentsMargins(12, 0, 12, 0)
        current = self.crafting.config.get("sort") or "gather"
        flip = self.crafting.config.get("sort_flip", False)
        for text, width in ((tr("Objeto"), None),) + COLUMNS:
            key = SORTABLE[text]
            active = key == current
            arrow = ""
            if active:
                descending = SORT_KEYS[key][1] != flip
                arrow = " ▾" if descending else " ▴"
            cell = (label(text + arrow, f.small, theme.TEXT if active else theme.ACCENT) if width is None else
                    _cell(text + arrow, f.small, theme.TEXT if active else theme.ACCENT, width))
            cell.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            cell.setCursor(Qt.PointingHandCursor)
            cell.setToolTip(tr("Ordenar por esta columna (otro clic invierte el orden)"))
            cell.mousePressEvent = lambda e, k=key: (self.crafting.set_sort(k), self.refresh())
            lay.addWidget(cell, 1 if width is None else 0)
        lay.addSpacing(8 + self._chart_width())
        return line

    def _chart_button(self, name):
        button = QPushButton(tr("Gráfica de precio"))
        button.setProperty("small", True)
        button.setFixedWidth(self._chart_width())
        button.clicked.connect(lambda _=False, n=name: self.open_chart(n))
        return button

    def _chart_width(self):
        if not hasattr(self, "_chart_w"):
            probe = QPushButton(tr("Gráfica de precio"), self)
            probe.hide()                        # only measured (a child of a window not shown yet would show with it)
            probe.setProperty("small", True)
            probe.ensurePolished()
            self._chart_w = probe.sizeHint().width() + 4
            probe.deleteLater()
        return self._chart_w

    def _product_row(self, row):
        f = self.fonts
        frame = QFrame()
        frame.setObjectName("Row")
        frame.setProperty("open", row.product in self._open)
        frame.setCursor(Qt.PointingHandCursor)
        frame.mousePressEvent = lambda e, p=row.product: self._toggle(p) if e.button() == Qt.LeftButton else None
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(12, 7, 12, 7)
        names = QVBoxLayout()
        names.setSpacing(0)
        arrow = "▾" if row.product in self._open else "▸"
        count = f" ×{row.count}" if row.count > 1 else ""
        locked = row.can_craft is False
        title = QHBoxLayout()
        title.setSpacing(6)
        color = theme.MUTED if locked else QColor(GRADE_COLOR.get(row.recipe.get("grade"), "#D6DAE3"))
        if row is getattr(self, "_level_pick", None):
            title.addWidget(label(f"{arrow}  {row.product}{count}", f.name, color))
            title.addWidget(self._exp_badge(row), 0, Qt.AlignVCenter)
            title.addStretch(1)
        else:
            title.addWidget(label(f"{arrow}  {row.product}{count}", f.name, color, elide=True), 1)
        names.addLayout(title)
        supply = ""
        if row.supply:
            units, at_lowest, partial = row.supply
            supply = tr("{0}{1} uds. a la venta", units, '+' if partial else '')
            if at_lowest:
                supply += tr(" ({0} al más bajo)", at_lowest)
        needs = (tr("requiere {0} (tienes {1})", level_text(row.needs), level_text(row.level)) if locked else
                 level_text(row.needs) if row.needs else "")
        makes = self.crafting.makes(row)
        have = tr("tienes para {0}", makes[0]) if makes and makes[0] > 0 else ""
        age = tr("precio de {0}", _ago(row.sell_at)) if row.sell_at else ""
        if age and self.crafting.price_is_shared(row.product):
            age += tr(" (de otro usuario)")
        if row.thin:
            sub = [tr("⚠ solo {0} {1}: precio poco fiable", row.listings, 'oferta' if row.listings == 1 else 'ofertas'),
                   have, needs, age]
        else:
            sub = [have, needs, tr("{0} ofertas", row.listings) if row.listings is not None else "", supply,
                   age or tr("sin precio de mercado")]
        names.addWidget(label(" · ".join(s for s in sub if s), f.small,
                              theme.WARN if locked or row.thin else theme.MUTED, elide=True))
        if row.thin:
            frame.setToolTip(tr("Solo hay {0} a la venta y no se ha visto ninguna venta: el precio más bajo puede "
                                "ser cualquier cosa (alguien pidiendo de más) y nadie pagarlo. No entra en las "
                                "recomendaciones.", row.listings))
        lay.addLayout(names, 1)
        cells = [(_kinah(row.sell), theme.TEXT), (_kinah(row.net), theme.TEXT), _fail_cell(row, locked),
                 _profit_cell(row.profit, row.sell), _profit_cell(row.buy_profit, row.sell)]
        text, kind = row.sales.verdict()
        cells.append((text, VERDICT_COLOR[kind]))
        widgets = [_cell(text, f.name, color, width) for (text, color), (_, width) in zip(cells, COLUMNS)]
        widgets[2].setToolTip(_fail_tip(row))
        widgets[2].setAttribute(Qt.WA_TransparentForMouseEvents, False)
        widgets[2].mousePressEvent = frame.mousePressEvent
        widgets[3].setToolTip(_cost_tip(tr("Recolectando"), row.cost, row.known_cost, row.missing, row))
        widgets[4].setToolTip(_cost_tip(tr("Comprando todo"), row.buy_cost, row.known_buy_cost, row.missing_buy, row))
        for w in widgets[3:5]:
            w.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            w.mousePressEvent = frame.mousePressEvent
        for w in widgets:
            lay.addWidget(w)
        chart = self._chart_button(row.product)
        chart.setEnabled(row.sales.observations > 0)
        lay.addSpacing(8)
        lay.addWidget(chart)
        return frame

    def _detail(self, row):
        """One recipe at a glance: five figures, the materials as a table with totals, then three short notes."""
        f = self.fonts
        frame = QFrame()
        frame.setObjectName("Detail")
        col = QVBoxLayout(frame)
        col.setContentsMargins(16, 12, 16, 12)
        col.setSpacing(10)

        # 1. the figures
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        success = row.success
        sold = tr("× {0} uds.", row.count) if row.count > 1 else tr("1 unidad")
        tiles.addWidget(self._tile(tr("Se vende a"), _kinah(row.sell), sold))
        tiles.addWidget(self._tile(tr("Te pagan"), _kinah(row.net), tr("tras el {0:.0%} del mercado", row.fee)))
        if success is not None:
            best = row.min_fail
            tiles.addWidget(self._tile(tr("Sale bien"), f"{success:.0%}",
                                       tr("falla {0:.0%}", 1 - success) + (tr(" · mín {0:.0%}", best[0]) if best else ""),
                                       tip=_fail_tip(row)))
        for title, value, missing in ((tr("Ganas recolectando"), row.profit, row.missing),
                                      (tr("Ganas comprando"), row.buy_profit, row.missing_buy)):
            if value is None:
                tiles.addWidget(self._tile(title, "?", tr("falta precio de ") + ", ".join(missing)[:40], theme.WARN))
            else:
                tiles.addWidget(self._tile(title, _kinah(value, sign=True), tr("por intento, de media"),
                                           theme.GAIN if value > 0 else theme.COMBAT,
                                           tip=self._profit_tip(row, value is row.buy_profit)))
        col.addLayout(tiles)

        # 2. the materials
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        for c, text in ((0, tr("Materiales de 1 intento")), (1, tr("Tienes")), (3, tr("Recolectando")), (4, tr("Comprando"))):
            head = label(text, f.small, theme.MUTED)
            grid.addWidget(head, 0, c, Qt.AlignLeft if c == 0 else Qt.AlignRight)
        r = 1
        for m in row.materials:
            grid.addWidget(self._material_name(m.name, f"{m.count}×  {m.name.replace(' (Bound)', '')}"), r, 0)
            grid.addWidget(self._have_cell(m.name, m.count), r, 1, Qt.AlignRight)
            text, color = SOURCE.get(m.how, SOURCE["unknown"])
            grid.addWidget(_chip(text, color, f), r, 2, Qt.AlignLeft | Qt.AlignVCenter)
            grid.addWidget(self._price_cell(m.unit, m.total, m.how, m.at), r, 3, Qt.AlignRight)
            grid.addWidget(self._price_cell(m.buy, m.buy_total, m.buy_how, m.at), r, 4, Qt.AlignRight)
            if m.source != "npc":
                grid.addWidget(self._price_edit(m), r, 5, Qt.AlignLeft)
            r += 1
            if m.source == "craft":
                grid.addWidget(self._raw_line(m), r, 0, 1, 5)
                r += 1
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #2b3242;")
        grid.addWidget(line, r, 0, 1, 5)
        r += 1
        grid.addWidget(label("Total", f.name, theme.TEXT), r, 0)
        for c, (cost, known, missing) in ((3, (row.cost, row.known_cost, row.missing)),
                                          (4, (row.buy_cost, row.known_buy_cost, row.missing_buy))):
            total = label(_kinah(cost) if cost is not None else _kinah(known) + " + ?", f.name,
                          theme.TEXT if cost is not None else theme.WARN)
            grid.addWidget(total, r, c, Qt.AlignRight)
        grid.setColumnStretch(0, 1)
        col.addLayout(grid)

        # 3. short notes
        notes = QVBoxLayout()
        notes.setSpacing(2)
        notes.addWidget(self._note("▣", *self._makes_text(row)))
        if success is not None:
            curve = row.fail_curve()
            if row.can_craft is False:
                text = (tr("Fallo: pide {0} (tienes {1}); al llegar fallarías un {2:.0%}", level_text(row.needs), level_text(row.level), 1 - success))
            else:
                where = tr("al nivel justo") if row.level is None else tr("con tu nivel ({0})", level_text(row.level))
                text = tr("Fallo: {0:.0%} {1}", 1 - success, where)
                if curve and curve[-1][1] < 1 - success - 0.005:
                    text += tr("  →  {0:.0%} desde {1}", curve[-1][1], level_text(curve[-1][0]))
            notes.addWidget(self._note("✕", text, _fail_tip(row)))
        combo = row.recipe.get("combo")
        if isinstance(combo, dict) and combo.get("chance"):
            what = tr("otro {0}", row.product) if combo.get("same") else combo.get("name", "")
            text = tr("Combo: {0}% tras un éxito → {1}", combo['chance'], what)
            price = self.crafting.latest_prices().get(combo.get("name"))
            tip = tr("No se suma a la ganancia: los datos no dicen si reemplaza al resultado normal o viene aparte.")
            if price and not combo.get("same"):
                bonus = (success if success is not None else 1) * combo["chance"] / 100 * price[0] * (1 - row.fee)
                text += tr(" (vale {0}: {1} de media, no sumado)", _kinah(price[0]), _kinah(bonus, sign=True))
            else:
                text += tr(" (no sumado)")
            notes.addWidget(self._note("★", text, tip))
        sales = row.sales
        if sales.observations >= 2:
            unit = "uds." if sales.by_offers else "ofertas"
            text = tr("Ventas: {0} {1} en {2}", sales.sold, unit, span_text(sales.hours))
            if sales.by_offers:
                text += tr(" · {0} nuevas a la venta", sales.added)
            tip = (tr("Entre tus {0} muestreos desaparecieron {1} {2} (vendidas o retiradas).", sales.observations, sales.sold, unit))
            if not sales.by_offers:
                tip += tr(" Escanea su pantalla de ofertas para contar unidades.")
            if sales.hours < 6:
                tip += tr(" Con 6 h o más entre muestreos se calcula cuánto se vende al día.")
            notes.addWidget(self._note("↻", text, tip))
        elif row.sell is not None:
            notes.addWidget(self._note("↻", tr("Ventas: haz otro muestreo más tarde para ver si se vende"),
                                       tr("Lo que desaparece del mercado entre dos muestreos se vendió (o lo "
                                          "retiraron).")))
        col.addLayout(notes)
        return frame

    def _have_cell(self, name, needed=None):
        """How many of a material the character has (bag + storage), green when it is enough for one craft."""
        owned = self.crafting.owned(name)
        who = self.crafting.levels_character or tr("tu personaje")
        if owned is None:
            cell = label("—", self.fonts.body, theme.MUTED)
            cell.setToolTip(tr("Entra al juego con {0} con AionDex abierto para leer su bolsa y su almacén.", who))
        else:
            total, where = owned
            enough = needed is None or total >= needed
            cell = label(f"{total:,}", self.fonts.name, theme.GAIN if enough and total else
                         theme.TEXT if needed is None else theme.COMBAT)
            parts = " · ".join(f"{k} {v:,}" for k, v in sorted(where.items())) or tr("no tienes")
            read = self.crafting.inventory.read_at(self.crafting.levels_character)
            cell.setToolTip(f"{who}: {parts}" + (tr(" (necesitas {0} por intento)", needed) if needed else "")
                            + (tr("\nLeído del juego {0}; se actualiza al craftear, recoger o comprar.", _ago(read))
                               if read else ""))
        cell.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        return cell

    def _makes_text(self, row):
        """("Con lo que tienes: 3 intentos · te limita Salt", tip) for the recipe's note."""
        who = self.crafting.levels_character or tr("tu personaje")
        makes = self.crafting.makes(row)
        if makes is None:
            return (tr("Lo que tienes: entra al juego con {0} con AionDex abierto y verás cuántos puedes hacer", who),
                    tr("AionDex lee la bolsa y el almacén del personaje cuando entra al juego."))
        times, limit = makes
        short = []
        for m in row.materials:
            have = self.crafting.owned(m.name)[0]
            if have < m.count:
                short.append(f"{m.count - have} {m.name.replace(' (Bound)', '')}")
        if times:
            text = tr("Con lo que tienes: {0} {1}", times, 'intento' if times == 1 else 'intentos')
            if len(row.materials) > 1:
                text += tr(" · te limita {0}", limit.replace(' (Bound)', ''))
        else:
            text = tr("Con lo que tienes: ninguno · te falta ") + ", ".join(short)
        return text, (tr("Cuenta la bolsa y el almacén de {0} (leídos del juego). Cada intento gasta los materiales "
                         "aunque falle.", who))

    def _material_name(self, name, text, color=None):
        """A material's name: a click shows its market price, how many you have and (one you can gather) a button to
        the map of where to gather it; one that monsters drop shows the best drop rate you can farm now."""
        g = self.ctl.gathering
        gather = g.has(name)
        best = g.best_drop(name) if gather else None
        rate = ""
        if best:
            mob, reachable = best
            rate = (f"  <span style='color:{theme.GOLD.name() if reachable is True else theme.MUTED.name()}'>"
                    f"{mob['chance']:g}%</span>")
        link = label(f"{text}  ⌖{rate}" if gather else text, self.fonts.body,
                     theme.ACCENT if gather else (color or theme.TEXT))
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.setToolTip((_drop_tip(g, name) + "\n" if best else "") +
                        tr("Clic: precio de {0} en el mercado", name) + (tr(" y dónde recolectarlo") if gather else ""))
        link.mousePressEvent = lambda e, n=name, w=link: (self._material_menu(n, w)
                                                           if e.button() == Qt.LeftButton else None)
        return link

    def _material_menu(self, name, widget):
        """A material's market price (lowest now, its week, offers), what you have, and its map and chart."""
        crafting, g, f = self.crafting, self.ctl.gathering, self.fonts
        menu = styled_menu(self)
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(12, 8, 12, 6)
        col.setSpacing(2)
        col.addWidget(label(name.replace(" (Bound)", ""), f.name, theme.TEXT))
        price = crafting.latest_prices().get(name)
        if price:
            lowest, at = price
            shared = tr(" (de otro usuario)") if crafting.price_is_shared(name) else ""
            col.addWidget(label(tr("Mercado: {0} el más bajo · {1}{2}", _kinah(lowest), _ago(at), shared), f.body, theme.GOLD))
            avg, n = crafting.week_average(name)
            if avg is not None and n >= 2:
                diff = (lowest - avg) / avg if avg else 0
                col.addWidget(label(tr("Media 7 días: {0} ({1:+.0%} ahora, {2} lecturas)", _kinah(avg), diff, n), f.small,
                                    theme.GAIN if diff <= -0.05 else theme.COMBAT if diff >= 0.05 else theme.MUTED))
            listings = crafting._latest_field(name, "listings")
            supply = crafting.supply(name)
            bits = ([tr("{0} ofertas", listings)] if listings is not None else []) + \
                ([tr("{0}{1} uds. a la venta", supply[0], '+' if supply[2] else '')] if supply else [])
            if bits:
                col.addWidget(label(" · ".join(bits), f.small, theme.MUTED))
        else:
            npc = next((m.get("npc_price") for r in crafting.recipes for m in r["materials"]
                        if m["name"] == name and m.get("npc_price")), None)
            col.addWidget(label(tr("Lo vende un NPC a {0}", _kinah(npc)) if npc else
                                tr("Sin precio del mercado: escanéalo en el mercado (Market > Capturas)"), f.small,
                                theme.TEXT if npc else theme.WARN))
        owned = crafting.owned(name)
        if owned is not None:
            total, where = owned
            parts = " · ".join(f"{k} {v:,}" for k, v in sorted(where.items()))
            col.addWidget(label(tr("Tienes {0:,}", total) + (f" ({parts})" if parts else ""), f.small,
                                theme.GAIN if total else theme.MUTED))
        holder = QWidgetAction(menu)
        holder.setDefaultWidget(box)
        menu.addAction(holder)
        menu.addSeparator()
        if g.has(name):
            verb = "conseguir" if g.is_drop(name) else "recolectar"
            menu.addAction(tr("⌖  Ver en el mapa dónde {0}", verb), lambda: self.ctl.open_gather_map(name))
        if crafting.history(name):
            menu.addAction(tr("Gráfica de precio"), lambda: self.open_chart(name))
        menu.addAction(tr("Ver en Market"), lambda: self._find_in_market(name))
        menu.exec(widget.mapToGlobal(widget.rect().bottomLeft()))

    def _find_in_market(self, name):
        self.search.setText(name.replace(" (Bound)", ""))
        self.show_tab("materials" if not any(r["product"] == name for r in self.crafting.recipes) else "profit")

    def _exp_badge(self, row):
        """The little EXP mark of the recipe to level up with."""
        badge = QLabel("EXP")
        badge.setFont(self.fonts.badge)
        badge.setStyleSheet("color: #06101f; background: #7bd88f; border-radius: 6px; padding: 0px 5px;")
        badge.setFixedHeight(16)
        prof = self.crafting.professions.get(self.crafting.profession, {}).get("name", self.crafting.profession)
        exp = getattr(row, "exp", None)
        cost = row.profit if row.profit is not None else -row.cost
        lines = [tr("<b>Para subir {0}</b>: receta de {1}, cerca de tu nivel ({2}); las muy por debajo dan poca "
                    "experiencia.", prof, level_text(row.needs), level_text(row.level))]
        if exp:
            seen = self.crafting.exp_seen(row.recipe["id"])
            lines.append(tr("Te da unos {0:.0f} de experiencia por intento (visto: ", exp)
                         + ", ".join(f"{k} {v[-1]}" for k, v in ((tr("éxito"), seen.get("ok")), (tr("fallo"), seen.get("fail")))
                                     if v) + ").")
            lines.append(tr("{0} por intento: {1} por punto de experiencia, lo mejor de las que conoces.", _kinah(cost, sign=True), _kinah(cost / exp, sign=True)))
        else:
            lines.append(tr("De las de tu nivel, la que menos te cuesta: {0} por intento. Cuando la craftees, "
                            "AionDex aprende cuánta experiencia da y elige por Kinah por punto de experiencia.", _kinah(cost, sign=True)))
        badge.setToolTip("<br>".join(lines))
        badge.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        return badge

    def _tile(self, title, value, sub="", color=None, tip=""):
        f = self.fonts
        box = QFrame()
        box.setObjectName("Tile")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        lay.addWidget(label(title, f.small, theme.MUTED))
        lay.addWidget(label(value, f.name_big, color or theme.TEXT))
        if sub:
            lay.addWidget(label(sub, f.small, theme.MUTED, elide=True))
        if tip:
            box.setToolTip(tip)
        return box

    def _note(self, glyph, text, tip=""):
        f = self.fonts
        line = QWidget()
        lay = QHBoxLayout(line)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        mark = label(glyph, f.small, theme.ACCENT)
        mark.setFixedWidth(12)
        lay.addWidget(mark)
        body = label(text, f.small, theme.MUTED)
        body.setWordWrap(True)
        lay.addWidget(body, 1)
        if tip:
            line.setToolTip(tip)
        return line

    def _profit_tip(self, row, buying):
        cost = row.buy_cost if buying else row.cost
        how = (tr("comprando todos los materiales (lo de NPC a su precio; lo procesado, por el camino más barato)")
               if buying else tr("recolectando lo que se recolecta (gratis) y fabricando lo que se fabrica"))
        rate = tr(" × {0:.0%} de éxito", row.success) if row.success is not None else ""
        return (tr("Ganancia por intento, {0}:<br>te pagan {1}{2} = {3} de media<br>− materiales {4}<br>= {5}<br>Si "
                   "el craft falla, se pierden los materiales.", how, _kinah(row.net), rate, _kinah(row.expected), _kinah(cost), _kinah(row.expected - cost, sign=True)))

    def _price_edit(self, m):
        """Your price for a material: a small 'editar' button, the box itself when the price is missing."""
        f = self.fonts
        key = m.name
        if key in self._editing or m.unit is None or m.buy is None or self.crafting.manual_price(m.name):
            edit = QLineEdit()
            edit.setFixedWidth(96)
            edit.setValidator(QIntValidator(0, 2_000_000_000))
            edit.setPlaceholderText(tr("tu precio"))
            manual = self.crafting.manual_price(m.name)
            if manual:
                edit.setText(str(manual))
            edit.setToolTip(tr("Tu precio por unidad, para cuando no aparece en los muestreos (si aparece, manda el "
                               "del mercado). Vacío = sin precio."))
            edit.editingFinished.connect(lambda n=m.name, e=edit: self._set_price(n, e))
            return edit
        button = QPushButton("editar")
        button.setProperty("flat", True)
        button.setToolTip(tr("Poner tu propio precio para este material"))
        button.clicked.connect(lambda _=False, k=key: (self._editing.add(k), self.refresh()))
        return button

    def _raw_line(self, m):
        """'↳ hacerlo tú 205 c/u · comprarlo hecho 300 c/u → mejor hacerlo tú (ahorras 95)', with the recipe in the
        tooltip."""
        f = self.fonts
        recipe = m.raw_recipe
        made = tr("hacerlo tú {0:,.0f} c/u", m.raw) if m.raw is not None else tr("hacerlo tú: ?")
        bought = tr("comprarlo hecho {0:,.0f} c/u", m.market) if m.market is not None else tr("comprarlo hecho: ?")
        verdict, color = "", theme.MUTED
        if m.market is not None and m.raw is not None:
            if m.raw < m.market:
                verdict, color = tr("  →  mejor hacerlo tú (ahorras {0:,.0f})", m.market - m.raw), theme.GAIN
            else:
                verdict, color = tr("  →  mejor comprarlo hecho"), theme.WARN
        line = label(f"      ↳ {made} · {bought}{verdict}", f.small, color)
        if recipe is not None:
            row = RecipeRow(recipe, m.name, level=self.crafting.level(recipe["profession"]))
            raws = " + ".join(f"{x['count']}× {x['name'].replace(' (Bound)', '')}" for x in recipe["materials"])
            line.setToolTip(tr("Hacerlo tú: {0} → {1} {2}, con {3} {4}", raws, row.count, m.name, PROFESSION_ES.get(recipe['profession'], recipe['profession']), recipe['proficiency'])
                            + (tr(" ({0:.0%} de éxito)", row.success) if row.success is not None else "")
                            + tr(", comprando el material raw."))
        else:
            line.setToolTip(tr("Para hacerlo tú falta el precio de su material raw o el nivel de la profesión que "
                               "lo hace."))
        line.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        return line

    def _price_cell(self, unit, total, how, at):
        """'4,400 (880 c/u)' / 'gratis' / '?' for one material in one of the two ways."""
        if unit is None:
            text, color = "?", theme.WARN
        elif unit == 0:
            text, color = "gratis", theme.GAIN
        else:
            text, color = tr("{0}  ({1:,.0f} c/u)", _kinah(total), unit), theme.TEXT
        cell = label(text, self.fonts.body, color)
        cell.setFixedWidth(DETAIL_W)
        cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tip = {"npc": tr("precio del NPC"), "market": tr("precio del mercado"), "manual": tr("tu precio"),
               "craft": tr("lo que te cuesta fabricarlo"), "gather": tr("lo recolectas"),
               "raw": tr("comprando su material raw y procesándolo tú (más barato que procesado)")}.get(how, "sin precio")
        if how == "market" and at:
            tip += f" ({_ago(at)})"
        cell.setToolTip(tip)
        cell.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        return cell

    # --- Capturas ---
    def _shots_tab(self):
        f, crafting = self.fonts, self.crafting
        shots = crafting.shots
        self._hint(self._how_to())
        bar = QHBoxLayout()
        ready = crafting.ready()
        process = QPushButton(tr("Procesar {0} {1}", len(ready), tr("captura") if len(ready) == 1 else tr("capturas")))
        process.setEnabled(bool(ready))
        process.setToolTip(tr("Junta las capturas marcadas en un muestreo con la fecha y hora en que las tomaste (y "
                              "las borra). Si las tomaste a menos de 15 min de un muestreo, se suman a ese."))
        process.clicked.connect(self._process)
        bar.addWidget(process)
        unchecked = [s for s in shots if not s.checked]
        if unchecked:
            drop = QPushButton(tr("Borrar las {0} no marcadas", len(unchecked)))
            drop.setProperty("flat", True)
            drop.clicked.connect(self._remove_unchecked)
            bar.addWidget(drop)
        bar.addStretch(1)
        if any(not s.read for s in shots):
            bar.addWidget(label(tr("Leyendo capturas…"), f.small, theme.GOLD))
        self.body.addLayout(bar)
        if not shots:
            self._hint(tr("No hay capturas por procesar."), theme.TEXT)
            return
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        per_row = 3
        for i, shot in enumerate(shots):
            grid.addWidget(self._shot_card(shot), i // per_row, i % per_row)
        for c in range(per_row):
            grid.setColumnStretch(c, 1)
        self.body.addLayout(grid)

    def _shot_card(self, shot):
        f = self.fonts
        card = QFrame()
        card.setObjectName("Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(10, 10, 10, 10)
        col.setSpacing(6)
        thumb = QLabel()
        thumb.setPixmap(self._thumb(shot))
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setCursor(Qt.PointingHandCursor)
        thumb.setToolTip(tr("Abrir la captura"))
        path = self.crafting.path(shot)
        thumb.mousePressEvent = lambda e, p=path: os.startfile(str(p)) if p.exists() else None
        col.addWidget(thumb)
        check = QCheckBox(shot.caption())
        check.setChecked(shot.checked)
        check.setEnabled(shot.read and shot.kind in ("list", "listings") and bool(shot.rows))
        check.toggled.connect(lambda on, s=shot: (self.crafting.set_checked(s, on), self.refresh()))
        col.addWidget(check)
        if shot.rows:
            if shot.kind == "list":
                lines = [f"{n} · {lowest:,} ({count})" for n, count, lowest in shot.rows]
            else:
                lines = [tr("{0:,} c/u × {1}", unit, qty) + (tr(" · quedan {0:.0f} h", h) if h is not None else "")
                         for _, unit, _, qty, h in shot.rows]
            check.setToolTip("<br>".join(lines))
        foot = QHBoxLayout()
        foot.addWidget(label(time.strftime("%H:%M:%S", time.localtime(shot.at)), f.small, theme.MUTED), 1)
        if shot.read and (shot.error or shot.kind == "unknown"):
            again = QPushButton(tr("Volver a leer"))
            again.setProperty("flat", True)
            again.clicked.connect(lambda _=False, s=shot: self.ctl.crafting_reread(s))
            foot.addWidget(again)
        remove = QPushButton(tr("Borrar"))
        remove.setProperty("flat", True)
        remove.clicked.connect(lambda _=False, s=shot: self._remove(s))
        foot.addWidget(remove)
        col.addLayout(foot)
        return card

    def _thumb(self, shot):
        if shot.file not in self._thumbs:
            src = QPixmap(str(self.crafting.path(shot)))
            dpr = self.devicePixelRatioF()
            if src.isNull():
                pix = QPixmap(round(THUMB_W * dpr), round(THUMB_W * 9 / 16 * dpr))
                pix.fill(QColor("#10131b"))
            else:
                pix = src.scaledToWidth(round(THUMB_W * dpr), Qt.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
            self._thumbs[shot.file] = pix
        return self._thumbs[shot.file]

    def shot_read(self, shot):
        """A shot finished reading: its caption and checkbox change."""
        if self.isVisible():
            self.refresh()

    # --- Ventas ---
    def _sales_tab(self):
        from .salesview import SalesView
        bar = QHBoxLayout()
        bar.addStretch(1)
        load = QPushButton(tr("Importar imagen…"))
        load.setProperty("flat", True)
        load.setToolTip(tr("Lee una captura de Settlement o de List que guardaste antes (con la tecla de captura "
                           "del juego o de Windows)"))
        load.clicked.connect(self._import_images)
        bar.addWidget(load)
        self.body.addLayout(bar)
        self.body.addWidget(SalesView(self))

    def set_sales_period(self, key):
        self.sales_period = key
        self.crafting.set_option("sales_period", key)
        self.refresh()

    def crafting_fee(self):
        return self.crafting.fee

    def delete_sale(self, sale):
        answer = QMessageBox.question(self, tr("Borrar venta"),
                                      tr("¿Borrar la venta de {0} ×{1} del {2}? Deja de contar en las estadísticas "
                                         "(su captura se conserva).", sale['item'], sale['qty'], _clock(sale['at'])))
        if answer == QMessageBox.Yes:
            self.crafting.ledger.remove(sale["id"])
            self.refresh()
            self.ctl.refresh()

    def _import_images(self):
        """Screenshots saved earlier (Settlement, List or the market) read as if they had just been scanned."""
        import shutil
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(self, tr("Capturas del mercado"), str(Path.home() / "Pictures"),
                                                tr("Imágenes (*.png *.jpg *.jpeg *.bmp)"))
        for name in files:
            target = self.crafting.new_shot_path()
            try:
                from PIL import Image
                Image.open(name).convert("RGB").save(target)
            except OSError:
                try:
                    shutil.copy(name, target)
                except OSError:
                    continue
            self.ctl.crafting_import(target)

    # --- Muestreos ---
    def _samples_tab(self):
        f, crafting = self.fonts, self.crafting
        samples = list(reversed(crafting.samples))
        if not samples:
            self._hint(tr("Aún no hay muestreos. ") + self._how_to())
            return
        if self._sample not in crafting.samples:
            self._sample = samples[0]
        self._hint(tr("Cada muestreo guarda los precios y ofertas de ese momento. Compáralos para ver qué se vende: "
                      "lo que desaparece entre uno y otro se vendió (o lo retiraron)."))
        for sample in samples:
            line = QFrame()
            line.setObjectName("Row")
            line.setProperty("open", sample is self._sample)
            line.setCursor(Qt.PointingHandCursor)
            line.mousePressEvent = lambda e, s=sample: self._select_sample(s)
            lay = QHBoxLayout(line)
            lay.setContentsMargins(12, 6, 12, 6)
            prof = crafting.professions.get(sample.get("profession"), {}).get(
                "name", tr("Equipo") if sample.get("profession") == "Gear" else sample.get("profession", ""))
            lay.addWidget(label(f"{_clock(sample['at'])}  ·  {prof}", f.name,
                                theme.GOLD if sample is self._sample else theme.TEXT), 1)
            n = len(sample["items"])
            lay.addWidget(label(tr("{0} {1} · {2} capturas", n, 'objeto' if n == 1 else 'objetos', sample.get('shots', 0)),
                                f.small, theme.MUTED))
            remove = QPushButton(tr("Borrar"))
            remove.setProperty("flat", True)
            remove.clicked.connect(lambda _=False, s=sample: self._delete_sample(s))
            lay.addWidget(remove)
            self.body.addWidget(line)
        self.body.addWidget(SectionTitle(tr("Muestreo del {0}", _clock(self._sample['at'])), f))
        self._sample_items(self._sample)

    def _select_sample(self, sample):
        self._sample = sample
        self.refresh()

    def _delete_sample(self, sample):
        answer = QMessageBox.question(self, tr("Borrar muestreo"),
                                      tr("¿Borrar el muestreo del {0}? Sus precios dejan de contar.", _clock(sample['at'])))
        if answer == QMessageBox.Yes:
            self.crafting.delete_sample(sample)
            self.refresh()
            self.ctl.refresh()

    def _sample_items(self, sample):
        """Each item of a sample with its change since the previous sample that has it."""
        f = self.fonts
        query = self.search.text().strip().lower()
        before = [s for s in self.crafting.samples if s["at"] < sample["at"]]
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for c, text in enumerate((tr("Objeto"), tr("Precio más bajo"), tr("Cambio"), tr("Ofertas"), tr("Cambio"), "")):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignRight if 0 < c < 5 else Qt.AlignLeft)
        r = 1
        for name, item in sorted(sample["items"].items()):
            if query and query not in name.lower():
                continue
            prev = next((s["items"][name] for s in reversed(before) if name in s["items"]), None)
            grid.addWidget(label(name, f.body, QColor(GRADE_COLOR.get(self._grade(name), "#D6DAE3"))), r, 0)
            grid.addWidget(label(_kinah(item.get("lowest")), f.name, theme.TEXT), r, 1, Qt.AlignRight)
            grid.addWidget(_delta(item.get("lowest"), prev and prev.get("lowest"), f, pct=True), r, 2, Qt.AlignRight)
            grid.addWidget(label("—" if item.get("listings") is None else str(item["listings"]), f.name, theme.TEXT),
                           r, 3, Qt.AlignRight)
            grid.addWidget(_delta(item.get("listings"), prev and prev.get("listings"), f, invert=True), r, 4,
                           Qt.AlignRight)
            grid.addWidget(self._chart_button(name), r, 5)
            r += 1
        grid.setColumnStretch(0, 1)
        self.body.addLayout(grid)

    def _grade(self, name):
        return next((rec.get("grade") for rec in self.crafting.recipes if rec["product"] == name), None)


class PriceChartWindow(QWidget):
    """One item's lowest price over the samples (line) and how many offers it had (bars), with the numbers below."""

    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.name = None
        self.fonts = Fonts(1.0)
        self.setObjectName("MapWindow")
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + CARD_STYLE)
        self.resize(900, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)
        self.title = label("", self.fonts.name_big, theme.TEXT)
        root.addWidget(self.title)
        self.subtitle = label("", self.fonts.small, theme.MUTED)
        self.subtitle.setWordWrap(True)
        root.addWidget(self.subtitle)
        self.chart = PriceChart(self.fonts)
        root.addWidget(self.chart, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(170)
        holder = QWidget()
        self.table = QGridLayout(holder)
        self.table.setHorizontalSpacing(18)
        self.table.setVerticalSpacing(2)
        scroll.setWidget(holder)
        root.addWidget(scroll)

    def show_item(self, name):
        self.name = name
        self.refresh()

    def refresh(self):
        crafting, f = self.ctl.crafting, self.fonts
        hist = crafting.history(self.name)
        self.setWindowTitle(tr("{0} — Gráfica de precio: {1}", APP_NAME, self.name))
        self.title.setText(tr("Gráfica de precio · {0}", self.name))
        sales = crafting.sales(self.name)
        text, _ = sales.verdict()
        prices = [p for _, p, _, _ in hist if p]
        parts = [f"{len(hist)} {'muestreo' if len(hist) == 1 else 'muestreos'}"]
        if prices:
            parts.append(tr("más bajo {0:,} · más alto {1:,} · último {2:,}", min(prices), max(prices), prices[-1]))
        parts.append(text)
        self.subtitle.setText(" · ".join(parts) + tr(". Línea: precio más bajo; barras: ofertas a la venta."))
        self.chart.set_points([(at, p, n) for at, p, n, _ in hist])
        _clear(self.table)
        for c, head in enumerate((tr("Fecha"), tr("Precio más bajo"), tr("Ofertas"), tr("Unidades vistas"), tr("Precio medio visto"))):
            self.table.addWidget(label(head, f.small, theme.MUTED), 0, c, Qt.AlignLeft if c == 0 else Qt.AlignRight)
        for r, (at, p, n, offers) in enumerate(reversed(hist), start=1):
            units = sum(o[2] for o in offers)
            avg = sum(o[0] * o[2] for o in offers) / units if units else None
            values = (_clock(at), _kinah(p), "—" if n is None else str(n), str(units) if offers else "—", _kinah(avg))
            for c, value in enumerate(values):
                self.table.addWidget(label(value, f.body, theme.TEXT), r, c, Qt.AlignLeft if c == 0 else Qt.AlignRight)
        self.table.setColumnStretch(5, 1)
        self.table.setRowStretch(len(hist) + 1, 1)


class PriceChart(QWidget):
    PAD = (84, 24, 76, 46)          # left, top, right, bottom

    def __init__(self, fonts):
        super().__init__()
        self.fonts = fonts
        self.points = []            # [(when, lowest price, offers)]
        self._screen = []           # [(QPointF, point)] for the hover tooltip
        self.setMouseTracking(True)
        self.setMinimumHeight(260)

    def set_points(self, points):
        self.points = [p for p in points if p[1]]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = self.fonts
        left, top, right, bottom = self.PAD
        area = QRectF(left, top, self.width() - left - right, self.height() - top - bottom)
        p.fillRect(self.rect(), theme.WINDOW_BG)
        p.setPen(QPen(QColor("#2b3242"), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        self._screen = []
        if not self.points:
            p.setPen(theme.MUTED)
            p.setFont(f.body)
            p.drawText(self.rect(), Qt.AlignCenter, tr("Sin precios todavía: procesa un muestreo que tenga este "
                                                       "objeto."))
            return
        times = [t for t, _, _ in self.points]
        prices = [v for _, v, _ in self.points]
        t0, t1 = min(times), max(times)
        lo, hi = min(prices), max(prices)
        span = max(hi - lo, hi * 0.05, 1)
        lo, hi = max(0, lo - span * 0.15), hi + span * 0.15
        offers = [n for _, _, n in self.points if n is not None]
        top_offers = max(offers) if offers else 0

        def x_of(t):
            return area.center().x() if t1 == t0 else area.left() + (t - t0) / (t1 - t0) * area.width()

        def y_of(v):
            return area.bottom() - (v - lo) / (hi - lo) * area.height()

        # grid + price axis
        p.setFont(f.small)
        for i in range(5):
            v = lo + (hi - lo) * i / 4
            y = y_of(v)
            p.setPen(QPen(QColor(255, 255, 255, 16), 1))
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            p.setPen(theme.MUTED)
            p.drawText(QRectF(0, y - 8, left - 8, 16), Qt.AlignRight | Qt.AlignVCenter, _axis(v))
        # offers as bars
        if top_offers:
            width = max(4.0, min(22.0, area.width() / max(1, len(self.points)) * 0.5))
            for t, _, n in self.points:
                if n is None:
                    continue
                h = n / top_offers * area.height() * 0.35
                bar = QRectF(x_of(t) - width / 2, area.bottom() - h, width, h)
                p.fillRect(bar, QColor(46, 192, 242, 40))
            p.setPen(theme.MUTED)
            p.drawText(QRectF(area.right() + 4, area.bottom() - area.height() * 0.35 - 8, right, 16),
                       Qt.AlignLeft | Qt.AlignVCenter, tr("{0} ofertas", top_offers))
        # price line
        path = QPainterPath()
        for i, point in enumerate(self.points):
            pt = QPointF(x_of(point[0]), y_of(point[1]))
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
            self._screen.append((pt, point))
        p.setPen(QPen(theme.ACCENT, 2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setBrush(theme.ACCENT)
        label_all = len(self.points) <= 12
        for i, (pt, (_, v, _)) in enumerate(self._screen):
            p.setPen(Qt.NoPen)
            p.drawEllipse(pt, 3.5, 3.5)
            if label_all or v in (max(prices), min(prices)) or i == len(self._screen) - 1:
                p.setPen(theme.TEXT)
                p.drawText(QRectF(pt.x() - 50, pt.y() - 22, 100, 16), Qt.AlignCenter, _axis(v))
        # time axis
        p.setPen(theme.MUTED)
        marks = sorted({0, len(times) - 1, *range(0, len(times), max(1, len(times) // 5))})
        for i in marks:
            x = x_of(times[i])
            p.drawText(QRectF(x - 45, area.bottom() + 8, 90, 16), Qt.AlignCenter, _clock(times[i]))

    def mouseMoveEvent(self, event):
        pos = event.position()
        near = min(self._screen, key=lambda s: abs(s[0].x() - pos.x()), default=None)
        if near and abs(near[0].x() - pos.x()) < 20:
            t, v, n = near[1]
            text = tr("{0}<br><b>{1:,}</b> el más bajo", _clock(t), v) + (tr("<br>{0} ofertas", n) if n is not None else "")
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()


def _fail_cell(row, locked):
    """'17% · mín 9%': your chance of failing and the lowest this recipe can get to."""
    if row.success is None:
        return "—", theme.MUTED
    best = row.min_fail
    text = f"{1 - row.success:.0%}" + (tr(" · mín {0:.0%}", best[0]) if best and best[0] < 1 - row.success - 0.005 else "")
    return text, theme.MUTED if locked else theme.TEXT


def _drop_tip(gathering, name):
    """Who drops a material and how often (the closed Abyss maps apart), for the ⌖ link's tooltip."""
    lines = [tr("<b>{0}</b>: lo sueltan monstruos (drop rate por kill)", name)]
    for mob, reachable, maps in gathering.drops(name)[:8]:
        kills = kills_for(mob["chance"])
        where = _spawn_text(maps) or mob.get("zone") or tr("metabot no dice dónde está (jefe o mazmorra)")
        line = (f"{mob['name']} · Lv {mob['level']} · <b>{mob['chance']:g}%</b>"
                + (tr(" · ~{0} kills para un 90%", kills) if kills and kills > 1 else "")
                + (f"<br>&nbsp;&nbsp;<span style='color:#8a93a8'>{where}</span>" if where else ""))
        lines.append(line if reachable is not False else tr("<span style='color:#8a93a8'>{0} · cerrado ahora</span>", line))
    lines.append(tr("Clic: ver en el mapa dónde están, la mejor zona y una ruta"))
    return "<br>".join(lines)


def _spawn_text(maps):
    """'Chaotic Lower Reshanta, Chaotic Middle Reshanta (cerrado)': the maps a monster spawns in."""
    return ", ".join(MAP_NAMES.get(m, m).split(" (")[0] + ("" if map_open(m) else tr(" (cerrado)")) for m in maps)


class LevelBox(QSpinBox):
    """Your proficiency as the game writes it (Novice 1-50, then Professional 1-50): the arrows go from Novice 50 to
    Professional 1, and you can type "Novice 17", "pro 5" or just the number (of the rank you are at). Underneath,
    the one 1-100 scale of the recipes."""

    def __init__(self):
        super().__init__()
        self.setRange(0, NOVICE_MAX * (len(RANKS) - 1))          # Novice and Professional on the global servers
        self.setSpecialValueText(tr("¿?"))
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance("Professional 50") + 40)

    def textFromValue(self, value):
        return level_text(value) if value else tr("¿?")

    def valueFromText(self, text):
        value = parse_level(text, self.value())
        return self.value() if value is None else value

    def validate(self, text, pos):
        value = parse_level(text, self.value())
        if value is not None and value <= self.maximum():
            return QValidator.Acceptable, text, pos
        return QValidator.Intermediate, text, pos


def _fail_tip(row):
    if row.success is None:
        return tr("Sin datos de éxito para esta receta")
    lines = [tr("Fallo {0}: {1:.0%} (si falla, pierdes los materiales)", 'al nivel justo' if row.level is None else tr("con tu nivel"), 1 - row.success)]
    for lv, fail in row.fail_curve():
        lines.append(f"{level_text(lv)}: {fail:.0%}")
    best = row.min_fail
    if best:
        lines.append(tr("Lo mínimo para esta receta: {0:.0%} (desde {1})", best[0], level_text(best[1])))
    return "<br>".join(lines)


def _profit_cell(profit, sell):
    """(text, colour) of a profit column."""
    if profit is None:
        return ("?" if sell is not None else "—"), theme.MUTED
    return _kinah(profit, sign=True), theme.GAIN if profit > 0 else theme.COMBAT


def _cost_tip(way, cost, known, missing, row):
    if cost is None:
        return tr("{0}: falta el precio de {1} (escanea su pantalla del mercado o escríbelo al abrir la fila)", way, ', '.join(missing))
    tip = tr("{0}: materiales {1} por intento", way, _kinah(cost))
    if row.margin is not None and way == tr("Recolectando"):
        tip += tr(" · margen {0:+.0%}", row.margin)
    return tip


def _cell(text, font, color, width):
    """A right-aligned table cell of fixed width ('…' if the text does not fit)."""
    cell = label(text, font, color, elide=True)
    cell.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)     # an eliding label is "Ignored" by layouts
    cell.setFixedWidth(width)
    cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return cell


def _chip(text, color, fonts):
    c = QColor(color)
    chip = QLabel(text)
    chip.setFont(fonts.small)
    chip.setStyleSheet(f"color: {c.name()}; background: rgba({c.red()},{c.green()},{c.blue()},0.12); "
                       f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.5); border-radius: 8px; "
                       f"padding: 0px 7px;")
    chip.setAttribute(Qt.WA_TransparentForMouseEvents)
    return chip


def _delta(now, before, fonts, pct=False, invert=False):
    """'▲ 1,000 (+3.8%)' coloured: green when the price rose / the offers dropped (selling)."""
    if now is None or before is None or now == before:
        return label("—" if before is None else "=", fonts.small, theme.MUTED)
    diff = now - before
    up = diff > 0
    good = up != invert
    text = f"{'▲' if up else '▼'} {abs(diff):,}"
    if pct and before:
        text += f" ({diff / before:+.1%})"
    return label(text, fonts.small, theme.GAIN if good else theme.COMBAT)


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.hide()                   # gone at once: a deleteLater'd widget still paints over the new ones
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            _clear(item.layout())
