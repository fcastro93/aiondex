"""Status window: the alerts (a light that says whether they hold now and a bell to be told every hour) and one card
per character with each counter, its light and what comes next."""
import time
import webbrowser

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from .. import APP_NAME
from ..modules.status import ago
from ..status import ALERTS, KINDS, amount, countdown, next_daily, next_weekly, note
from . import theme
from .appmenu import glyph_icon
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .trackerwindow import CARD_STYLE
from .tunerwindow import EXTRA_STYLE
from .widgets import LED_COLORS, Led, SectionTitle, label
from ..i18n import tr

GLYPH_BELL, GLYPH_BELL_OFF = "", ""
LED = 14
CARD_COLUMNS = 2


class StatusWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.status = ctl.status
        self.fonts = Fonts(1.0)
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Status de tus personajes", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + CARD_STYLE)
        self.resize(1080, 860)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(tr("Status de tus personajes"), f.name_big, theme.TEXT))
        titles.addWidget(label(tr("AionDex lo lee del juego cada vez que entras con un personaje; los que no estás "
                                  "jugando se calculan con sus recargas."), f.small, theme.MUTED))
        head.addLayout(titles, 1)
        self.membership = QCheckBox(tr("Membresía (energía hasta 840)"))
        self.membership.setChecked(self.status.membership)
        self.membership.setToolTip(tr("Sin membresía la energía de Odyle llega hasta 560."))
        self.membership.toggled.connect(self._set_membership)
        head.addWidget(self.membership)
        root.addLayout(head)
        self.resets = label("", f.body, theme.TEXT)
        root.addWidget(self.resets)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setSpacing(10)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        for text, url in ((tr("Límites y recargas: metabot.gg"), "https://metabot.gg/en/aion-2/weekly-reset"),
                          ("aion2data.com", "https://aion2data.com/checklist")):
            credit = QPushButton(text)
            credit.setProperty("flat", True)
            credit.clicked.connect(lambda _=False, u=url: webbrowser.open(u))
            foot.addWidget(credit)
        foot.addStretch(1)
        root.addLayout(foot)

        self._timer = QTimer(self, interval=30_000, timeout=self.refresh)
        self.refresh()

    def showEvent(self, event):
        self._timer.start()
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _set_membership(self, on):
        self.status.set_membership(on)
        self.ctl.status_changed()

    # --- content ---
    def refresh(self):
        _clear(self.body)
        f, st, now = self.fonts, self.status, time.time()
        daily, weekly = next_daily(now), next_weekly(now)
        self.resets.setText(tr("Reinicio diario: {0} (en {1})   ·   Reinicio semanal: miércoles {2} (en {3})", time.strftime('%H:%M', time.localtime(daily)), countdown(daily - now), time.strftime('%H:%M', time.localtime(weekly)), countdown(weekly - now)))
        playing = self.ctl.character_name()
        names = st.names(now, playing)

        self.body.addWidget(SectionTitle(tr("Alertas"), f))
        hint = label(tr("La luz dice si se cumple ahora. Con la campana encendida AionDex te avisa (con sonido) en "
                        "cuanto se cumple y luego cada hora mientras siga igual. Todas empiezan apagadas."), f.small, theme.MUTED)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        alerts = QFrame()
        alerts.setObjectName("Card")
        lay = QVBoxLayout(alerts)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)
        for key, (title, when) in ALERTS.items():
            lay.addWidget(self._alert_row(key, title, when, st.targets(key, now, playing)))
        self.body.addWidget(alerts)

        accounts = st.accounts(now, playing)
        if accounts:
            self.body.addWidget(SectionTitle(tr("Tu cuenta"), f))
            hint = label(tr("Lo que comparten tus personajes del mismo servidor: un solo valor para todos (el de la "
                            "última lectura de cualquiera de ellos)."), f.small, theme.MUTED)
            hint.setWordWrap(True)
            self.body.addWidget(hint)
            grid = QGridLayout()
            grid.setSpacing(10)
            for i, (account, chars) in enumerate(accounts):
                grid.addWidget(self._card(account, False, now, chars), i // CARD_COLUMNS, i % CARD_COLUMNS)
            for c in range(CARD_COLUMNS):
                grid.setColumnStretch(c, 1)
            self.body.addLayout(grid)

        self.body.addWidget(SectionTitle(tr("Personajes"), f))
        if not names:
            empty = label(tr("Todavía no hay lecturas. Entra al juego con AionDex abierto (o cambia de personaje): "
                             "cada personaje aparece aquí en cuanto entra."), f.body, theme.MUTED)
            empty.setWordWrap(True)
            self.body.addWidget(empty)
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, name in enumerate(names):
            grid.addWidget(self._card(name, name == playing, now), i // CARD_COLUMNS, i % CARD_COLUMNS)
        for c in range(CARD_COLUMNS):
            grid.setColumnStretch(c, 1)
        self.body.addLayout(grid)
        self.body.addStretch(1)

    def _alert_row(self, key, title, when, chars):
        f = self.fonts
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(10)
        lay.addWidget(Led("red" if chars else "green", LED + 4), 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label(title, f.name, theme.TEXT))
        desc = label(when, f.small, theme.MUTED)
        desc.setWordWrap(True)
        text.addWidget(desc)
        now_text = (tr("Ahora: {0}", ', '.join(chars)) if chars else tr("Ahora: nadie"))
        text.addWidget(label(now_text, f.small, LED_COLORS["red"] if chars else LED_COLORS["green"]))
        lay.addLayout(text, 1)
        on = self.status.alert_on(key)
        bell = QPushButton(tr("Te aviso cada hora") if on else tr("Avisarme"))
        bell.setProperty("bell", True)
        bell.setCheckable(True)
        bell.setChecked(on)
        bell.setIcon(glyph_icon(GLYPH_BELL if on else GLYPH_BELL_OFF, theme.GOLD if on else theme.MUTED,
                                f.icons_family))
        bell.setIconSize(QSize(16, 16))
        bell.setToolTip(tr("Apagar la alerta") if on else tr("Encender la alerta"))
        bell.clicked.connect(lambda checked, k=key: self._toggle(k, checked))
        lay.addWidget(bell, 0, Qt.AlignVCenter)
        return row

    def _toggle(self, key, on):
        self.status.set_alert(key, on)
        self.ctl.status_changed()

    def _card(self, name, playing, now, shared_by=None):
        """A character's own counters, or (shared_by: its characters) an account's shared ones."""
        f, st = self.fonts, self.status
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.setSpacing(4)
        head = QFrame()
        head.setObjectName("CardHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 8, 12, 8)
        red = any(st.active(a, name, now) for a in ALERTS)
        hl.addWidget(Led("red" if red else "green", LED + 4))
        hl.addWidget(label(name, f.name_big, theme.TEXT), 1)
        if shared_by is not None:
            when = tr("de {0} · leído {1}", ', '.join(shared_by), ago(st.read_at(name), now))
            hl.addWidget(label(when, f.small, theme.MUTED))
        else:
            level = self.ctl.settings.get("characters", {}).get(name, {}).get("level")
            when = tr("en juego ahora") if playing else tr("leído {0} · calculado desde entonces", ago(st.read_at(name), now))
            hl.addWidget(label((tr("Nivel {0} · ", level) if level else "") + when, f.small, theme.MUTED))
        lay.addWidget(head)
        for kind in [k for k in KINDS if k.account == (shared_by is not None)]:
            v = st.value(name, kind.key, now)
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 2, 12, 2)
            rl.setSpacing(8)
            rl.addWidget(Led(st.lit(name, kind.key, now), LED), 0, Qt.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(0)
            top = QHBoxLayout()
            top.addWidget(label(kind.name, f.body, theme.TEXT), 1)
            top.addWidget(label(amount(v) if v.known else "—", f.name, theme.TEXT if v.known else theme.MUTED))
            text.addLayout(top)
            text.addWidget(label(note(v, now), f.small, theme.MUTED))
            rl.addLayout(text, 1)
            lay.addWidget(row)
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
