"""Mejoras window: who the character is (item level, CP, main or alt), what it has to spend and five tabs - your route
(the plan of upgrades.py: «Lo siguiente» big, then one short row per step, grouped, whose detail opens on a click,
each with what it raises: ↑ ilvl / ↑ CP), your pieces (a tile each: +N, item level, soul binding and sockets as bars
and dots, the best replacement), «Contenido» (every activity of the game: what it asks for, whether you can do it and
what it gives you), the food for upgrades, and your preferences (the dungeons you run, the steps you said no to)."""
import time
import webbrowser

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..arcana import farm_options, short_kind
from ..gear import SLOTS
from ..upgrades import CATEGORIES, GUIDE, SUBSTATS_GUIDE, can_do, kinah_text, stat_value
from . import theme
from .design import Stat
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label
from ..i18n import tr

KIND = {   # step kind -> (chip text, colour)
    "own": (tr("Equipar"), "#7bd88f"), "voucher": ("Voucher", "#f0c24e"), "enhance": (tr("Encantar"), "#2ec0f2"),
    "morph": (tr("Subir de grado"), "#2ec0f2"), "soul": ("Soul Binding", "#c38bff"), "manastone": ("Manastones", "#8fb3ff"),
    "soulstone": ("Soulstones", "#8fb3ff"), "amp": ("Amp", "#2ec0f2"), "buy": (tr("Comprar"), "#f0c24e"),
    "save": (tr("Ahorrar"), "#e0a050"), "farm": (tr("Farmear"), "#7bd88f"), "spare": (tr("Piedras"), "#8a93a8"),
    "theostone": ("Theostone", "#8a93a8"), "boss": (tr("Jefe"), "#7bd88f"), "arcana": ("Arcana", "#2ec0f2"),
    "content": (tr("Actividad"), "#5fd3c6"), "shop": (tr("Tienda"), "#f0c24e"), "goal": (tr("Meta"), "#e06c6c"),
}
GROUPS = (("now", tr("Para hacer ya"), tr("Tienes todo lo que piden")),
          ("later", tr("Te falta algo"), tr("Kinah, piedras o materiales")),
          ("buy", tr("Comprar en el market"), tr("Con los precios de tus muestreos")),
          ("farm", tr("Farmear"), tr("Tus mazmorras y los jefes a los que llegas")),
          ("week", tr("Cada semana"), tr("Lo que puedes hacer y lo que te da")),
          ("locked", tr("Cuando subas tu nivel de objeto"), tr("Aún no puedes entrar: lo que te falta")),
          ("extra", "Extras", ""))
OPTION = {"own": tr("La tienes"), "voucher": "Voucher", "drop": tr("Mazmorra"), "market": "Market", "boss": tr("Jefe")}
BOOST = {"ilvl": ("↑ ilvl", "#5fb8ff", tr("Sube tu nivel de objeto: lo que piden las mazmorras, la Trascendencia y "
                                          "el Abismo (la suma del nivel de tus piezas y su +N)")),
         "cp": ("↑ CP", "#c38bff", tr("Sube tu CP (Combat Power): todo lo que te da estadísticas"))}
ROLE_TIP = (tr("Main: Mejoras usa todo lo que tienes (Kinah comerciable y ligada, el market y los materiales que se "
               "pueden vender). Alt: solo lo ligado (Kina (Bound) y lo que no se puede comerciar), nada del market: "
               "lo comerciable de un alt es para tu main."))
GRADE = {"Unique": "#FFD02B", "Epic": "#379AFF", "Rare": "#19E048", "Special": "#00FFD8"}
TILES = (("kina", "Kinah", kinah_text), ("kina_bound", tr("Kinah ligada"), kinah_text),
         ("enhance_stone", "Enhance Stones", lambda v: f"{v:,}"),
         ("transfer_stone", "Transfer Stones", str), ("soul_codex", "Soul Codex", str),
         ("soul_reset", "Codex: Reset", str), ("sync_stone", "Sync Stones", str),
         ("philosopher", "Philosopher's", str), ("amplify_stone", "Amplify Stones", str))
TABS = (("route", tr("Tu ruta")), ("pieces", tr("Tu equipo")), ("lines", tr("Líneas")), ("content", tr("Contenido")),
        ("food", tr("Comida")), ("prefs", tr("Preferencias")))
# the equipment screen: (column, row) of each 1156 position, like the game lays them out
DOLL = {3: (0, 0), 4: (0, 1), 5: (0, 2), 7: (0, 3), 6: (0, 4), 8: (0, 5), 19: (0, 6),
        1: (1, 0), 2: (1, 1),
        10: (2, 0), 11: (2, 1), 12: (2, 2), 13: (2, 3), 14: (2, 4), 15: (2, 5), 16: (2, 6), 17: (2, 7), 22: (2, 8),
        23: (2, 9), 24: (2, 10)}
STAT_ES = {"skill": "Skill", "Move Speed": "Movement Speed", "Attack increase": "Attack Increase"}
STATUS = {"ok": QColor("#7bd88f"), "no": QColor("#e0a050"), "closed": QColor("#4a5470")}
CARD_STYLE = """
QFrame#Card { background: #151923; border: 1px solid #2b3242; border-radius: 8px; }
QFrame#Hero { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1d1f2a, stop:1 #151923);
    border: 1px solid #5a4a1e; border-radius: 10px; }
QFrame#Row { background: #12161f; border: 1px solid #262c3a; border-radius: 6px; }
QFrame#Row:hover { border-color: #4a5368; }
QFrame#Tile { background: #151923; border: 1px solid #2b3242; border-radius: 8px; }
QFrame#Pill { background: #12161f; border: 1px solid #262c3a; border-radius: 6px; }
QPushButton[link="true"] { background: transparent; border: none; color: #8a93a8; padding: 2px 4px; }
QPushButton[link="true"]:hover { color: #e8ecf4; }
"""


def _chip(text, color, fonts, tip=""):
    c = QColor(color)
    chip = QLabel(text)
    chip.setFont(fonts.small)
    chip.setStyleSheet(f"color: {c.name()}; background: rgba({c.red()},{c.green()},{c.blue()},0.12); "
                       f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.5); border-radius: 8px; "
                       f"padding: 0px 7px;")
    if tip:
        chip.setToolTip(tip)
    else:
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
        return tr("nunca")
    mins = max(0, (time.time() - ts) / 60)
    if mins < 1:
        return tr("hace un momento")
    if mins < 60:
        return tr("hace {0:.0f} min", mins)
    if mins < 48 * 60:
        return tr("hace {0:.0f} h", mins / 60)
    return tr("hace {0:.0f} días", mins / 1440)


