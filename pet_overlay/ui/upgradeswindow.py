"""Mejoras, a page of the AionDex window, about the character the window's picker names (ctl.focus_character(); the
hub calls refresh() when it changes): who it is (item level, CP, main or alt), what it has to spend, and six tabs -
your route (the plan of upgrades.py: «Lo siguiente» big, then one short row per step, grouped, whose detail opens on
a click, each with what it raises: ↑ ilvl / ↑ CP), your pieces (each one as the game's tooltip: ui/itemcard.py, laid
out armor / weapon, guard and Arcana / accessories), «Líneas» (per piece what it has, what the guide asks for and what
to do, with filters), «Contenido» (every activity of the game: what it asks for, whether you can do it and what it
gives you, with filters), «Sobrantes» (the pieces you do not use and what each one is for) and your preferences (the
dungeons you run, the steps you said no to)."""
import time
import webbrowser

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLayout, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..arcana import farm_options, short_kind
from ..gear import SLOTS
from ..upgrades import CATEGORIES, GUIDE, SUBSTATS, SUBSTATS_GUIDE, can_do, kinah_text, stat_value
from .charpicker import CharacterPicker
from . import theme
from .design import Chip, ChipGroup, GameTabs, PageHeader, RewardTile, SearchBox, Stat
from .itemcard import STAT_GRADE, ItemCard, ItemData, Socket, SoulLine, item_from_official
from .theme import Fonts
from .widgets import SectionTitle, label
from ..i18n import tr

KIND = {   # step kind -> (chip text, colour)
    "own": (tr("Equipar"), theme.GAIN), "voucher": ("Voucher", theme.GOLD), "enhance": (tr("Encantar"), theme.ACCENT),
    "morph": (tr("Subir de grado"), theme.ACCENT), "soul": ("Soul Binding", theme.PURPLE),
    "manastone": ("Manastones", theme.ELYOS), "soulstone": ("Soulstones", theme.ELYOS), "amp": ("Amp", theme.ACCENT),
    "buy": (tr("Comprar"), theme.GOLD), "save": (tr("Ahorrar"), theme.WARN), "farm": (tr("Farmear"), theme.GAIN),
    "spare": (tr("Piedras"), theme.MUTED), "theostone": ("Theostone", theme.MUTED), "boss": (tr("Jefe"), theme.GAIN),
    "arcana": ("Arcana", theme.ACCENT), "content": (tr("Actividad"), theme.ACCENT), "shop": (tr("Tienda"), theme.GOLD),
    "goal": (tr("Meta"), theme.COMBAT),
}
GROUPS = (("now", tr("Para hacer ya"), tr("Tienes todo lo que piden")),
          ("later", tr("Te falta algo"), tr("Kinah, piedras o materiales")),
          ("buy", tr("Comprar en el market"), tr("Con los precios de tus muestreos")),
          ("farm", tr("Farmear"), tr("Tus mazmorras y los jefes a los que llegas")),
          ("week", tr("Cada semana"), tr("Lo que puedes hacer y lo que te da")),
          ("locked", tr("Cuando subas tu nivel de objeto"), tr("Aún no puedes entrar: lo que te falta")),
          ("extra", "Extras", ""))
OPTION = {"own": tr("La tienes"), "voucher": "Voucher", "drop": tr("Mazmorra"), "market": "Market", "boss": tr("Jefe")}
BOOST = {"ilvl": ("↑ ilvl", theme.ELYOS, tr("Sube tu nivel de objeto: lo que piden las mazmorras, la Trascendencia y "
                                            "el Abismo (la suma del nivel de tus piezas y su +N)")),
         "cp": ("↑ CP", theme.ASMODIAN, tr("Sube tu CP (Combat Power): todo lo que te da estadísticas"))}
ROLE_TIP = (tr("Main: Mejoras usa todo lo que tienes (Kinah comerciable y ligada, el market y los materiales que se "
               "pueden vender). Alt: solo lo ligado (Kina (Bound) y lo que no se puede comerciar), nada del market: "
               "lo comerciable de un alt es para tu main."))
TILES = (("kina", "Kinah", kinah_text), ("kina_bound", tr("Kinah ligada"), kinah_text),
         ("enhance_stone", "Enhance Stones", lambda v: f"{v:,}"),
         ("transfer_stone", "Transfer Stones", str), ("soul_codex", "Soul Codex", str),
         ("soul_reset", "Codex: Reset", str), ("sync_stone", "Sync Stones", str),
         ("philosopher", "Philosopher's", str), ("amplify_stone", "Amplify Stones", str))
TABS = (("route", tr("Tu ruta")), ("pieces", tr("Tu equipamiento")), ("lines", tr("Líneas")), ("content", tr("Contenido")),
        ("food", tr("Sobrantes")), ("prefs", tr("Preferencias")))
# Tu equipo in three columns, like the game's equipment screen: armor at the left; weapon, guard and the Arcana in the
# middle (belt, amulet and runes under them); the accessories at the right
COLUMNS = ((3, 4, 5, 7, 6, 8, 19), (1, 2, "arcana", 17, 22, 23, 24), (10, 11, 12, 13, 14, 15, 16))
ARCANA_ORDER = ("Chalice", "Parchment", "Compass", "Bell", "Mirror")
ARCANA_POS = {k: 41 + i for i, k in enumerate(ARCANA_ORDER)}
STAT_ES = {"skill": "Skill", "Move Speed": "Movement Speed", "Attack increase": "Attack Increase"}
# a manastone / soulstone's stat id in the traffic -> the game's name (the client's stat ids: 193 HP, 317 Attack,
# 307 Defense, 6 Willpower; 199, 104 and 116 matched against the game's tooltip of the Kromede Warhammer)
SOCKET_STAT = {193: "HP", 199: "MP", 317: "Attack", 307: "Defense", 6: "Willpower", 104: "Accuracy Bonus",
               116: "Evasion Bonus"}
ACTION = {"sync": ("Sync", theme.PURPLE), "reset": ("Reset", theme.WARN), "fuse": ("Soul Fuse", theme.PURPLE),
          "bind": ("Bind", theme.ACCENT), "transfer": ("Transfer", theme.GOLD), "cheap": (tr("Más barato"), theme.GOLD),
          "replace": (tr("Cambio"), theme.GOLD), "ok": ("✓", theme.GAIN)}
# kept for the windows that still import them (Arcanas)
GRADE = {"Unique": "#FFD02B", "Epic": "#379AFF", "Rare": "#19E048", "Special": "#00FFD8"}
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


def _wrap(text, font, color):
    lbl = label(text, font, color)
    lbl.setWordWrap(True)
    return lbl


class Bar(QWidget):
    """A thin progress bar, with an optional mark (the +N that never fails). Kept for Arcanas: pages use
    design.Meter."""

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


