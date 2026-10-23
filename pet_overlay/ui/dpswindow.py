"""Combat details: the whole contribution of every player in an encounter (damage, healing, shields, damage taken,
avoided hits, stagger) with filters, search and sortable columns, plus the breakdown of the selected player; the
enemies one by one (who hit each); the fights of the last 48 hours (★ keeps one) and a report image with its MVP.
Names only for you and your party/force; everyone else is "Jugador N" (also in the saved fights)."""
import time
import webbrowser
from dataclasses import dataclass, field

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView,
                               QLineEdit, QPushButton, QSplitter, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import APP_NAME
from ..combat import aggro_counts, in_instance
from ..sessions import history_entries
from . import theme
from .dps_panel import mmss, short_number
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import label
from ..i18n import tr

BAR_ROLE = Qt.UserRole + 1          # 0-1: length of the bar behind the first column
COLOR_ROLE = Qt.UserRole + 2        # QColor of that bar (the player's class)
ICON_ROLE = Qt.UserRole + 3         # QPixmap shown before the text
KEY_ROLE = Qt.UserRole + 4          # what the row stands for (actor eid, skill id...)

ROW_BG, ROW_ALT, ROW_HOVER, ROW_SELECTED = QColor("#0d1628"), QColor("#0f192e"), QColor("#16243f"), QColor("#1b3156")
CLASS_COLOR = {"Gladiador": "#e8a35c", "Templario": "#d9c27a", "Asesino": "#c77dff", "Cazador": "#7bd88f",
               "Hechicero": "#6cb4ff", "Invocador": "#9d8cff", "Clérigo": "#ffe08a", "Cantor": "#ff9e6b"}
TABLE_STYLE = f"""
QTableWidget {{ background: {ROW_BG.name()}; color: {theme.TEXT.name()}; border: 1px solid #1c2944;
    border-radius: 10px; gridline-color: transparent; outline: 0; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: #0f1830; color: {theme.MUTED.name()}; border: none;
    border-bottom: 1px solid #1c2944; padding: 7px 8px; font-weight: 600; }}
QHeaderView::section:first {{ border-top-left-radius: 10px; }}
QHeaderView::section:last {{ border-top-right-radius: 10px; }}
QLineEdit {{ padding: 6px 10px; }}
QFrame#Card {{ background: #0f1830; border: 1px solid #1c2944; border-radius: 10px; }}
QSplitter::handle {{ background: transparent; width: 10px; }}
"""
GROUPS = [("all", tr("Todos")), ("group", tr("Tú y tu grupo")), ("others", tr("Fuera de tu grupo"))]
ENEMY_NOTE = (tr("«Enemigo» a secas: un monstruo que ya estaba a la vista cuando AionDex empezó a escuchar (el "
                 "juego solo dice qué es cuando aparece). «Enemigo (40K HP)»: una variante que la base de datos no "
                 "tiene con nombre."))


def _empty(layout):
    """Remove a layout's widgets now (deleteLater alone leaves them painted until the next event loop pass)."""
    while layout.count():
        widget = layout.takeAt(0).widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


def kind_is_skill(text):
    return text.startswith(("Efecto #", "Poción #"))


def _pct(ratio):
    value = 100 * ratio
    return f"{value:.2f}%" if 0 < value < 1 else f"{value:.1f}%"


def _rate(actor, value, enc, total):
    seconds = getattr(enc, "rate_time", None) or enc.duration    # the boss's time with "Solo al jefe"
    return short_number(value / seconds), value / seconds


def _share(actor, value, enc, total):
    return _pct(value / total), value / total


@dataclass
class Metric:
    """One kind of contribution: how to read it from an ActorStats and how to show it."""
    key: str
    tab: str
    card: str                       # title of its summary card
    attr: str                       # ActorStats field with the amount
    columns: list                   # [(header, fn(actor, value, enc, total) -> (text, sort key))]
    breakdown: list                 # [(title, Counter field, hits Counter field or None, what the keys are)]
    rate: str = ""                  # per-second column header ("" = none)
    boss_filter: bool = False       # "solo al jefe" applies
    note: str = ""                  # shown under the table when this tab is open
    count: bool = False             # the amount is a number of events, not hit points

    def value(self, actor, boss=None):
        if self.key == "damage" and boss is not None:
            return actor.by_target[boss]
        return getattr(actor, self.attr, 0) or 0


def _hits(attr):
    return lambda a, v, enc, total: (str(getattr(a, attr, 0)), getattr(a, attr, 0))


def _ratio(num, den):
    def column(a, v, enc, total):
        n, d = getattr(a, num, 0), getattr(a, den, 0)
        return (f"{100 * n / d:.0f}%" if d else "—"), (n / d if d else -1)
    return column


def _amount(attr):
    return lambda a, v, enc, total: (short_number(getattr(a, attr, 0)) if getattr(a, attr, 0) else "—",
                                     getattr(a, attr, 0))


