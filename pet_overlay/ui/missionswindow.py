"""Season missions window: like the game's Season Mission window (tabs Abyss / Arena / Transcendence / Nightmare /
Challenge, weekly and season), for any of your characters, with the rewards waiting to be collected."""
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from .. import APP_NAME
from ..missions import TAB_ORDER, amount
from ..modules.status import ago
from ..status import countdown, next_weekly
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .trackerwindow import CARD_STYLE
from .tunerwindow import EXTRA_STYLE
from .widgets import ProgressBar, label

COLUMNS = 4
WHERE = {"abyss": "Abismo", "arena": "Arena", "transcendence": "Trascendencia", "nightmare": "Pesadilla",
         "ascension": "Ascension Trial", "battlefield": "Battlefield", "pvp": "PvP"}


class MissionsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.missions = ctl.missions
        self.fonts = Fonts(1.0)
        self.tab, self.period = "abyss", "weekly"
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — Misiones de temporada")
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
        titles.addWidget(label("Misiones de temporada", f.name_big, theme.TEXT))
        titles.addWidget(label("AionDex lee tu progreso del juego al entrar y en cada avance. Las semanales vuelven a "
                               "cero el miércoles; las de temporada, al acabar la temporada.", f.small, theme.MUTED))
        head.addLayout(titles, 1)
        head.addWidget(label("Personaje:", f.small, theme.MUTED))
        self.who = QComboBox()
        self.who.currentIndexChanged.connect(lambda _: self.refresh())
        head.addWidget(self.who)
        root.addLayout(head)
        self.resets = label("", f.body, theme.TEXT)
        root.addWidget(self.resets)

        tabs = QHBoxLayout()
        self.tab_buttons = {}
        for key in TAB_ORDER:
            button = QPushButton(self.missions.tabs.get(key, key))
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self._show(tab=k))
            tabs.addWidget(button)
            self.tab_buttons[key] = button
        tabs.addSpacing(24)
        self.period_buttons = {}
        for key, text in (("weekly", "Semanales"), ("season", "Temporada")):
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self._show(period=k))
            tabs.addWidget(button)
            self.period_buttons[key] = button
        tabs.addStretch(1)
        root.addLayout(tabs)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setSpacing(10)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        credit = self.missions.credit or {"text": "Misiones de temporada: metabot.gg",
                                          "url": "https://metabot.gg/en/aion-2/achievements"}
        link = QPushButton(credit["text"])
        link.setProperty("flat", True)
        link.clicked.connect(lambda: webbrowser.open(credit["url"]))
        foot.addWidget(link)
        foot.addStretch(1)
        self.read = label("", f.small, theme.MUTED)
        foot.addWidget(self.read)
        root.addLayout(foot)
        self._timer = QTimer(self, interval=60_000, timeout=self.refresh)
        self.refresh()

    def showEvent(self, event):
        self._timer.start()
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _show(self, tab=None, period=None):
        self.tab = tab or self.tab
        self.period = period or self.period
        self.refresh()

    def _fill_who(self, now):
        playing = self.ctl.missions_character()
        names = self.missions.names(now, playing)
        chosen = self.who.currentData()
        self.who.blockSignals(True)
        self.who.clear()
        for n in names:
            self.who.addItem(n + (" (en juego)" if n == playing and self.ctl.character_name() == n else ""), n)
        index = self.who.findData(chosen) if chosen in names else 0
        self.who.setCurrentIndex(max(0, index))
        self.who.blockSignals(False)
        return self.who.currentData()

    def refresh(self):
        _clear(self.body)
        f, ms, now = self.fonts, self.missions, time.time()
        name = self._fill_who(now)
        weekly, end = next_weekly(now), ms.season_end()
        self.resets.setText(f"Reinicio semanal: miércoles {time.strftime('%H:%M', time.localtime(weekly))} (en "
                            f"{countdown(weekly - now)})" + (f"   ·   La temporada acaba el "
                                                             f"{time.strftime('%d/%m', time.localtime(end))} (en "
                                                             f"{countdown(end - now)})" if end and end > now else ""))
        faction = self._faction(name)
        for key, button in self.tab_buttons.items():
            todo = [m for m in ms.db.values() if m.tab == key]
            done = sum(ms.progress(name, m.id, now).done for m in todo) if name else 0
            waiting = sum(ms.progress(name, m.id, now).ready for m in todo) if name else 0
            button.setText(f"{ms.tabs.get(key, key)} {done}/{len(todo)}" + (f" · {waiting} ★" if waiting else ""))
            button.setChecked(key == self.tab)
        for key, button in self.period_buttons.items():
            button.setChecked(key == self.period)
        at = ms.characters.get(name, {}).get("at") if name else None
        self.read.setText(f"{name}: leído del juego {ago(at, now)}" if at else "")
        if not name:
            empty = label("Todavía no hay lecturas. Entra al juego con AionDex abierto: tus misiones aparecen aquí en "
                          "cuanto entras con cada personaje.", f.body, theme.MUTED)
            empty.setWordWrap(True)
            self.body.addWidget(empty)
            self.body.addStretch(1)
            return
        shown = [m for m in ms.db.values() if m.tab == self.tab and m.period == self.period]
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, m in enumerate(shown):
            grid.addWidget(self._card(m, ms.progress(name, m.id, now), faction), i // COLUMNS, i % COLUMNS)
        for c in range(COLUMNS):
            grid.setColumnStretch(c, 1)
        self.body.addLayout(grid)
        self.body.addStretch(1)

    def _faction(self, name):
        side = self.ctl.settings.get("characters", {}).get(name, {}).get("faction")
        if not side:
            letter = (self.ctl.settings.get("gear", {}).get(name) or {}).get("faction")
            side = {"A": "asmodian", "E": "elyos"}.get(letter)
        return side or self.ctl.crafting_faction()

    def _card(self, m, p, faction):
        f = self.fonts
        mission_name, text, where = m.view(faction)
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 10)
        lay.setSpacing(4)
        head = QFrame()
        head.setObjectName("CardHead")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(12, 8, 12, 8)
        hl.setSpacing(1)
        color = theme.GOLD if p.ready else theme.GAIN if p.done else theme.TEXT
        title = label(mission_name, f.name, color, elide=True)
        title.setToolTip(mission_name)
        hl.addWidget(title)
        hl.addWidget(label("Completa" if p.done else f"Paso {p.step}/{p.total}", f.small, theme.MUTED))
        lay.addWidget(head)
        for widget in (label(text, f.body, theme.TEXT),):
            widget.setWordWrap(True)
            widget.setContentsMargins(12, 2, 12, 0)
            lay.addWidget(widget)
        count = label(f"{amount(m, p.count)} / {amount(m, p.target)}", f.name_big, color)
        count.setContentsMargins(12, 2, 12, 0)
        lay.addWidget(count)
        bar = ProgressBar(p.ratio, theme.GOLD if p.ready else theme.GAIN if p.done else theme.ACCENT, 6)
        bar.setContentsMargins(12, 0, 12, 0)
        holder = QWidget()
        hb = QHBoxLayout(holder)
        hb.setContentsMargins(12, 0, 12, 0)
        hb.addWidget(bar)
        lay.addWidget(holder)
        state = ("¡Recompensa lista! Recógela en el juego" + (f" ({p.ready})" if p.ready > 1 else "") if p.ready else
                 "Todos los pasos hechos" if p.done else
                 " · ".join(WHERE.get(w, w.split(":", 1)[-1]) for w in where))
        note = label(state, f.small, theme.GOLD if p.ready else theme.MUTED)
        note.setWordWrap(True)
        note.setContentsMargins(12, 2, 12, 0)
        lay.addWidget(note)
        steps = " / ".join(amount(m, s) for s in m.steps)
        card.setToolTip(f"{mission_name}\n{text}\nPasos: {steps}" + (f"\nRecompensas (todos los pasos): {m.rewards}"
                                                                     if m.rewards else ""))
        lay.addStretch(1)
        return card


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
