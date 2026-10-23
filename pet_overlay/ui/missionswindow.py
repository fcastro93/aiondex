"""Season missions and achievements windows: like the game's (its tabs and their second row: weekly / season, or the
achievement's subcategory), for any of your characters, with the rewards waiting to be collected, and a summary tab
with the totals: done, in progress, not started, rewards waiting and the share of steps, overall and by category."""
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from .. import APP_NAME
from ..missions import amount
from ..modules.status import ago
from ..status import countdown, next_weekly
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .trackerwindow import CARD_STYLE
from .tunerwindow import EXTRA_STYLE
from .widgets import ProgressBar, SectionTitle, label
from ..i18n import tr

COLUMNS = 4
SUMMARY = "summary"
EXPIRING = "expiring"
WHERE = {"abyss": tr("Abismo"), "arena": "Arena", "transcendence": tr("Trascendencia"), "nightmare": tr("Pesadilla"),
         "ascension": "Ascension Trial", "battlefield": "Battlefield", "pvp": "PvP", "hunt": "cazando",
         "boss": tr("jefes"), "fieldboss": tr("jefe de campo"), "event": "evento", "pets": "pets"}


class MissionsWindow(QWidget):
    def __init__(self, ctl, model=None, title="Season Missions", subtitle=None, credit=None):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.missions = model or ctl.missions
        self.fonts = Fonts(1.0)
        self.tab = SUMMARY
        self.sub = None
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — {title}")
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
        titles.addWidget(label(title, f.name_big, theme.TEXT))
        titles.addWidget(label(subtitle or tr("AionDex lee tu progreso del juego al entrar y en cada avance."), f.small,
                               theme.MUTED))
        head.addLayout(titles, 1)
        head.addWidget(label(tr("Personaje:"), f.small, theme.MUTED))
        self.who = QComboBox()
        self.who.currentIndexChanged.connect(lambda _: self.refresh())
        head.addWidget(self.who)
        root.addLayout(head)
        self.resets = label("", f.body, theme.TEXT)
        root.addWidget(self.resets)

        tabs = QHBoxLayout()
        self.tab_buttons = {}
        first = [(SUMMARY, tr("Resumen"))] + ([(EXPIRING, tr("Expiran pronto"))] if self._expires else [])
        for key, text in first + list(self.missions.tabs.items()):
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self._show(tab=k))
            tabs.addWidget(button)
            self.tab_buttons[key] = button
        tabs.addStretch(1)
        root.addLayout(tabs)
        self.subs_row = QHBoxLayout()
        root.addLayout(self.subs_row)

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
        credit = self.missions.credit or credit or {"text": "metabot.gg",
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

    @property
    def _expires(self):
        return any(m.period in ("weekly", "season") for m in self.missions.db.values())

    def showEvent(self, event):
        self._timer.start()
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _show(self, tab=None, sub=None):
        if tab is not None and tab != self.tab:
            self.tab, self.sub = tab, None
        if sub is not None:
            self.sub = sub
        self.refresh()
        self.scroll.verticalScrollBar().setValue(0)

    def _fill_who(self, now):
        playing = self.ctl.missions_character(self.missions)
        names = self.missions.names(now, playing)
        chosen = self.who.currentData()
        self.who.blockSignals(True)
        self.who.clear()
        for n in names:
            self.who.addItem(n + (tr(" (en juego)") if self.ctl.character_name() == n else ""), n)
        self.who.setCurrentIndex(max(0, self.who.findData(chosen) if chosen in names else 0))
        self.who.blockSignals(False)
        return self.who.currentData()

    def refresh(self):
        _clear(self.body)
        _clear(self.subs_row)
        f, ms, now = self.fonts, self.missions, time.time()
        name = self._fill_who(now)
        weekly, end = next_weekly(now), ms.season_end()
        bits = []
        if any(m.period == "weekly" for m in ms.db.values()):
            bits.append(tr("Reinicio semanal: miércoles {0} (en {1})", time.strftime('%H:%M', time.localtime(weekly)), countdown(weekly - now)))
        if end and end > now:
            bits.append(tr("La temporada acaba el {0} (en {1})", time.strftime('%d/%m', time.localtime(end)), countdown(end - now)))
        self.resets.setText("   ·   ".join(bits))
        self.resets.setVisible(bool(bits))
        groups, totals = ms.stats(name, now) if name else ({}, None)
        expiring = ms.expiring(name, now) if name and self._expires else []
        for key, button in self.tab_buttons.items():
            t = groups.get((key, None))
            text = tr("Resumen") if key == SUMMARY else tr("Expiran pronto") if key == EXPIRING else ms.tabs.get(key, key)
            if key == EXPIRING:
                text += f" ({len(expiring)})" if expiring else ""
                button.setStyleSheet(f"color: {theme.WARN.name()};" if expiring else "")
            if t is not None:
                text += f" {t.done}/{t.count}" + (f" · {t.ready} ★" if t.ready else "")
            button.setText(text)
            button.setChecked(key == self.tab)
        at = ms.characters.get(name, {}).get("at") if name else None
        self.read.setText(tr("{0}: leído del juego {1}", name, ago(at, now)) if at else "")
        if not name:
            empty = label(tr("Todavía no hay lecturas. Entra al juego con AionDex abierto: tu progreso aparece aquí "
                             "en cuanto entras con cada personaje."), f.body, theme.MUTED)
            empty.setWordWrap(True)
            self.body.addWidget(empty)
            self.body.addStretch(1)
            return
        if self.tab == SUMMARY:
            self._summary(name, groups, totals, now)
            self.body.addStretch(1)
            return
        if self.tab == EXPIRING:
            self._expiring(name, expiring, now)
            self.body.addStretch(1)
            return
        subs = ms.subs.get(self.tab) or []
        if subs and self.sub not in [k for k, _ in subs]:
            self.sub = subs[0][0]
        for key, text in subs:
            t = groups.get((self.tab, key))
            button = QPushButton(text + (f" {t.done}/{t.count}" if t else "") + (f" · {t.ready} ★" if t and t.ready
                                                                                  else ""))
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.setChecked(key == self.sub)
            button.clicked.connect(lambda _=False, k=key: self._show(sub=k))
            self.subs_row.addWidget(button)
        self.subs_row.addStretch(1)
        faction = self._faction(name)
        shown = [m for m in ms.db.values() if m.tab == self.tab and (not subs or m.sub == self.sub)]
        shown.sort(key=lambda m: _order(ms.progress(name, m.id, now)))
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, m in enumerate(shown):
            grid.addWidget(self._card(m, ms.progress(name, m.id, now), faction), i // COLUMNS, i % COLUMNS)
        for c in range(COLUMNS):
            grid.setColumnStretch(c, 1)
        self.body.addLayout(grid)
        self.body.addStretch(1)

    def _expiring(self, name, items, now):
        f = self.fonts
        if not items:
            hint = label(tr("Nada por ahora: aquí salen las semanales sin terminar en las últimas {0} h antes del "
                            "reinicio del miércoles (en {1}) y las de temporada en su última semana. AionDex te "
                            "avisa 24 h y 3 h antes.", 48, countdown(next_weekly(now) - now)), f.body, theme.MUTED)
            hint.setWordWrap(True)
            self.body.addWidget(hint)
            return
        ready = sum(1 for _, p, _ in items if p.ready)
        deadline = min(d for _, _, d in items)
        hint = label(tr("Se reinician en {0}: {1} sin terminar", countdown(deadline - now), len(items))
                     + (tr(", {0} con la recompensa esperando (recógela en el juego antes)", ready) if ready else "")
                     + tr(". AionDex te avisa 24 h y 3 h antes."), f.name, theme.WARN)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        faction = self._faction(name)
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, (m, p, _) in enumerate(items):
            grid.addWidget(self._card(m, p, faction), i // COLUMNS, i % COLUMNS)
        for c in range(COLUMNS):
            grid.setColumnStretch(c, 1)
        self.body.addLayout(grid)

    # --- summary ---
    def _summary(self, name, groups, total, now):
        f, ms = self.fonts, self.missions
        cards = QGridLayout()
        cards.setSpacing(10)
        for i, (title, value, color, note) in enumerate((
                (tr("Completas"), total.done, theme.GAIN, tr("{0:.0f}% de {1}", total.pct(total.done), total.count)),
                (tr("En curso"), total.active, theme.ACCENT, f"{total.pct(total.active):.0f}%"),
                (tr("Sin empezar"), total.new, theme.MUTED, f"{total.pct(total.new):.0f}%"),
                (tr("Recompensas por recoger"), total.ready, theme.GOLD, tr("en la ventana del juego")),
                (tr("Pasos cobrados"), f"{total.steps_pct:.0f}%", theme.TEXT, tr("{0} de {1}", total.steps, total.steps_total)))):
            card = QFrame()
            card.setObjectName("Card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(14, 10, 14, 10)
            lay.setSpacing(2)
            lay.addWidget(label(title, f.small, theme.MUTED))
            lay.addWidget(label(str(value), f.name_big, color))
            lay.addWidget(label(note, f.small, theme.MUTED))
            cards.addWidget(card, 0, i)
        self.body.addLayout(cards)
        self.body.addWidget(_stack(total, 10))
        self.body.addWidget(label(tr("■ completas   ■ en curso   ■ sin empezar"), f.small, theme.MUTED))
        for tab, tab_name in ms.tabs.items():
            t = groups.get((tab, None))
            if t is None:
                continue
            self.body.addWidget(SectionTitle(tr("{0} · {1}/{2} completas ({3:.0f}%) · pasos {4:.0f}%", tab_name, t.done, t.count, t.pct(t.done), t.steps_pct) + (tr(" · {0} por recoger", t.ready)
                                                                            if t.ready else ""), f))
            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(4)
            for row, (sub, sub_name) in enumerate(ms.subs.get(tab) or []):
                s = groups.get((tab, sub))
                if s is None:
                    continue
                grid.addWidget(label(sub_name, f.body, theme.TEXT), row, 0)
                grid.addWidget(_stack(s, 8), row, 1)
                grid.addWidget(label(f"{s.done}/{s.count} ({s.pct(s.done):.0f}%)", f.body, theme.TEXT), row, 2,
                               Qt.AlignRight)
                grid.addWidget(label(tr("en curso {0} · sin empezar {1}", s.active, s.new) +
                                     (f" · {s.ready} ★" if s.ready else ""), f.small,
                                     theme.GOLD if s.ready else theme.MUTED), row, 3)
            grid.setColumnStretch(1, 1)
            self.body.addLayout(grid)
        name_faction = self._faction(name)
        tips = ms.recommend(name, name_faction, now)[:6]
        if tips:
            self.body.addWidget(SectionTitle(tr("Fáciles para avanzar (sin ir a un sitio concreto)"), f))
            grid = QGridLayout()
            grid.setSpacing(10)
            for i, (m, p) in enumerate(tips):
                grid.addWidget(self._card(m, p, name_faction), i // COLUMNS, i % COLUMNS)
            for c in range(COLUMNS):
                grid.setColumnStretch(c, 1)
            self.body.addLayout(grid)

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
        hl.addWidget(label(tr("Completa") if p.done else tr("Paso {0}/{1}", p.step, p.total), f.small, theme.MUTED))
        lay.addWidget(head)
        desc = label(text, f.body, theme.TEXT)
        desc.setWordWrap(True)
        desc.setContentsMargins(12, 2, 12, 0)
        lay.addWidget(desc)
        count = label(f"{amount(m, p.count)} / {amount(m, p.target)}", f.name_big, color)
        count.setContentsMargins(12, 2, 12, 0)
        lay.addWidget(count)
        holder = QWidget()
        hb = QHBoxLayout(holder)
        hb.setContentsMargins(12, 0, 12, 0)
        hb.addWidget(ProgressBar(p.ratio, theme.GOLD if p.ready else theme.GAIN if p.done else theme.ACCENT, 6))
        lay.addWidget(holder)
        places = " · ".join(WHERE.get(w, w.split(":", 1)[-1].replace("-", " ").title()) for w in where)
        state = (tr("¡Recompensa lista! Recógela en el juego") + (f" ({p.ready})" if p.ready > 1 else "") if p.ready else
                 tr("Todos los pasos hechos") if p.done else places)
        note = label(state, f.small, theme.GOLD if p.ready else theme.MUTED)
        note.setWordWrap(True)
        note.setContentsMargins(12, 2, 12, 0)
        lay.addWidget(note)
        if m.rewards:
            prize = label(tr("Recompensas: ") + rewards_text(m.rewards), f.small, QColor("#e9c46a"))
            prize.setWordWrap(True)
            prize.setContentsMargins(12, 2, 12, 0)
            prize.setToolTip(tr("En total, sumando todos sus pasos"))
            lay.addWidget(prize)
        steps = " / ".join(amount(m, s) for s in m.steps)
        card.setToolTip(tr("{0}\n{1}\nPasos: {2}", mission_name, text, steps) + (tr("\nRecompensas (todos los pasos): {0}", m.rewards)
                                                                     if m.rewards else ""))
        lay.addStretch(1)
        return card


class _Stack(QWidget):
    """One bar: done (green), in progress (blue), not started (grey)."""

    def __init__(self, totals, height):
        super().__init__()
        self.t = totals
        self.setFixedHeight(height)
        self.setMinimumWidth(120)

    def paintEvent(self, _):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(theme.BAR_BG)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if not self.t.count:
            return
        x = r.left()
        p.setClipRect(r)
        for n, color in ((self.t.done, theme.GAIN), (self.t.active, theme.ACCENT), (self.t.new, theme.MUTED)):
            w = r.width() * n / self.t.count
            c = QColor(color)
            if color == theme.MUTED:
                c.setAlpha(90)
            p.setBrush(c)
            p.drawRect(QRectF(x, r.top(), w, r.height()))
            x += w


def _stack(totals, height):
    return _Stack(totals, height)


def _order(p):
    """Rewards waiting first, then in progress (the closest first), then not started, then done."""
    return (0 if p.ready else 3 if p.done else 1 if p.count or p.collected else 2, -p.ratio)


def rewards_text(text):
    """metabot's rewards ("2,940× Oath Coin 5,280× Abyss Points") with a separator between the items."""
    import re
    parts = re.split(r"\s+(?=\d[\d,]*×)", text.strip())
    return " · ".join(p.strip(" ,") for p in parts if p.strip())


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