METRICS = [
    Metric("damage", tr("Daño"), tr("Daño"), "damage", [
        (tr("Daño"), lambda a, v, enc, t: (short_number(v), v)),
        ("DPS", _rate),
        ("%", _share),
        (tr("Golpes"), _hits("hits")),
        (tr("Críticos"), _ratio("crits", "hits")),
        (tr("Espalda"), _ratio("backs", "hits")),
        (tr("Mayor golpe"), _amount("max_hit")),
    ], [(tr("Habilidades"), "skills", "skill_hits", "skill"), (tr("Objetivos"), "by_target", None, "npc")],
        rate="DPS", boss_filter=True),
    Metric("healing", tr("Curación"), tr("Curación"), "healing", [
        (tr("Curación"), lambda a, v, enc, t: (short_number(v), v)),
        ("HPS", _rate),
        ("%", _share),
        (tr("Curas"), _hits("heal_hits")),
        (tr("Sobrecuración"), _amount("overheal")),
        (tr("Revividos"), _hits("revives")),
    ], [(tr("Habilidades"), "heal_skills", "heal_skill_hits", "skill"), (tr("A quién"), "heal_to", None, "player")],
        rate="HPS"),
    Metric("shields", tr("Escudos"), tr("Escudos"), "shielding", [
        (tr("Absorbido"), lambda a, v, enc, t: (short_number(v), v)),
        (tr("Por segundo"), _rate),
        ("%", _share),
    ], [(tr("Habilidades"), "shield_skills", None, "skill"), (tr("A quién"), "shield_to", None, "player")],
        note=tr("Daño que absorbieron los escudos que lanzó cada jugador.")),
    Metric("taken", tr("Daño recibido"), tr("Recibido"), "taken", [
        (tr("Recibido"), lambda a, v, enc, t: (short_number(v), v)),
        (tr("Por segundo"), _rate),
        ("%", _share),
        (tr("Golpes"), _hits("taken_hits")),
        (tr("Mayor golpe"), _amount("taken_max")),
        (tr("Muertes"), _hits("deaths")),
        ("Aggro", lambda a, v, enc, t: (str(aggro_counts(enc).get(a.eid, 0)), aggro_counts(enc).get(a.eid, 0))),
        (tr("Absorbido"), _amount("absorbed")),
    ], [(tr("De quién"), "taken_from", None, "npc"), (tr("Habilidades enemigas"), "taken_skills", None, "enemy_skill")],
        note=tr("«Absorbido»: daño que los escudos le quitaron antes de llegar a su vida. «Aggro»: enemigos que le "
                "pegaron a este jugador más que a nadie (los que aguantó).")),
    Metric("avoided", tr("Evitado"), tr("Evitado"), "avoided_hits", [
        (tr("Evitados"), lambda a, v, enc, t: (str(v), v)),
        ("Parry", _hits("parries")),
        (tr("Bloqueo"), _hits("blocks")),
        (tr("Esquiva"), _hits("evades")),
        (tr("Aguante"), _hits("endures")),
        (tr("Daño evitado"), _amount("avoided")),
    ], [(tr("Ataques evitados"), "avoided_skills", None, "enemy_skill")], count=True,
        note=tr("Golpes enemigos que el jugador desvió (parry), bloqueó o amortiguó (la mitad del daño), esquivó o "
                "resistió (nada de daño) o aguantó (le quitó parte). El juego solo dice cuánto quitó el aguante.")),
    Metric("stagger", tr("Aturdimiento"), tr("Aturdimiento"), "stagger", [
        (tr("Aturdimiento"), lambda a, v, enc, t: (short_number(v), v)),
        (tr("Por segundo"), _rate),
        ("%", _share),
    ], [(tr("Habilidades"), "stagger_skills", None, "skill")], boss_filter=False,
        note=tr("Puntos que cada jugador quitó de la barra de aturdimiento (stagger) del jefe. El juego solo envía "
                "la barra, no quién la bajó: AionDex reparte cada bajada entre los golpes que llegaron en ese "
                "instante.")),
    Metric("upkeep", tr("Buffs y consumibles"), "", "upkeep_time", [
        (tr("Comida/bebida"), lambda a, v, enc, t: (_pct(a.food_uptime), a.food_uptime)),
        (tr("Pociones"), lambda a, v, enc, t: (str(a.potions), a.potions)),
        ("Buffs", lambda a, v, enc, t: (str(sum(1 for b in a.buff_list if b[5] == "class")),
                                        sum(1 for b in a.buff_list if b[5] == "class"))),
        (tr("Distancia"), lambda a, v, enc, t: (f"{a.distance:,.0f} m" if a.distance >= 0 else "—", a.distance)),
        (tr("Muertes"), _hits("deaths")),
        (tr("Tiempo muerto"), lambda a, v, enc, t: (mmss(a.dead_time) if a.dead_time > 0 else
                                                ("0:00" if a.dead_time == 0 else "—"), a.dead_time)),
    ], [("Buffs", "buff_list", None, "buff"), (tr("Consumibles"), "item_uses", None, "use")],
        note=tr("Lo que cada uno de tu grupo tuvo puesto y usó: «Comida/bebida», la parte de la pelea con comida, "
                "bebida o pergamino activos; «Buffs», los de clase que tuvo (suyos y de otros), y a la derecha "
                "cuánto de la pelea estuvo activo cada uno y quién lo puso. Distancia y tiempo muerto, en las "
                "peleas de jefe en mazmorra (las que tienen Simulación). El nombre de una comida o bebida lo "
                "aprende AionDex la primera vez que tú la usas; hasta entonces sale con su número.")),
]
METRIC_BY_KEY = {m.key: m for m in METRICS}


class SortItem(QTableWidgetItem):
    """Shows formatted text, sorts by a number."""

    def __init__(self, text, key=None, align=Qt.AlignRight | Qt.AlignVCenter):
        super().__init__(text)
        self.key = key if key is not None else text
        self.setTextAlignment(align)
        self.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)

    def __lt__(self, other):
        try:
            return self.key < other.key
        except TypeError:
            return str(self.key) < str(other.key)


class MeterTable(QTableWidget):
    """A table drawn like a damage meter: flat full-row highlight, a bar behind the first column."""

    def __init__(self, columns, fonts):
        super().__init__(0, len(columns))
        self.hover_row = -1
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(fonts.px(34))
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setFocusPolicy(Qt.NoFocus)
        self.setShowGrid(False)
        self.setSortingEnabled(True)
        self.setMouseTracking(True)
        self.setItemDelegate(RowDelegate(self))
        self.setIconSize(QSize(fonts.px(22), fonts.px(22)))
        self.set_columns(columns)

    def set_columns(self, columns):
        self.setColumnCount(len(columns))
        header = self.horizontalHeader()
        for col, text in enumerate(columns):
            item = QTableWidgetItem(text)
            item.setTextAlignment((Qt.AlignLeft if col == 0 else Qt.AlignRight) | Qt.AlignVCenter)
            self.setHorizontalHeaderItem(col, item)
            header.setSectionResizeMode(col, QHeaderView.Stretch if col == 0 else QHeaderView.ResizeToContents)
        header.setHighlightSections(False)
        header.setMinimumSectionSize(64)

    def mouseMoveEvent(self, event):
        row = self.rowAt(event.position().toPoint().y())
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hover_row = -1
        self.viewport().update()
        super().leaveEvent(event)


class RowDelegate(QStyledItemDelegate):
    def paint(self, p, option, index):
        table = self.parent()
        row = index.row()
        selected = table.selectionModel().isRowSelected(row, index.parent())
        r = option.rect
        p.save()
        p.fillRect(r, ROW_SELECTED if selected else ROW_HOVER if row == table.hover_row else
                   ROW_ALT if row % 2 else ROW_BG)
        x = r.left() + 10
        if index.column() == 0:
            if selected:
                p.fillRect(QRect(r.left(), r.top(), 3, r.height()), theme.ACCENT)
            bar = index.data(BAR_ROLE)
            if bar:
                color = QColor(index.data(COLOR_ROLE) or theme.ACCENT)
                color.setAlpha(64 if selected else 46)
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawRoundedRect(QRectF(r.left() + 4, r.top() + 5, max(4.0, (r.width() - 8) * bar), r.height() - 10),
                                  4, 4)
            pix = index.data(ICON_ROLE)
            if isinstance(pix, QPixmap) and not pix.isNull():
                side = r.height() - 10
                p.drawPixmap(QRect(x, r.top() + 5, side, side), pix)
                x += side + 8
            elif index.data(COLOR_ROLE) is not None:
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(index.data(COLOR_ROLE)))
                p.drawEllipse(QRectF(x, r.center().y() - 4, 8, 8))
                x += 16
        fg = index.data(Qt.ForegroundRole)
        p.setPen(fg.color() if hasattr(fg, "color") else fg if fg else theme.TEXT)
        p.setFont(index.data(Qt.FontRole) or option.font)
        align = index.data(Qt.TextAlignmentRole) or (Qt.AlignLeft | Qt.AlignVCenter)
        text_rect = QRect(x, r.top(), r.right() - 10 - x, r.height())
        text = p.fontMetrics().elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, text_rect.width())
        p.drawText(text_rect, int(align), text)
        p.restore()


