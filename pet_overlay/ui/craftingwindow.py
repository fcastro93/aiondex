"""Crafting window: which recipes of your profession are worth crafting (sorted by profit after the market's tax, with
what each one needs and where it comes from), the market shots waiting to be processed, the samples taken and each
item's price over time."""
import os
import time
import webbrowser

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QToolTip, QVBoxLayout, QWidget)

from .. import APP_NAME
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
VERDICT_COLOR = {"good": theme.GAIN, "bad": theme.COMBAT, "none": theme.MUTED}
COLUMNS = (("Precio", 84), ("Recibes", 84), ("Coste", 84), ("Ganancia", 92), ("Margen", 62), ("¿Se vende?", 150))
THUMB_W = 340
CARD_STYLE = """
QFrame#Card { background: #121c33; border: 1px solid #1f2b47; border-radius: 8px; }
QFrame#Row { background: #101a30; border: 1px solid #1a2643; border-radius: 6px; }
QFrame#Row[open="true"] { border-color: #2c4170; }
QFrame#Detail { background: #0d1628; border: 1px solid #1a2643; border-radius: 6px; }
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
        if not crafting.samples:
            self._hint("Aún no hay muestreos. " + self._how_to())
        else:
            last = crafting.samples[-1]
            self._hint(f"Precios del muestreo más reciente que tiene cada objeto (el último: {_clock(last['at'])}, "
                       f"{_ago(last['at'])}). El mercado se queda el {crafting.fee:.0%} de cada venta. Ganancia por "
                       f"craft = precio × cantidad − {crafting.fee:.0%} − coste de los materiales.")
        options = QHBoxLayout()
        gathered = QCheckBox("Contar lo recolectado a precio de mercado")
        gathered.setToolTip("Apagado: lo que recolectas cuesta 0. Encendido: vale lo que se paga en el mercado "
                            "(lo que dejas de ganar si lo vendieras).")
        gathered.setChecked(bool(crafting.config.get("value_gathered")))
        gathered.toggled.connect(lambda on: (crafting.set_option("value_gathered", on), self.refresh(),
                                             self.ctl.refresh()))
        options.addWidget(gathered)
        everything = QCheckBox("Mostrar también los que no tienen precio")
        everything.setChecked(bool(crafting.config.get("show_unpriced")))
        everything.toggled.connect(lambda on: (crafting.set_option("show_unpriced", on), self.refresh()))
        options.addWidget(everything)
        options.addStretch(1)
        self.body.addLayout(options)

        rows = crafting.recipe_rows(self.ctl.character_faction())
        if query:
            rows = [r for r in rows if query in r.product.lower() or any(query in m.name.lower() for m in r.materials)]
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

    def _header_row(self):
        f = self.fonts
        line = QWidget()
        lay = QHBoxLayout(line)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.addWidget(label("Objeto", f.small, theme.MUTED), 1)
        for text, width in COLUMNS:
            lay.addWidget(_cell(text, f.small, theme.MUTED, width))
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
        names.addWidget(label(f"{arrow}  {row.product}{count}", f.name,
                              QColor(GRADE_COLOR.get(row.recipe.get("grade"), "#D6DAE3")), elide=True))
        sub = [f"Oficio {row.recipe['proficiency']}" if row.recipe.get("proficiency") else "",
               f"{row.listings} ofertas" if row.listings is not None else "",
               f"precio de {_ago(row.sell_at)}" if row.sell_at else "sin precio de mercado"]
        names.addWidget(label(" · ".join(s for s in sub if s), f.small, theme.MUTED, elide=True))
        lay.addLayout(names, 1)
        missing = row.missing
        cost = row.cost if row.cost is not None else (row.known_cost if missing else None)
        cells = [(_kinah(row.sell), theme.TEXT), (_kinah(row.net), theme.TEXT),
                 ((_kinah(cost) + ("+?" if missing else "")) if cost is not None else "—",
                  theme.WARN if missing else theme.TEXT)]
        profit = row.profit
        cells.append((_kinah(profit, sign=True) if profit is not None else ("?" if row.sell is not None else "—"),
                      theme.MUTED if profit is None else (theme.GAIN if profit > 0 else theme.COMBAT)))
        cells.append((f"{row.margin:+.0%}" if row.margin is not None else "—",
                      theme.MUTED if row.margin is None else (theme.GAIN if row.margin > 0 else theme.COMBAT)))
        text, kind = row.sales.verdict()
        cells.append((text, VERDICT_COLOR[kind]))
        for (text, color), (_, width) in zip(cells, COLUMNS):
            lay.addWidget(_cell(text, f.name, color, width))
        if missing:
            frame.setToolTip(f"Falta el precio de {', '.join(missing)}: escanea su categoría del mercado o escríbelo "
                             f"al abrir la fila.")
        chart = self._chart_button(row.product)
        chart.setEnabled(row.sales.observations > 0)
        lay.addSpacing(8)
        lay.addWidget(chart)
        return frame

    def _detail(self, row):
        """What one craft needs: each material, where it comes from and what it costs."""
        f = self.fonts
        frame = QFrame()
        frame.setObjectName("Detail")
        col = QVBoxLayout(frame)
        col.setContentsMargins(16, 8, 16, 10)
        col.setSpacing(4)
        col.addWidget(label(f"Para 1 craft ({row.count} {'unidad' if row.count == 1 else 'unidades'}):", f.small,
                            theme.MUTED))
        for m in row.materials:
            line = QHBoxLayout()
            line.setSpacing(10)
            line.addWidget(label(f"{m.count}×  {m.name}", f.body, theme.TEXT), 1)
            text, color = SOURCE.get(m.how, SOURCE["unknown"])
            if m.how == "market" and m.source == "gather":
                text = "Recolectas · a precio de mercado"
            line.addWidget(_chip(text, color, f), 0, Qt.AlignVCenter)
            if m.how in ("market", "manual", "unknown") or (m.source == "market"):
                edit = QLineEdit()
                edit.setFixedWidth(110)
                edit.setValidator(QIntValidator(0, 2_000_000_000))
                edit.setPlaceholderText("precio c/u")
                manual = self.crafting.manual_price(m.name)
                if manual:
                    edit.setText(str(manual))
                edit.setToolTip("Tu precio por unidad, para cuando no aparece en los muestreos (si aparece, manda el "
                                "del mercado). Vacío = sin precio.")
                edit.editingFinished.connect(lambda n=m.name, e=edit: self._set_price(n, e))
                line.addWidget(edit)
            unit = "gratis" if m.unit == 0 else (f"{m.unit:,.0f} c/u" if m.unit is not None else "—")
            if m.how == "market" and m.at:
                unit += f" ({_ago(m.at)})"
            cell = label(unit, f.body, theme.MUTED)
            cell.setFixedWidth(180)
            cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            line.addWidget(cell)
            total = label(_kinah(m.total) if m.unit else ("0" if m.unit == 0 else "?"), f.name,
                          theme.TEXT if m.unit is not None else theme.WARN)
            total.setFixedWidth(100)
            total.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            line.addWidget(total)
            col.addLayout(line)
        summary = QHBoxLayout()
        summary.addStretch(1)
        parts = [f"Coste {_kinah(row.cost) if row.cost is not None else _kinah(row.known_cost) + ' + ?'}"]
        if row.net is not None:
            parts.append(f"recibes {_kinah(row.net)} ({_kinah(row.sell)} × {row.count} − {row.fee:.0%})")
        if row.profit is not None:
            parts.append(f"ganas {_kinah(row.profit, sign=True)} por craft")
        summary.addWidget(label(" · ".join(parts), f.name, theme.TEXT))
        col.addLayout(summary)
        sales = row.sales
        if sales.observations >= 2:
            unit = "unidades" if sales.by_offers else "ofertas"
            what = (f"Entre tus {sales.observations} muestreos ({sales.hours:.0f} h) desaparecieron {sales.sold} "
                    f"{unit} (vendidas o retiradas)")
            if not sales.by_offers:
                what += "; escanea su pantalla de ofertas para contar unidades"
            col.addWidget(label(what + ".", f.small, theme.MUTED))
        elif row.sell is not None:
            col.addWidget(label("Con otro muestreo más tarde se verá si se vende (lo que desaparece del mercado).",
                                f.small, theme.MUTED))
        return frame

    # --- Capturas ---
    def _shots_tab(self):
        f, crafting = self.fonts, self.crafting
        shots = crafting.shots
        self._hint(self._how_to())
        bar = QHBoxLayout()
        ready = crafting.ready()
        process = QPushButton(f"Procesar {len(ready)} {'captura' if len(ready) == 1 else 'capturas'}")
        process.setEnabled(bool(ready))
        process.setToolTip("Junta las capturas marcadas en un muestreo con la fecha y hora de ahora (y las borra)")
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
