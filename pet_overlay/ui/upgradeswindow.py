"""Mejoras window: what you have to spend, your route (the plan of upgrades.py, in order) and every piece with its
+N, item level, soul binding, manastones and its best replacement; the dungeons you run decide what you can farm."""
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..upgrades import GUIDE, kinah_text, stat_value
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label

CARD_STYLE = """
QFrame#Card { background: #121c33; border: 1px solid #1f2b47; border-radius: 8px; }
QFrame#Step { background: #101a30; border: 1px solid #1a2643; border-radius: 6px; }
QFrame#Step[ready="true"] { border-color: #3a3218; }
QFrame#Tile { background: #121c33; border: 1px solid #1f2b47; border-radius: 6px; }
"""
KIND = {   # step kind -> (chip text, colour)
    "own": ("Equipar", "#7bd88f"), "voucher": ("Voucher", "#f0c24e"), "enhance": ("Encantar", "#2ec0f2"),
    "morph": ("Subir de grado", "#2ec0f2"), "soul": ("Soul Binding", "#c38bff"), "manastone": ("Manastones", "#8fb3ff"),
    "soulstone": ("Soulstones", "#8fb3ff"), "amp": ("Amp", "#2ec0f2"), "buy": ("Comprar", "#f0c24e"),
    "save": ("Ahorrar", "#e0a050"), "farm": ("Farmear", "#7bd88f"), "spare": ("Transfer Stones", "#8a93a8"),
    "theostone": ("Theostone", "#8a93a8"),
}
OPTION = {"own": "La tienes", "voucher": "Voucher", "drop": "Mazmorra", "market": "Market"}
TILES = (("kina", "Kinah", kinah_text), ("enhance_stone", "Enhance Stones", lambda v: f"{v:,}"),
         ("transfer_stone", "Transfer Stones", str), ("soul_codex", "Soul Codex", str),
         ("soul_reset", "Soul Codex: Reset", str), ("sync_stone", "Sync Stones", str),
         ("philosopher", "Philosopher's Stones", str), ("amplify_stone", "Amplify Stones", str))


def _chip(text, color, fonts):
    c = QColor(color)
    chip = QLabel(text)
    chip.setFont(fonts.small)
    chip.setStyleSheet(f"color: {c.name()}; background: rgba({c.red()},{c.green()},{c.blue()},0.12); "
                       f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.5); border-radius: 8px; "
                       f"padding: 0px 7px;")
    chip.setAttribute(Qt.WA_TransparentForMouseEvents)
    return chip


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            _clear(item.layout())


def _ago(ts):
    if not ts:
        return "nunca"
    mins = max(0, (time.time() - ts) / 60)
    if mins < 1:
        return "hace un momento"
    if mins < 60:
        return f"hace {mins:.0f} min"
    if mins < 48 * 60:
        return f"hace {mins / 60:.0f} h"
    return f"hace {mins / 1440:.0f} días"


class UpgradesWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.character = None              # None: the one you play
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — Mejoras")
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + CARD_STYLE)
        self.resize(1180, 900)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("Mejoras", f.name_big, theme.TEXT))
        self.subtitle = label("", f.small, theme.MUTED)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        head.addWidget(label("Personaje:", f.small, theme.MUTED))
        self.who = QComboBox()
        self.who.currentIndexChanged.connect(self._who_changed)
        head.addWidget(self.who)
        prices = QPushButton("Precios de equipo")
        prices.setToolTip("Market › Equipo: escanea en el juego las listas de equipo del mercado y aquí verás qué "
                          "conviene comprar")
        prices.clicked.connect(lambda: self.ctl.open_crafting("gear"))
        head.addWidget(prices)
        root.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setSpacing(10)
        scroll.setWidget(holder)
        self.scroll = scroll
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        for credit in (self.ctl.upgrades_db.credit, GUIDE):
            link = QPushButton(credit["text"])
            link.setProperty("flat", True)
            link.clicked.connect(lambda _=False, u=credit["url"]: webbrowser.open(u))
            foot.addWidget(link)
        foot.addStretch(1)
        foot.addWidget(label("Todo sale de tu tráfico (equipo, bolsa y almacén) y de tus muestreos del market: nada sale "
                             "de tu PC.", f.small, theme.MUTED))
        root.addLayout(foot)
        self._timer = QTimer(self, singleShot=True, interval=300, timeout=self.refresh)
        self.refresh()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def changed(self):
        """Something it shows changed (items, gear, prices): one redraw shortly after."""
        if self.isVisible():
            self._timer.start()

    def _who_changed(self, _):
        self.character = self.who.currentData()
        self.refresh()

    def _fill_who(self):
        names = self.ctl.gear_characters()
        playing = self.ctl.character_name()
        self.who.blockSignals(True)
        self.who.clear()
        self.who.addItem(f"El que juegas ({playing})" if playing else "El que juegas", None)
        for name in names:
            if name != playing:
                self.who.addItem(name, name)
        self.who.setCurrentIndex(max(0, self.who.findData(self.character)) if self.character else 0)
        self.who.blockSignals(False)

    # --- content ---
    def refresh(self):
        f = self.fonts
        self._fill_who()
        bar = self.scroll.verticalScrollBar().value()
        _clear(self.body)
        plan = self.ctl.upgrade_plan(self.character)
        if plan is None:
            self.subtitle.setText("")
            self._hint("Entra al juego con tu personaje con AionDex abierto: leo tu equipo, tu bolsa y tus almacenes "
                       "(con el Soul Binding, las manastones y la Theostone de cada pieza) y aquí te digo qué hacer "
                       "primero.", theme.WARN)
            self.body.addStretch(1)
            return
        prices = self.ctl.gear_prices()
        bits = [plan.character, f"bolsa y almacén leídos {_ago(plan.read_at)}"]
        bits.append(f"{len(prices)} precios de equipo" if prices else "sin precios de equipo (Market › Equipo)")
        self.subtitle.setText(" · ".join(bits))
        if plan.read_at is None:
            self._hint("Aún no leí tu bolsa ni tu almacén: cambia de canal o vuelve a entrar al personaje y aparecerán "
                       "tu Kinah, tus piedras, tus vouchers y lo que tienes guardado.", theme.WARN)
        self._resources(plan)
        self._route(plan)
        self._pieces(plan)
        self._food(plan)
        self._dungeons(plan)
        self.body.addStretch(1)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(bar))

    def _hint(self, text, color=None):
        lbl = label(text, self.fonts.body, color or theme.MUTED)
        lbl.setWordWrap(True)
        self.body.addWidget(lbl)

    def _resources(self, plan):
        f = self.fonts
        self.body.addWidget(SectionTitle("Lo que tienes", f))
        grid = QGridLayout()
        grid.setSpacing(8)
        tiles = [(text, fmt(plan.have(key))) for key, text, fmt in TILES if plan.have(key) or key in
                 ("kina", "enhance_stone", "transfer_stone")]
        for dungeon, count in plan.vouchers.items():
            tiles.append((f"Vouchers: {dungeon}", str(count)))
        frags = plan.have("amplify_fragment")
        if frags:
            tiles.append(("Amplify Fragments (Unique)", str(frags)))
        sup = {n: c for n, c in plan.stones.items() if n.startswith("Superior")}
        for name, count in sorted(sup.items()):
            tiles.append((name.replace(" (Bound)", ""), str(count)))
        for i, (title, value) in enumerate(tiles):
            tile = QFrame()
            tile.setObjectName("Tile")
            lay = QVBoxLayout(tile)
            lay.setContentsMargins(10, 6, 10, 6)
            lay.setSpacing(0)
            lay.addWidget(label(value, f.name_big, theme.GOLD))
            lay.addWidget(label(title, f.small, theme.MUTED))
            grid.addWidget(tile, i // 6, i % 6)
        for c in range(6):
            grid.setColumnStretch(c, 1)
        self.body.addLayout(grid)

    def _route(self, plan):
        f = self.fonts
        self.body.addWidget(SectionTitle("Tu ruta", f))
        if not plan.steps:
            self._hint("Nada que mejorar con lo que sé: escanea precios de equipo en el market o marca las mazmorras "
                       "que haces.")
            return
        self._hint("En orden: lo de arriba primero. En dorado lo que ya puedes hacer con lo que tienes.")
        for n, step in enumerate(plan.steps, start=1):
            card = QFrame()
            card.setObjectName("Step")
            card.setProperty("ready", step.ready)
            lay = QVBoxLayout(card)
            lay.setContentsMargins(12, 8, 12, 8)
            lay.setSpacing(3)
            top = QHBoxLayout()
            top.setSpacing(8)
            top.addWidget(label(f"{n}.", f.name, theme.MUTED))
            top.addWidget(label(step.title, f.name, theme.GOLD if step.ready else theme.TEXT), 1)
            text, color = KIND.get(step.kind, (step.kind, "#8a93a8"))
            top.addWidget(_chip(text, color, f))
            if step.gain and step.kind in ("own", "voucher", "buy", "save", "farm", "enhance"):
                top.addWidget(_chip(f"+{step.gain} nivel", "#7bd88f", f))
            if step.kinah:
                top.addWidget(_chip(f"{kinah_text(step.kinah)} Kinah", "#f0c24e", f))
            lay.addLayout(top)
            detail = label(step.detail, f.body, theme.TEXT if step.ready else theme.MUTED)
            detail.setWordWrap(True)
            lay.addWidget(detail)
            self.body.addWidget(card)

    def _pieces(self, plan):
        f = self.fonts
        self.body.addWidget(SectionTitle("Pieza por pieza", f))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        for c, text in enumerate(("Pieza", "Llevas", "Nivel", "Sin fallar", "Soul Binding", "Huecos",
                                  "Mejor opción")):
            grid.addWidget(label(text, f.small, theme.ACCENT), 0, c)
        for r, piece in enumerate(plan.pieces, start=1):
            grid.addWidget(label(piece.slot, f.body, theme.MUTED), r, 0)
            if piece.item is None:
                grid.addWidget(label("—", f.body, theme.MUTED), r, 1)
                continue
            grade = {"Unique": "#FFD02B", "Epic": "#379AFF", "Rare": "#19E048"}.get(piece.item.grade, "#D6DAE3")
            grid.addWidget(label(f"{piece.item.name} +{piece.enchant}", f.name, QColor(grade), elide=True), r, 1)
            grid.addWidget(label(f"{piece.level} ({piece.base} base)", f.body, theme.TEXT), r, 2)
            safe = f"+{piece.safe}" if piece.safe else "—"
            cell = label(("✓ " if piece.enchant >= piece.safe and piece.safe else "") + safe, f.body,
                         theme.GAIN if piece.safe and piece.enchant >= piece.safe else theme.TEXT)
            if piece.to_safe:
                cell.setToolTip(f"Hasta +{piece.safe}: {kinah_text(piece.to_safe[0])} Kinah y "
                                f"{piece.to_safe[1]:,} Enhance Stones")
                cell.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            grid.addWidget(cell, r, 3)
            s = piece.soul
            if s and s.lines:
                text = "100% ✓" if s.locked else f"{s.rate:.0%}"
                color = theme.GAIN if s.locked else theme.WARN if s.bad else theme.TEXT
                cell = label(text + (f" · {len(s.bad)} a cambiar" if s.bad and not s.locked else ""), f.body, color)
                cell.setToolTip("\n".join(
                    f"{n or '?'}: {stat_value(n, v)}" + (f" de {stat_value(n, lo)}–{stat_value(n, hi)} ({p:.0%})"
                                                          if p is not None else "")
                    + ("  ✗ no es de las buscadas" if n in s.bad else "")
                    for n, v, lo, hi, p in s.lines) + (f"\n+ {s.skills} efecto(s) de skill" if s.skills else ""))
                cell.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            else:
                cell = label("—", f.body, theme.MUTED)
            grid.addWidget(cell, r, 4)
            if s and s.sockets:
                filled = len(s.sockets) - s.empty
                cell = label(f"{filled}/{len(s.sockets)}" + (" · Theostone" if s.theostone else ""), f.body,
                             theme.WARN if s.empty else theme.TEXT)
            else:
                cell = label("Theostone" if s and s.theostone else "—", f.body, theme.MUTED)
            grid.addWidget(cell, r, 5)
            opts = plan.options.get(piece.pos) or []
            if opts:
                o = opts[0]
                cell = label(f"{OPTION[o.kind]}: {o.item.name} (+{o.gain})", f.body, theme.GOLD, elide=True)
                cell.setToolTip("\n".join(f"{OPTION[x.kind]}: {x.item.name} (+{x.gain}) — {x.text}" for x in opts[:6]))
                cell.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            else:
                cell = label("—", f.body, theme.MUTED)
            grid.addWidget(cell, r, 6)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(6, 3)
        card = QFrame()
        card.setObjectName("Card")
        card.setLayout(grid)
        grid.setContentsMargins(12, 8, 12, 8)
        self.body.addWidget(card)
        self._hint("Soul Binding: el porcentaje del juego (lo que lleva cada línea entre su mínimo y su máximo, en "
                   "promedio). «A cambiar»: líneas que no son las que busca la guía para esa pieza (pasa el ratón para "
                   "verlas). Huecos: manastones o soulstones puestas / huecos de la pieza.")

    def _food(self, plan):
        """The pieces of your bag and storage you do not need, and what each one is best eaten for."""
        f = self.fonts
        if not plan.spare:
            return
        self.body.addWidget(SectionTitle("Comida para mejorar", f))
        self._hint("Piezas de tu bolsa y tus almacenes que no te sirven para llevar: lo que cada una te da mejor. Una del "
                   "mismo tipo y grado sirve para Sync de la tuya; cualquier Unique se convierte en 1 Sync Stone o 1 "
                   "Transfer Stone (Substance Morph). Primero las +0 y de menos nivel.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        for c, text in enumerate(("Pieza", "Dónde", "Nivel", "Su Soul Binding", "Úsala para")):
            grid.addWidget(label(text, f.small, theme.ACCENT), 0, c)
        for r, x in enumerate(plan.spare, start=1):
            grade = {"Unique": "#FFD02B", "Epic": "#379AFF"}.get(x.item.grade, "#D6DAE3")
            grid.addWidget(label(f"{x.item.name} +{x.enchant}", f.name, QColor(grade), elide=True), r, 0)
            grid.addWidget(label(x.place, f.body, theme.MUTED), r, 1)
            grid.addWidget(label(str(x.level), f.body, theme.TEXT), r, 2)
            grid.addWidget(label(f"{x.rate:.0%}" if x.rate else "—", f.body, theme.MUTED), r, 3)
            use = x.use or ("guárdala" if x.item.grade != "Unique" else "Transfer Stone o Sync Stone")
            grid.addWidget(label(use, f.body, theme.GOLD if x.use.startswith("Sync") else theme.TEXT), r, 4)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(4, 3)
        card = QFrame()
        card.setObjectName("Card")
        grid.setContentsMargins(12, 8, 12, 8)
        card.setLayout(grid)
        self.body.addWidget(card)

    def _dungeons(self, plan):
        f = self.fonts
        self.body.addWidget(SectionTitle("Mazmorras que haces", f))
        auto = self.ctl.upgrades_auto_dungeons()
        chosen = self.ctl.upgrades_config().get("dungeons")
        self._hint("De ellas salen las piezas que te dice que farmees y sus garantizados. "
                   + (f"Detecté: {', '.join(auto)} (los jefes finales que mataste). " if auto else "")
                   + ("Elegidas a mano." if chosen is not None else "Automático: las que detecto."))
        row = QHBoxLayout()
        row.setSpacing(12)
        boxes = QGridLayout()
        boxes.setHorizontalSpacing(16)
        for i, dungeon in enumerate(self.ctl.upgrades_db.dungeons()):
            box = QCheckBox(dungeon + (" (detectada)" if dungeon in auto else ""))
            box.setChecked(dungeon in plan.dungeons)
            box.toggled.connect(lambda on, d=dungeon: self._toggle(d, on))
            boxes.addWidget(box, i // 3, i % 3)
        row.addLayout(boxes, 1)
        if chosen is not None:
            reset = QPushButton("Automático")
            reset.clicked.connect(lambda: self._set_dungeons(None))
            row.addWidget(reset, 0, Qt.AlignTop)
        self.body.addLayout(row)

    def _toggle(self, dungeon, on):
        current = list(self.ctl.upgrade_plan(self.character).dungeons)
        if on and dungeon not in current:
            current.append(dungeon)
        elif not on and dungeon in current:
            current.remove(dungeon)
        self._set_dungeons(current)

    def _set_dungeons(self, dungeons):
        self.ctl.set_upgrades_dungeons(dungeons)
        QTimer.singleShot(0, self.refresh)