class StatCard(QFrame):
    def __init__(self, title, value, sub, fonts, color=None):
        super().__init__()
        self.setObjectName("Card")
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(2)
        col.addWidget(label(title.upper(), fonts.section, theme.MUTED))
        col.addWidget(label(value, fonts.count_big, color or theme.TEXT))
        if sub:
            col.addWidget(label(sub, fonts.small, theme.MUTED, elide=True))


@dataclass
class Row:
    actor: object
    name: str
    mine: bool
    value: int
    color: QColor = field(default=None)


class DpsDetailsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.shown_metric = METRICS[0]
        self.group_filter = "all"
        self.selected = None             # actor eid whose breakdown is shown
        self.user_sorted = False         # the player clicked a column header (keep their order)
        self._target_set_for = None      # encounter whose "solo al jefe" default was applied
        self._icons = {}
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Detalles del combate", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + TABLE_STYLE)
        self.resize(1360, 860)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(34, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(tr("Detalles del combate"), f.name_big, theme.TEXT))
        self.summary = label("", f.small, theme.MUTED)
        self.summary.setWordWrap(True)
        titles.addWidget(self.summary)
        head.addLayout(titles, 1)
        self.encounter_box = QComboBox()
        self.encounter_box.setMinimumContentsLength(30)
        self.encounter_box.setMaxVisibleItems(20)
        self.encounter_box.currentIndexChanged.connect(lambda _: self._fill())
        head.addWidget(self.encounter_box)
        self.pin_button = QPushButton(tr("☆ Conservar"))
        self.pin_button.setCheckable(True)
        self.pin_button.setToolTip(tr("Las peleas se guardan 48 horas; las marcadas con ★ se quedan hasta que las "
                                      "quites"))
        self.pin_button.clicked.connect(self._toggle_pin)
        head.addWidget(self.pin_button)
        sessions = QPushButton(tr("Sesiones"))
        sessions.setToolTip(tr("Comenzar o terminar una sesión (varios runs con la misma party) y ver sus informes"))
        sessions.clicked.connect(self._sessions)
        head.addWidget(sessions)
        report = QPushButton(tr("Informe"))
        report.setProperty("primary", True)
        report.setToolTip(tr("Una imagen con la pelea, los números de tu grupo y su MVP, para guardar o pegar"))
        report.clicked.connect(self._report)
        head.addWidget(report)
        self.sim_button = QPushButton(tr("Simulación"))
        self.sim_button.clicked.connect(self._simulation)
        head.addWidget(self.sim_button)
        root.addLayout(head)

        self.cards = QHBoxLayout()
        self.cards.setSpacing(10)
        root.addLayout(self.cards)

        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.metric_buttons = {}
        for m in METRICS:
            button = QPushButton(m.tab)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=m.key: self._set_metric(k))
            tabs.addWidget(button)
            self.metric_buttons[m.key] = button
        self.enemy_view = False          # the Enemigos tab: the enemies one by one
        self.selected_enemy = None
        self.enemy_button = QPushButton(tr("Enemigos"))
        self.enemy_button.setProperty("tab", True)
        self.enemy_button.setCheckable(True)
        self.enemy_button.setToolTip(tr("Cada enemigo por separado: su vida, si murió, el daño que recibió y quién "
                                        "se lo hizo, y el daño que hizo"))
        self.enemy_button.clicked.connect(self._show_enemies)
        tabs.addWidget(self.enemy_button)
        tabs.addStretch(1)
        root.addLayout(tabs)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.target_box = QComboBox()
        self.target_box.addItem(tr("Todos los objetivos"), "all")
        self.target_box.addItem(tr("Solo al jefe"), "boss")
        self.target_box.currentIndexChanged.connect(lambda _: self._fill())
        filters.addWidget(self.target_box)
        self.group_buttons = {}
        for key, text in GROUPS:
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.setChecked(key == self.group_filter)
            button.clicked.connect(lambda _=False, k=key: self._set_group(k))
            filters.addWidget(button)
            self.group_buttons[key] = button
        self.class_box = QComboBox()
        self.class_box.currentIndexChanged.connect(lambda _: self._fill())
        filters.addWidget(self.class_box)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar jugador o clase…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._fill())
        filters.addWidget(self.search, 1)
        root.addLayout(filters)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.table = MeterTable([tr("Jugador")], f)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.horizontalHeader().sectionClicked.connect(lambda _: setattr(self, "user_sorted", True))
        split.addWidget(self.table)
        side = QWidget()
        side_col = QVBoxLayout(side)
        side_col.setContentsMargins(0, 0, 0, 0)
        side_col.setSpacing(6)
        side_head = QHBoxLayout()
        self.side_title = label("", f.name, theme.TEXT, elide=True)
        side_head.addWidget(self.side_title, 1)
        self.side_buttons = QHBoxLayout()
        self.side_buttons.setSpacing(4)
        side_head.addLayout(self.side_buttons)
        side_col.addLayout(side_head)
        self.side_view = 0               # which breakdown of the metric is shown
        self.breakdown = MeterTable(["", "", "%", ""], f)
        side_col.addWidget(self.breakdown, 1)
        split.addWidget(side)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([830, 490])
        root.addWidget(split, 1)

        foot = QHBoxLayout()
        self.note = label("", f.small, theme.MUTED)
        self.note.setWordWrap(True)
        foot.addWidget(self.note, 1)
        foot.addWidget(label(tr("En el overlay:"), f.small, theme.MUTED))
        for key, text, tip in (("dps_boss_only", tr("En jefes, solo el daño al jefe"),
                                tr("Como LOXIN: en peleas contra jefes no cuenta el daño a los adds")),):
            box = QCheckBox(text)
            box.setToolTip(tip)
            box.setChecked(bool(self.ctl.settings.get(key, True)))
            box.toggled.connect(lambda on, k=key: self._overlay_option(k, on))
            foot.addWidget(box)
        credit = self.ctl.npc_db.credit or {"text": tr("Nombres de monstruos y furia de los jefes: aion2data.com"),
                                            "url": "https://aion2data.com/monsters"}
        link = QPushButton(credit["text"].split(" (")[0])
        link.setProperty("flat", True)
        link.clicked.connect(lambda: webbrowser.open(credit["url"]))
        foot.addWidget(link)
        root.addLayout(foot)

        self._timer = QTimer(self, interval=1000, timeout=self._live)
        self.refresh()

    def _overlay_option(self, key, on):
        self.ctl.settings[key] = on
        self.ctl.settings.save()
        self.ctl.refresh()

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # --- data -----------------------------------------------------------------------------------------
    def encounters(self):
        """Summaries of this session's fights and the saved ones (fightlog.py), newest first."""
        return self.ctl.fight_list()

    def refresh(self):
        """Reload the encounter list (keeps the selection when it still exists)."""
        current = self.encounter_box.currentData()
        self.encounter_box.blockSignals(True)
        self.encounter_box.clear()
        today = time.strftime("%d/%m")
        items = self.encounters()
        self._listed = [(i["id"], i["pinned"]) for i in items]

        def when(item):
            day = time.strftime("%d/%m", time.localtime(item["last"]))
            return tr("en curso") if item["live"] else time.strftime("%H:%M" if day == today else "%d/%m %H:%M",
                                                                 time.localtime(item["last"]))

        def add(item, indent=""):
            self.encounter_box.addItem(f"{indent}{'★ ' if item['pinned'] else ''}{item['target']}"
                                       f"{' ✓' if item['killed'] else ''} · {mmss(item['duration'])} · {when(item)}",
                                       item["id"])

        bold = QFont(self.encounter_box.font())
        bold.setBold(True)
        for kind, entry in history_entries(items):     # a dungeon visit: its name, then its boss fights
            if kind == "fight":
                add(entry)
                continue
            n = len(entry.bosses)
            self.encounter_box.addItem(f"{tr(entry.place)} · " + (tr("1 jefe") if n == 1 else tr("{0} jefes", n))
                                       + f" · {when(entry.bosses[-1])}")
            header = self.encounter_box.model().item(self.encounter_box.count() - 1)
            header.setFlags(Qt.NoItemFlags)
            header.setData(bold, Qt.FontRole)
            header.setData(theme.ACCENT, Qt.ForegroundRole)
            for item in entry.bosses:
                add(item, "    ↳ ")
        idx = self.encounter_box.findData(current)
        if idx < 0:
            idx = next((i for i in range(self.encounter_box.count()) if self.encounter_box.itemData(i)), 0)
        self.encounter_box.setCurrentIndex(idx)
        self.encounter_box.blockSignals(False)
        self._fill()

    def show_encounter(self, enc):
        idx = self.encounter_box.findData(enc if isinstance(enc, str) else enc.id)
        if idx >= 0:
            self.encounter_box.setCurrentIndex(idx)

    def encounter(self):
        key = self.encounter_box.currentData()
        return self.ctl.fight(key) if key else None

    def _group(self, enc):
        return enc.group_names(self.ctl.combat.group)

    def _live(self):
        enc = self.encounter()
        items = self.encounters()
        if [(i["id"], i["pinned"]) for i in items] != getattr(self, "_listed", None):
            self.refresh()
        elif enc is not None and not enc.ended:
            self._fill()

    def _toggle_pin(self):
        enc = self.encounter()
        if enc is not None:
            self.ctl.pin_fight(enc.id, self.pin_button.isChecked())

    def _report(self):
        enc = self.encounter()
        if enc is None:
            return
        from .dps_report import ReportDialog
        ReportDialog(self.ctl, enc, self).exec()

    def _sessions(self):
        SessionsDialog(self.ctl, self).exec()

    def _simulation(self):
        enc = self.encounter()
        if enc is not None and enc.replay:
            self.ctl.open_simulation(enc)

    def _show_enemies(self):
        self.enemy_view = True
        self._fill()

    def _set_group(self, key):
        self.group_filter = key
        for k, button in self.group_buttons.items():
            button.setChecked(k == key)
        self._fill()

    def _set_metric(self, key):
        self.enemy_view = False
        self.shown_metric = METRIC_BY_KEY[key]
        self.user_sorted = False
        self.side_view = 0
        self._fill()

    def _names(self, enc):
        """eid -> (display name, in your group); outsiders are numbered by damage, the same in every tab."""
        group = self._group(enc)
        inside = lambda a: a.is_self or a.name in group  # noqa: E731
        outsiders = sorted((a for a in enc.actors.values() if not inside(a)), key=lambda a: -a.damage)
        alias = {a.eid: tr("Jugador {0}", i) for i, a in enumerate(outsiders, 1)}
        return {a.eid: ((a.name if inside(a) else alias[a.eid]) + (tr(" (tú)") if a.is_self else ""), inside(a))
                for a in enc.actors.values()}

    def _rows(self, enc, metric):
        boss = enc.main_target_eid if metric.boss_filter and self.target_box.currentData() == "boss" else None
        names = self._names(enc)
        rows = []
        for a in enc.actors.values():
            value = metric.value(a, boss)
            if not value:
                continue
            name, mine = names[a.eid]
            if self.group_filter == "group" and not mine or self.group_filter == "others" and mine:
                continue
            rows.append(Row(a, name, mine, value, QColor(CLASS_COLOR.get(a.class_name, theme.ACCENT.name()))))
        return rows

    def _fill(self):
        enc = self.encounter()
        for m in METRICS:
            has = enc is not None and any(m.value(a) for a in enc.actors.values())
            button = self.metric_buttons[m.key]
            button.setEnabled(has or m is METRICS[0])
            button.setToolTip("" if has else
                              tr("Se calcula al terminar la pelea (desde la versión 1.45)") if m.key == "upkeep" else
                              tr("No hay datos de esto en esta pelea"))
            button.setChecked(m is self.shown_metric and not self.enemy_view)
        self.enemy_button.setChecked(self.enemy_view)
        self.enemy_button.setEnabled(enc is not None and bool(enc.targets or enc.enemy_damage))
        self.pin_button.setEnabled(enc is not None)
        self.pin_button.setChecked(bool(enc is not None and enc.pinned))
        self.pin_button.setText(tr("★ Conservada") if enc is not None and enc.pinned else tr("☆ Conservar"))
        has_sim = bool(enc is not None and enc.replay)
        self.sim_button.setEnabled(has_sim)
        self.sim_button.setText(tr("Simulación") if has_sim or enc is None else tr("Sin simulación"))
        if has_sim:
            why = (tr("La pelea desde arriba: dónde estaba cada uno, las muertes (calaveras en la línea de tiempo) "
                      "y lo que lanzó el jefe"))
        elif enc is not None and not enc.ended:
            why = tr("La simulación se arma cuando termina la pelea")
        elif enc is not None and in_instance(enc.map_id) and enc.activity(self._group(enc)).boss:
            why = (tr("Esta pelea no se grabó: es de antes de la versión 1.44 (o AionDex se abrió ya dentro de la "
                      "mazmorra, sin ver la entrada)"))
        else:
            why = tr("Solo hay simulación de las peleas de jefe dentro de mazmorras")
        self.sim_button.setToolTip(why)
        if enc is not None and not self.metric_buttons[self.shown_metric.key].isEnabled():
            self.shown_metric = METRICS[0]
            self.metric_buttons[self.shown_metric.key].setChecked(True)
        metric = self.shown_metric
        if enc is not None and self._target_set_for != enc.id:
            # boss fights open on the damage to the boss (what LOXIN shows); the player can switch
            self._target_set_for = enc.id
            boss_fight = enc.activity(self._group(enc)).boss and self.ctl.settings.get("dps_boss_only", True)
            self.target_box.blockSignals(True)
            self.target_box.setCurrentIndex(1 if boss_fight else 0)
            self.target_box.blockSignals(False)
        self.target_box.setVisible(metric.boss_filter and not self.enemy_view)
        self.class_box.setVisible(not self.enemy_view)
        self.note.setText(" ".join(x for x in (ENEMY_NOTE if self.enemy_view else metric.note,
                                   tr("Los jugadores de fuera de tu party/force aparecen sin nombre (Jugador 1, "
                                      "2…), numerados por daño. Las peleas se guardan 48 horas en tu PC (las "
                                      "marcadas con ★, hasta que las quites), y los de fuera de tu grupo, ya sin "
                                      "nombre. En jefes de mundo solo ves lo que pasa cerca de ti.")) if x))
        self._fill_cards(enc)
        if self.enemy_view and enc is not None:
            self._fill_enemies(enc)
            return
        table = self.table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        columns = [tr("Jugador"), tr("Clase")] + [c[0] for c in metric.columns]
        if table.columnCount() != len(columns) or \
                [table.horizontalHeaderItem(i).text() for i in range(len(columns))] != columns:
            table.set_columns(columns)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            table.horizontalHeaderItem(1).setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        if enc is None:
            self.summary.setText(tr("Aún no hay peleas en esta sesión."))
            self._fill_breakdown(None, None)
            return
        rows = self._rows(enc, metric)
        self._fill_classes(rows)
        chosen = self.class_box.currentData()
        query = self.search.text().strip().lower()
        shown = [r for r in rows if (not chosen or r.actor.class_name == chosen)
                 and (not query or query in r.name.lower() or query in r.actor.class_name.lower())]
        total = sum(r.value for r in rows) or 1
        boss_only = metric.boss_filter and self.target_box.currentData() == "boss"
        enc.rate_time = enc.target_time(enc.main_target_eid) if boss_only else None
        summary = (f"{enc.activity(self._group(enc)).label} · {enc.main_target or tr('Pelea')} · "
                   f"{mmss(enc.duration)}" + (tr(" (jefe {0}: el DPS es sobre ese tiempo)", mmss(enc.rate_time))
                                             if boss_only else "") + tr(" · {0} jugadores", len(enc.actors)))
        contribution = enc.contribution(self._group(enc))
        if metric.key == "damage" and self.target_box.currentData() == "boss" and contribution:
            total = contribution.total          # % of what the boss lost, not of the few hits we see
            if contribution.hp_lost and contribution.seen < 0.9:
                summary += (tr(" · el jefe perdió {0} de vida y solo ves el {1:.1f}% de los golpes: el % es sobre "
                               "su vida perdida", short_number(contribution.hp_lost), 100 * contribution.seen))
        self.summary.setText(summary)
        top = max((r.value for r in shown), default=1) or 1
        table.setRowCount(len(shown))
        for i, row in enumerate(shown):
            a = row.actor
            name_item = SortItem(row.name, row.name.lower(), Qt.AlignLeft | Qt.AlignVCenter)
            name_item.setData(KEY_ROLE, a.eid)
            name_item.setData(BAR_ROLE, row.value / top)
            name_item.setData(COLOR_ROLE, row.color)
            name_item.setForeground(theme.GOLD if a.is_self else theme.ACCENT if row.mine else theme.TEXT)
            name_item.setFont(self.fonts.name)
            klass = SortItem(a.class_name or "—", a.class_name, Qt.AlignLeft | Qt.AlignVCenter)
            klass.setForeground(row.color if a.class_name else theme.MUTED)
            table.setItem(i, 0, name_item)
            table.setItem(i, 1, klass)
            for col, (_, fn) in enumerate(metric.columns, 2):
                text, key = fn(a, row.value, enc, total)
                table.setItem(i, col, SortItem(text, key))
        table.setSortingEnabled(True)
        if not self.user_sorted:
            table.sortItems(2, Qt.DescendingOrder)
        target = self.selected if any(r.actor.eid == self.selected for r in shown) else \
            next((r.actor.eid for r in shown if r.actor.is_self), shown[0].actor.eid if shown else None)
        self.selected = target
        for i in range(table.rowCount()):
            if table.item(i, 0).data(KEY_ROLE) == target:
                table.blockSignals(True)
                table.selectRow(i)
                table.blockSignals(False)
                break
        self._fill_breakdown(enc, enc.actors.get(target))

    def _fill_classes(self, rows):
        classes = sorted({r.actor.class_name for r in rows if r.actor.class_name})
        chosen = self.class_box.currentData()
        self.class_box.blockSignals(True)
        self.class_box.clear()
        self.class_box.addItem(tr("Todas las clases"), None)
        for name in classes:
            self.class_box.addItem(name, name)
        self.class_box.setCurrentIndex(max(0, self.class_box.findData(chosen)))
        self.class_box.blockSignals(False)

    def _fill_cards(self, enc):
        _empty(self.cards)
        if enc is None:
            return
        f = self.fonts
        me = next((a for a in enc.actors.values() if a.is_self), None)
        for m in METRICS:
            values = [m.value(a) for a in enc.actors.values()]
            total = sum(values)
            if not total or not m.card:
                continue
            mine = m.value(me) if me else 0
            if m.count:
                sub = tr("tú: {0}", mine) if me else ""
                self.cards.addWidget(StatCard(m.card, str(total), sub, f))
                continue
            sub = tr("tú: {0} ({1})", short_number(mine), _pct(mine / total)) if me else ""
            if m.rate:
                sub = f"{short_number(total / enc.duration)} {m.rate}" + (f" · {sub}" if sub else "")
            self.cards.addWidget(StatCard(m.card, short_number(total), sub, f))
        contribution = enc.contribution(self._group(enc))
        if contribution and enc.activity(self._group(enc)).boss:
            sub = (tr("tu {0}: {1}", 'party' if contribution.group_kind == 'party' else 'force', _pct(contribution.share(contribution.group))) if contribution.group_kind else "")
            self.cards.addWidget(StatCard(tr("Tu contribución al jefe"), _pct(contribution.share(contribution.me)), sub,
                                          f, theme.GOLD))
        state = tr("derrotado ✓") if enc.killed else (tr("en curso") if not enc.ended else tr("terminó"))
        self.cards.addWidget(StatCard(tr("Duración"), mmss(enc.duration), state, f))
        from ..mvp import FORMULA, scores
        ranked = scores(enc, self._group(enc), self.ctl.settings.get("dps_boss_only", True))
        if len(ranked) > 1:
            best = ranked[0]
            card = StatCard("MVP", best.actor.name, tr("{0:.1f} pts · el porqué, en Informe", best.points), f, theme.GOLD)
            card.setToolTip(FORMULA)
            self.cards.addWidget(card)

    # --- the enemies one by one --------------------------------------------------------------------------
    def _fill_enemies(self, enc):
        """One row per enemy (the main one first): its HP, whether it died, the damage it took (and how much of it
        was your group's) and the damage it dealt; the side shows who hit the selected one."""
        table = self.table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        table.set_columns([tr("Enemigo"), tr("Vida"), tr("Estado"), tr("Daño recibido"), "%", tr("De tu grupo"), tr("Daño que hizo")])
        group = self._group(enc)
        query = self.search.text().strip().lower()
        rows, merged = [], {}
        for eid, name, ratio, is_dead, dealt, took in enc.enemies():
            if query and query not in name.lower():
                continue
            if took:
                rows.append((eid, name, ratio, is_dead, dealt, took))
                continue
            # monsters nobody hit (areas and totems a boss leaves, adds that only hit you): one row per kind
            row = merged.get(name)
            merged[name] = (row[0], row[1], None, False, row[4] + dealt, 0, row[6] + 1) if row else \
                (eid, name, ratio, is_dead, dealt, 0, 1)
        rows += [r[:6] for r in merged.values()]
        counts = {r[0]: r[6] for r in merged.values()}
        total = sum(enc.targets.values()) or 1
        dead = sum(1 for e in rows if e[3])
        self.summary.setText(tr("{0} enemigos · {1} derrotados · daño total {2} · {3}", len(rows), dead, short_number(total), mmss(enc.duration)))
        top = max((r[5] for r in rows), default=1) or 1
        table.setRowCount(len(rows))
        for i, (eid, name, ratio, is_dead, dealt, took) in enumerate(rows):
            max_hp = (enc.hp.get(eid) or [0, None])[1] or enc.target_max_hp.get(eid)
            mine = sum(a.by_target[eid] for a in enc.actors.values() if a.is_self or a.name in group)
            shown = f"{name} ×{counts[eid]}" if counts.get(eid, 1) > 1 else name
            first = SortItem(shown, shown.lower(), Qt.AlignLeft | Qt.AlignVCenter)
            first.setData(KEY_ROLE, eid)
            first.setData(BAR_ROLE, took / top)
            first.setData(COLOR_ROLE, theme.COMBAT)
            first.setFont(self.fonts.name)
            if enc.target_npc.get(eid):
                first.setToolTip(tr("Tipo de monstruo (id del juego): {0}", enc.target_npc[eid]))
            life = SortItem("—" if max_hp is None else short_number(max_hp), max_hp or 0)
            state = SortItem("derrotado" if is_dead else ("—" if ratio is None else tr("{0:.0f}% de vida", 100 * ratio)),
                             -1 if is_dead else (ratio if ratio is not None else 2), Qt.AlignLeft | Qt.AlignVCenter)
            state.setForeground(theme.GAIN if is_dead else theme.TEXT)
            items = [first, life, state, SortItem(short_number(took) if took else "—", took),
                     SortItem(_pct(took / total) if took else "—", took / total),
                     SortItem(short_number(mine) if mine else "—", mine),
                     SortItem(short_number(dealt) if dealt else "—", dealt)]
            for col, item in enumerate(items):
                table.setItem(i, col, item)
        table.setSortingEnabled(True)
        table.sortItems(3, Qt.DescendingOrder)
        target = self.selected_enemy if any(r[0] == self.selected_enemy for r in rows) else \
            (rows[0][0] if rows else None)
        self.selected_enemy = target
        for i in range(table.rowCount()):
            if table.item(i, 0).data(KEY_ROLE) == target:
                table.blockSignals(True)
                table.selectRow(i)
                table.blockSignals(False)
                break
        self._fill_enemy_breakdown(enc, target)

    def _fill_enemy_breakdown(self, enc, eid):
        table = self.breakdown
        table.setSortingEnabled(False)
        table.setRowCount(0)
        _empty(self.side_buttons)
        if enc is None or eid is None:
            self.side_title.setText(tr("Elige un enemigo en la tabla"))
            return
        names = self._names(enc)
        self.side_title.setText(tr("Quién le pegó a {0}", enc.target_names.get(eid) or 'Enemigo'))
        rows = [(names[a.eid][0], a.by_target[eid], a) for a in enc.actors.values() if a.by_target[eid]]
        total = sum(r[1] for r in rows) or 1
        top = max((r[1] for r in rows), default=1) or 1
        table.set_columns([tr("Jugador"), tr("Daño"), "%", "DPS"])
        table.setRowCount(len(rows))
        for i, (name, amount, a) in enumerate(rows):
            first = SortItem(name, name.lower(), Qt.AlignLeft | Qt.AlignVCenter)
            first.setData(BAR_ROLE, amount / top)
            first.setData(COLOR_ROLE, QColor(CLASS_COLOR.get(a.class_name, theme.ACCENT.name())))
            first.setForeground(theme.GOLD if a.is_self else theme.TEXT)
            for col, item in enumerate([first, SortItem(short_number(amount), amount),
                                        SortItem(_pct(amount / total), amount / total),
                                        SortItem(short_number(amount / enc.target_time(eid)),
                                                 amount / enc.target_time(eid))]):
                table.setItem(i, col, item)
        table.setSortingEnabled(True)
        table.sortItems(1, Qt.DescendingOrder)

    def _on_select(self):
        items = self.table.selectedItems()
        if not items:
            return
        if self.enemy_view:
            eid = self.table.item(items[0].row(), 0).data(KEY_ROLE)
            if eid != self.selected_enemy:
                self.selected_enemy = eid
                self._fill_enemy_breakdown(self.encounter(), eid)
            return
        eid = self.table.item(items[0].row(), 0).data(KEY_ROLE)
        if eid != self.selected:
            self.selected = eid
            enc = self.encounter()
            self._fill_breakdown(enc, enc.actors.get(eid) if enc else None)

    def _set_side_view(self, index):
        self.side_view = index
        enc = self.encounter()
        self._fill_breakdown(enc, enc.actors.get(self.selected) if enc else None)

    def _fill_breakdown(self, enc, actor):
        table = self.breakdown
        table.setSortingEnabled(False)
        table.setRowCount(0)
        _empty(self.side_buttons)
        metric = self.shown_metric
        if enc is None or actor is None:
            self.side_title.setText(tr("Elige un jugador en la tabla"))
            return
        names = self._names(enc)
        views = [v for v in metric.breakdown if getattr(actor, v[1], None)]
        if not views:
            self.side_title.setText(tr("{0} — sin desglose", names[actor.eid][0]))
            return
        self.side_view = min(self.side_view, len(views) - 1)
        title, attr, hits_attr, kind = views[self.side_view]
        self.side_title.setText(names[actor.eid][0])
        if metric.key == "upkeep":
            self._side_tabs(views)
            self._fill_upkeep(enc, actor, kind, names)
            return
        if len(views) > 1:
            for i, v in enumerate(views):
                button = QPushButton(v[0])
                button.setProperty("tab", True)
                button.setCheckable(True)
                button.setChecked(i == self.side_view)
                button.clicked.connect(lambda _=False, i=i: self._set_side_view(i))
                self.side_buttons.addWidget(button)
        rows = self._breakdown_rows(enc, actor, attr, hits_attr, kind, names)
        total = sum(r[1] for r in rows) or 1
        top = max((r[1] for r in rows), default=1) or 1
        hits_header = tr("Golpes") if hits_attr else ""
        columns = [title, "Cantidad" if metric.count else "Total", "%"] + ([tr("Golpes"), tr("Media")] if hits_attr else [])
        table.set_columns(columns)
        table.setRowCount(len(rows))
        color = QColor(CLASS_COLOR.get(actor.class_name, theme.ACCENT.name()))
        for i, (text, amount, hits, key, pix, muted) in enumerate(rows):
            first = SortItem(text, text.lower(), Qt.AlignLeft | Qt.AlignVCenter)
            first.setData(BAR_ROLE, amount / top)
            first.setData(COLOR_ROLE, color)
            first.setData(KEY_ROLE, key)
            if pix is not None:
                first.setData(ICON_ROLE, pix)
            if muted:
                first.setForeground(theme.MUTED)
                if kind_is_skill(text):
                    first.setToolTip(tr("Efecto de un objeto, una poción, el equipo o un buff, no una habilidad de "
                                        "clase."))
            items = [first, SortItem(str(amount) if metric.count else short_number(amount), amount),
                     SortItem(_pct(amount / total), amount / total)]
            if hits_header:
                n = max(1, hits)
                items += [SortItem(str(hits), hits), SortItem(short_number(amount / n), amount / n)]
            for col, item in enumerate(items):
                table.setItem(i, col, item)
        table.setSortingEnabled(True)
        table.sortItems(1, Qt.DescendingOrder)
        self._request_icons(rows)

    def _side_tabs(self, views):
        if len(views) > 1:
            for i, v in enumerate(views):
                button = QPushButton(v[0])
                button.setProperty("tab", True)
                button.setCheckable(True)
                button.setChecked(i == self.side_view)
                button.clicked.connect(lambda _=False, i=i: self._set_side_view(i))
                self.side_buttons.addWidget(button)

    def _fill_upkeep(self, enc, actor, kind, names):
        """Buffs: each one with the share of the fight it was on, how many times it was put and by whom.
        Consumibles: what was used and how many times. Food names come from what AionDex learned."""
        table = self.breakdown
        consumables = self.ctl.consumables
        color = QColor(CLASS_COLOR.get(actor.class_name, theme.ACCENT.name()))
        me = next((a.name for a in enc.actors.values() if a.is_self), None)
        if kind == "buff":
            table.set_columns(["Buff", tr("Activo"), tr("Veces"), tr("De")])
            rows = actor.buff_list
            table.setRowCount(len(rows))
            for i, (key, name, caster, uptime, times, what) in enumerate(rows):
                if what == "item":
                    name = consumables.name(key)
                first = SortItem(name, name.lower(), Qt.AlignLeft | Qt.AlignVCenter)
                first.setData(BAR_ROLE, uptime)
                first.setData(COLOR_ROLE, color)
                first.setData(KEY_ROLE, key)
                if what == "class":
                    pix = self._icon(key)
                    if pix is not None:
                        first.setData(ICON_ROLE, pix)
                else:
                    first.setForeground(theme.GOLD)
                    first.setToolTip(tr("Comida, bebida o pergamino (5 min)") + (
                        "" if "#" not in name else tr(": AionDex aprende su nombre la primera vez que tú lo usas")))
                who = tr("tú") if caster == me else caster
                cells = [first, SortItem(_pct(uptime), uptime), SortItem(str(times), times),
                         SortItem(who, who.lower(), Qt.AlignLeft | Qt.AlignVCenter)]
                for col, item in enumerate(cells):
                    table.setItem(i, col, item)
            table.setSortingEnabled(True)
            table.sortItems(1, Qt.DescendingOrder)
            self._request_icons([(r[1], 0, 0, r[0], None, False) for r in rows if r[5] == "class"])
            return
        table.set_columns([tr("Consumible"), tr("Usos"), tr("Tipo")])
        rows = actor.item_uses
        table.setRowCount(len(rows))
        top = max((r[2] for r in rows), default=1) or 1
        for i, (skill, name, times, category) in enumerate(rows):
            name = consumables.name(skill)
            category = consumables.category(skill) or category or "—"
            first = SortItem(name, name.lower(), Qt.AlignLeft | Qt.AlignVCenter)
            first.setData(BAR_ROLE, times / top)
            first.setData(COLOR_ROLE, color)
            if "#" in name:
                first.setToolTip(tr("AionDex aprende el nombre la primera vez que tú lo usas"))
            for col, item in enumerate([first, SortItem(str(times), times),
                                        SortItem(category, category, Qt.AlignLeft | Qt.AlignVCenter)]):
                table.setItem(i, col, item)
        table.setSortingEnabled(True)
        table.sortItems(1, Qt.DescendingOrder)

    def _breakdown_rows(self, enc, actor, attr, hits_attr, kind, names):
        """[(text, amount, hits, key, pixmap or None, muted)] of one breakdown of the actor; rows with the same
        name are one (a boss's copies, players outside the encounter)."""
        merged = {}
        for text, amount, hits, key, pix, muted in self._breakdown_raw(enc, actor, attr, hits_attr, kind, names):
            row = merged.get(text)
            merged[text] = (text, amount + (row[1] if row else 0), hits + (row[2] if row else 0),
                            row[3] if row else key, row[4] if row else pix, muted)
        return list(merged.values())

    def _breakdown_raw(self, enc, actor, attr, hits_attr, kind, names):
        counter = getattr(actor, attr)
        hits = getattr(actor, hits_attr) if hits_attr else {}
        tuner = self.ctl.tuner
        if kind == "skill":
            merged = {}                  # chain hits and effects under the skill you press
            for skill, amount in counter.items():
                base = tuner.fold(skill) if len(str(skill)) == 8 else skill
                entry = merged.setdefault(base, [0, 0])
                entry[0] += amount
                entry[1] += hits.get(skill, 0)
            rows = []
            for base, (amount, n) in merged.items():
                if len(str(base)) == 8:
                    rows.append((tuner.skill_name(base), amount, n, base, self._icon(base), False))
                else:
                    what = tr("Poción") if str(base).startswith("2010") else tr("Efecto")
                    rows.append((f"{what} #{base}", amount, n, base, None, True))
            return rows
        if kind == "player":
            return [(names.get(eid, (self.ctl.combat.players.get(eid, tr("Jugador")), False))[0]
                     if eid in names else tr("Otro jugador"), amount, 0, eid, None, eid not in names)
                    for eid, amount in counter.items()]
        if kind == "npc":
            return [(enc.target_names.get(eid) or self.ctl.combat.npc_names.get(eid) or tr("Enemigo"), amount,
                     0, eid, None, False) for eid, amount in counter.items()]
        return [(tr("Ataque enemigo #{0}", sid), amount, 0, sid, None, True) for sid, amount in counter.items()]

    # --- skill icons -----------------------------------------------------------------------------------
    def _icon(self, sid):
        if sid in self._icons:
            return self._icons[sid]
        path = self.ctl.tuner.icon_path(sid)
        pix = QPixmap(str(path)) if path else None
        if pix is not None and not pix.isNull():
            self._icons[sid] = pix
        return pix

    def _request_icons(self, rows):
        """Download the icons of the skills shown that are not on disk yet (any class), then redraw."""
        tuner = self.ctl.tuner
        missing = sorted({tuner.fold(key) for _, _, _, key, pix, _ in rows
                          if pix is None and isinstance(key, int) and len(str(key)) == 8
                          and str(tuner.fold(key)) in tuner.skills
                          and not (tuner.icons_dir / f"{tuner.fold(key)}.png").exists()})
        if missing:
            self.ctl.fetch_skill_icons(missing, self._icons_arrived)

    def _icons_arrived(self, _=None):
        if self.isVisible():
            enc = self.encounter()
            self._fill_breakdown(enc, enc.actors.get(self.selected) if enc else None)


