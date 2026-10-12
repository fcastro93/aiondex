"""Crafting's Ventas tab: your sales one by one (price, tax, what you collected, the materials, the profit, the proof),
what you have on sale, the totals of a period, each product's numbers and a chart of the last two weeks. The real
profit counts what you marked: the materials you bought (at the market's price of the sale's time) and the ones you
farmed, gathered or made (free, or their own materials)."""
import os
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ..sales import RECENT_DAYS
from . import theme
from .widgets import SectionTitle, label

PERIODS = [("day", "Hoy"), ("week", "7 días"), ("month", "30 días"), ("all", "Todo")]
SALES_SHOWN = 40
SOURCE_TEXT = {"gather": "se recolecta", "market": "botín de monstruos / mercado", "craft": "se fabrica",
               "npc": "NPC"}


def _kinah(value, sign=False):
    return "—" if value is None else (f"{value:+,.0f}" if sign else f"{value:,.0f}")


def _clock(ts):
    return time.strftime("%d/%m %H:%M", time.localtime(ts))


def _span(hours):
    if hours is None:
        return "—"
    return f"{hours * 60:.0f} min" if hours < 1 else f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.1f} días"


def period_start(key, now):
    local = time.localtime(now)
    today = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    return {"day": today, "week": today - 6 * 86400, "month": today - 29 * 86400}.get(key, 0)


class SalesChart(QWidget):
    """Bars per day: what you collected (blue) and what it left after the materials (green / red)."""

    def __init__(self, days, fonts):
        super().__init__()
        self.days, self.fonts = days, fonts
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(46, 10, -8, -24)
        top = max([max(net, profit or 0) for _, net, profit, _ in self.days] + [1])
        low = min([min(0, profit or 0) for _, _, profit, _ in self.days])
        span = top - low or 1
        y0 = r.bottom() - (0 - low) / span * r.height()
        p.setFont(self.fonts.small)
        p.setPen(QPen(QColor(255, 255, 255, 30)))
        for value in (top, top / 2, 0):
            y = r.bottom() - (value - low) / span * r.height()
            p.drawLine(int(r.left()), int(y), int(r.right()), int(y))
        p.setPen(theme.MUTED)
        for value in (top, 0):
            y = r.bottom() - (value - low) / span * r.height()
            text = f"{value / 1_000_000:.1f}M" if value >= 1_000_000 else f"{value / 1000:.0f}k" if value >= 1000 \
                else f"{value:.0f}"
            p.drawText(QRectF(0, y - 8, 42, 16), Qt.AlignRight | Qt.AlignVCenter, text)
        n = len(self.days)
        slot = r.width() / max(1, n)
        bar = slot * 0.34
        for i, (start, net, profit, count) in enumerate(self.days):
            x = r.left() + i * slot + slot * 0.14
            if net:
                h = net / span * r.height()
                p.fillRect(QRectF(x, y0 - h, bar, h), theme.ACCENT)
            if profit:
                h = abs(profit) / span * r.height()
                p.fillRect(QRectF(x + bar + 2, y0 - h if profit > 0 else y0, bar, h),
                           theme.GAIN if profit > 0 else theme.COMBAT)
            if (n - 1 - i) % 2 == 0 or n <= 8:             # today always labelled
                p.setPen(theme.MUTED)
                p.drawText(QRectF(r.left() + i * slot, r.bottom() + 4, slot, 16), Qt.AlignCenter,
                           time.strftime("%d/%m", time.localtime(start)))


