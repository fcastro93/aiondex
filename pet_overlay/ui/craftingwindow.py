"""Crafting window: which recipes of your profession are worth crafting (sorted by profit after the market's tax, with
what each one needs and where it comes from), the market shots waiting to be processed, the samples taken and each
item's price over time."""
import os
import time
import webbrowser

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QToolTip, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..crafting import SORT_KEYS, RecipeRow, span_text
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label

TABS = [("profit", "Rentabilidad"), ("shots", "Capturas"), ("samples", "Muestreos")]
# aion2data's grades in the game's colours (its "Epic" is the game's blue Legend tier)
GRADE_COLOR = {"Common": "#C3C3C3", "Rare": "#19E048", "Epic": "#379AFF", "Unique": "#FFD02B", "Special": "#00FFD8"}
SOURCE = {   # how a material is priced -> (chip text, colour)
    "npc": ("NPC", "#2ec0f2"), "gather": ("Recolectas", "#7bd88f"), "craft": ("Lo fabricas", "#c38bff"),
    "market": ("Mercado", "#f0c24e"), "manual": ("Tu precio", "#e0a050"), "unknown": ("Sin precio", "#ff7a59"),
}
PROFESSION_ES = {"Cooking": "Cocina", "Alchemy": "Alquimia", "Blacksmithing": "Herrería",
                 "Armorsmithing": "Armería", "Handicrafting": "Artesanía"}
VERDICT_COLOR = {"good": theme.GAIN, "bad": theme.COMBAT, "none": theme.MUTED}
COLUMNS = (("Precio", 74), ("Recibes", 74), ("Fallo", 96), ("Recolectando", 108), ("Comprando", 108),
           ("¿Se vende?", 146))
SORTABLE = {"Objeto": "name", "Precio": "price", "Recibes": "net", "Fallo": "fail", "Recolectando": "gather",
            "Comprando": "buy", "¿Se vende?": "sales"}             # click a column title to sort by it
DETAIL_W = 170
THUMB_W = 340
CARD_STYLE = """
QFrame#Card { background: #121c33; border: 1px solid #1f2b47; border-radius: 8px; }
QFrame#Row { background: #101a30; border: 1px solid #1a2643; border-radius: 6px; }
QFrame#Row[open="true"] { border-color: #2c4170; }
QFrame#Detail { background: #0d1628; border: 1px solid #1a2643; border-radius: 6px; }
QFrame#Tile { background: #121c33; border: 1px solid #1f2b47; border-radius: 6px; }
QPushButton[small="true"] { padding: 3px 10px; font-weight: 600; }
"""


def _clock(ts):
    return time.strftime("%d/%m %H:%M", time.localtime(ts))


def _ago(ts):
    mins = max(0, (time.time() - ts) / 60)
    if mins < 1:
        return "hace un momento"
    if mins < 60:
        return f"hace {mins:.0f} min"
    if mins < 48 * 60:
        return f"hace {mins / 60:.0f} h"
    return f"hace {mins / 1440:.0f} días"


def _kinah(value, sign=False):
    return "—" if value is None else (f"{value:+,.0f}" if sign else f"{value:,.0f}")


def _axis(value):
    """Price labels of the chart: 26,500 / 1.25M (short forms like 27K repeat on a narrow range)."""
    return f"{value / 1_000_000:,.2f}M" if value >= 1_000_000 else f"{value:,.0f}"


class CraftingWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.crafting = ctl.crafting
        self.fonts = Fonts(1.0)
        self.tab = "shots" if self.crafting.shots else "profit"
        self._open = set()                # products whose materials are shown
        self._editing = set()             # materials whose price box is open
        self._thumbs = {}                 # shot file -> QPixmap thumbnail
        self._sample = None               # the sample shown in Muestreos
        self.chart_window = None
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — Crafting")
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
        titles.addWidget(label("Crafting", f.name_big, theme.TEXT))
        titles.addWidget(label("Escanea el mercado, procesa un muestreo y mira qué vale la pena craftear y vender.",
                               f.small, theme.MUTED))
        head.addLayout(titles, 1)
        head.addWidget(label("Profesión:", f.small, theme.MUTED))
        self.profession = QComboBox()
        for key, info in self.crafting.professions.items():
            self.profession.addItem(info["name"], key)
        self.profession.setCurrentIndex(max(0, self.profession.findData(self.crafting.profession)))
        self.profession.currentIndexChanged.connect(self._profession_changed)
        head.addWidget(self.profession)
        head.addWidget(label("Tu nivel:", f.small, theme.MUTED))
        self.level = QSpinBox()
        self.level.setRange(0, 200)
        self.level.setSpecialValueText("¿?")
        self.level.setToolTip("Tu nivel en esta profesión: decide tu probabilidad de éxito y qué recetas puedes hacer")
        self.level.setValue(self.crafting.level() or 0)
        self._level_timer = QTimer(self, singleShot=True, interval=500, timeout=self._level_changed)
        self.level.valueChanged.connect(lambda _: self._level_timer.start())
        head.addWidget(self.level)
        self.level_back = QPushButton("")
        self.level_back.setProperty("flat", True)
        self.level_back.clicked.connect(self._level_back)
        head.addWidget(self.level_back)
        self.scan_button = QPushButton("Escanear")
        self.scan_button.clicked.connect(self._scan)
        head.addWidget(self.scan_button)
        root.addLayout(head)

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
        self.search.setPlaceholderText("Buscar objeto o material…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self._search_timer = QTimer(self, singleShot=True, interval=250, timeout=self.refresh)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
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
        credit = self.crafting.credit or {"text": "Recetas, tiendas y objetos: aion2data.com",
                                          "url": "https://aion2data.com/recipes"}
        link = QPushButton(credit["text"])
        link.setProperty("flat", True)
        link.clicked.connect(lambda: webbrowser.open(credit["url"]))
        foot.addWidget(link)
        foot.addStretch(1)
        foot.addWidget(label("El texto del mercado se lee con el OCR de Windows, en tu PC: las capturas no salen de "
                             "ella.", f.small, theme.MUTED))
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
        source = crafting.level_source()
        self.level.blockSignals(True)
        self.level.setValue(crafting.level() or 0)
        self.level.blockSignals(False)
        game = crafting.game_level()
        self.level_back.setVisible(source == "try")
        if source == "try":
            self.level_back.setText(f"↺ usar el del juego ({game})")
            self.level.setToolTip(f"Estás probando con nivel {crafting.level()}: los números son como si lo tuvieras. "
                                  f"Tu nivel real (del juego) es {game}.")
        elif source == "game":
            exp = crafting.game_exp()
            self.level.setToolTip(f"Leído del juego para {crafting.character}: nivel {game}"
                                  + (f", {exp} de experiencia en el nivel" if exp is not None else "")
                                  + ". Se actualiza al entrar y cada vez que crafteas. Cámbialo para ver los números "
                                    "con otro nivel (por ejemplo, el que tendrás).")
        else:
            self.level.setToolTip("Tu nivel en esta profesión: decide tu probabilidad de éxito y qué recetas puedes "
                                  "hacer. AionDex lo lee del juego al entrar con el personaje (y lo aprende de "
                                  "cada profesión la primera vez que crafteas algo de ella).")

    def _level_changed(self):
        if self.crafting.game_level() is not None:
            self.crafting.try_level(self.level.value())          # over the game's level: "what if I were N"
        else:
            self.crafting.set_level(self.level.value())
        self.refresh()
        self.ctl.refresh()

    def _level_back(self):
        self.crafting.try_level(None)
        self.refresh()
        self.ctl.refresh()

    def _scan(self):
        self.ctl.crafting_scan(from_window=True)

    def show_tab(self, key):
        self.tab = key
        self.refresh()
        self.scroll.verticalScrollBar().setValue(0)

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
        counts = {"shots": len(self.crafting.shots), "samples": len(self.crafting.samples)}
        for key, button in self.tab_buttons.items():
            text = dict(TABS)[key]
            button.setText(f"{text} ({counts[key]})" if counts.get(key) else text)
            button.setChecked(key == self.tab)
        self.search.setVisible(self.tab != "shots")
        hk = self.ctl.settings["hotkeys"].get("crafting_scan")
        self.scan_button.setToolTip("Captura la ventana del juego (con el mercado abierto)"
                                    + (f". En el juego: {hk}" if hk else ""))
        self._show_level()
        _clear(self.body)
        {"profit": self._profit_tab, "shots": self._shots_tab, "samples": self._samples_tab}[self.tab]()
        self.body.addStretch(1)
        if self.chart_window and self.chart_window.isVisible():
            self.chart_window.refresh()

    def _hint(self, text, color=None):
        hint = label(text, self.fonts.body, color or theme.MUTED)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        return hint

    def _how_to(self):
        prof = self.crafting.professions.get(self.crafting.profession, {})
        hk = self.ctl.settings["hotkeys"].get("crafting_scan")
        return (f"En el juego, abre el mercado (Exchange) en «{prof.get('market', '…')}» y pulsa Escanear"
                + (f" o {hk}" if hk else "") + " en cada página. Escanea también la pantalla de ofertas de los objetos "
                "que te interesen (así se ve cuánto se vende entre muestreos) y la de los materiales que no da un "
                "NPC. Luego, en Capturas, marca las buenas y pulsa Procesar.")

    # --- Rentabilidad ---
    def _profit_tab(self):
        f, crafting = self.fonts, self.crafting
        query = self.search.text().strip().lower()
        prof = crafting.professions.get(crafting.profession, {}).get("name", crafting.profession)
        level = crafting.level()
        if not crafting.samples:
            self._hint("Aún no hay muestreos. " + self._how_to())
        else:
            last = crafting.samples[-1]
            hint = self._hint(f"Precios de tus muestreos (el último: {_clock(last['at'])}, {_ago(last['at'])}). "
                              f"Ganancia media por intento, con tu % de éxito y el {crafting.fee:.0%} del mercado.  ⓘ")
            hint.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            hint.setToolTip(f"Ganancia por intento = lo que te paga el mercado (precio − {crafting.fee:.0%}) × tu "
                            f"probabilidad de éxito − los materiales (si el craft falla, se pierden).<br>"
                            f"<b>Recolectando</b>: lo que se recolecta no te cuesta y lo que se fabrica, a su coste.<br>"
                            f"<b>Comprando</b>: todos los materiales al precio del mercado (lo de NPC a su precio; lo "
                            f"procesado, por el camino más barato).<br>Clic en una receta para ver sus materiales.")
        if level is None:
            self._hint(f"Falta tu nivel de {prof}: escríbelo arriba (o craftea algo de {prof} y AionDex lo aprende). "
                       f"Mientras, se usa el % de éxito más bajo.", theme.WARN)
        rows = crafting.recipe_rows(self.ctl.crafting_faction())
        if query:
            rows = [r for r in rows if query in r.product.lower() or any(query in m.name.lower() for m in r.materials)]
        picks = crafting.recommend(rows)
        if picks and not query:
            self.body.addWidget(self._picks(picks, level, prof))
        elif level is not None and not query:
            ahead = sorted((r for r in rows if r.can_craft is False and r.profit is not None and r.profit > 0),
                           key=lambda r: (r.needs, -r.profit))
            text = f"Con tu nivel de {prof} ({level}) ninguna receta deja ganancia con los precios de tus muestreos."
            if ahead:
                first = ahead[0]
                text += (f" La primera que sí: {first.product} (nivel {first.needs}), "
                         f"{_kinah(first.profit, sign=True)} por intento al llegar. Mientras subes, lo que cuenta es "
                         f"la experiencia: haz lo que menos pierde.")
            self._hint(text, theme.WARN)
        options = QHBoxLayout()
        only = QCheckBox(f"Solo lo que puedo craftear con mi nivel ({level})" if level is not None else
                         "Solo lo que puedo craftear con mi nivel")
        only.setToolTip("Marcado: solo las recetas de tu nivel o menos. Desmarcado: todas; las de más nivel salen en "
                        "gris con el nivel que piden.")
        if level is None:
            only.setEnabled(False)
            only.setToolTip("Aún no se sabe tu nivel: entra con el personaje (AionDex lo lee del juego) o escríbelo "
                            "arriba.")
        only.setChecked(level is not None and crafting.config.get("only_craftable", True))
        only.toggled.connect(lambda on: (crafting.set_option("only_craftable", on), self.refresh()))
        options.addWidget(only)
        everything = QCheckBox("Mostrar también los que no tienen precio")
        everything.setChecked(bool(crafting.config.get("show_unpriced")))
        everything.toggled.connect(lambda on: (crafting.set_option("show_unpriced", on), self.refresh()))
        options.addWidget(everything)
        options.addStretch(1)
        self.body.addLayout(options)

        if only.isChecked():
            rows = [r for r in rows if r.can_craft]
        priced = [r for r in rows if r.sell is not None]
        shown = rows if everything.isChecked() else priced
        if not shown:
            if rows and not priced and crafting.samples:
                self._hint("Ningún producto de esta profesión aparece en los muestreos: escanea su categoría del "
                           "mercado o marca «Mostrar también los que no tienen precio».")
            elif query:
                self._hint(f"Nada coincide con «{self.search.text().strip()}».")
            return
        self.body.addWidget(self._header_row())
        for row in shown:
            self.body.addWidget(self._product_row(row))
            if row.product in self._open:
                self.body.addWidget(self._detail(row))
        self._materials(query)

    def _materials(self, query):
        """The profession's materials with a price in your samples: what you scanned that no recipe makes (Lesser
        Manastone...) shows here too, not only inside the recipes that use it."""
        f, crafting = self.fonts, self.crafting
        mats = [m for m in crafting.material_rows() if not query or query in m[0].lower()]
        if not mats:
            return
        prof = crafting.professions.get(crafting.profession, {}).get("name", crafting.profession)
        self.body.addWidget(SectionTitle(f"Materiales de {prof} en tus muestreos", f))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for c, text in enumerate(("Material", "", "Más bajo", "Ofertas", "A la venta", "¿Se vende?", "Lo usan", "")):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignRight if 1 < c < 6 else Qt.AlignLeft)
        for r, (name, source, lowest, at, listings, sales, supply, products) in enumerate(mats, start=1):
            grid.addWidget(label(name, f.body, QColor(GRADE_COLOR.get(self._grade(name), "#D6DAE3"))), r, 0)
            text, color = SOURCE.get(source if source != "market" else "market", SOURCE["unknown"])
            grid.addWidget(_chip(text if source != "market" else "Del juego / mercado", color, f), r, 1)
            price = label(_kinah(lowest), f.name, theme.TEXT)
            price.setToolTip(f"precio {_ago(at)}")
            grid.addWidget(price, r, 2, Qt.AlignRight)
            grid.addWidget(label("—" if listings is None else str(listings), f.body, theme.TEXT), r, 3, Qt.AlignRight)
            units = f"{supply[0]}{'+' if supply[2] else ''} uds." if supply else "—"
            grid.addWidget(label(units, f.body, theme.MUTED), r, 4, Qt.AlignRight)
            verdict, kind = sales.verdict()
            grid.addWidget(label(verdict, f.small, VERDICT_COLOR[kind]), r, 5, Qt.AlignRight)
            used = label(", ".join(products[:2]) + (f" y {len(products) - 2} más" if len(products) > 2 else ""),
                         f.small, theme.MUTED, elide=True)
            used.setToolTip("Lo usan: " + ", ".join(products))
            used.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            grid.addWidget(used, r, 6)
            grid.addWidget(self._chart_button(name), r, 7)
        grid.setColumnStretch(6, 1)
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
            who = f"Si tuvieras nivel {level} en {prof} (el tuyo es {self.crafting.game_level()})"
        else:
            who = f"Con tu nivel de {prof} ({level})" if level is not None else "Al nivel justo de cada receta"
        col.addWidget(label(f"{who}, lo que más deja recolectando:", f.name, theme.GOLD))
        for i, r in enumerate(picks, start=1):
            sold, kind = r.sales.verdict()
            parts = [f"{_kinah(r.profit, sign=True)} por intento",
                     f"{1 - r.success:.0%} de fallo" if r.success is not None else "",
                     sold if kind == "good" else "aún sin ventas vistas"]
            line = label(f"{i}. {r.product}  —  " + " · ".join(x for x in parts if x), f.body,
                         theme.TEXT if kind == "good" else theme.MUTED)
            col.addWidget(line)
        col.addWidget(label("Solo cuenta el precio más bajo de cada material: si necesitas muchos, mira cuántos hay a ese "
                            "precio en su pantalla de ofertas.", f.small, theme.MUTED))
        return card

    def _header_row(self):
        f = self.fonts
        line = QWidget()
        lay = QHBoxLayout(line)
        lay.setContentsMargins(12, 0, 12, 0)
        current = self.crafting.config.get("sort") or "gather"
        flip = self.crafting.config.get("sort_flip", False)
        for text, width in (("Objeto", None),) + COLUMNS:
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
            cell.setToolTip("Ordenar por esta columna (otro clic invierte el orden)")
            cell.mousePressEvent = lambda e, k=key: (self.crafting.set_sort(k), self.refresh())
            lay.addWidget(cell, 1 if width is None else 0)
        lay.addSpacing(8 + self._chart_width())
        return line

    def _chart_button(self, name):
        button = QPushButton("Gráfica de precio")
        button.setProperty("small", True)
        button.setFixedWidth(self._chart_width())
        button.clicked.connect(lambda _=False, n=name: self.open_chart(n))
        return button

    def _chart_width(self):
        if not hasattr(self, "_chart_w"):
            probe = QPushButton("Gráfica de precio", self)
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
        names.addWidget(label(f"{arrow}  {row.product}{count}", f.name,
                              theme.MUTED if locked else QColor(GRADE_COLOR.get(row.recipe.get("grade"), "#D6DAE3")),
                              elide=True))
        supply = ""
        if row.supply:
            units, at_lowest, partial = row.supply
            supply = f"{units}{'+' if partial else ''} uds. a la venta"
            if at_lowest:
                supply += f" ({at_lowest} al más bajo)"
        needs = (f"requiere nivel {row.needs} (tienes {row.level})" if locked else
                 f"nivel {row.needs}" if row.needs else "")
        if row.thin:
            sub = [f"⚠ solo {row.listings} {'oferta' if row.listings == 1 else 'ofertas'}: precio poco fiable",
                   needs, f"precio de {_ago(row.sell_at)}" if row.sell_at else ""]
        else:
            sub = [needs, f"{row.listings} ofertas" if row.listings is not None else "", supply,
                   f"precio de {_ago(row.sell_at)}" if row.sell_at else "sin precio de mercado"]
        names.addWidget(label(" · ".join(s for s in sub if s), f.small,
                              theme.WARN if locked or row.thin else theme.MUTED, elide=True))
        if row.thin:
            frame.setToolTip(f"Solo hay {row.listings} a la venta y no se ha visto ninguna venta: el precio más bajo "
                             f"puede ser cualquier cosa (alguien pidiendo de más) y nadie pagarlo. No entra en las "
                             f"recomendaciones.")
        lay.addLayout(names, 1)
        cells = [(_kinah(row.sell), theme.TEXT), (_kinah(row.net), theme.TEXT), _fail_cell(row, locked),
                 _profit_cell(row.profit, row.sell), _profit_cell(row.buy_profit, row.sell)]
        text, kind = row.sales.verdict()
        cells.append((text, VERDICT_COLOR[kind]))
        widgets = [_cell(text, f.name, color, width) for (text, color), (_, width) in zip(cells, COLUMNS)]
        widgets[2].setToolTip(_fail_tip(row))
        widgets[2].setAttribute(Qt.WA_TransparentForMouseEvents, False)
        widgets[2].mousePressEvent = frame.mousePressEvent
        widgets[3].setToolTip(_cost_tip("Recolectando", row.cost, row.known_cost, row.missing, row))
        widgets[4].setToolTip(_cost_tip("Comprando todo", row.buy_cost, row.known_buy_cost, row.missing_buy, row))
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
        sold = f"× {row.count} uds." if row.count > 1 else "1 unidad"
        tiles.addWidget(self._tile("Se vende a", _kinah(row.sell), sold))
        tiles.addWidget(self._tile("Te pagan", _kinah(row.net), f"tras el {row.fee:.0%} del mercado"))
        if success is not None:
            best = row.min_fail
            tiles.addWidget(self._tile("Sale bien", f"{success:.0%}",
                                       f"falla {1 - success:.0%}" + (f" · mín {best[0]:.0%}" if best else ""),
                                       tip=_fail_tip(row)))
        for title, value, missing in (("Ganas recolectando", row.profit, row.missing),
                                      ("Ganas comprando", row.buy_profit, row.missing_buy)):
            if value is None:
                tiles.addWidget(self._tile(title, "?", "falta precio de " + ", ".join(missing)[:40], theme.WARN))
            else:
                tiles.addWidget(self._tile(title, _kinah(value, sign=True), "por intento, de media",
                                           theme.GAIN if value > 0 else theme.COMBAT,
                                           tip=self._profit_tip(row, value is row.buy_profit)))
        col.addLayout(tiles)

        # 2. the materials
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        for c, text in ((0, "Materiales de 1 intento"), (2, "Recolectando"), (3, "Comprando")):
            head = label(text, f.small, theme.MUTED)
            grid.addWidget(head, 0, c, Qt.AlignLeft if c == 0 else Qt.AlignRight)
        r = 1
        for m in row.materials:
            grid.addWidget(label(f"{m.count}×  {m.name.replace(' (Bound)', '')}", f.body, theme.TEXT), r, 0)
            text, color = SOURCE.get(m.how, SOURCE["unknown"])
            grid.addWidget(_chip(text, color, f), r, 1, Qt.AlignLeft | Qt.AlignVCenter)
            grid.addWidget(self._price_cell(m.unit, m.total, m.how, m.at), r, 2, Qt.AlignRight)
            grid.addWidget(self._price_cell(m.buy, m.buy_total, m.buy_how, m.at), r, 3, Qt.AlignRight)
            if m.source != "npc":
                grid.addWidget(self._price_edit(m), r, 4, Qt.AlignLeft)
            r += 1
            if m.source == "craft":
                grid.addWidget(self._raw_line(m), r, 0, 1, 4)
                r += 1
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #1f2b47;")
        grid.addWidget(line, r, 0, 1, 4)
        r += 1
        grid.addWidget(label("Total", f.name, theme.TEXT), r, 0)
        for c, (cost, known, missing) in ((2, (row.cost, row.known_cost, row.missing)),
                                          (3, (row.buy_cost, row.known_buy_cost, row.missing_buy))):
            total = label(_kinah(cost) if cost is not None else _kinah(known) + " + ?", f.name,
                          theme.TEXT if cost is not None else theme.WARN)
            grid.addWidget(total, r, c, Qt.AlignRight)
        grid.setColumnStretch(0, 1)
        col.addLayout(grid)

        # 3. three short notes
        notes = QVBoxLayout()
        notes.setSpacing(2)
        if success is not None:
            curve = row.fail_curve()
            if row.can_craft is False:
                text = f"Fallo: pide nivel {row.needs} (tienes {row.level}); al llegar fallarías un {1 - success:.0%}"
            else:
                where = "al nivel justo" if row.level is None else f"con tu nivel {row.level}"
                text = f"Fallo: {1 - success:.0%} {where}"
                if curve and curve[-1][1] < 1 - success - 0.005:
                    text += f"  →  {curve[-1][1]:.0%} desde el nivel {curve[-1][0]}"
            notes.addWidget(self._note("✕", text, _fail_tip(row)))
        combo = row.recipe.get("combo")
        if isinstance(combo, dict) and combo.get("chance"):
            what = f"otro {row.product}" if combo.get("same") else combo.get("name", "")
            text = f"Combo: {combo['chance']}% tras un éxito → {what}"
            price = self.crafting.latest_prices().get(combo.get("name"))
            tip = "No se suma a la ganancia: los datos no dicen si reemplaza al resultado normal o viene aparte."
            if price and not combo.get("same"):
                bonus = (success if success is not None else 1) * combo["chance"] / 100 * price[0] * (1 - row.fee)
                text += f" (vale {_kinah(price[0])}: {_kinah(bonus, sign=True)} de media, no sumado)"
            else:
                text += " (no sumado)"
            notes.addWidget(self._note("★", text, tip))
        sales = row.sales
        if sales.observations >= 2:
            unit = "uds." if sales.by_offers else "ofertas"
            text = f"Ventas: {sales.sold} {unit} en {span_text(sales.hours)}"
            if sales.by_offers:
                text += f" · {sales.added} nuevas a la venta"
            tip = (f"Entre tus {sales.observations} muestreos desaparecieron {sales.sold} {unit} (vendidas o "
                   f"retiradas).")
            if not sales.by_offers:
                tip += " Escanea su pantalla de ofertas para contar unidades."
            if sales.hours < 6:
                tip += " Con 6 h o más entre muestreos se calcula cuánto se vende al día."
            notes.addWidget(self._note("↻", text, tip))
        elif row.sell is not None:
            notes.addWidget(self._note("↻", "Ventas: haz otro muestreo más tarde para ver si se vende",
                                       "Lo que desaparece del mercado entre dos muestreos se vendió (o lo retiraron)."))
        col.addLayout(notes)
        return frame

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
        how = ("comprando todos los materiales (lo de NPC a su precio; lo procesado, por el camino más barato)"
               if buying else "recolectando lo que se recolecta (gratis) y fabricando lo que se fabrica")
        rate = f" × {row.success:.0%} de éxito" if row.success is not None else ""
        return (f"Ganancia por intento, {how}:<br>te pagan {_kinah(row.net)}{rate} = {_kinah(row.expected)} de media"
                f"<br>− materiales {_kinah(cost)}<br>= {_kinah(row.expected - cost, sign=True)}"
                f"<br>Si el craft falla, se pierden los materiales.")

    def _price_edit(self, m):
        """Your price for a material: a small 'editar' button, the box itself when the price is missing."""
        f = self.fonts
        key = m.name
        if key in self._editing or m.unit is None or m.buy is None or self.crafting.manual_price(m.name):
            edit = QLineEdit()
            edit.setFixedWidth(96)
            edit.setValidator(QIntValidator(0, 2_000_000_000))
            edit.setPlaceholderText("tu precio")
            manual = self.crafting.manual_price(m.name)
            if manual:
                edit.setText(str(manual))
            edit.setToolTip("Tu precio por unidad, para cuando no aparece en los muestreos (si aparece, manda el del "
                            "mercado). Vacío = sin precio.")
            edit.editingFinished.connect(lambda n=m.name, e=edit: self._set_price(n, e))
            return edit
        button = QPushButton("editar")
        button.setProperty("flat", True)
        button.setToolTip("Poner tu propio precio para este material")
        button.clicked.connect(lambda _=False, k=key: (self._editing.add(k), self.refresh()))
        return button

    def _raw_line(self, m):
        """'↳ hacerlo tú 205 c/u · comprarlo hecho 300 c/u → mejor hacerlo tú (ahorras 95)', with the recipe in the
        tooltip."""
        f = self.fonts
        recipe = m.raw_recipe
        made = f"hacerlo tú {m.raw:,.0f} c/u" if m.raw is not None else "hacerlo tú: ?"
        bought = f"comprarlo hecho {m.market:,.0f} c/u" if m.market is not None else "comprarlo hecho: ?"
        verdict, color = "", theme.MUTED
        if m.market is not None and m.raw is not None:
            if m.raw < m.market:
                verdict, color = f"  →  mejor hacerlo tú (ahorras {m.market - m.raw:,.0f})", theme.GAIN
            else:
                verdict, color = "  →  mejor comprarlo hecho", theme.WARN
        line = label(f"      ↳ {made} · {bought}{verdict}", f.small, color)
        if recipe is not None:
            row = RecipeRow(recipe, m.name, level=self.crafting.level(recipe["profession"]))
            raws = " + ".join(f"{x['count']}× {x['name'].replace(' (Bound)', '')}" for x in recipe["materials"])
            line.setToolTip(f"Hacerlo tú: {raws} → {row.count} {m.name}, con "
                            f"{PROFESSION_ES.get(recipe['profession'], recipe['profession'])} {recipe['proficiency']}"
                            + (f" ({row.success:.0%} de éxito)" if row.success is not None else "")
                            + ", comprando el material raw.")
        else:
            line.setToolTip("Para hacerlo tú falta el precio de su material raw o el nivel de la profesión que lo hace.")
        line.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        return line

    def _price_cell(self, unit, total, how, at):
        """'4,400 (880 c/u)' / 'gratis' / '?' for one material in one of the two ways."""
        if unit is None:
            text, color = "?", theme.WARN
        elif unit == 0:
            text, color = "gratis", theme.GAIN
        else:
            text, color = f"{_kinah(total)}  ({unit:,.0f} c/u)", theme.TEXT
        cell = label(text, self.fonts.body, color)
        cell.setFixedWidth(DETAIL_W)
        cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tip = {"npc": "precio del NPC", "market": "precio del mercado", "manual": "tu precio",
               "craft": "lo que te cuesta fabricarlo", "gather": "lo recolectas",
               "raw": "comprando su material raw y procesándolo tú (más barato que procesado)"}.get(how, "sin precio")
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
        process = QPushButton(f"Procesar {len(ready)} {'captura' if len(ready) == 1 else 'capturas'}")
        process.setEnabled(bool(ready))
        process.setToolTip("Junta las capturas marcadas en un muestreo con la fecha y hora en que las tomaste (y las "
                           "borra). Si las tomaste a menos de 15 min de un muestreo, se suman a ese.")
        process.clicked.connect(self._process)
        bar.addWidget(process)
        unchecked = [s for s in shots if not s.checked]
        if unchecked:
            drop = QPushButton(f"Borrar las {len(unchecked)} no marcadas")
            drop.setProperty("flat", True)
            drop.clicked.connect(self._remove_unchecked)
            bar.addWidget(drop)
        bar.addStretch(1)
        if any(not s.read for s in shots):
            bar.addWidget(label("Leyendo capturas…", f.small, theme.GOLD))
        self.body.addLayout(bar)
        if not shots:
            self._hint("No hay capturas por procesar.", theme.TEXT)
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
        thumb.setToolTip("Abrir la captura")
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
                lines = [f"{unit:,} c/u × {qty}" + (f" · quedan {h:.0f} h" if h is not None else "")
                         for _, unit, _, qty, h in shot.rows]
            check.setToolTip("<br>".join(lines))
        foot = QHBoxLayout()
        foot.addWidget(label(time.strftime("%H:%M:%S", time.localtime(shot.at)), f.small, theme.MUTED), 1)
        if shot.read and (shot.error or shot.kind == "unknown"):
            again = QPushButton("Volver a leer")
            again.setProperty("flat", True)
            again.clicked.connect(lambda _=False, s=shot: self.ctl.crafting_reread(s))
            foot.addWidget(again)
        remove = QPushButton("Borrar")
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
                pix.fill(QColor("#0d1628"))
            else:
                pix = src.scaledToWidth(round(THUMB_W * dpr), Qt.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
            self._thumbs[shot.file] = pix
        return self._thumbs[shot.file]

    def shot_read(self, shot):
        """A shot finished reading: its caption and checkbox change."""
        if self.isVisible():
            self.refresh()

    # --- Muestreos ---
    def _samples_tab(self):
        f, crafting = self.fonts, self.crafting
        samples = list(reversed(crafting.samples))
        if not samples:
            self._hint("Aún no hay muestreos. " + self._how_to())
            return
        if self._sample not in crafting.samples:
            self._sample = samples[0]
        self._hint("Cada muestreo guarda los precios y ofertas de ese momento. Compáralos para ver qué se vende: lo "
                   "que desaparece entre uno y otro se vendió (o lo retiraron).")
        for sample in samples:
            line = QFrame()
            line.setObjectName("Row")
            line.setProperty("open", sample is self._sample)
            line.setCursor(Qt.PointingHandCursor)
            line.mousePressEvent = lambda e, s=sample: self._select_sample(s)
            lay = QHBoxLayout(line)
            lay.setContentsMargins(12, 6, 12, 6)
            prof = crafting.professions.get(sample.get("profession"), {}).get("name", sample.get("profession", ""))
            lay.addWidget(label(f"{_clock(sample['at'])}  ·  {prof}", f.name,
                                theme.GOLD if sample is self._sample else theme.TEXT), 1)
            n = len(sample["items"])
            lay.addWidget(label(f"{n} {'objeto' if n == 1 else 'objetos'} · {sample.get('shots', 0)} capturas",
                                f.small, theme.MUTED))
            remove = QPushButton("Borrar")
            remove.setProperty("flat", True)
            remove.clicked.connect(lambda _=False, s=sample: self._delete_sample(s))
            lay.addWidget(remove)
            self.body.addWidget(line)
        self.body.addWidget(SectionTitle(f"Muestreo del {_clock(self._sample['at'])}", f))
        self._sample_items(self._sample)

    def _select_sample(self, sample):
        self._sample = sample
        self.refresh()

    def _delete_sample(self, sample):
        answer = QMessageBox.question(self, "Borrar muestreo",
                                      f"¿Borrar el muestreo del {_clock(sample['at'])}? Sus precios dejan de contar.")
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
        for c, text in enumerate(("Objeto", "Precio más bajo", "Cambio", "Ofertas", "Cambio", "")):
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
        self.setWindowTitle(f"{APP_NAME} — Gráfica de precio: {self.name}")
        self.title.setText(f"Gráfica de precio · {self.name}")
        sales = crafting.sales(self.name)
        text, _ = sales.verdict()
        prices = [p for _, p, _, _ in hist if p]
        parts = [f"{len(hist)} {'muestreo' if len(hist) == 1 else 'muestreos'}"]
        if prices:
            parts.append(f"más bajo {min(prices):,} · más alto {max(prices):,} · último {prices[-1]:,}")
        parts.append(text)
        self.subtitle.setText(" · ".join(parts) + ". Línea: precio más bajo; barras: ofertas a la venta.")
        self.chart.set_points([(at, p, n) for at, p, n, _ in hist])
        _clear(self.table)
        for c, head in enumerate(("Fecha", "Precio más bajo", "Ofertas", "Unidades vistas", "Precio medio visto")):
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
        p.setPen(QPen(QColor("#1f2b47"), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        self._screen = []
        if not self.points:
            p.setPen(theme.MUTED)
            p.setFont(f.body)
            p.drawText(self.rect(), Qt.AlignCenter, "Sin precios todavía: procesa un muestreo que tenga este objeto.")
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
                       Qt.AlignLeft | Qt.AlignVCenter, f"{top_offers} ofertas")
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
            text = f"{_clock(t)}<br><b>{v:,}</b> el más bajo" + (f"<br>{n} ofertas" if n is not None else "")
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()


def _fail_cell(row, locked):
    """'17% · mín 9%': your chance of failing and the lowest this recipe can get to."""
    if row.success is None:
        return "—", theme.MUTED
    best = row.min_fail
    text = f"{1 - row.success:.0%}" + (f" · mín {best[0]:.0%}" if best and best[0] < 1 - row.success - 0.005 else "")
    return text, theme.MUTED if locked else theme.TEXT


def _fail_tip(row):
    if row.success is None:
        return "Sin datos de éxito para esta receta"
    lines = [f"Fallo {'al nivel justo' if row.level is None else 'con tu nivel'}: {1 - row.success:.0%} "
             f"(si falla, pierdes los materiales)"]
    for lv, fail in row.fail_curve():
        lines.append(f"nivel {lv}: {fail:.0%}")
    best = row.min_fail
    if best:
        lines.append(f"Lo mínimo para esta receta: {best[0]:.0%} (desde nivel {best[1]})")
    return "<br>".join(lines)


def _profit_cell(profit, sell):
    """(text, colour) of a profit column."""
    if profit is None:
        return ("?" if sell is not None else "—"), theme.MUTED
    return _kinah(profit, sign=True), theme.GAIN if profit > 0 else theme.COMBAT


def _cost_tip(way, cost, known, missing, row):
    if cost is None:
        return f"{way}: falta el precio de {', '.join(missing)} (escanea su pantalla del mercado o escríbelo al abrir la fila)"
    tip = f"{way}: materiales {_kinah(cost)} por intento"
    if row.margin is not None and way == "Recolectando":
        tip += f" · margen {row.margin:+.0%}"
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