class SessionsDialog(QDialog):
    """Start / end a session and the reports of the past ones."""

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle(tr("{0} — Sesiones", APP_NAME))
        self.setObjectName("MapWindow")                    # the app's dark window style
        self.setStyleSheet(STYLE + EXTRA_STYLE + TABLE_STYLE)
        self.resize(720, 520)
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(16, 14, 16, 14)
        self.col.setSpacing(8)
        self._fill()

    def _clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout() is not None:
                self._clear(item.layout())

    def _fill(self):
        self._clear(self.col)
        f = Fonts(1.0)
        active = self.ctl.sessions.active()
        intro = label(tr("Una sesión junta varios runs (de una mazmorra o de varias) con la misma party: cada pelea "
                         "se guarda en ella y su informe trae cada run, el resumen y el MVP de toda la sesión."), f.small,
                      theme.MUTED)
        intro.setWordWrap(True)
        self.col.addWidget(intro)
        row = QHBoxLayout()
        if active is None:
            self.name = QLineEdit(time.strftime(tr("Sesión %d/%m %H:%M")))
            row.addWidget(self.name, 1)
            start = QPushButton(tr("Comenzar sesión"))
            start.setProperty("primary", True)
            start.clicked.connect(self._start)
            row.addWidget(start)
        else:
            row.addWidget(label(tr("En marcha: {0} · {1} peleas · desde {2}", active['name'], len(active['fights']), time.strftime('%H:%M', time.localtime(active['start']))), f.name, theme.GOLD), 1)
            end = QPushButton(tr("Terminar sesión"))
            end.setProperty("primary", True)
            end.clicked.connect(self._end)
            row.addWidget(end)
        self.col.addLayout(row)
        self.col.addWidget(label(tr("SESIONES"), f.section, theme.MUTED))
        sessions = self.ctl.sessions.newest_first()
        if not sessions:
            self.col.addWidget(label(tr("Aún no hay sesiones."), f.body, theme.MUTED))
        for s in sessions[:30]:
            line = QHBoxLayout()
            end = s.get("end")
            when = time.strftime("%d/%m %H:%M", time.localtime(s["start"])) + (
                time.strftime("–%H:%M", time.localtime(end)) if end else tr(" · en marcha"))
            line.addWidget(label(s["name"], f.name, theme.TEXT, elide=True), 1)
            line.addWidget(label(tr("{0} · {1} peleas", when, len(s['fights'])), f.small, theme.MUTED))
            report = QPushButton(tr("Informe"))
            report.clicked.connect(lambda _=False, sid=s["id"]: self.ctl.open_session_report(sid))
            line.addWidget(report)
            remove = QPushButton(tr("Borrar"))
            remove.setProperty("flat", True)
            remove.clicked.connect(lambda _=False, sid=s["id"]: self._delete(sid))
            line.addWidget(remove)
            self.col.addLayout(line)
        self.col.addStretch(1)

    def _start(self):
        self.ctl.start_session(self.name.text())
        self._fill()

    def _end(self):
        self.ctl.end_session()
        self._fill()

    def _delete(self, sid):
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, tr("Borrar sesión"), tr("¿Borrar esta sesión? Sus peleas siguen en el historial "
                                                          "48 horas.")) == QMessageBox.Yes:
            self.ctl.sessions.delete(sid)
            self._fill()