class SalesView(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.ctl = window, window.ctl
        self.ledger = window.crafting.ledger
        self.fonts = window.fonts
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(10)
        self._build()

    def _build(self):
        f, ledger, now = self.fonts, self.ledger, time.time()
        crafting = self.window.crafting
        crafting.faction = self.ctl.crafting_faction()
        crafting.forget_costs()                      # today's prices, levels or marks may have changed
        crafting.keep_yields()
        hk = self.ctl.settings["hotkeys"].get("crafting_scan", "")
        how = label("Cuando vendas algo: en el mercado abre Settlement (antes de cobrar) y pulsa Escanear"
                    + (f" ({hk})" if hk else "") + ". AionDex guarda cada venta con su captura como prueba y calcula "
                    "lo que te dejó. Escanea también List para seguir lo que tienes a la venta y cuánto tarda en "
                    "venderse.", f.small, theme.MUTED)
        how.setWordWrap(True)
        self.col.addWidget(how)

        # period + totals
        bar = QHBoxLayout()
        period = self.window.sales_period
        for key, text in PERIODS:
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.setChecked(key == period)
            button.clicked.connect(lambda _=False, k=key: self.window.set_sales_period(k))
            bar.addWidget(button)
        bar.addStretch(1)
        self.col.addLayout(bar)
        query = self.window.search.text().strip().lower()
        start = period_start(period, now)
        sales = [s for s in ledger.sales if s["at"] >= start and (not query or query in s["item"].lower())]
        t = ledger.totals(sales)
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        tile = self.window._tile
        tiles.addWidget(tile("Vendido", _kinah(t["gross"]), f"{t['count']} ventas · {t['units']} uds."))
        tiles.addWidget(tile("Impuesto del mercado", _kinah(t["tax"]),
                             f"{t['tax'] / t['gross']:.0%} de lo vendido" if t["gross"] else ""))
        tiles.addWidget(tile("Cobrado", _kinah(t["net"]), "lo que te llega"))
        tiles.addWidget(tile("Materiales", _kinah(t["cost"]), "según lo que marcaste",
                             tip="Lo que te costaron los materiales según marcaste abajo: los comprados al precio "
                                 "medio del mercado del día de la venta (de 00:00 a 23:59; cuando el día acaba queda "
                                 "fijo), los del NPC a su precio, los farmeados o recolectados gratis, contando los "
                                 "intentos que fallan. Lo que no fabricas (botín, compras) no tiene coste."))
        unknown = f" · {t['unknown']} sin precio de materiales" if t["unknown"] else ""
        tiles.addWidget(tile("Ganancia real", _kinah(t["profit"], sign=True),
                             (f"margen {t['margin']:.0%}" if t["margin"] is not None else "") + unknown,
                             theme.GAIN if t["profit"] > 0 else theme.COMBAT if t["profit"] < 0 else theme.TEXT,
                             tip="Lo cobrado (ya sin el impuesto del mercado) menos lo que te costaron los "
                                 "materiales según lo que marcaste, con los intentos que fallan"))
        tiles.addWidget(tile("Comprando todo", _kinah(t["buy_profit"], sign=True), "si compraras los materiales",
                             theme.GAIN if t["buy_profit"] > 0 else theme.COMBAT if t["buy_profit"] < 0 else theme.TEXT))
        self.col.addLayout(tiles)
        if sales:
            self._highlights(sales)
        self._marks()

        # what is on sale
        self._on_sale(now)

        # chart
        if ledger.sales:
            self.col.addWidget(SectionTitle(f"Últimos {RECENT_DAYS} días · azul cobrado, verde ganancia", f))
            self.col.addWidget(SalesChart(ledger.by_day(now), f))

        # per product
        products = [p for p in ledger.by_product() if not query or query in p[0].lower()]
        if products:
            self.col.addWidget(SectionTitle("Por producto (todo)", f))
            self._products(products)

        # one by one
        self.col.addWidget(SectionTitle(f"Ventas · {dict(PERIODS)[period].lower()}", f))
        if not sales:
            self.col.addWidget(label("Ninguna venta en este periodo." if ledger.sales else
                                     "Aún no hay ventas guardadas.", f.body, theme.MUTED))
        else:
            self._sales(list(reversed(sales))[:SALES_SHOWN])
            if len(sales) > SALES_SHOWN:
                self.col.addWidget(label(f"y {len(sales) - SALES_SHOWN} más (busca un objeto o cambia el periodo)",
                                         f.small, theme.MUTED))

    def _highlights(self, sales):
        """Short lines: the best sale, the product that left the most, the profit per day, the time to sell."""
        f, ledger = self.fonts, self.ledger
        known = [s for s in sales if ledger.profit(s) is not None]
        lines = []
        if known:
            best = max(known, key=ledger.profit)
            lines.append(f"Mejor venta: {best['item']} ×{best['qty']} el {_clock(best['at'])} "
                         f"({_kinah(ledger.profit(best), sign=True)})")
            days = max(1.0, (time.time() - min(s["at"] for s in sales)) / 86400)
            lines.append(f"De media: {_kinah(sum(ledger.profit(s) for s in known) / days, sign=True)} al día")
        hours = [h for h in (ledger.hours_to_sell(s) for s in sales) if h is not None]
        if hours:
            lines.append(f"Tarda en venderse: {_span(sum(hours) / len(hours))} de media")
        under = [s["price"] / s["qty"] / s["market"] - 1 for s in sales if s.get("market")]
        if under:
            avg = sum(under) / len(under)
            lines.append(f"Tu precio frente al más bajo del mercado: {avg:+.0%}")
        if lines:
            row = label("   ·   ".join(lines), f.small, theme.TEXT)
            row.setWordWrap(True)
            self.col.addWidget(row)

    def _on_sale(self, now):
        f = self.fonts
        rows, total, collect = self.ledger.on_sale(self.window.crafting_fee())
        listings = self.ledger.listings
        if not listings:
            return
        listed = listings.get("listed")
        seen = f"{len(rows)} de {listed}" if listed and listed > len(rows) else str(len(rows))
        self.col.addWidget(SectionTitle(f"A la venta · escaneado {_clock(listings['at'])}", f))
        head = label(f"{seen} {'publicación' if len(rows) == 1 else 'publicaciones'} por {_kinah(total)} · cobrarías "
                     f"{_kinah(collect)} tras el impuesto", f.body, theme.TEXT)
        self.col.addWidget(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(2)
        for c, text in enumerate(("Objeto", "Uds", "Precio", "Por unidad", "Lleva", "Le queda", "Mercado")):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignLeft if c == 0 else Qt.AlignRight)
        gone = (now - listings["at"]) / 3600
        for r, (name, qty, price, unit, left, listed_at) in enumerate(sorted(rows, key=lambda x: x[4] or 0), start=1):
            grid.addWidget(label(name, f.body, theme.TEXT), r, 0)
            grid.addWidget(label(str(qty), f.body, theme.TEXT), r, 1, Qt.AlignRight)
            grid.addWidget(label(_kinah(price), f.name, theme.TEXT), r, 2, Qt.AlignRight)
            grid.addWidget(label(_kinah(unit), f.body, theme.MUTED), r, 3, Qt.AlignRight)
            grid.addWidget(label(_span((now - listed_at) / 3600) if listed_at else "—", f.body, theme.MUTED), r, 4,
                           Qt.AlignRight)
            remaining = None if left is None else left - gone
            grid.addWidget(label("caducó" if remaining is not None and remaining <= 0 else _span(remaining), f.body,
                                 theme.WARN if remaining is not None and remaining < 12 else theme.MUTED), r, 5,
                           Qt.AlignRight)
            market = self.window.crafting.market_price(name)
            vs = f"{unit / market - 1:+.0%}" if market and unit else "—"
            grid.addWidget(label(vs, f.body, theme.COMBAT if market and unit and unit > market * 1.05 else
                                 theme.MUTED), r, 6, Qt.AlignRight)
        grid.setColumnStretch(0, 1)
        self.col.addLayout(grid)

    def _products(self, products):
        f = self.fonts
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(2)
        for c, text in enumerate(("Objeto", "Ventas", "Uds", "Cobrado", "Ganancia", "Margen", "Precio medio / ud",
                                  "vs mercado", "Tarda")):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignLeft if c == 0 else Qt.AlignRight)
        for r, (item, t, unit, vs, hours) in enumerate(products, start=1):
            grid.addWidget(label(item, f.body, theme.TEXT), r, 0)
            grid.addWidget(label(str(t["count"]), f.body, theme.TEXT), r, 1, Qt.AlignRight)
            grid.addWidget(label(str(t["units"]), f.body, theme.TEXT), r, 2, Qt.AlignRight)
            grid.addWidget(label(_kinah(t["net"]), f.name, theme.TEXT), r, 3, Qt.AlignRight)
            grid.addWidget(label(_kinah(t["profit"], sign=True), f.name,
                                 theme.GAIN if t["profit"] > 0 else theme.COMBAT if t["profit"] < 0 else theme.TEXT),
                           r, 4, Qt.AlignRight)
            grid.addWidget(label(f"{t['margin']:.0%}" if t["margin"] is not None else "—", f.body, theme.MUTED), r, 5,
                           Qt.AlignRight)
            grid.addWidget(label(_kinah(unit), f.body, theme.TEXT), r, 6, Qt.AlignRight)
            grid.addWidget(label(f"{vs:+.0%}" if vs is not None else "—", f.body, theme.MUTED), r, 7, Qt.AlignRight)
            grid.addWidget(label(_span(hours), f.body, theme.MUTED), r, 8, Qt.AlignRight)
        grid.setColumnStretch(0, 1)
        self.col.addLayout(grid)

    def _marks(self):
        """Which materials you bought and which you farmed: each sale's real cost comes from these marks."""
        f = self.fonts
        crafting = self.window.crafting
        mats = crafting.sold_materials()
        if not mats:
            return
        self.col.addWidget(SectionTitle("Materiales: ¿comprados o farmeados?", f))
        note = label("Marca de dónde sacaste cada material de lo que vendes: los comprados cuentan al precio medio "
                     "del mercado del día de la venta (de 00:00 a 23:59, con tus muestreos de ese día; cuando el día "
                     "acaba queda fijo y los cambios del mercado ya no mueven tus ventas), los farmeados o recolectados "
                     "no cuestan nada, y lo que fabricas tú cuenta sus propios materiales.",
                     f.small, theme.MUTED)
        note.setWordWrap(True)
        self.col.addWidget(note)
        prices = crafting.day_prices(time.time())
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        for c, text in enumerate(("Material", "De dónde sale", "Para", "Media de hoy", "")):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignRight if c == 3 else Qt.AlignLeft)
        npc = []
        r = 1
        for name, source, products, depth in mats:
            if source == "npc":
                npc.append(name)
                continue
            grid.addWidget(label(("    " * depth) + name, f.body, theme.TEXT), r, 0)
            grid.addWidget(label(SOURCE_TEXT.get(source, source), f.small, theme.MUTED), r, 1)
            used = label(", ".join(products), f.small, theme.MUTED, elide=True)
            grid.addWidget(used, r, 2)
            price = prices.get(name)
            grid.addWidget(label(_kinah(price[0]) if price else "sin precio", f.body,
                                 theme.TEXT if price else theme.WARN), r, 3, Qt.AlignRight)
            choice = crafting.mat_choice(name, source)
            buttons = QHBoxLayout()
            buttons.setSpacing(4)
            for key, text in (("buy", "Comprado"), ("farm", "Hecho por mí" if source == "craft" else
                                                     "Recolectado" if source == "gather" else "Farmeado")):
                button = QPushButton(text)
                button.setProperty("tab", True)
                button.setCheckable(True)
                button.setChecked(choice == key)
                button.clicked.connect(lambda _=False, n=name, k=key: self._set_mark(n, k))
                buttons.addWidget(button)
            holder = QWidget()
            holder.setLayout(buttons)
            buttons.setContentsMargins(0, 0, 0, 0)
            grid.addWidget(holder, r, 4)
            r += 1
        grid.setColumnStretch(2, 1)
        self.col.addLayout(grid)
        if npc:
            self.col.addWidget(label("Siempre comprados al NPC (a su precio): " + ", ".join(npc), f.small,
                                     theme.MUTED))

    def _set_mark(self, name, key):
        self.window.crafting.set_mat_choice(name, key)
        self.window.refresh()
        self.ctl.refresh()

    def _sales(self, sales):
        f, ledger = self.fonts, self.ledger
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(2)
        for c, text in enumerate(("Fecha", "Objeto", "Uds", "Precio", "Impuesto", "Cobrado", "Materiales", "Ganancia",
                                  "Margen", "", "")):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignLeft if c < 2 else Qt.AlignRight)
        for r, s in enumerate(sales, start=1):
            when = label(_clock(s["at"]) + ("" if s.get("time_known") else " ?"), f.body, theme.MUTED)
            if not s.get("time_known"):
                when.setToolTip(f"No se pudo leer la hora de la venta ({s.get('when_text') or 'vacía'}): es la del "
                                f"escaneo.")
                when.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            grid.addWidget(when, r, 0)
            grid.addWidget(label(s["item"], f.body, theme.TEXT), r, 1)
            grid.addWidget(label(str(s["qty"]), f.body, theme.TEXT), r, 2, Qt.AlignRight)
            grid.addWidget(label(_kinah(s["price"]), f.body, theme.TEXT), r, 3, Qt.AlignRight)
            grid.addWidget(label(_kinah(s["tax"]), f.body, theme.MUTED), r, 4, Qt.AlignRight)
            grid.addWidget(label(_kinah(s["net"]), f.name, theme.TEXT), r, 5, Qt.AlignRight)
            paid = ledger.cost(s)
            cost = "no fabricado" if not s.get("crafted") else _kinah(paid) if paid is not None else "?"
            cost_label = label(cost, f.body, theme.MUTED)
            if s.get("crafted"):
                bought = self.window.crafting.sale_prices_text(s)
                cost_label.setToolTip(f"Precio medio del {time.strftime('%d/%m', time.localtime(s['at']))}"
                                      + (f" de lo comprado: {bought}" if bought else ": no compraste nada")
                                      + f"\nComprando todos los materiales: {_kinah(ledger.cost(s, buying=True))}")
                cost_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            grid.addWidget(cost_label, r, 6, Qt.AlignRight)
            profit = ledger.profit(s)
            grid.addWidget(label(_kinah(profit, sign=True) if profit is not None else "?", f.name,
                                 theme.GAIN if profit and profit > 0 else theme.COMBAT if profit and profit < 0 else
                                 theme.MUTED), r, 7, Qt.AlignRight)
            grid.addWidget(label(f"{profit / s['net']:.0%}" if profit is not None and s["net"] else "—", f.body,
                                 theme.MUTED), r, 8, Qt.AlignRight)
            proof = ledger.proof_path(s)
            see = QPushButton("Prueba")
            see.setProperty("flat", True)
            see.setEnabled(bool(proof and proof.exists()))
            see.setToolTip("Abrir la captura de Settlement donde salió esta venta")
            see.clicked.connect(lambda _=False, p=proof: os.startfile(str(p)))
            grid.addWidget(see, r, 9)
            remove = QPushButton("Borrar")
            remove.setProperty("flat", True)
            remove.clicked.connect(lambda _=False, sale=s: self.window.delete_sale(sale))
            grid.addWidget(remove, r, 10)
        grid.setColumnStretch(1, 1)
        holder = QFrame()
        holder.setLayout(grid)
        self.col.addWidget(holder)
