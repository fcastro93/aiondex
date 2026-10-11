"""Damage details: every player of an encounter with filters, search and sortable columns, plus the skills of
the selected one. Names only for you and your party/force; everyone else is "Jugador N" (nothing is stored)."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..skills import base_skill
from . import theme
from .dps_panel import mmss, short_number
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import label

TABLE_STYLE = f"""
QTableWidget {{ background: #0d1628; color: {theme.TEXT.name()}; border: 1px solid #1c2944; border-radius: 8px;
    gridline-color: transparent; selection-background-color: #1d3157; alternate-background-color: #101b31; }}
QHeaderView::section {{ background: #0f1830; color: {theme.MUTED.name()}; border: none; padding: 6px 8px;
    font-weight: 600; }}
QLineEdit {{ padding: 6px 10px; }}
"""
PLAYER_COLUMNS = ["Jugador", "Clase", "Daño", "DPS", "%", "Golpes", "Críticos", "Espalda", "Mayor golpe"]
SKILL_COLUMNS = ["Habilidad", "Daño", "%", "Golpes", "Media"]
GROUPS = [("all", "Todos"), ("group", "Tú y tu grupo"), ("others", "Fuera de tu grupo")]


def _pct(ratio):
    value = 100 * ratio
    return f"{value:.2f}%" if value < 1 else f"{value:.1f}%"


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


class DpsDetailsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.group_filter = "all"
        self.selected = None             # actor eid whose skills are shown
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — Detalles del daño")
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + TABLE_STYLE)
        self.resize(1100, 820)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("Detalles del daño", f.name_big, theme.TEXT))
        self.summary = label("", f.small, theme.MUTED)
        self.summary.setWordWrap(True)
        titles.addWidget(self.summary)
        head.addLayout(titles, 1)
        root.addLayout(head)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.encounter_box = QComboBox()
        self.encounter_box.setMinimumContentsLength(28)
        self.encounter_box.currentIndexChanged.connect(lambda _: self._fill())
        filters.addWidget(self.encounter_box)
        self.target_box = QComboBox()
        self.target_box.addItem("Todo el daño", "all")
        self.target_box.addItem("Solo al jefe", "boss")
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
        self.search.setPlaceholderText("Buscar jugador o clase…")
        self.search.textChanged.connect(lambda _: self._fill())
        filters.addWidget(self.search, 1)
        root.addLayout(filters)

        self.table = self._table(PLAYER_COLUMNS)
        self.table.itemSelectionChanged.connect(self._on_select)
        root.addWidget(self.table, 3)
        self.skills_title = label("Habilidades — elige un jugador en la tabla", f.name, theme.TEXT)
        root.addWidget(self.skills_title)
        self.skills = self._table(SKILL_COLUMNS)
        root.addWidget(self.skills, 2)
        note = label("Los jugadores de fuera de tu party/force aparecen sin nombre (Jugador 1, 2…), numerados por "
                     "daño. Nada de esto se guarda: al cerrar AionDex se olvida. En jefes de mundo solo ves los "
                     "golpes de los jugadores cercanos.", f.small, theme.MUTED)
        note.setWordWrap(True)
        root.addWidget(note)

        self._timer = QTimer(self, interval=1000, timeout=self._live)
        self.refresh()

    def _table(self, columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setSortingEnabled(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(columns)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        return table

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # --- data -----------------------------------------------------------------------------------------
    def encounters(self):
        combat = self.ctl.combat
        recent = list(combat.recent)
        if combat.current is not None and combat.current not in recent:
            recent.insert(0, combat.current)
        return recent

    def refresh(self):
        """Reload the encounter list (keeps the selection when it still exists)."""
        current = self.encounter_box.currentData()
        self.encounter_box.blockSignals(True)
        self.encounter_box.clear()
        for enc in self.encounters():
            state = "en curso" if not enc.ended else time.strftime("%H:%M", time.localtime(enc.last))
            mark = " ✓" if enc.killed else ""
            self.encounter_box.addItem(f"{enc.main_target or 'Pelea'}{mark} · {mmss(enc.duration)} · {state}",
                                       id(enc))
        idx = self.encounter_box.findData(current)
        self.encounter_box.setCurrentIndex(max(0, idx))
        self.encounter_box.blockSignals(False)
        self._fill()

    def encounter(self):
        key = self.encounter_box.currentData()
        return next((e for e in self.encounters() if id(e) == key), None)

    def _live(self):
        enc = self.encounter()
        known = {self.encounter_box.itemData(i) for i in range(self.encounter_box.count())}
        if {id(e) for e in self.encounters()} != known:
            self.refresh()
        elif enc is not None and not enc.ended:
            self._fill()

    def _set_group(self, key):
        self.group_filter = key
        for k, button in self.group_buttons.items():
            button.setChecked(k == key)
        self._fill()

    def _rows(self, enc):
        """[(actor, display name, in group, damage)] for the current filters."""
        group = self.ctl.combat.group
        boss = enc.main_target_eid
        only_boss = self.target_box.currentData() == "boss"
        inside = lambda a: a.is_self or a.name in group  # noqa: E731
        outsiders = sorted((a for a in enc.actors.values() if not inside(a)), key=lambda a: -a.damage)
        alias = {a.eid: f"Jugador {i}" for i, a in enumerate(outsiders, 1)}
        rows = []
        for a in enc.actors.values():
            mine = inside(a)
            name = a.name if mine else alias[a.eid]
            damage = a.by_target[boss] if only_boss else a.damage
            if not damage:
                continue
            if self.group_filter == "group" and not mine or self.group_filter == "others" and mine:
                continue
            rows.append((a, name + (" (tú)" if a.is_self else ""), mine, damage))
        return rows

    def _fill(self):
        enc = self.encounter()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if enc is None:
            self.summary.setText("Aún no hay peleas en esta sesión.")
            self._fill_skills(None, None)
            return
        rows = self._rows(enc)
        classes = sorted({a.class_name for a, *_ in rows if a.class_name})
        chosen = self.class_box.currentData()
        self.class_box.blockSignals(True)
        self.class_box.clear()
        self.class_box.addItem("Todas las clases", None)
        for name in classes:
            self.class_box.addItem(name, name)
        self.class_box.setCurrentIndex(max(0, self.class_box.findData(chosen)))
        self.class_box.blockSignals(False)
        chosen = self.class_box.currentData()
        query = self.search.text().strip().lower()
        rows = [r for r in rows if (not chosen or r[0].class_name == chosen)
                and (not query or query in r[1].lower() or query in r[0].class_name.lower())]
        total = sum(r[3] for r in self._rows(enc)) or 1
        summary = (f"{enc.main_target or 'Pelea'} · {mmss(enc.duration)} · {len(enc.actors)} jugadores · "
                   f"{short_number(enc.total)} de daño visible")
        contribution = enc.contribution(self.ctl.combat.group)
        if self.target_box.currentData() == "boss" and contribution:
            total = contribution.total          # % of what the boss lost, not of the few hits we see
            if contribution.hp_lost and contribution.seen < 0.9:
                summary += (f" · el jefe perdió {short_number(contribution.hp_lost)} de vida y solo ves el "
                            f"{100 * contribution.seen:.1f}% de los golpes: el % es sobre su vida perdida")
        self.summary.setText(summary)
        self.table.setRowCount(len(rows))
        selected_row = None
        for r, (a, name, mine, damage) in enumerate(rows):
            hits = max(1, a.hits)
            name_item = SortItem(name, name.lower(), Qt.AlignLeft | Qt.AlignVCenter)
            name_item.setData(Qt.UserRole, a.eid)
            if a.is_self:
                name_item.setForeground(theme.GOLD)
            elif mine:
                name_item.setForeground(theme.ACCENT)
            values = [name_item,
                      SortItem(a.class_name or "—", a.class_name, Qt.AlignLeft | Qt.AlignVCenter),
                      SortItem(short_number(damage), damage),
                      SortItem(short_number(damage / enc.duration), damage / enc.duration),
                      SortItem(_pct(damage / total), damage / total),
                      SortItem(str(a.hits), a.hits),
                      SortItem(f"{100 * a.crits / hits:.0f}%", a.crits / hits),
                      SortItem(f"{100 * a.backs / hits:.0f}%", a.backs / hits),
                      SortItem(short_number(a.max_hit), a.max_hit)]
            for col, item in enumerate(values):
                self.table.setItem(r, col, item)
            if a.eid == self.selected:
                selected_row = r
        self.table.setSortingEnabled(True)
        if self.table.horizontalHeader().sortIndicatorSection() >= len(PLAYER_COLUMNS) or \
                not self.table.property("sorted"):
            self.table.sortItems(2, Qt.DescendingOrder)
            self.table.setProperty("sorted", True)
        if selected_row is not None:
            for r in range(self.table.rowCount()):
                if self.table.item(r, 0).data(Qt.UserRole) == self.selected:
                    self.table.selectRow(r)
                    break
        actor = enc.actors.get(self.selected)
        self._fill_skills(enc, actor)

    def _on_select(self):
        items = self.table.selectedItems()
        if not items:
            return
        eid = self.table.item(items[0].row(), 0).data(Qt.UserRole)
        if eid != self.selected:
            self.selected = eid
            enc = self.encounter()
            self._fill_skills(enc, enc.actors.get(eid) if enc else None)

    def _fill_skills(self, enc, actor):
        self.skills.setSortingEnabled(False)
        self.skills.setRowCount(0)
        if enc is None or actor is None:
            self.skills_title.setText("Habilidades — elige un jugador en la tabla")
            return
        row = next((r for r in self._rows(enc) if r[0] is actor), None)
        who = row[1] if row else actor.name
        self.skills_title.setText(f"Habilidades — {who}")
        names = self.ctl.tuner
        by_skill, hits = {}, {}
        for skill, amount in actor.skills.items():
            base = base_skill(skill) if len(str(skill)) == 8 else skill
            by_skill[base] = by_skill.get(base, 0) + amount
            hits[base] = hits.get(base, 0) + actor.skill_hits.get(skill, 0)
        total = sum(by_skill.values()) or 1
        self.skills.setRowCount(len(by_skill))
        for r, (base, amount) in enumerate(sorted(by_skill.items(), key=lambda x: -x[1])):
            n = max(1, hits[base])
            name = names.skill_name(base) if len(str(base)) == 8 else f"Otro efecto (#{base})"
            values = [SortItem(name, name, Qt.AlignLeft | Qt.AlignVCenter),
                      SortItem(short_number(amount), amount),
                      SortItem(f"{100 * amount / total:.1f}%", amount / total),
                      SortItem(str(hits[base]), hits[base]),
                      SortItem(short_number(amount / n), amount / n)]
            for col, item in enumerate(values):
                self.skills.setItem(r, col, item)
        self.skills.setSortingEnabled(True)