def _link(text, action, tip=""):
    button = QPushButton(text)
    button.setProperty("link", True)
    button.setCursor(Qt.PointingHandCursor)
    if tip:
        button.setToolTip(tip)
    button.clicked.connect(action)
    return button


class Bar(QWidget):
    """A thin progress bar, with an optional mark (the +N that never fails)."""

    def __init__(self, ratio, color, mark=None, height=6, parent=None):
        super().__init__(parent)
        self.ratio, self.color, self.mark = max(0.0, min(1.0, ratio)), QColor(color), mark
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(r, radius, radius)
        if self.ratio > 0:
            p.setBrush(self.color)
            p.drawRoundedRect(QRectF(r.x(), r.y(), max(r.height(), r.width() * self.ratio), r.height()), radius,
                              radius)
        if self.mark is not None and 0 < self.mark < 1:
            p.setBrush(QColor(255, 255, 255, 150))
            x = r.x() + r.width() * self.mark
            p.drawRect(QRectF(x - 0.75, r.y() - 1, 1.5, r.height() + 2))
        p.end()


class UpgradesWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.character = None              # None: the one you play
        self.tab = "route"
        self._open = set()                 # step keys whose detail is open
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — {tr('Mejoras')}")
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + CARD_STYLE)
        self.resize(1100, 860)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(tr("Mejoras"), f.name_big, theme.TEXT))
        head.addLayout(titles, 1)
        head.addWidget(label(tr("Personaje:"), f.small, theme.MUTED))
        self.who = QComboBox()
        self.who.currentIndexChanged.connect(self._who_changed)
        head.addWidget(self.who)
        self.role = QComboBox()
        self.role.addItem(tr("Main: usa todo"), "main")
        self.role.addItem(tr("Alt: solo lo ligado"), "alt")
        self.role.setToolTip(ROLE_TIP)
        self.role.currentIndexChanged.connect(self._role_changed)
        head.addWidget(self.role)
        self._shown = None                  # the character whose plan is on screen
        prices = QPushButton(tr("Precios de equipo"))
        prices.setToolTip(tr("Market › Equipo: escanea en el juego las listas de equipo del mercado y aquí verás "
                             "qué conviene comprar"))
        prices.clicked.connect(lambda: self.ctl.open_crafting("gear"))
        head.addWidget(prices)
        self.update_button = QPushButton(tr("↻ Actualizar"))
        self.update_button.setToolTip(tr("Rehace todo ahora con lo último que leí del juego y vuelve a pedir tu "
                                         "perfil oficial (nivel de objeto y CP). Tu equipo, tu bolsa y tus "
                                         "almacenes se leen solos mientras juegas; si algo no aparece, cambia de "
                                         "canal y el juego los manda de nuevo."))
        self.update_button.setMinimumWidth(self.update_button.fontMetrics().horizontalAdvance(tr("Actualizando…")) + 36)
        self.update_button.clicked.connect(self._update_now)
        head.addWidget(self.update_button)
        root.addLayout(head)
        self.subtitle = label("", f.small, theme.MUTED)       # its own line: a long one wraps, never widens the window
        self.subtitle.setWordWrap(True)
        root.addWidget(self.subtitle)

        self.resources = QHBoxLayout()
        self.resources.setSpacing(8)
        root.addLayout(self.resources)
        self._folds = set()                 # the folded sections that are open (categories, the transfer trick...)

        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_buttons = {}
        for key, text in TABS:
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.setMinimumWidth(130)
            button.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            tabs.addWidget(button)
            self.tab_buttons[key] = button
        tabs.addStretch(1)
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
        for credit, short in ((self.ctl.upgrades_db.credit, tr("Datos: aion2data.com")),
                              (GUIDE, tr("Guía: Aion 2 Bible (kanonxo)")), (SUBSTATS_GUIDE, tr("Tabla de líneas: r/Aion2"))):
            link = QPushButton(short)                 # short: three in a row never widen the window
            link.setToolTip(credit["text"])
            link.setProperty("flat", True)
            link.clicked.connect(lambda _=False, u=credit["url"]: webbrowser.open(u))
            foot.addWidget(link)
        foot.addStretch(1)
        foot.addWidget(label(tr("Todo sale de tu tráfico y de tus muestreos del market: nada sale de tu PC."), f.small,
                             theme.MUTED))
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

    def show_tab(self, key):
        self.tab = key
        self.refresh()
        self.scroll.verticalScrollBar().setValue(0)

    def _who_changed(self, _):
        self.character = self.who.currentData()
        self.refresh()

    def _update_now(self):
        self.ctl.upgrades_refresh(self._shown)
        self.update_button.setEnabled(False)
        self.update_button.setText(tr("Actualizando…"))
        self.refresh()
        QTimer.singleShot(1500, self._update_done)

    def _update_done(self):
        self.update_button.setEnabled(True)
        self.update_button.setText(tr("↻ Actualizar"))

    def _role_changed(self, _):
        if self._shown:
            self.ctl.set_upgrades_role(self._shown, self.role.currentData())
            QTimer.singleShot(0, self.refresh)

    def _fill_who(self):
        names = self.ctl.gear_characters()
        playing = self.ctl.character_name()
        self.who.blockSignals(True)
        self.who.clear()
        self.who.addItem(tr("El que juegas ({0})", playing) if playing else tr("El que juegas"), None)
        for name in names:
            if name != playing:
                self.who.addItem(name, name)
        self.who.setCurrentIndex(max(0, self.who.findData(self.character)) if self.character else 0)
        self.who.blockSignals(False)

    # --- content ---
    def refresh(self):
        self._fill_who()
        bar = self.scroll.verticalScrollBar().value()
        _clear(self.body)
        _clear(self.resources)
        plan = self.ctl.upgrade_plan(self.character)
        for key, button in self.tab_buttons.items():
            button.setChecked(key == self.tab)
        if plan is None:
            self.subtitle.setText("")
            self._hint(tr("Entra al juego con tu personaje con AionDex abierto: leo tu equipo, tu bolsa y tus "
                          "almacenes y aquí te digo qué hacer primero."), theme.WARN)
            self.body.addStretch(1)
            return
        self._shown = plan.character
        self.role.blockSignals(True)
        self.role.setCurrentIndex(max(0, self.role.findData(self.ctl.upgrades_role(plan.character))))
        self.role.blockSignals(False)
        me = plan.me
        bits = [tr("bolsa y almacén leídos {0}", _ago(plan.read_at))]
        if me and me.item_level is not None:
            bits.insert(0, tr("perfil oficial {0}", _ago(me.at)) + (tr(" + tus cambios") if me.item_level != me.official else ""))
        if self.ctl.armory.loading(plan.character):
            bits.append(tr("pidiendo tu perfil oficial…"))
        if not (me and me.alt) and not self.ctl.gear_prices():
            bits.append(tr("sin precios de equipo (Market › Equipo)"))
        self.subtitle.setText(" · ".join(bits))
        counts = {"route": len(plan.steps), "food": len(plan.spare),
                  "content": sum(1 for a in plan.content if not a.reason and not a.closed),
                  "prefs": len(self.ctl.upgrades_skipped(plan.character))}
        for key, text in TABS:
            n = counts.get(key)
            self.tab_buttons[key].setText(f"{text} ({n})" if n else text)
        self._resources(plan)
        if plan.read_at is None:
            self._hint(tr("Aún no leí tu bolsa ni tu almacén: cambia de canal o vuelve a entrar al personaje."), theme.WARN)
        if me and me.alt and self.tab == "route":
            keep = [f"{kinah_text(n)} Kinah" if k == "Kina" else f"{n:,} {k}" for k, n in plan.for_main.items()]
            self._hint(tr("Alt: solo cuento lo ligado (Kina (Bound) y lo que no se puede comerciar) y nada del "
                          "market.")
                       + (tr(" Comerciable, para tu main: {0}.", ', '.join(keep[:6])) if keep else ""))
        {"route": self._route, "pieces": self._pieces, "lines": self._lines, "content": self._content,
         "food": self._food, "prefs": self._prefs}[self.tab](plan)
        self.body.addStretch(1)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(bar))

    def _hint(self, text, color=None):
        lbl = label(text, self.fonts.small, color or theme.MUTED)
        lbl.setWordWrap(True)
        self.body.addWidget(lbl)

    def _resources(self, plan):
        """Your numbers in a row: Item Level, CP and what you have to spend (the rest in each tile's tooltip)."""
        f = self.fonts
        me = plan.me
        alt = me is not None and me.alt
        il = me.item_level if me and me.item_level else None
        tip = (tr("El oficial {0:,} ({1}) y tus cambios desde entonces", me.official, _ago(me.at))
               if il and me.item_level != me.official else tr("Tu perfil oficial, {0}", _ago(me.at)) if il else
               tr("Aún no lo sé: cargando tu perfil oficial"))
        self.resources.addWidget(Stat(f, f"{il:,}" if il else "—", tr("Nivel de objeto"), theme.GOLD, tip))
        self.resources.addWidget(Stat(f, f"{me.cp:,}" if me and me.cp else "—", "CP", theme.ACCENT))
        kina = plan.have("kina_bound") if alt else plan.have("kina")
        self.resources.addWidget(Stat(f, kinah_text(kina), tr("Kinah ligada") if alt else "Kinah", theme.TEXT,
                                      tr("Ligada: {0}", kinah_text(plan.have('kina_bound'))) if not alt else ""))
        self.resources.addWidget(Stat(f, f"{plan.have('enhance_stone'):,}", "Enhance Stones"))
        self.resources.addWidget(Stat(f, str(plan.have("transfer_stone")), "Transfer Stones"))
        more = [(fmt(plan.have(key)), text) for key, text, fmt in TILES[5:] if plan.have(key)]
        more += [(str(n), f"Vouchers {d}") for d, n in plan.vouchers.items()]
        sup = sum(c for n, c in plan.stones.items() if n.startswith("Superior Manastone"))
        if sup:
            more.append((str(sup), tr("Manastones Sup.")))
        if more:
            rest = Stat(f, str(len(more)), tr("más recursos"), theme.MUTED,
                        "\n".join(f"{v} {t}" for v, t in more))
            self.resources.addWidget(rest)
        self.resources.addStretch(1)

    def _group(self, step):
        if step.kind in ("buy", "save"):
            return "buy"
        if step.kind in ("farm", "boss") or step.key == "arcana":       # Transcendence's better cards: farm them
            return "farm"
        if step.kind == "content":
            return "week"
        if step.kind == "goal":
            return "locked"
        if step.kind in ("spare", "theostone"):
            return "extra"
        return "now" if step.ready else "later"

    def _fold(self, key, title, color=None):
        """A folded section's header (▸ / ▾); True when it is open."""
        opened = key in self._folds
        button = QPushButton(("▾ " if opened else "▸ ") + title)
        button.setProperty("flat", True)
        button.setStyleSheet(f"text-align: left; color: {(color or theme.MUTED).name()};")
        button.clicked.connect(lambda: (self._folds.symmetric_difference_update({key}), self.refresh()))
        self.body.addWidget(button)
        return opened

    def _categories(self, plan):
        """A check per kind of upgrade: what you switch off is left out of your route (this character's)."""
        f = self.fonts
        off = [text for key, text, _ in CATEGORIES if key in plan.off]
        title = tr("Qué tener en cuenta") + (tr(" · sin {0}", ', '.join(off)) if off else "")
        if not self._fold("categories", title, theme.WARN if off else None):
            return
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 6, 12, 8)
        lay.setSpacing(4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(2)
        for i, (key, text, _) in enumerate(CATEGORIES):
            n = plan.counts.get(key, 0)
            box = QCheckBox(f"{text} ({n})" if n else text)
            box.setChecked(key not in plan.off)
            box.setToolTip(tr("Desmárcalo y tu ruta se rehace sin esto (lo de Soul Binding deja libre su comida)"))
            box.toggled.connect(lambda on, k=key: self._category(k, on))
            grid.addWidget(box, i // 5, i % 5)
        lay.addLayout(grid)
        lay.addWidget(label(tr("Solo para {0}. Cada paso dice qué sube: ↑ ilvl (lo que piden las mazmorras) y / o "
                               "↑ CP.", plan.character), f.small, theme.MUTED))
        self.body.addWidget(card)

    def _category(self, key, on):
        self.ctl.set_upgrades_category(self._shown, key, on)
        QTimer.singleShot(0, self.refresh)

    def _route(self, plan):
        f = self.fonts
        if not plan.steps:
            self._hint(tr("Nada que mejorar con lo que sé: escanea precios de equipo en el market o marca las "
                          "mazmorras que haces (Preferencias)."))
            self._next_up(plan)
            self._categories(plan)
            return
        nxt = self.ctl.next_step(plan.character)
        first = nxt.step if nxt is not None and nxt.step in plan.steps else \
            next((s for s in plan.steps if s.ready and self._group(s) == "now"), plan.steps[0])
        self._hero(first)
        self._next_up(plan)
        rest = [s for s in plan.steps if s is not first]
        for key, title, sub in GROUPS:
            steps = [s for s in rest if self._group(s) == key]
            if not steps:
                continue
            head = QHBoxLayout()
            head.setContentsMargins(2, 8, 0, 0)
            head.addWidget(label(title, f.name, theme.TEXT))
            head.addWidget(_chip(str(len(steps)), "#8a93a8", f))
            if sub:
                head.addWidget(label(sub, f.small, theme.MUTED))
            head.addStretch(1)
            self.body.addLayout(head)
            for step in steps:
                self.body.addWidget(self._row(step, plan.steps.index(step) + 1))
        self._categories(plan)
        self._arcana_note(plan)

    def _next_up(self, plan):
        """«Qué hacer a continuación»: what the rest of AionDex knows for this character, as tiles - the
        Transcendence that gives the best cards (Arcanas), the dungeon or boss the route farms, the Tracker's next
        activity, the missions with a reward to claim."""
        f = self.fonts
        ctl = self.ctl
        tiles = []
        me = plan.me
        il = me.item_level if me and me.item_level else None
        cards = plan.arcana_plan
        if cards is not None and getattr(ctl.arcana_db, "transcendence", None):
            farms = farm_options(cards, ctl.arcana_db, il)
            if farms:
                fm = farms[0]
                better = ", ".join(f"{short_kind(k)} {fm.better[k]:.0%}" for k in fm.better)
                tiles.append(("Transcendence", f"{fm.dungeon} · {fm.stage.replace('Transcendence ', '')}",
                              (tr("cartas mejores: {0} por clear", better) if better else
                               tr("comida: ~{0:,.0f} exp por clear", fm.food)) if fm.can else
                              tr("pide {0:,} de nivel de objeto", fm.il),
                              theme.GOLD if fm.can else theme.MUTED, tr("Abrir Arcanas"), ctl.open_arcana))
        farm = next((s for s in plan.steps if s.kind in ("farm", "boss")), None)
        if farm is not None:
            tiles.append((tr("Mazmorra"), farm.title, farm.summary or "", theme.GAIN, tr("Ver en la ruta"),
                          lambda _=False, k=farm.key: self._reveal(k)))
        rows = ctl.tracker.upcoming()
        if rows:
            from ..modules.tracker import when_text
            entry, st = rows[0]
            text, color = when_text(st, time.time())
            tiles.append((tr("Tracker"), entry.name, text, color, tr("Abrir Tracker"), ctl.open_tracker))
        try:
            ready = ctl.missions.ready(plan.character, time.time()) if ctl.missions.available else []
        except Exception:                        # noqa: BLE001 - missions are their own module
            ready = []
        if ready:
            tiles.append((tr("Season Missions"), tr("{0} recompensas por cobrar", len(ready)),
                          ready[0][0].view(ctl.crafting_faction())[0],
                          theme.GAIN, tr("Abrir"), ctl.open_missions))
        if not tiles:
            return
        self.body.addWidget(SectionTitle(tr("Qué hacer a continuación"), f))
        row = QHBoxLayout()
        row.setSpacing(8)
        for eyebrow, title, line, color, action, slot in tiles[:4]:
            tile = QFrame()
            tile.setObjectName("Tile")
            lay = QVBoxLayout(tile)
            lay.setContentsMargins(12, 8, 12, 8)
            lay.setSpacing(2)
            lay.addWidget(label(eyebrow.upper(), f.section, theme.MUTED))
            t = label(title, f.name, color)
            t.setWordWrap(True)
            lay.addWidget(t)
            if line:
                sub = label(line, f.small, theme.MUTED)
                sub.setWordWrap(True)
                lay.addWidget(sub)
            lay.addStretch(1)
            lay.addWidget(_link(action + " →", slot), 0, Qt.AlignLeft)
            row.addWidget(tile, 1)
        self.body.addLayout(row)

    def _reveal(self, key):
        self._open.add(key)
        self.refresh()

    def _arcana_note(self, plan):
        """How to improve the Arcana cards, in short, and the way to Personaje › Arcanas."""
        f = self.fonts
        card = QFrame()
        card.setObjectName("Card")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 8, 12, 8)
        cards = plan.arcana_plan
        text = tr("Arcanas: una Unique tiene 4 efectos de skill y llega a +5 (9 niveles). Para subirlas, dales de comer "
                  "las que no usas: pasan la exp de su grado más toda la que tenían, así que una mejorada que ya no "
                  "sirve vale como comida.")
        if cards is not None and cards.look_for:
            kind, rows = next(iter(cards.look_for.items()))
            text += " " + tr("Te falta sobre todo {0}: búscalo en un {1}.", rows[0][1], short_kind(kind))
        note = label(text, f.small, theme.TEXT)
        note.setWordWrap(True)
        lay.addWidget(note, 1)
        lay.addWidget(_link(tr("Abrir Arcanas"), self.ctl.open_arcana))
        self.body.addWidget(card)

    def _hero(self, step):
        """«Lo siguiente»: the first step you can do now, in big; its detail opens on «ver cómo»."""
        f = self.fonts
        card = QFrame()
        card.setObjectName("Hero")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(label(tr("LO SIGUIENTE"), f.small, theme.GOLD))
        text, color = KIND.get(step.kind, (step.kind, "#8a93a8"))
        top.addWidget(_chip(text, color, f))
        top.addStretch(1)
        for chip in self._chips(step):
            top.addWidget(chip)
        lay.addLayout(top)
        lay.addWidget(label(step.title, f.name_big, theme.TEXT))
        if step.summary:
            lay.addWidget(label(step.summary, f.body, theme.GOLD if step.ready else theme.MUTED))
        is_open = step.key in self._open or not step.summary
        if is_open:
            detail = label(step.detail, f.small, theme.MUTED)
            detail.setWordWrap(True)
            lay.addWidget(detail)
            self._parts(lay, step)
        actions = QHBoxLayout()
        if step.summary:
            actions.addWidget(_link(tr("▾ ocultar") if is_open else tr("▸ ver cómo"),
                                    lambda _=False: self._toggle(step.key)))
        actions.addStretch(1)
        if step.key:
            actions.addWidget(_link(tr("No quiero hacer este paso"), lambda _=False: self._skip(step.key, step.title),
                                    tr("Lo quito de tu ruta y la rehago sin él; lo recuperas en Preferencias")))
        lay.addLayout(actions)
        self.body.addWidget(card)

    def _chips(self, step):
        f = self.fonts
        out = []
        for boost in step.boosts:
            text, color, tip = BOOST[boost]
            out.append(_chip(text, color, f, tip=tip))
        if step.gain and step.kind in ("own", "voucher", "buy", "save", "farm", "enhance", "boss", "arcana", "goal"):
            out.append(_chip(tr("+{0} nivel", step.gain), "#7bd88f", f))
        if step.kinah:
            out.append(_chip(f"{kinah_text(step.kinah)}", "#f0c24e", f, tip=tr("Kinah que cuesta")))
        return out

    def _row(self, step, n):
        """One step in a short row: number, title and its one line; «ver cómo» opens the detail."""
        f = self.fonts
        text, color = KIND.get(step.kind, (step.kind, "#8a93a8"))
        row = QFrame()
        row.setObjectName("Row")
        row.setStyleSheet(f"QFrame#Row {{ border-left: 3px solid {color}; }}")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(label(str(n), f.small, theme.MUTED))
        title = QVBoxLayout()
        title.setSpacing(0)
        title.addWidget(label(step.title, f.name, theme.GOLD if step.ready else theme.TEXT, elide=True))
        if step.summary:
            title.addWidget(label(step.summary, f.small, theme.MUTED, elide=True))
        top.addLayout(title, 1)
        top.addWidget(_chip(text, color, f), 0, Qt.AlignVCenter)
        for chip in self._chips(step):
            top.addWidget(chip, 0, Qt.AlignVCenter)
        is_open = step.key in self._open
        toggle = _link(tr("▾ ocultar") if is_open else tr("▸ ver cómo"), lambda _=False: self._toggle(step.key))
        top.addWidget(toggle, 0, Qt.AlignVCenter)
        lay.addLayout(top)
        if is_open:
            detail = label(step.detail, f.small, theme.TEXT)
            detail.setWordWrap(True)
            detail.setContentsMargins(18, 2, 0, 0)
            lay.addWidget(detail)
            self._parts(lay, step)
            actions = QHBoxLayout()
            actions.setContentsMargins(18, 0, 0, 0)
            actions.addStretch(1)
            if step.key:
                actions.addWidget(_link(tr("No quiero hacer este paso"),
                                        lambda _=False: self._skip(step.key, step.title),
                                        tr("Lo quito de tu ruta y la rehago sin él; lo recuperas en Preferencias")))
            lay.addLayout(actions)
        return row

    def _parts(self, lay, step):
        """A card of several (the pieces of «Encanta», the voucher's choices...): each one can be left out."""
        if len(step.parts) <= 1:
            return
        f = self.fonts
        lay.addWidget(label(tr("Quitar solo una:"), f.small, theme.MUTED))
        grid = QGridLayout()
        grid.setContentsMargins(18, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(0)
        for i, (key, text) in enumerate(step.parts):
            grid.addWidget(_link(f"✕ {text}", lambda _=False, k=key, t=text: self._skip(k, t),
                                 tr("No quiero esto: rehago la ruta sin ello")), i // 3, i % 3, Qt.AlignLeft)
        lay.addLayout(grid)

    def _toggle(self, key):
        self._open.symmetric_difference_update({key})
        QTimer.singleShot(0, self.refresh)

    # --- Tus piezas ---
    def _pieces(self, plan):
        """Tu equipo, laid out like the game's equipment screen: armor on the left, weapon and guard in the middle,
        the accessories on the right; the Arcana cards under them."""
        f = self.fonts
        self._hint(tr("Barras: el +N hasta su máximo (la marca blanca: hasta donde nunca falla) y el Soul Binding. "
                      "Puntos: huecos de manastone o soulstone. Pasa el ratón por una pieza para ver sus líneas."))
        grid = QGridLayout()
        grid.setSpacing(8)
        by_pos = {p.pos: p for p in plan.pieces}
        for pos, (col, row) in DOLL.items():
            piece = by_pos.get(pos)
            if piece is not None and piece.item:
                grid.addWidget(self._tile(plan, piece), row, col)
            else:
                grid.addWidget(self._empty_tile(SLOTS.get(pos, ("", tr("Hueco")))[1]), row, col)
        if plan.arcana_plan is not None and plan.arcana_plan.worn:
            cards = QFrame()
            cards.setObjectName("Card")
            lay = QVBoxLayout(cards)
            lay.setContentsMargins(10, 8, 10, 8)
            lay.setSpacing(4)
            lay.addWidget(label(tr("ARCANA"), f.small, theme.MUTED))
            for kind in sorted(plan.arcana_plan.worn, key=lambda k: ("Chalice", "Parchment", "Compass", "Bell", "Mirror").index(k) if k in ("Chalice", "Parchment", "Compass", "Bell", "Mirror") else 9):
                card = plan.arcana_plan.worn[kind]
                lay.addWidget(label(f"{card.name} +{card.enchant}", f.body, QColor(GRADE.get(card.grade, "#D6DAE3")),
                                    elide=True))
            lay.addWidget(_link(tr("Abrir Arcanas"), self.ctl.open_arcana), 0, Qt.AlignLeft)
            grid.addWidget(cards, 2, 1, 2, 1, Qt.AlignTop)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        self.body.addLayout(grid)

    def _empty_tile(self, slot):
        f = self.fonts
        tile = QFrame()
        tile.setObjectName("Tile")
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.addWidget(label(slot.upper(), f.small, theme.MUTED))
        lay.addWidget(label(tr("vacío"), f.small, theme.WARN))
        return tile

    def _tile(self, plan, piece):
        f = self.fonts
        tile = QFrame()
        tile.setObjectName("Tile")
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(label(piece.slot.upper(), f.small, theme.MUTED))
        top.addStretch(1)
        top.addWidget(label(f"{piece.level}", f.name, theme.TEXT))
        top.addWidget(label(f"({piece.base})", f.small, theme.MUTED))
        lay.addLayout(top)
        lay.addWidget(label(f"{piece.item.name} +{piece.enchant}", f.name,
                            QColor(GRADE.get(piece.item.grade, "#D6DAE3")), elide=True))
        if piece.top:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(label("+N", f.small, theme.MUTED))
            row.addWidget(Bar(piece.enchant / piece.top, "#2ec0f2", piece.safe / piece.top if piece.safe else None), 1)
            row.addWidget(label(f"+{piece.enchant}/{piece.top}", f.small, theme.TEXT))
            lay.addLayout(row)
        s = piece.soul
        tips = []
        if s and s.lines:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(label("Soul", f.small, theme.MUTED))
            color = "#f0c24e" if s.locked else "#e0a050" if s.bad else "#c38bff"
            row.addWidget(Bar(s.rate, color), 1)
            row.addWidget(label("100%" if s.locked else f"{s.rate:.0%}", f.small, theme.TEXT))
            lay.addLayout(row)
            tips = [f"{n or '?'}: {stat_value(n, v)}" + (f"  ({p:.0%})" if p is not None else "")
                    + (tr("  · a cambiar") if n in s.bad else "") for n, v, lo, hi, p in s.lines]
            if s.skills:
                tips.append(tr("+ {0} efecto(s) de skill", s.skills))
        if s and (s.sockets or s.theostone):
            filled = len(s.sockets) - s.empty
            dots = f"<span style='color:#8fb3ff'>{'●' * filled}</span><span style='color:#3a4560'>{'●' * s.empty}</span>"
            extra = " · Theostone" if s.theostone else ""
            lbl = QLabel(dots + f"<span style='color:#8a93a8'>{extra}</span>")
            lbl.setFont(f.small)
            lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(lbl)
        opts = plan.options.get(piece.pos) or []
        if opts:
            o = opts[0]
            lay.addWidget(label(f"→ {OPTION[o.kind]}: {o.item.name} (+{o.gain})", f.small, theme.GOLD, elide=True))
            tips += [""] + [f"{OPTION[x.kind]}: {x.item.name} (+{x.gain}) — {x.text}" for x in opts[:5]]
        elif piece.pos in plan.replace:
            lay.addWidget(label(tr("→ a cambiar"), f.small, theme.GOLD))
        lay.addStretch(1)
        if tips:
            tile.setToolTip("\n".join(tips).strip())
        return tile

    # --- Líneas ---
    def _lines(self, plan):
        """The substats table per piece against your pieces, and the gear transfer trick (folded)."""
        f = self.fonts
        stones = plan.have("transfer_stone")
        self._hint(tr("Cada pieza con las líneas que la tabla pide, en orden (› = mejor que la siguiente): en verde "
                      "las que tiene, con su porcentaje. Skill = una línea de habilidad. Tienes {0} Transfer "
                      "Stone{1}.", stones, 's' if stones != 1 else ''))
        if self._fold("transfer", tr("El truco del transfer"), theme.GOLD):
            box = QFrame()
            box.setObjectName("Card")
            lay = QVBoxLayout(box)
            lay.setContentsMargins(12, 8, 12, 8)
            lay.setSpacing(3)
            for text in (tr("Haz las líneas y el +N en una pieza Unique barata del mismo tipo (nivel 36-70) y pásalas "
                            "con transfer a la que te quedas: el transfer lleva el +N y el Soul Binding de la donante, "
                            "siempre sale y no pide Kinah. Encantar una de nivel 36 a +10 cuesta 325K Kinah y 12,960 "
                            "Enhance Stones; una de 86, 775K y 30,920. El Bind, el Sync y el Reset cuestan lo mismo, y "
                            "para el Sync sirve cualquier Unique del mismo tipo."),
                         tr("Reglas (tablas del cliente): mismo tipo de pieza (pecho a pecho, arco a arco). Una pieza "
                            "de nivel 86-102 acepta donantes de cualquier tier por 2 Transfer Stones; hasta 86, 1. "
                            "Desde un tier más bajo, solo si tu pieza nunca recibió un transfer. No pasan las "
                            "manastones, la Theostone, el Potencial ni la línea extra del Philosopher's Stone. Las "
                            "guías no coinciden en si una donante al 100% pasa sus líneas: compruébalo en la vista "
                            "previa del juego antes de confirmar."),
                         tr("Cada Unique que no usas se convierte en una Transfer Stone (Substance Morph).")):
                lbl = label(text, f.small, theme.TEXT)
                lbl.setWordWrap(True)
                lay.addWidget(lbl)
            self.body.addWidget(box)
        if not plan.substats:
            self._hint(tr("Aún no leí tu equipo puesto: entra al juego con el personaje."))
            return
        for review in plan.substats:
            self.body.addWidget(self._review(review))

    def _review(self, review):
        f = self.fonts
        piece = review.piece
        card = QFrame()
        card.setObjectName("Row")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.addWidget(label(piece.slot.upper(), f.small, theme.MUTED))
        top.addWidget(label(f"{piece.item.name} +{piece.enchant}", f.name, QColor(GRADE.get(piece.item.grade,
                                                                                            "#D6DAE3"))))
        s = piece.soul
        top.addWidget(label(tr("sin Soul Binding") if not s or not (s.lines or s.skills) else
                            "Soul Binding 100%" if s.locked else f"Soul Binding {s.rate:.0%}", f.small, theme.MUTED))
        top.addStretch(1)
        lay.addLayout(top)
        chips = QHBoxLayout()
        chips.setSpacing(4)
        for i, tier in enumerate(review.tiers):
            if i:
                chips.addWidget(label("›", f.small, theme.MUTED))
            for j, (stat, has, pct) in enumerate(tier):
                if j:
                    chips.addWidget(label("=", f.small, theme.MUTED))
                text = STAT_ES.get(stat, stat) + (f" {pct:.0%}" if has and pct is not None else "")
                chips.addWidget(_chip(("✓ " if has else "") + text, "#7bd88f" if has else "#4a5470", f))
        chips.addStretch(1)
        lay.addLayout(chips)
        lines = review.piece.soul.skill_lines if review.piece.soul else []
        if lines:                                 # which skills its skill lines raise, against your targets
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(label(tr("Skills:"), f.small, theme.MUTED))
            targets = self.ctl.skill_targets(self._shown or "")
            for _, name, level, wanted in lines:
                color = "#7bd88f" if wanted else ("#e0a050" if targets else "#8a93a8")
                row.addWidget(_chip(("✓ " if wanted else "") + f"{name} +{level}", color, f,
                                    tip=tr("Una de tus skills objetivo") if wanted else
                                    tr("No está en tus skills objetivo") if targets else
                                    tr("Marca tus skills objetivo en Personaje › Arcanas y aquí verás cuáles te sirven")))
            row.addStretch(1)
            lay.addLayout(row)
        if review.others:                         # their own row: a long one never widens the window
            bad = QHBoxLayout()
            bad.setSpacing(4)
            bad.addWidget(label(tr("A cambiar:"), f.small, theme.MUTED))
            for stat, pct in review.others:
                bad.addWidget(_chip(f"✕ {stat}" + (f" {pct:.0%}" if pct is not None else ""), "#e0a050", f,
                                    tip=tr("Línea de una skill que no está en tus skills objetivo: a cambiar con Sync")
                                    if any(stat == x[1] for x in lines) else
                                    tr("Línea que la tabla no pide en esta pieza: a cambiar con Sync")))
            bad.addStretch(1)
            lay.addLayout(bad)
        notes = [review.note] if review.note and review.note != tr("skill: niveles de habilidades pasivas") else []
        for donor, _, found, cost, safe in review.donors[:2]:
            plural = "s" if review.stones != 1 else ""
            text = (tr("Donante: tu {0} +{1} ({2}) tiene ", donor.item.name, donor.enchant, donor.place)
                    + ", ".join(STAT_ES.get(x, x) for x in found) + tr(" → transfer con {0} Transfer Stone{1}. El "
                                                                       "transfer pasa también su +{2}", review.stones, plural, donor.enchant))
            if donor.enchant < piece.enchant:
                text += (tr(": súbela antes a +{0}", piece.enchant) + (tr(" ({0} y {1:,} piedras", kinah_text(cost[0]), cost[1])
                                                               + ("" if safe else tr(", pasado +10 puede fallar")) + ")"
                                                               if cost else "")
                         + tr(" o tu {0} quedará a +{1}.", piece.item.name, donor.enchant))
            else:
                text += "."
            notes.append(text)
        if review.cheap:
            low, theirs, mine = review.cheap
            saved_k, saved_s = mine[0] - theirs[0], mine[1] - theirs[1]
            if saved_k > 0:
                notes.append(tr("Barato: una Unique de nivel {0} del mismo tipo (como {1}) de +0 a +10 cuesta {2} y "
                                "{3:,} piedras; la tuya, de +{4} a +{5}, {6} y {7:,}: hazle allí el +10 y las "
                                "líneas y pásalo, te ahorras {8} y {9:,} piedras por {10} Transfer Stone{11}.", low.level, low.name, kinah_text(theirs[0]), theirs[1], piece.enchant, piece.safe, kinah_text(mine[0]), mine[1], kinah_text(saved_k), saved_s, review.stones, 's' if review.stones != 1 else ''))
        for text in notes:
            lbl = label(text, f.small, theme.MUTED if text == review.note else theme.TEXT)
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
        return card

    # --- Contenido ---
    def _content(self, plan):
        """Every activity: what it asks for, whether you can do it (or why not) and what it gives you."""
        f = self.fonts
        rows = plan.content
        me = plan.me
        can = sum(1 for a in rows if not a.reason and not a.closed)
        self._hint(tr("{0} actividades: puedes {1}", len(rows), can)
                   + (tr(" con {0:,} de nivel de objeto", me.item_level) if me and me.item_level else "")
                   + tr(". «Te da»: lo que mejora lo que llevas puesto."))
        for group in dict.fromkeys(a.group for a in rows):
            mine = [a for a in rows if a.group == group]
            live = [a for a in mine if not a.closed]
            closed = [a for a in mine if a.closed]
            ok = sum(1 for a in live if not a.reason)
            head = QHBoxLayout()
            head.setContentsMargins(2, 8, 0, 0)
            head.addWidget(label(group, f.name, theme.TEXT))
            head.addWidget(label(tr("puedes {0} de {1}", ok, len(live)) if live else tr("aún no abre"), f.small,
                                 STATUS["ok"] if ok else theme.MUTED))
            head.addStretch(1)
            self.body.addLayout(head)
            card = QFrame()
            card.setObjectName("Card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(10, 6, 10, 6)
            lay.setSpacing(2)
            for a in live:
                lay.addWidget(self._activity(a))
            if closed:
                by_mode = {}
                for a in closed:
                    by_mode.setdefault(a.mode, []).append(a.name)
                more = label(tr("Aún no abren en Global ({0}): ", len(closed)) + "; ".join(
                    (f"{mode}: " if mode else "") + ", ".join(dict.fromkeys(names)) for mode, names in by_mode.items()),
                    f.small, theme.MUTED)
                more.setWordWrap(True)
                more.setContentsMargins(4, 4, 0, 2)
                lay.addWidget(more)
            self.body.addWidget(card)

    def _activity(self, a):
        f = self.fonts
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(2, 3, 2, 3)
        lay.setSpacing(10)
        color = STATUS["ok"] if not a.reason else STATUS["no"]
        dot = label("●", f.small, color)
        dot.setFixedWidth(12)
        lay.addWidget(dot, 0, Qt.AlignTop)
        name = QVBoxLayout()
        name.setContentsMargins(0, 0, 0, 0)
        name.setSpacing(0)
        name.addWidget(label(a.name + (f" · {a.mode}" if a.mode else ""), f.name, theme.TEXT, elide=True))
        if a.needs:
            name.addWidget(label(a.needs, f.small, theme.MUTED, elide=True))
        box = QWidget()
        box.setLayout(name)
        box.setFixedWidth(330)
        lay.addWidget(box, 0, Qt.AlignTop)
        status = label(tr("Puedes") if not a.reason else a.reason, f.small, color)
        status.setWordWrap(True)
        status.setFixedWidth(220)
        lay.addWidget(status, 0, Qt.AlignTop)
        gives = label(a.gives, f.small, theme.TEXT if a.useful else theme.MUTED)
        gives.setWordWrap(True)
        lay.addWidget(gives, 1, Qt.AlignTop)
        for boost in a.boosts:
            text, c, tip = BOOST[boost]
            lay.addWidget(_chip(text, c, f, tip=tip), 0, Qt.AlignTop)
        return row

    # --- Comida ---
    def _food(self, plan):
        f = self.fonts
        if not plan.spare:
            self._hint(tr("No tienes piezas sobrantes en la bolsa ni en los almacenes."))
            return
        self._hint(tr("Piezas de tu bolsa y tus almacenes que no llevas. Una del mismo tipo y grado sirve para el "
                      "Sync de la tuya; cualquier Unique se convierte en 1 Sync Stone o 1 Transfer Stone (Substance "
                      "Morph). Primero las +0 y de menos nivel."))
        groups = {}
        for x in plan.spare:
            groups.setdefault(x.use or (tr("Guárdala") if x.item.grade != "Unique" else tr("Transfer Stone o Sync Stone")),
                              []).append(x)
        mine = tr("Sync de tu {0}", "")
        order = sorted(groups, key=lambda u: (not u.startswith(mine), not u.startswith("Sync Stone"), u))
        for use in order:
            card = QFrame()
            card.setObjectName("Card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(12, 8, 12, 8)
            lay.setSpacing(4)
            lay.addWidget(label(use, f.name, theme.GOLD if use.startswith("Sync") else theme.TEXT))
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(2)
            for i, x in enumerate(groups[use]):
                color = QColor(GRADE.get(x.item.grade, "#D6DAE3")).name()
                name = QLabel(f"<span style='color:{color}'>{x.item.name} +{x.enchant}</span>"
                              f"<span style='color:#8a93a8'>  ·  {x.place}</span>")
                name.setFont(f.body)
                name.setToolTip(tr("nivel {0}", x.level) + (tr(" · su Soul Binding {0:.0%}", x.rate) if x.rate else ""))
                grid.addWidget(name, i // 3, i % 3)
            for c in range(3):
                grid.setColumnStretch(c, 1)
            lay.addLayout(grid)
            self.body.addWidget(card)

    # --- Preferencias ---
    def _prefs(self, plan):
        f = self.fonts
        self.body.addWidget(SectionTitle(tr("Main o alt"), f))
        self._hint(tr("{0} es {1}: se cambia arriba, junto al personaje.", plan.character, 'alt' if plan.me and plan.me.alt else 'main'))
        self.body.addWidget(SectionTitle(tr("Skills objetivo de {0}", plan.character), f))
        names = {int(k): v.get("name") or k for k, v in self.ctl.tuner.skills.items()}
        row = QHBoxLayout()
        row.addWidget(label(", ".join(f"{names.get(k, k)} {v}" for k, v in sorted(plan.targets.items(), key=lambda x: -x[1]))
                            if plan.targets else tr("Ninguna: cualquier línea de skill de tu equipo cuenta como buena."),
                            f.small, theme.TEXT if plan.targets else theme.MUTED), 1)
        row.addWidget(_link(tr("Elegir skills objetivo"), lambda: self.ctl.open_arcana("skills"),
                            tr("Las skills que quieres subir y a qué nivel: con ellas, una línea de skill en otra skill "
                               "sale «a cambiar» (Sync) y Arcanas te dice qué cartas buscar")))
        self.body.addLayout(row)
        self.body.addWidget(SectionTitle(tr("Mazmorras que hace {0}", plan.character), f))
        auto = self.ctl.upgrades_auto_dungeons(plan.character)
        _, chosen = self.ctl.upgrades_dungeons(plan.character)
        self._hint(tr("De ellas salen las piezas que te dice que farmees y sus garantizados (cada personaje las "
                      "suyas). ")
                   + (tr("Detecté: {0}. ", ', '.join(auto)) if auto else "")
                   + (tr("Elegidas a mano.") if chosen else tr("Automático: las que detecto."))
                   + tr(" Una en la que aún no puedes entrar no se farmea: sale como meta."))
        boxes = QGridLayout()
        boxes.setHorizontalSpacing(16)
        db = self.ctl.upgrades_db
        for i, dungeon in enumerate(db.dungeons()):
            modes = [c for c in db.content if c["kind"] == "dungeon" and c["name"] == dungeon and c["open"]]
            reasons = [can_do(c, plan.me) for c in modes]
            locked = modes and all(reasons)
            box = QCheckBox(dungeon + (tr(" (detectada)") if dungeon in auto else "")
                            + (tr(" · aún no puedes") if locked else ""))
            if locked:
                box.setToolTip(min(reasons, key=len))
            box.setChecked(dungeon in plan.dungeons)
            box.toggled.connect(lambda on, d=dungeon: self._toggle_dungeon(d, on))
            boxes.addWidget(box, i // 3, i % 3)
        self.body.addLayout(boxes)
        if chosen:
            reset = QPushButton(tr("Automático"))
            reset.clicked.connect(lambda: self._set_dungeons(None))
            self.body.addWidget(reset, 0, Qt.AlignLeft)
        self.body.addWidget(SectionTitle(tr("Pasos que {0} no quiere hacer", plan.character), f))
        skipped = self.ctl.upgrades_skipped(plan.character)
        if not skipped:
            self._hint(tr("Ninguno. En cada paso de tu ruta, «No quiero hacer este paso» lo quita y la rehace sin "
                          "él."))
            return
        self._hint(tr("No cuentan en tu ruta. «Volver a tenerlo en cuenta» lo devuelve y la ruta se rehace con él."))
        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(12, 8, 12, 8)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)
        for r, (key, title) in enumerate(sorted(skipped.items(), key=lambda x: x[1])):
            grid.addWidget(label(title, f.body, theme.TEXT), r, 0)
            grid.addWidget(_link(tr("Volver a tenerlo en cuenta"), lambda _=False, k=key: self._unskip(k)), r, 1)
        grid.setColumnStretch(0, 1)
        self.body.addWidget(card)
        if len(skipped) > 1:
            every = QPushButton(tr("Recuperar todos"))
            every.clicked.connect(lambda: self._unskip(None))
            self.body.addWidget(every, 0, Qt.AlignLeft)

    def _skip(self, key, title):
        self.ctl.upgrades_skip(key, title, self._shown)
        QTimer.singleShot(0, self.refresh)

    def _unskip(self, key=None):
        self.ctl.upgrades_unskip(key, self._shown)
        QTimer.singleShot(0, self.refresh)

    def _toggle_dungeon(self, dungeon, on):
        current = list(self.ctl.upgrade_plan(self.character).dungeons)
        if on and dungeon not in current:
            current.append(dungeon)
        elif not on and dungeon in current:
            current.remove(dungeon)
        self._set_dungeons(current)

    def _set_dungeons(self, dungeons):
        self.ctl.set_upgrades_dungeons(dungeons, self._shown)
        QTimer.singleShot(0, self.refresh)