class _Flow(QLayout):
    """A layout that wraps its items into rows (many filter chips in a narrow page)."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self._space = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _place(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line:
                x, y, line = rect.x(), y + line + self._space, 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._space
            line = max(line, hint.height())
        return y + line - rect.y()


class _FlowChips(QWidget):
    """design.ChipGroup's chips (one chosen) in rows that wrap: the dozen kinds of «Contenido»."""
    changed = Signal()

    def __init__(self, fonts, chips, current=None, parent=None):
        super().__init__(parent)
        lay = _Flow(self, fonts.px(6))
        self.buttons = {}
        self.group = QButtonGroup(self)
        for key, text in chips:
            btn = QPushButton(text)
            btn.setProperty("chip", True)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(fonts.small)
            self.group.addButton(btn)
            self.buttons[key] = btn
            lay.addWidget(btn)
            btn.toggled.connect(lambda on, k=key: on and self.changed.emit())
        first = current if current in self.buttons else next(iter(self.buttons), None)
        if first is not None:
            self.buttons[first].blockSignals(True)
            self.buttons[first].setChecked(True)
            self.buttons[first].blockSignals(False)

    def selected(self):
        return next((k for k, b in self.buttons.items() if b.isChecked()), None)

    def set_label(self, key, text):
        if key in self.buttons:
            self.buttons[key].setText(text)


class UpgradesWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.tab = "route"
        self._open = set()                 # step keys whose detail is open
        self._folds = set()                 # the folded sections that are open (categories, the transfer trick...)
        self._shown = None                  # the character whose plan is on screen
        self._icon_tries = 0
        self._icons_missing = False
        self._groups_key = None
        self.setWindowTitle(f"{APP_NAME} — {tr('Mejoras')}")
        self.setWindowIcon(ctl.app_icon)
        self.resize(1100, 860)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(f.px(22), f.px(16), f.px(22), f.px(12))
        root.setSpacing(f.px(10))
        self.header = PageHeader(f, tr("Mejoras"), "")
        self.header.add_picker(CharacterPicker(self.ctl, f))
        self.header.subtitle.setWordWrap(True)
        self.role = QComboBox()
        self.role.addItem(tr("Main: usa todo"), "main")
        self.role.addItem(tr("Alt: solo lo ligado"), "alt")
        self.role.setToolTip(ROLE_TIP)
        self.role.currentIndexChanged.connect(self._role_changed)
        self.header.add_right(self.role)
        prices = QPushButton(tr("Precios de equipo"))
        prices.setProperty("ghost", True)
        prices.setToolTip(tr("Market › Equipo: escanea en el juego las listas de equipo del mercado y aquí verás "
                             "qué conviene comprar"))
        prices.clicked.connect(lambda: self.ctl.open_crafting("gear"))
        self.header.add_right(prices)
        self.update_button = QPushButton(tr("↻ Actualizar"))
        self.update_button.setProperty("ghost", True)
        self.update_button.setToolTip(tr("Rehace todo ahora con lo último que leí del juego y vuelve a pedir tu "
                                         "perfil oficial (nivel de objeto y CP). Tu equipo, tu bolsa y tus "
                                         "almacenes se leen solos mientras juegas; si algo no aparece, cambia de "
                                         "canal y el juego los manda de nuevo."))
        self.update_button.setMinimumWidth(self.update_button.fontMetrics().horizontalAdvance(tr("Actualizando…")) + 36)
        self.update_button.clicked.connect(self._update_now)
        self.header.add_right(self.update_button)
        root.addWidget(self.header)

        self.resources = QHBoxLayout()
        self.resources.setSpacing(f.px(8))
        root.addLayout(self.resources)
        self.tabs = GameTabs(f, TABS, self.tab)
        self.tabs.changed.connect(self.show_tab)
        root.addWidget(self.tabs)

        # the filters of each tab: made once (typing in a search box never loses its focus to a redraw)
        self.filter_bars = {}
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(f.px(10))
        self.lines_filter = ChipGroup(f, [("all", tr("Todas")), ("change", tr("Con algo que cambiar")),
                                          ("nosoul", tr("Sin Soul Binding")), ("donor", tr("Con donante"))], "all")
        self.lines_filter.changed.connect(self._filters_changed)
        lay.addWidget(self.lines_filter, 1)
        self.lines_search = SearchBox(f, tr("Buscar pieza…"))
        self.lines_search.changed.connect(lambda _t: self._search_timer.start())
        lay.addWidget(self.lines_search)
        self.filter_bars["lines"] = bar
        bar = QWidget()
        col = QVBoxLayout(bar)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(f.px(6))
        self.groups_box = QVBoxLayout()
        self.groups_box.setContentsMargins(0, 0, 0, 0)
        col.addLayout(self.groups_box)
        self.content_group = None
        row = QHBoxLayout()
        row.setSpacing(f.px(10))
        self.content_state = ChipGroup(f, [("all", tr("Todo")), ("can", tr("Puedes")), ("cannot", tr("Aún no"))],
                                       "all")
        self.content_state.changed.connect(self._filters_changed)
        row.addWidget(self.content_state)
        self.content_useful = ChipGroup(f, [("useful", tr("Solo lo que te da algo"),
                                             tr("Solo las actividades que sueltan algo que mejora lo que llevas"))],
                                        multi=True, gold=True)
        self.content_useful.changed.connect(self._filters_changed)
        row.addWidget(self.content_useful, 1)
        self.content_search = SearchBox(f, tr("Buscar actividad o premio…"))
        self.content_search.setMinimumWidth(f.px(220))
        self.content_search.changed.connect(lambda _t: self._search_timer.start())
        row.addWidget(self.content_search)
        col.addLayout(row)
        self.filter_bars["content"] = bar
        for bar in self.filter_bars.values():
            bar.hide()
            root.addWidget(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(0, 0, f.px(6), 0)
        self.body.setSpacing(f.px(8))
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
        self._search_timer = QTimer(self, singleShot=True, interval=200, timeout=self._filters_changed)
        self.refresh()

    def showEvent(self, event):
        self._icon_tries = 0
        self.refresh()
        super().showEvent(event)

    def changed(self):
        """Something it shows changed (items, gear, prices): one redraw shortly after."""
        if self.isVisible():
            self._timer.start()

    def show_tab(self, key):
        self.tab = key
        self._icon_tries = 0
        self.refresh()
        self.scroll.verticalScrollBar().setValue(0)

    def _filters_changed(self):
        self.refresh()
        self.scroll.verticalScrollBar().setValue(0)

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

    # --- content ---
    def refresh(self):
        bar = self.scroll.verticalScrollBar().value()
        _clear(self.body)
        _clear(self.resources)
        self.tabs.set_current(self.tab)
        plan = self.ctl.upgrade_plan()
        for key, widget in self.filter_bars.items():
            widget.setVisible(key == self.tab and plan is not None)
        if plan is None:
            name = self.ctl.focus_character()
            self.header.set_subtitle("")
            self._hint(tr("Aún no leí el equipo de {0}: entra al juego con ese personaje con AionDex abierto y aquí "
                          "te digo qué hacer primero.", name) if name else
                       tr("Entra al juego con tu personaje con AionDex abierto: leo tu equipo, tu bolsa y tus "
                          "almacenes y aquí te digo qué hacer primero."), theme.WARN)
            self.body.addStretch(1)
            return
        self._shown = plan.character
        self.role.blockSignals(True)
        self.role.setCurrentIndex(max(0, self.role.findData(self.ctl.upgrades_role(plan.character))))
        self.role.blockSignals(False)
        me = plan.me
        bits = [plan.character, tr("bolsa y almacén leídos {0}", _ago(plan.read_at))]
        if me and me.item_level is not None:
            bits.insert(1, tr("perfil oficial {0}", _ago(me.at)) + (tr(" + tus cambios") if me.item_level != me.official else ""))
        if self.ctl.armory.loading(plan.character):
            bits.append(tr("pidiendo tu perfil oficial…"))
        if not (me and me.alt) and not self.ctl.gear_prices():
            bits.append(tr("sin precios de equipo (Market › Equipo)"))
        self.header.set_subtitle(" · ".join(bits))
        counts = {"route": len(plan.steps), "food": len(plan.spare),
                  "lines": sum(1 for r in plan.substats if self._line_state(plan, r) != "ok"),
                  "content": sum(1 for a in plan.content if not a.reason and not a.closed),
                  "prefs": len(self.ctl.upgrades_skipped(plan.character))}
        for key, text in TABS:
            n = counts.get(key)
            self.tabs.set_label(key, f"{text} ({n})" if n else text)
        self._resources(plan)
        if plan.read_at is None:
            self._hint(tr("Aún no leí tu bolsa ni tu almacén: cambia de canal o vuelve a entrar al personaje."), theme.WARN)
        if me and me.alt and self.tab == "route":
            keep = [f"{kinah_text(n)} Kinah" if k == "Kina" else f"{n:,} {k}" for k, n in plan.for_main.items()]
            self._hint(tr("Alt: solo cuento lo ligado (Kina (Bound) y lo que no se puede comerciar) y nada del "
                          "market.")
                       + (tr(" Comerciable, para tu main: {0}.", ', '.join(keep[:6])) if keep else ""))
        self._icons_missing = False
        {"route": self._route, "pieces": self._pieces, "lines": self._lines, "content": self._content,
         "food": self._food, "prefs": self._prefs}[self.tab](plan)
        self.body.addStretch(1)
        if self._icons_missing and self._icon_tries < 4:      # the pictures come from NCSOFT's CDN: look again
            self._icon_tries += 1
            QTimer.singleShot(4000, self.changed)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(bar))

    def _hint(self, text, color=None):
        self.body.addWidget(_wrap(text, self.fonts.small, color or theme.MUTED))

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
        lay.setContentsMargins(f.px(12), f.px(6), f.px(12), f.px(8))
        lay.setSpacing(f.px(4))
        grid = QGridLayout()
        grid.setHorizontalSpacing(f.px(14))
        grid.setVerticalSpacing(f.px(2))
        for i, (key, text, _) in enumerate(CATEGORIES):
            n = plan.counts.get(key, 0)
            box = QCheckBox(f"{text} ({n})" if n else text)
            box.setChecked(key not in plan.off)
            box.setToolTip(tr("Desmárcalo y tu ruta se rehace sin esto (sin Soul Binding quedan libres las piezas "
                              "de sus Sync)"))
            box.toggled.connect(lambda on, k=key: self._category(k, on))
            grid.addWidget(box, i // 4, i % 4)
        lay.addLayout(grid)
        lay.addWidget(_wrap(tr("Solo para {0}. Cada paso dice qué sube: ↑ ilvl (lo que piden las mazmorras) y / o "
                               "↑ CP.", plan.character), f.small, theme.MUTED))
        self.body.addWidget(card)

    def _category(self, key, on):
        self.ctl.set_upgrades_category(self._shown, key, on)
        QTimer.singleShot(0, self.refresh)

    # --- Tu ruta ---
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
            head.setContentsMargins(f.px(2), f.px(8), 0, 0)
            head.setSpacing(f.px(8))
            head.addWidget(label(title, f.card_title, theme.TEXT))
            head.addWidget(Chip(str(len(steps)), theme.MUTED, f))
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
                               tr("exp para tus cartas: ~{0:,.0f} por clear", fm.food)) if fm.can else
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
            sub = ready[0][0].view(ctl.crafting_faction())[0]
            if plan.character != ctl.character_name():     # what you collect in the game arrives with the next login
                read = (ctl.missions.characters.get(plan.character) or {}).get("at")
                if read:
                    sub += " · " + tr("según lo leído {0}", _ago(read))
            tiles.append((tr("Season Missions"), tr("{0} recompensas por cobrar", len(ready)), sub,
                          theme.GAIN, tr("Abrir"), ctl.open_missions))
        if not tiles:
            return
        self.body.addWidget(SectionTitle(tr("Qué hacer a continuación"), f))
        row = QHBoxLayout()
        row.setSpacing(f.px(8))
        for eyebrow, title, line, color, action, slot in tiles[:4]:
            tile = QFrame()
            tile.setObjectName("Tile")
            lay = QVBoxLayout(tile)
            lay.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
            lay.setSpacing(f.px(2))
            lay.addWidget(label(eyebrow.upper(), f.section, theme.MUTED))
            lay.addWidget(_wrap(title, f.name, color))
            if line:
                lay.addWidget(_wrap(line, f.small, theme.MUTED))
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
        lay.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
        cards = plan.arcana_plan
        text = tr("Arcanas: una Unique tiene 4 efectos de skill y llega a +5 (9 niveles). Se suben con las cartas que "
                  "no usas: cada una pasa la exp de su grado más toda la que tenía, así que una mejorada que ya no "
                  "sirve vale mucho como material.")
        if cards is not None and cards.look_for:
            kind, rows = next(iter(cards.look_for.items()))
            text += " " + tr("Te falta sobre todo {0}: búscalo en un {1}.", rows[0][1], short_kind(kind))
        lay.addWidget(_wrap(text, f.small, theme.TEXT), 1)
        lay.addWidget(_link(tr("Abrir Arcanas"), self.ctl.open_arcana))
        self.body.addWidget(card)

    def _hero(self, step):
        """«Lo siguiente»: the first step you can do now, in big; its detail opens on «ver cómo»."""
        f = self.fonts
        card = QFrame()
        card.setObjectName("Hero")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(f.px(18), f.px(14), f.px(18), f.px(12))
        lay.setSpacing(f.px(6))
        top = QHBoxLayout()
        top.setSpacing(f.px(6))
        top.addWidget(label(tr("LO SIGUIENTE"), f.section, theme.GOLD))
        text, color = KIND.get(step.kind, (step.kind, theme.MUTED))
        top.addWidget(Chip(text, color, f))
        top.addStretch(1)
        for chip in self._chips(step):
            top.addWidget(chip)
        lay.addLayout(top)
        lay.addWidget(_wrap(step.title, f.display, theme.TEXT))
        if step.summary:
            lay.addWidget(_wrap(step.summary, f.body, theme.GOLD if step.ready else theme.MUTED))
        is_open = step.key in self._open or not step.summary
        if is_open:
            lay.addWidget(_wrap(step.detail, f.small, theme.MUTED))
            self._parts(lay, step)
        self._links(lay, step)
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
            out.append(Chip(text, color, f, tip=tip))
        if step.gain and step.kind in ("own", "voucher", "buy", "save", "farm", "enhance", "boss", "arcana", "goal"):
            out.append(Chip(tr("+{0} nivel", step.gain), theme.GAIN, f))
        if step.kinah:
            out.append(Chip(f"{kinah_text(step.kinah)}", theme.GOLD, f, tip=tr("Kinah que cuesta")))
        return out

    def _row(self, step, n):
        """One step in a short row: number, title and its one line; «ver cómo» opens the detail."""
        f = self.fonts
        text, color = KIND.get(step.kind, (step.kind, theme.MUTED))
        row = QFrame()
        row.setObjectName("Row")
        row.setStyleSheet(f"QFrame#Row {{ border-left: 3px solid {QColor(color).name()}; }}")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(f.px(10), f.px(6), f.px(10), f.px(6))
        lay.setSpacing(f.px(3))
        top = QHBoxLayout()
        top.setSpacing(f.px(8))
        top.addWidget(label(str(n), f.small, theme.MUTED))
        title = QVBoxLayout()
        title.setSpacing(0)
        title.addWidget(label(step.title, f.name, theme.GOLD if step.ready else theme.TEXT, elide=True))
        if step.summary:
            title.addWidget(label(step.summary, f.small, theme.MUTED, elide=True))
        top.addLayout(title, 1)
        top.addWidget(Chip(text, color, f), 0, Qt.AlignVCenter)
        for chip in self._chips(step):
            top.addWidget(chip, 0, Qt.AlignVCenter)
        is_open = step.key in self._open
        top.addWidget(_link(tr("▾ ocultar") if is_open else tr("▸ ver cómo"), lambda _=False: self._toggle(step.key)),
                      0, Qt.AlignVCenter)
        lay.addLayout(top)
        if is_open:
            detail = _wrap(step.detail, f.small, theme.TEXT)
            detail.setContentsMargins(f.px(18), f.px(2), 0, 0)
            lay.addWidget(detail)
            self._parts(lay, step)
            self._links(lay, step)
            actions = QHBoxLayout()
            actions.setContentsMargins(f.px(18), 0, 0, 0)
            actions.addStretch(1)
            if step.key:
                actions.addWidget(_link(tr("No quiero hacer este paso"),
                                        lambda _=False: self._skip(step.key, step.title),
                                        tr("Lo quito de tu ruta y la rehago sin él; lo recuperas en Preferencias")))
            lay.addLayout(actions)
        return row

    def _links(self, lay, step):
        """What the rest of AionDex knows about a step: on a farm step, your Odyle energy against the dungeon's
        cubes (Status); on a boss step, the Tracker's state of the boss, its bell and the map."""
        f = self.fonts
        ctl = self.ctl
        now = time.time()
        if step.kind == "farm" and step.key.startswith("farm-dungeon:"):
            dungeon = step.key.split(":", 1)[1]
            cubes = [c for c in ctl.upgrades_db.cubes if c["dungeon"] == dungeon and c["open"] and c["energy"]]
            if not cubes or not self._shown:
                return
            energy = min(c["energy"] for c in cubes)
            try:
                v = ctl.status.value(self._shown, "energy", now)
            except Exception:                    # noqa: BLE001 - Status is its own module
                return
            if not v.known:
                return
            have = v.count + v.bonus
            n = have // energy
            text = (tr("Energía de Odyle: {0}(+{1}) → 1 cubo de {2} ahora", v.count, v.bonus, energy) if n == 1 else
                    tr("Energía de Odyle: {0}(+{1}) → {2} cubos de {3} ahora", v.count, v.bonus, n, energy))
            if v.full_at and v.full_at > now:
                text += tr(" · llena en {0:.0f} h", (v.full_at - now) / 3600)
            row = QHBoxLayout()
            row.setContentsMargins(f.px(18), 0, 0, 0)
            row.addWidget(label(text, f.small, theme.GAIN if n else theme.WARN))
            row.addStretch(1)
            row.addWidget(_link(tr("Abrir Status"), ctl.open_status))
            lay.addLayout(row)
        elif step.kind == "boss" and step.key.startswith("boss:"):
            boss = step.key.split(":", 1)[1]
            entry = next((e for e in ctl.tracker.entries if e.name == boss), None)
            if entry is None:
                return
            from ..modules.tracker import when_text
            st = ctl.tracker.status(entry, now)
            text, color = when_text(st, now)
            row = QHBoxLayout()
            row.setContentsMargins(f.px(18), 0, 0, 0)
            row.addWidget(label(tr("Tracker: {0}", text), f.small, color))
            if getattr(st, "detail", ""):
                row.addWidget(label(st.detail, f.small, theme.MUTED, elide=True), 1)
            else:
                row.addStretch(1)
            on = ctl.tracker.marked(entry.id)
            row.addWidget(_link(tr("🔔 Avisándome") if on else tr("🔔 Avisarme"),
                                lambda _=False, i=entry.id: (ctl.tracker.toggle(i), self.refresh(), ctl.refresh()),
                                tr("La campana del Tracker: te avisa cuando sale")))
            if ctl.boss_on_map(entry):
                row.addWidget(_link(tr("Mapa"), lambda _=False, e=entry: ctl.open_boss_map(e)))
            lay.addLayout(row)

    def _parts(self, lay, step):
        """A card of several (the pieces of «Encanta», the voucher's choices...): each one can be left out."""
        if len(step.parts) <= 1:
            return
        f = self.fonts
        lay.addWidget(label(tr("Quitar solo una:"), f.small, theme.MUTED))
        grid = QGridLayout()
        grid.setContentsMargins(f.px(18), 0, 0, 0)
        grid.setHorizontalSpacing(f.px(6))
        grid.setVerticalSpacing(0)
        for i, (key, text) in enumerate(step.parts):
            grid.addWidget(_link(f"✕ {text}", lambda _=False, k=key, t=text: self._skip(k, t),
                                 tr("No quiero esto: rehago la ruta sin ello")), i // 3, i % 3, Qt.AlignLeft)
        lay.addLayout(grid)

    def _toggle(self, key):
        self._open.symmetric_difference_update({key})
        QTimer.singleShot(0, self.refresh)

    # --- the official profile: pictures and the tooltip's stats ---
    def _profile(self, name):
        try:
            server = self.ctl._server_of(name)
            return self.ctl.armory.cached(name, server) if server else None
        except Exception:                        # noqa: BLE001 - no profile: the cards go without pictures
            return None

    @staticmethod
    def _entry(profile, pos, name):
        """The official profile's entry of the piece at pos, when it is still that piece."""
        if profile is None:
            return None
        entry = next((e for e in profile.items.values() if e.get("slotPos") == pos), None)
        return entry if entry and entry.get("name") == name else None

    def _detail(self, profile, entry, enchant):
        """The official tooltip of that piece (stats, Theostone) when it was asked for at this +N, else None."""
        if entry is None:
            return None
        detail = self.ctl.armory.item(profile, entry.get("slotPosName") or "")
        if detail and detail.get("name") == entry.get("name") and (detail.get("enchantLevel") or 0) == enchant:
            return detail
        return None

    def _icon(self, entry, size):
        if not entry or not entry.get("icon"):
            return None
        path = self.ctl.armory.icon(entry.get("icon"))
        if path is None:
            self._icons_missing = True
            return None
        dpr = self.devicePixelRatioF() or 1.0
        pix = QPixmap(str(path))
        if pix.isNull():
            return None
        pix = pix.scaled(QSize(size, size) * dpr, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pix.setDevicePixelRatio(dpr)
        return pix

    def _ask_details(self, profile, entries):
        for entry in entries:
            self.ctl.armory.want_item(profile, entry.get("slotPosName") or "")
        QTimer.singleShot(0, self.refresh)

    # --- Tu equipo ---
    def _pieces(self, plan):
        """Tu equipo: each piece as the game's tooltip, in three columns like the equipment screen."""
        f = self.fonts
        profile = self._profile(plan.character)
        reviews = {r.piece.pos: r for r in plan.substats}
        by_pos = {p.pos: p for p in plan.pieces}
        missing, loading = [], False
        for p in plan.pieces:
            if not p.item:
                continue
            shown = p.enchant - (max(0, p.enchant - p.top) if p.top else 0)
            entry = self._entry(profile, p.pos, p.item.name)
            if entry and (entry.get("enchantLevel") or 0) == shown and self._detail(profile, entry, shown) is None:
                missing.append(entry)
                loading = loading or self.ctl.armory.item_loading(profile, entry.get("slotPosName") or "")
        top = QHBoxLayout()
        top.setSpacing(f.px(10))
        top.addWidget(_wrap(tr("Cada pieza como su ficha del juego. La barra: el +N hasta su máximo (la marca: hasta "
                               "donde nunca falla); los rombos, sus niveles de Extend. ✓ las líneas que la guía pide en "
                               "esa pieza, ✗ las que conviene cambiar (Líneas te dice cómo)."), f.small, theme.MUTED), 1)
        if missing:
            ask = QPushButton(tr("Pidiendo al perfil oficial…") if loading else
                              tr("Traer stats del perfil oficial ({0})", len(missing)))
            ask.setProperty("ghost", True)
            ask.setEnabled(not loading)
            ask.setToolTip(tr("Tus stats base, el bonus del Extend y el nombre de tu Theostone salen de tu perfil "
                              "oficial (una petición por pieza, una por segundo)."))
            ask.clicked.connect(lambda _=False, e=list(missing): self._ask_details(profile, e))
            top.addWidget(ask, 0, Qt.AlignTop)
        self.body.addLayout(top)
        cols = QHBoxLayout()
        cols.setSpacing(f.px(10))
        for column in COLUMNS:
            col = QVBoxLayout()
            col.setSpacing(f.px(10))
            for pos in column:
                if pos == "arcana":
                    widget = self._arcana_card(plan, profile)
                else:
                    piece = by_pos.get(pos)
                    widget = (self._item_card(plan, piece, reviews.get(pos), profile) if piece is not None and
                              piece.item else self._empty_card(pos))
                if widget is not None:
                    col.addWidget(widget)
            col.addStretch(1)
            cols.addLayout(col, 1)
        self.body.addLayout(cols)

    def _empty_card(self, pos):
        f = self.fonts
        tile = QFrame()
        tile.setObjectName("Tile")
        lay = QHBoxLayout(tile)
        lay.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
        lay.addWidget(label(SLOTS.get(pos, ("", tr("Hueco")))[1].upper(), f.section, theme.MUTED), 1)
        lay.addWidget(label(tr("vacío"), f.small, theme.WARN))
        return tile

    def item_data(self, plan, piece, review=None, profile=None):
        """The ItemData of one of your pieces: what the traffic read (its +N, Extend, soul binding, sockets) with
        the official tooltip's stats and Theostone when they are on disk."""
        s = piece.soul
        top = piece.top
        exceed = max(0, piece.enchant - top) if top else 0
        shown = piece.enchant - exceed
        entry = self._entry(profile, piece.pos, piece.item.name)
        data = ItemData(name=piece.item.name, grade=piece.item.grade, kind=piece.item.category, slot=piece.slot,
                        base_level=piece.base, bonus_level=piece.level - piece.base, enchant=shown, enchant_top=top,
                        enchant_safe=piece.safe, exceed=exceed, icon=self._icon(entry, self.fonts.px(54)))
        official = self._detail(profile, entry, shown)
        off = item_from_official(official) if official else None
        if off is not None:
            data.stats, data.bonus = off.stats, off.bonus
        wanted = {stat for tier in SUBSTATS.get(piece.key, ()) for stat in tier}
        if s and (s.lines or s.skills):
            data.soul_rate, data.soul_locked = s.rate, s.locked
            for n, v, lo, hi, p in s.lines:
                mark = "bad" if n in s.bad else "good" if n in wanted else ""
                data.soul_lines.append(SoulLine(STAT_ES.get(n, n) if n else "?", stat_value(n, v), p, mark))
            for _, name, level, want in s.skill_lines:
                data.soul_lines.append(SoulLine(f"{name} +{level}", "", None,
                                                "good" if want else "bad" if name in s.bad else "", "skill"))
            hidden = s.skills - len(s.skill_lines)
            if hidden > 0:
                data.soul_lines.append(SoulLine(tr("{0} efecto(s) de skill", hidden), "", None,
                                                "good" if "skill" in wanted else ""))
            if s.locked:
                data.fuse = tr("Soul Fuse · Philosopher's Stone: al 100% admite una línea más")
        elif piece.key in SUBSTATS:
            data.soul_none = tr("Sin Soul Binding")
        names = self.ctl.upgrades_db.tracked_names()
        for sid, stat, grade in (s.sockets if s else []):
            if not sid:
                data.sockets.append(None)
                continue
            stone = names.get(sid, "").replace(" (Bound)", "")
            data.sockets.append(Socket(SOCKET_STAT.get(stat) or stone or tr("Piedra"), STAT_GRADE.get(grade, ""),
                                       stone))
        if s and s.theostone:
            data.theostone = off.theostone if off is not None and off.theostone else ("", "")
        elif piece.key in ("weapon", "guard"):
            data.theostone_missing = True
        opts = plan.options.get(piece.pos) or []
        tips = []
        if opts:
            o = opts[0]
            data.notes.append((f"→ {OPTION[o.kind]}: {o.item.name} (+{o.gain})", theme.GOLD))
            tips = [f"{OPTION[x.kind]}: {x.item.name} (+{x.gain}) — {x.text}" for x in opts[:5]]
        elif piece.pos in plan.replace:
            data.notes.append((tr("→ a cambiar"), theme.GOLD))
        data.tooltip = "\n".join(tips)
        return data

    def _item_card(self, plan, piece, review, profile):
        return ItemCard(self.fonts, self.item_data(plan, piece, review, profile))

    def _arcana_card(self, plan, profile):
        """The five Arcana cards you wear, with their pictures, and the way to Arcanas."""
        worn = plan.arcana_plan.worn if plan.arcana_plan is not None else None
        if not worn:
            return None
        f = self.fonts
        box = QFrame()
        box.setObjectName("Card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(f.px(12), f.px(10), f.px(12), f.px(10))
        lay.setSpacing(f.px(6))
        lay.addWidget(label("ARCANA", f.section, theme.MUTED))
        for kind in sorted(worn, key=lambda k: ARCANA_ORDER.index(k) if k in ARCANA_ORDER else 9):
            card = worn[kind]
            row = QHBoxLayout()
            row.setSpacing(f.px(8))
            entry = self._entry(profile, ARCANA_POS.get(kind), card.name)
            color = theme.grade_color(card.grade)
            row.addWidget(RewardTile(f, self._icon(entry, f.px(34)), f"+{card.enchant}" if card.enchant else "",
                                     size=f.px(34), text=short_kind(kind)[:3], color=color))
            text = QVBoxLayout()
            text.setSpacing(0)
            text.addWidget(label(card.name, f.name, color, elide=True))
            text.addWidget(label(f"{short_kind(kind)} · +{card.enchant}", f.small, theme.MUTED))
            row.addLayout(text, 1)
            lay.addLayout(row)
        lay.addWidget(_link(tr("Abrir Arcanas"), self.ctl.open_arcana), 0, Qt.AlignLeft)
        return box

    # --- Líneas ---
    def _line_state(self, plan, review):
        """What a piece of «Líneas» needs: "nosoul" | "replace" | "change" | "donor" | "cheap" | "ok"."""
        s = review.piece.soul
        if review.piece.pos in plan.replace:
            return "replace"
        if not s or not (s.lines or s.skills):
            return "nosoul"
        if s.bad:
            return "change"
        if review.donors:
            return "donor"
        if review.cheap:
            return "cheap"
        return "ok"

    def _lines(self, plan):
        """Per piece: the lines it has, the ones the guide asks for in order and what to do; the transfer trick
        folded at the top; the pieces with nothing to change in a line each."""
        f = self.fonts
        stones = plan.have("transfer_stone")
        if self._fold("transfer", tr("El truco del transfer"), theme.GOLD):
            box = QFrame()
            box.setObjectName("Card")
            lay = QVBoxLayout(box)
            lay.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
            lay.setSpacing(f.px(3))
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
                lay.addWidget(_wrap(text, f.small, theme.TEXT))
            self.body.addWidget(box)
        if not plan.substats:
            self._hint(tr("Aún no leí tu equipo puesto: entra al juego con el personaje."))
            return
        targets = self.ctl.skill_targets(plan.character)
        legend = tr("✓ la guía la pide en esa pieza · ✗ a cambiar · • vale, pero no es de las que pide · gris: le "
                    "falta · 1 › 2 › 3: su prioridad. Tienes {0} Transfer Stones.", stones)
        if not targets:
            legend += " " + tr("Sin skills objetivo, toda línea de skill cuenta como buena (elígelas en Arcanas).")
        self._hint(legend)
        states = {id(r): self._line_state(plan, r) for r in plan.substats}
        counts = {"all": len(plan.substats), "change": sum(1 for v in states.values() if v == "change"),
                  "nosoul": sum(1 for v in states.values() if v == "nosoul"),
                  "donor": sum(1 for r in plan.substats if r.donors)}
        for key, text in (("all", tr("Todas")), ("change", tr("Con algo que cambiar")),
                          ("nosoul", tr("Sin Soul Binding")), ("donor", tr("Con donante"))):
            self.lines_filter.set_label(key, f"{text} ({counts[key]})")
        which = self.lines_filter.selected() or "all"
        query = self.lines_search.text().strip().lower()
        shown = 0
        for review in plan.substats:
            state = states[id(review)]
            piece = review.piece
            if which == "change" and state != "change" or which == "nosoul" and state != "nosoul" or \
                    which == "donor" and not review.donors:
                continue
            if query and query not in f"{piece.slot} {piece.item.name}".lower():
                continue
            shown += 1
            if state == "ok":
                self.body.addWidget(self._line_ok(review))
            else:
                self.body.addWidget(self._review(plan, review, state))
        if not shown:
            self._hint(tr("Ninguna pieza con este filtro."))

    def _line_ok(self, review):
        """A piece with nothing to change, in one line."""
        f = self.fonts
        piece = review.piece
        row = QFrame()
        row.setObjectName("Pill")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(f.px(12), f.px(5), f.px(12), f.px(5))
        lay.setSpacing(f.px(8))
        lay.addWidget(label("✓", f.small, theme.GAIN))
        lay.addWidget(label(piece.slot, f.small, theme.MUTED))
        lay.addWidget(label(f"{piece.item.name} +{piece.enchant}", f.body, theme.grade_color(piece.item.grade)))
        s = piece.soul
        lay.addWidget(label("· " + ("Soul Binding 100%" if s.locked else f"Soul Binding {s.rate:.0%}") + " · "
                            + tr("todo bien"), f.small, theme.MUTED), 1)
        return row

    def _review(self, plan, review, state):
        """One piece of «Líneas»: three columns - what it has, what the guide asks for, what to do."""
        f = self.fonts
        piece = review.piece
        s = piece.soul
        card = QFrame()
        card.setObjectName("Row")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(10))
        lay.setSpacing(f.px(6))
        head = QHBoxLayout()
        head.setSpacing(f.px(8))
        head.addWidget(label(piece.slot.upper(), f.section, theme.MUTED))
        head.addWidget(label(f"{piece.item.name} +{piece.enchant}", f.card_title, theme.grade_color(piece.item.grade)))
        if s and (s.lines or s.skills):
            head.addWidget(Chip("Soul Binding 100%" if s.locked else f"Soul Binding {s.rate:.0%}", theme.ACCENT, f))
        head.addStretch(1)
        chip = {"nosoul": (tr("sin Soul Binding"), theme.MUTED), "replace": (tr("la vas a cambiar"), theme.GOLD),
                "change": (tr("{0} a cambiar", len(s.bad) if s else 0), theme.COMBAT),
                "donor": (tr("tienes donante"), theme.GOLD), "cheap": (tr("más barato con transfer"), theme.GOLD)}
        text, color = chip.get(state, ("", theme.MUTED))
        if text:
            head.addWidget(Chip(text, color, f))
        lay.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(f.px(18))
        grid.setVerticalSpacing(f.px(4))
        for c, title in enumerate((tr("Tiene"), tr("La guía pide"), tr("Qué hacer"))):
            grid.addWidget(label(title.upper(), f.section, theme.MUTED), 0, c)
        grid.addLayout(self._has_col(piece), 1, 0, Qt.AlignTop)
        grid.addLayout(self._guide_col(review), 1, 1, Qt.AlignTop)
        grid.addLayout(self._todo_col(plan, review, state), 1, 2, Qt.AlignTop)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 4)
        lay.addLayout(grid)
        return card

    def _mark_row(self, mark, text, tail="", color=None):
        f = self.fonts
        row = QHBoxLayout()
        row.setSpacing(f.px(6))
        sign, sign_color = {"good": ("✓", theme.GAIN), "bad": ("✗", theme.COMBAT)}.get(mark, ("•", theme.MUTED))
        tick = label(sign, f.small, sign_color)
        tick.setFixedWidth(f.px(12))
        row.addWidget(tick)
        row.addWidget(label(text, f.body, color or (theme.COMBAT if mark == "bad" else theme.TEXT), elide=True), 1)
        if tail:
            row.addWidget(label(tail, f.small, theme.MUTED))
        return row

    def _has_col(self, piece):
        f = self.fonts
        col = QVBoxLayout()
        col.setSpacing(f.px(2))
        s = piece.soul
        wanted = {stat for tier in SUBSTATS.get(piece.key, ()) for stat in tier}
        if not s or not (s.lines or s.skills):
            col.addWidget(label(tr("Ninguna línea aún"), f.small, theme.MUTED))
            return col
        for n, v, lo, hi, p in s.lines:
            mark = "bad" if n in s.bad else "good" if n in wanted else ""
            col.addLayout(self._mark_row(mark, STAT_ES.get(n, n) if n else "?", f"{p:.0%}" if p is not None else
                                         stat_value(n, v)))
        for _, name, level, want in s.skill_lines:
            col.addLayout(self._mark_row("good" if want else "bad" if name in s.bad else "", f"{name} +{level}",
                                         "skill"))
        hidden = s.skills - len(s.skill_lines)
        if hidden > 0:
            col.addLayout(self._mark_row("good" if "skill" in wanted else "", tr("{0} efecto(s) de skill", hidden)))
        return col

    def _guide_col(self, review):
        f = self.fonts
        col = QVBoxLayout()
        col.setSpacing(f.px(2))
        for i, tier in enumerate(review.tiers):
            row = QHBoxLayout()
            row.setSpacing(f.px(6))
            num = label(str(i + 1), f.small, theme.GOLD)
            num.setFixedWidth(f.px(12))
            row.addWidget(num)
            parts = []
            for stat, has, pct in tier:
                name = STAT_ES.get(stat, stat)
                if has:
                    parts.append(f"<span style='color:{theme.GAIN.name()}'>✓ {name}"
                                 + (f" {pct:.0%}" if pct is not None else "") + "</span>")
                else:
                    parts.append(f"<span style='color:{theme.MUTED.name()}'>{name}</span>")
            text = QLabel(f" <span style='color:{theme.MUTED.name()}'>=</span> ".join(parts))
            text.setFont(f.body)
            text.setWordWrap(True)
            text.setStyleSheet("background: transparent;")
            text.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.addWidget(text, 1)
            col.addLayout(row)
        if review.note and review.note != tr("skill: niveles de habilidades pasivas"):
            col.addWidget(_wrap(review.note, f.small, theme.MUTED))
        return col

    def _action(self, col, kind, text):
        f = self.fonts
        tag, color = ACTION[kind]
        row = QHBoxLayout()
        row.setSpacing(f.px(6))
        row.addWidget(Chip(tag, color, f), 0, Qt.AlignTop)
        row.addWidget(_wrap(text, f.small, theme.TEXT), 1)
        col.addLayout(row)

    def _todo_col(self, plan, review, state):
        """What to do with a piece's lines: the route's own soul binding step (Sync / Reset / Soul Fuse / Bind, with
        what it costs), a transfer from a donor you own, or the cheaper way through a low piece."""
        f = self.fonts
        col = QVBoxLayout()
        col.setSpacing(f.px(4))
        piece = review.piece
        s = piece.soul
        if state == "replace":
            self._action(col, "replace", tr("Vas a cambiar esta pieza: no gastes en sus líneas."))
            return col
        step = next((x for x in plan.steps if x.kind == "soul" and x.pos == piece.pos), None)
        if step is not None:
            kind = ("fuse" if "philosopher" in step.uses else "reset" if "soul_reset" in step.uses else
                    "sync" if "sync" in step.uses else "bind")
            self._action(col, kind, step.summary or step.title)
        elif state == "nosoul":
            self._action(col, "bind", tr("Aún sin Soul Binding: empieza con Bind (Soul Codex)."))
        elif s and s.bad:
            self._action(col, "sync", tr("Cambia {0} con Sync (una pieza del mismo tipo y grado o una Sync Stone).",
                                         ", ".join(dict.fromkeys(s.bad))))
        for donor, _, found, cost, safe in review.donors[:2]:
            plural = "s" if review.stones != 1 else ""
            text = (tr("Donante: tu {0} +{1} ({2}) tiene ", donor.item.name, donor.enchant, donor.place)
                    + ", ".join(STAT_ES.get(x, x) for x in found)
                    + tr(" → transfer con {0} Transfer Stone{1}. El transfer pasa también su +{2}", review.stones,
                         plural, donor.enchant))
            if donor.enchant < piece.enchant:
                text += (tr(": súbela antes a +{0}", piece.enchant)
                         + (tr(" ({0} y {1:,} piedras", kinah_text(cost[0]), cost[1])
                            + ("" if safe else tr(", pasado +10 puede fallar")) + ")" if cost else "")
                         + tr(" o tu {0} quedará a +{1}.", piece.item.name, donor.enchant))
            else:
                text += "."
            self._action(col, "transfer", text)
        if review.cheap:
            low, theirs, mine = review.cheap
            saved_k, saved_s = mine[0] - theirs[0], mine[1] - theirs[1]
            if saved_k > 0:
                self._action(col, "cheap", tr("Haz el +10 y las líneas en una {0} (nivel {1}) y pásalo con transfer: "
                                              "te ahorras {2} y {3:,} piedras por {4} Transfer Stone{5}.", low.name,
                                              low.level, kinah_text(saved_k), saved_s, review.stones,
                                              's' if review.stones != 1 else ''))
        if not col.count():
            self._action(col, "ok", tr("Nada: sus líneas están bien."))
        return col

    # --- Contenido ---
    def _content_groups(self, groups):
        """The chips of the activity kinds, made again only when the kinds change."""
        key = tuple(groups)
        if key == self._groups_key and self.content_group is not None:
            return
        current = self.content_group.selected() if self.content_group is not None else "all"
        if self.content_group is not None:
            self.content_group.hide()
            self.content_group.setParent(None)
            self.content_group.deleteLater()
        self._groups_key = key
        self.content_group = _FlowChips(self.fonts, [("all", tr("Todo"))] + [(g, g) for g in groups],
                                        current if current in groups else "all")
        self.content_group.changed.connect(self._filters_changed)
        self.groups_box.addWidget(self.content_group)

    def _content(self, plan):
        """Every activity: what it asks for, whether you can do it (or why not) and what it gives you; filtered by
        kind, by whether you can do it, by what it gives you and by a search."""
        f = self.fonts
        rows = plan.content
        me = plan.me
        groups = list(dict.fromkeys(a.group for a in rows))
        self._content_groups(groups)
        group = self.content_group.selected() or "all"
        state = self.content_state.selected() or "all"
        useful = "useful" in self.content_useful.selected()
        query = self.content_search.text().strip().lower()

        def state_ok(a, st):
            return st == "all" or (st == "can") == (not a.reason and not a.closed)

        def base_ok(a):
            return (not useful or a.useful) and (not query or query in " ".join(
                (a.name, a.mode, a.gives, a.group, a.needs or "")).lower())

        base = [a for a in rows if base_ok(a)]
        for g in ["all"] + groups:
            n = sum(1 for a in base if (g == "all" or a.group == g) and state_ok(a, state))
            self.content_group.set_label(g, f"{tr('Todo') if g == 'all' else g} ({n})")
        for st, text in (("all", tr("Todo")), ("can", tr("Puedes")), ("cannot", tr("Aún no"))):
            n = sum(1 for a in base if (group == "all" or a.group == group) and state_ok(a, st))
            self.content_state.set_label(st, f"{text} ({n})")
        can = sum(1 for a in rows if not a.reason and not a.closed)
        self._hint(tr("{0} actividades: puedes {1}", len(rows), can)
                   + (tr(" con {0:,} de nivel de objeto", me.item_level) if me and me.item_level else "")
                   + tr(". «Te da»: lo que mejora lo que llevas puesto."))
        shown = 0
        for g in groups:
            if group != "all" and g != group:
                continue
            mine = [a for a in base if a.group == g]
            live = [a for a in mine if not a.closed and state_ok(a, state)]
            closed = [a for a in mine if a.closed] if state != "can" else []
            if not live and not closed:
                continue
            shown += len(live) + len(closed)
            ok = sum(1 for a in mine if not a.closed and not a.reason)
            total = sum(1 for a in mine if not a.closed)
            head = QHBoxLayout()
            head.setContentsMargins(f.px(2), f.px(8), 0, 0)
            head.setSpacing(f.px(8))
            head.addWidget(label(g, f.card_title, theme.TEXT))
            head.addWidget(label(tr("puedes {0} de {1}", ok, total) if total else tr("aún no abre"), f.small,
                                 theme.GAIN if ok else theme.MUTED))
            head.addStretch(1)
            self.body.addLayout(head)
            card = QFrame()
            card.setObjectName("Card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(f.px(10), f.px(6), f.px(10), f.px(6))
            lay.setSpacing(f.px(2))
            for a in live:
                lay.addWidget(self._activity(a))
            if closed:
                by_mode = {}
                for a in closed:
                    by_mode.setdefault(a.mode, []).append(a.name)
                more = _wrap(tr("Aún no abren en Global ({0}): ", len(closed)) + "; ".join(
                    (f"{mode}: " if mode else "") + ", ".join(dict.fromkeys(names)) for mode, names in by_mode.items()),
                    f.small, theme.MUTED)
                more.setContentsMargins(f.px(4), f.px(4), 0, f.px(2))
                lay.addWidget(more)
            self.body.addWidget(card)
        if not shown:
            self._hint(tr("Ninguna actividad con estos filtros."))

    def _activity(self, a):
        f = self.fonts
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(f.px(2), f.px(3), f.px(2), f.px(3))
        lay.setSpacing(f.px(10))
        color = theme.GAIN if not a.reason else theme.WARN
        dot = label("●", f.small, color)
        dot.setFixedWidth(f.px(12))
        lay.addWidget(dot, 0, Qt.AlignTop)
        name = QVBoxLayout()
        name.setContentsMargins(0, 0, 0, 0)
        name.setSpacing(0)
        name.addWidget(label(a.name + (f" · {a.mode}" if a.mode else ""), f.name, theme.TEXT, elide=True))
        if a.needs:
            name.addWidget(label(a.needs, f.small, theme.MUTED, elide=True))
        box = QWidget()
        box.setLayout(name)
        box.setFixedWidth(f.px(270))
        lay.addWidget(box, 0, Qt.AlignTop)
        status = _wrap(tr("Puedes") if not a.reason else a.reason, f.small, color)
        status.setFixedWidth(f.px(170))
        lay.addWidget(status, 0, Qt.AlignTop)
        lay.addWidget(_wrap(a.gives, f.small, theme.TEXT if a.useful else theme.MUTED), 1, Qt.AlignTop)
        for boost in a.boosts:
            text, c, tip = BOOST[boost]
            lay.addWidget(Chip(text, c, f, tip=tip), 0, Qt.AlignTop)
        return row

    # --- Sobrantes ---
    def _food(self, plan):
        """The pieces in your bag and storages you do not wear, grouped by what each one is for."""
        f = self.fonts
        self.body.addWidget(_wrap(tr("Piezas que no usas y para qué sirve cada una (Sync, Transfer Stone, Sync "
                                     "Stone)."), f.body, theme.TEXT))
        if not plan.spare:
            self._hint(tr("No tienes piezas sobrantes en la bolsa ni en los almacenes."))
            return
        self._hint(tr("Están en tu bolsa o tus almacenes y no las llevas puestas. Una del mismo tipo y grado que una "
                      "tuya sirve para su Sync (vuelve a tirar una línea); cualquier Unique se convierte en 1 Sync "
                      "Stone o 1 Transfer Stone (Substance Morph). Primero las +0 y de menos nivel."))
        groups = {}
        for x in plan.spare:
            groups.setdefault(x.use or (tr("Guárdala") if x.item.grade != "Unique" else tr("Transfer Stone o Sync Stone")),
                              []).append(x)
        mine = tr("Sync de tu {0}", "")
        stone = tr("Sync Stone para tu {0}", "")
        order = sorted(groups, key=lambda u: (not u.startswith(mine), not u.startswith(stone), u))
        for use in order:
            if use.startswith(mine):
                why, color = tr("Se gasta en el Sync de esa pieza tuya: es del mismo tipo y grado."), theme.GOLD
            elif use.startswith(stone):
                why, color = tr("Conviértela en 1 Sync Stone (Substance Morph) para el Sync de esa pieza."), theme.GOLD
            elif use == tr("Guárdala"):
                why, color = tr("No es Unique: no se convierte en piedras; sirve para el Sync de una pieza de su tipo y "
                                "grado."), theme.TEXT
            else:
                why, color = tr("Cualquier Unique se convierte en 1 Transfer Stone o 1 Sync Stone (Substance Morph)."), \
                    theme.TEXT
            card = QFrame()
            card.setObjectName("Card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(10))
            lay.setSpacing(f.px(4))
            head = QHBoxLayout()
            head.setSpacing(f.px(8))
            head.addWidget(label(use, f.card_title, color))
            head.addWidget(Chip(str(len(groups[use])), theme.MUTED, f))
            head.addStretch(1)
            lay.addLayout(head)
            lay.addWidget(_wrap(why, f.small, theme.MUTED))
            grid = QGridLayout()
            grid.setHorizontalSpacing(f.px(14))
            grid.setVerticalSpacing(f.px(2))
            for i, x in enumerate(groups[use]):
                cell = QWidget()
                row = QHBoxLayout(cell)
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(f.px(6))
                row.addWidget(label(f"{x.item.name} +{x.enchant}", f.body, theme.grade_color(x.item.grade), elide=True),
                              1)
                row.addWidget(label(x.place, f.small, theme.MUTED))
                cell.setToolTip(tr("nivel {0}", x.level) + (tr(" · su Soul Binding {0:.0%}", x.rate) if x.rate else ""))
                grid.addWidget(cell, i // 3, i % 3)
            for c in range(3):
                grid.setColumnStretch(c, 1)
            lay.addLayout(grid)
            self.body.addWidget(card)

    # --- Preferencias ---
    def _prefs(self, plan):
        f = self.fonts
        self.body.addWidget(SectionTitle(tr("Main o alt"), f))
        self._hint(tr("{0} es {1}: se cambia arriba (Main / Alt).", plan.character,
                      'alt' if plan.me and plan.me.alt else 'main'))
        self.body.addWidget(SectionTitle(tr("Skills objetivo de {0}", plan.character), f))
        names = {int(k): v.get("name") or k for k, v in self.ctl.tuner.skills.items()}
        row = QHBoxLayout()
        row.addWidget(_wrap(", ".join(f"{names.get(k, k)} {v}" for k, v in sorted(plan.targets.items(), key=lambda x: -x[1]))
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
        boxes.setHorizontalSpacing(f.px(16))
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
            reset.setProperty("ghost", True)
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
        grid.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
        grid.setHorizontalSpacing(f.px(12))
        grid.setVerticalSpacing(f.px(3))
        for r, (key, title) in enumerate(sorted(skipped.items(), key=lambda x: x[1])):
            grid.addWidget(label(title, f.body, theme.TEXT), r, 0)
            grid.addWidget(_link(tr("Volver a tenerlo en cuenta"), lambda _=False, k=key: self._unskip(k)), r, 1)
        grid.setColumnStretch(0, 1)
        self.body.addWidget(card)
        if len(skipped) > 1:
            every = QPushButton(tr("Recuperar todos"))
            every.setProperty("ghost", True)
            every.clicked.connect(lambda: self._unskip(None))
            self.body.addWidget(every, 0, Qt.AlignLeft)

    def _skip(self, key, title):
        self.ctl.upgrades_skip(key, title, self._shown)
        QTimer.singleShot(0, self.refresh)

    def _unskip(self, key=None):
        self.ctl.upgrades_unskip(key, self._shown)
        QTimer.singleShot(0, self.refresh)

    def _toggle_dungeon(self, dungeon, on):
        plan = self.ctl.upgrade_plan(self._shown)
        current = list(plan.dungeons) if plan is not None else []
        if on and dungeon not in current:
            current.append(dungeon)
        elif not on and dungeon in current:
            current.remove(dungeon)
        self._set_dungeons(current)

    def _set_dungeons(self, dungeons):
        self.ctl.set_upgrades_dungeons(dungeons, self._shown)
        QTimer.singleShot(0, self.refresh)
