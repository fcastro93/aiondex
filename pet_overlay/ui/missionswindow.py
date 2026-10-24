"""Season Missions and Logros pages, laid out like the game's Season Mission and Achievements windows: the game's
tabs (Abyss, Arena, Transcendence... or the achievement tabs) with their second row (Weekly / Season, or the
subcategory), a grid of mission cards like the game's (the title and its step numeral on a band: green when complete,
gold when a reward waits; «Step x/y», the text with the step's count, the count with its cyan bar, the rewards as
icon tiles, a «Collect Rewards» bar) and, at the right, the game's rings (all, this tab) with the season's end, what
waits to be collected and what expires soon. Filters and a search above the grid; Resumen and Expiran pronto after
the game's tabs. The character is the app's (the picker in the window): ctl.missions_character(model).

Rewards are metabot's text (the sum of every step: the game shows one step's); their icons are metabot's item icons
(crafting.ItemIcons, looked up once per name and kept on disk), the short name in the tile until one arrives."""
import re
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QToolTip, \
    QVBoxLayout, QWidget

from .. import APP_NAME
from ..modules.status import ago
from ..status import countdown, next_weekly
from .charpicker import CharacterPicker
from . import theme
from .itemicons import icon_pixmap, item_icons
from .design import Card, ChipGroup, GameCard, GameTabs, Meter, PageHeader, ProgressRing, RewardTile, SearchBox, \
    Stat, serif_font
from .theme import Fonts
from .widgets import SectionTitle, label
from ..i18n import thousands, tr

SUMMARY = "summary"
EXPIRING = "expiring"
CARD_MIN = 206               # a card's width: the grid takes as many columns as fit
RIGHT_W = 196                # the rings' column
TILE = 40
WHERE = {"abyss": tr("Abismo"), "arena": "Arena", "transcendence": tr("Trascendencia"), "nightmare": tr("Pesadilla"),
         "ascension": "Ascension Trial", "battlefield": "Battlefield", "pvp": "PvP", "hunt": tr("cazando"),
         "boss": tr("jefes"), "fieldboss": tr("jefe de campo"), "event": tr("evento"), "pets": "pets"}
FILTERS = (("all", tr("Todas")), ("todo", tr("Por hacer")), ("ready", tr("Por cobrar")), ("done", tr("Completas")))
CURRENCY = ("Kina", "Abyss Points", "Oath Coin", "Quna", "Coin", "Medal", "Points")
SHORT = {"Abyss Points": "AP", "Oath Coin": "Oath", "Kina (Bound)": "Kina", "Kina": "Kina",
         "Silver Medal of Merit": "Medal", "Life Crystal (Bound)": "Life Cr.", "Pet Soul Crystal": "Soul Cr."}



class MissionsWindow(QWidget):
    def __init__(self, ctl, model=None, title="Season Missions", subtitle=None, credit=None):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.missions = model or ctl.missions
        self.fonts = Fonts(1.0)
        self.title = title
        self.subtitle = subtitle or tr("AionDex lee tu progreso del juego al entrar y en cada avance.")
        self.achievements = self.missions.path.stem == "achievements"
        self.tab = next(iter(self.missions.tabs), SUMMARY)
        self.sub = None
        self.cols = 3
        self.setWindowTitle(f"{APP_NAME} — {title}")
        self.setWindowIcon(ctl.app_icon)
        self.resize(1180, 860)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(f.px(22), f.px(16), f.px(22), f.px(16))
        root.setSpacing(f.px(8))
        self.header = PageHeader(f, title, self.subtitle)
        self.header.add_picker(CharacterPicker(self.ctl, f))
        root.addWidget(self.header)
        last = [(SUMMARY, tr("Resumen"))] + ([(EXPIRING, tr("Expiran pronto"))] if self._expires else [])
        self.tabs = GameTabs(f, list(self.missions.tabs.items()) + last, self.tab)
        self.tabs.changed.connect(lambda k: self._show(tab=k))
        root.addWidget(self.tabs)
        self.subs_holder = QVBoxLayout()
        self.subs_holder.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.subs_holder)
        self.subs = None
        bar = QHBoxLayout()
        bar.setSpacing(f.px(10))
        self.filter = ChipGroup(f, FILTERS, "all")
        self.filter.changed.connect(self.refresh)
        bar.addWidget(self.filter, 1)
        self.search = SearchBox(f, tr("Buscar en todas las pestañas…"))
        self.search.changed.connect(lambda _t: self.refresh())
        bar.addWidget(self.search)
        root.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(f.px(14))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(0, 0, f.px(6), 0)
        self.body.setSpacing(f.px(10))
        scroll.setWidget(holder)
        self.scroll = scroll
        body.addWidget(scroll, 1)
        right = QWidget()
        right.setFixedWidth(f.px(RIGHT_W))
        self.right = QVBoxLayout(right)
        self.right.setContentsMargins(0, 0, 0, 0)
        self.right.setSpacing(f.px(10))
        body.addWidget(right)
        root.addLayout(body, 1)

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
        self._relayout = QTimer(self, singleShot=True, interval=120, timeout=self.refresh)
        self._icons_later = QTimer(self, singleShot=True, interval=400, timeout=self._icons_arrived)
        _, relay = item_icons(ctl)
        relay.arrived.connect(self._icons_later.start)
        self.refresh()

    @property
    def _expires(self):
        return any(m.period in ("weekly", "season") for m in self.missions.db.values())

    @property
    def window_name(self):
        return "Achievements" if self.achievements else "Season Mission"

    def showEvent(self, event):
        self._timer.start()
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cols = max(2, min(6, (self.scroll.viewport().width() - self.fonts.px(10)) // self.fonts.px(CARD_MIN)))
        if cols != self.cols:
            self.cols = cols
            self._relayout.start()

    def _icons_arrived(self):
        if self.isVisible():
            self.refresh()

    def show_tab(self, key, sub=None):
        """A tab by key (one of the game's, SUMMARY or EXPIRING) and, if given, its second row."""
        self._show(tab=key if key in self.tabs.labels else None, sub=sub)

    def _show(self, tab=None, sub=None):
        if tab is not None and tab != self.tab:
            self.tab, self.sub = tab, None
            self.tabs.set_current(tab)
        if sub is not None:
            self.sub = sub
        self.refresh()
        self.scroll.verticalScrollBar().setValue(0)

    # --- drawing ---
    def refresh(self):
        _clear(self.body)
        _clear(self.right)
        _clear(self.subs_holder)
        self.subs = None
        f, ms, now = self.fonts, self.missions, time.time()
        name = self.ctl.missions_character(ms)
        groups, totals = ms.stats(name, now) if name else ({}, None)
        expiring = ms.expiring(name, now) if name and self._expires else []
        for key in self.tabs.keys():
            t = groups.get((key, None))
            text = tr("Resumen") if key == SUMMARY else tr("Expiran pronto") if key == EXPIRING else ms.tabs.get(key, key)
            if key == EXPIRING and expiring:
                text += f" ({len(expiring)})"
            if t is not None and t.ready:
                text += " ★"
            self.tabs.set_label(key, text)
        at = ms.characters.get(name, {}).get("at") if name else None
        self.read.setText(tr("{0}: leído del juego {1}", name, ago(at, now)) if at else "")
        self.header.set_subtitle(tr("{0} · {1}", name, self.subtitle) if name else self.subtitle)
        if not name:
            empty = label(tr("Todavía no hay lecturas. Entra al juego con AionDex abierto: tu progreso aparece aquí "
                             "en cuanto entras con cada personaje."), f.body, theme.MUTED)
            empty.setWordWrap(True)
            self.body.addWidget(empty)
            self.body.addStretch(1)
            return
        faction = self._faction(name)
        query = self.search.text().strip().lower()
        self._side(groups, totals, expiring, now)
        if query:
            self._search(name, query, faction, now)
        elif self.tab == SUMMARY:
            self._summary(name, groups, totals, now, faction)
        elif self.tab == EXPIRING:
            self._expiring(name, expiring, now, faction)
        else:
            self._tab(name, groups, faction, now)
        self.body.addStretch(1)

    def _side(self, groups, totals, expiring, now):
        """The right column, like the game's: «Progreso» with the season's end, the rings (everything and the tab on
        screen), then what the game does not put there: rewards waiting, what expires soon, the weekly reset."""
        f, ms = self.fonts, self.missions
        if totals is None or not totals.count:
            return
        head = label(tr("Progreso de logros") if self.achievements else tr("Progreso de la temporada"),
                     serif_font(f, 12), theme.TEXT)
        head.setAlignment(Qt.AlignHCenter)
        self.right.addWidget(head)
        end = ms.season_end()
        if end and end > now and not self.achievements:
            line = label(tr("La temporada acaba en {0}", countdown(end - now)), f.small, theme.GOLD)
            line.setAlignment(Qt.AlignHCenter)
            line.setToolTip(time.strftime("%d/%m/%Y %H:%M", time.localtime(end)))
            line.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.right.addWidget(line)
        ring = ProgressRing(f, totals.done / totals.count, f"{totals.pct(totals.done):.0f}%",
                            f"{totals.done}/{totals.count}", size=f.px(132), title=tr("Todas"))
        self.right.addWidget(ring, 0, Qt.AlignHCenter)
        t = groups.get((self.tab, None))          # the tab's ring counts both rows, like the game's
        if t is not None and t.count and self.tab not in (SUMMARY, EXPIRING):
            title = ms.tabs.get(self.tab, self.tab)
            self.right.addWidget(ProgressRing(f, t.done / t.count, f"{t.pct(t.done):.0f}%", f"{t.done}/{t.count}",
                                              size=f.px(118), color=theme.ACCENT_GLOW, title=title),
                                 0, Qt.AlignHCenter)
        if totals.ready:
            ready = Stat(f, str(totals.ready), tr("por cobrar en el juego"), theme.GOLD,
                         tip=tr("Clic: solo las que esperan. Se cobran en la ventana {0} del juego.", self.window_name))
            ready.setCursor(Qt.PointingHandCursor)
            ready.mousePressEvent = lambda _e: self.filter.set_selected("ready")
            self.right.addWidget(ready)
        if expiring:
            self.right.addWidget(self._expiring_card(expiring, now))
        if any(m.period == "weekly" for m in ms.db.values()):
            weekly = next_weekly(now)
            line = label(tr("Reinicio semanal en {0}", countdown(weekly - now)), f.small, theme.MUTED)
            line.setToolTip(tr("Miércoles {0} (hora de tu PC)", time.strftime("%H:%M", time.localtime(weekly))))
            line.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            line.setAlignment(Qt.AlignHCenter)
            self.right.addWidget(line)
        self.right.addWidget(Stat(f, f"{totals.steps_pct:.0f}%", tr("pasos cobrados"), theme.TEXT,
                                  tip=tr("{0} de {1} pasos, sumando todas las pestañas", totals.steps, totals.steps_total)))
        self.right.addStretch(1)

    def _expiring_card(self, items, now):
        f = self.fonts
        card = Card(f, tr("Expiran pronto"))
        deadline = min(d for _, _, d in items)
        card.body.addWidget(label(tr("{0} sin terminar · en {1}", len(items), countdown(deadline - now)), f.body,
                                  theme.WARN))
        faction = self._faction(self.ctl.missions_character(self.missions))
        for m, p, _d in items[:3]:
            row = label(("★ " if p.ready else "") + m.view(faction)[0], f.small, theme.GOLD if p.ready else theme.TEXT,
                        elide=True)
            card.body.addWidget(row)
        go = QPushButton(tr("Ver todas"))
        go.setProperty("flat", True)
        go.clicked.connect(lambda: self._show(tab=EXPIRING))
        card.body.addWidget(go, 0, Qt.AlignLeft)
        return card

    def _tab(self, name, groups, faction, now):
        f, ms = self.fonts, self.missions
        subs = ms.subs.get(self.tab) or []
        if subs and self.sub not in [k for k, _ in subs]:
            self.sub = subs[0][0]
        if subs:
            labels = []
            for key, text in subs:
                t = groups.get((self.tab, key))
                labels.append((key, text + (f"  {t.done}/{t.count}" if t else "") + (" ★" if t and t.ready else "")))
            self.subs = GameTabs(f, labels, self.sub, sub=True)
            self.subs.changed.connect(lambda k: self._show(sub=k))
            self.subs_holder.addWidget(self.subs)
        shown = [m for m in ms.db.values() if m.tab == self.tab and (not subs or m.sub == self.sub)]
        self._grid(name, shown, faction, now, empty=tr("Nada en esta pestaña con ese filtro."))

    def _grid(self, name, missions, faction, now, empty=""):
        ms = self.missions
        rows = [(m, ms.progress(name, m.id, now)) for m in missions]
        rows = [(m, p) for m, p in rows if self._passes(p)]
        rows.sort(key=lambda e: _order(e[1]))
        if not rows:
            self.body.addWidget(label(empty or tr("Nada que mostrar."), self.fonts.body, theme.MUTED))
            return
        self._want_icons([m for m, _ in rows])
        grid = QGridLayout()
        grid.setSpacing(self.fonts.px(10))
        for i, (m, p) in enumerate(rows):
            grid.addWidget(self._card(m, p, faction, now), i // self.cols, i % self.cols)
        for c in range(self.cols):
            grid.setColumnStretch(c, 1)
        self.body.addLayout(grid)

    def _want_icons(self, missions):
        icons, _ = item_icons(self.ctl)
        names = {n for m in missions for _, n in parse_rewards(m.rewards) if not n.startswith("Title:")}
        icons.want(sorted(n for n in names if n not in icons.map))

    def _passes(self, p):
        chosen = self.filter.selected()
        if chosen == "ready":
            return p.ready > 0
        if chosen == "done":
            return p.done
        if chosen == "todo":
            return not p.done and not p.ready
        return True

    def _search(self, name, query, faction, now):
        ms = self.missions
        hits = [m for m in ms.db.values() if query in f"{m.view(faction)[0]} {m.view(faction)[1]}".lower()]
        self.body.addWidget(label(tr("{0} resultados en todas las pestañas", len(hits)), self.fonts.small, theme.MUTED))
        self._grid(name, hits, faction, now, empty=tr("Nada con «{0}».", query))

    def _expiring(self, name, items, now, faction):
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
        self._grid(name, [m for m, _, _ in items], faction, now)

    # --- summary ---
    def _summary(self, name, groups, total, now, faction):
        f, ms = self.fonts, self.missions
        tiles = QHBoxLayout()
        tiles.setSpacing(f.px(10))
        for value, title, color, tip in (
                (total.done, tr("Completas"), theme.GAIN, tr("{0:.0f}% de {1}", total.pct(total.done), total.count)),
                (total.active, tr("En curso"), theme.ACCENT, f"{total.pct(total.active):.0f}%"),
                (total.new, tr("Sin empezar"), theme.MUTED, f"{total.pct(total.new):.0f}%"),
                (total.ready, tr("Recompensas por recoger"), theme.GOLD, tr("en la ventana del juego")),
                (f"{total.steps_pct:.0f}%", tr("Pasos cobrados"), theme.TEXT, tr("{0} de {1}", total.steps, total.steps_total))):
            tiles.addWidget(Stat(f, value, title, color, tip=tip), 1)
        self.body.addLayout(tiles)
        self.body.addWidget(_stack(total, f.px(10)))
        self.body.addWidget(label(tr("■ completas   ■ en curso   ■ sin empezar"), f.small, theme.MUTED))
        for tab, tab_name in ms.tabs.items():
            t = groups.get((tab, None))
            if t is None:
                continue
            head = QHBoxLayout()
            title = SectionTitle(tr("{0} · {1}/{2} completas ({3:.0f}%) · pasos {4:.0f}%", tab_name, t.done, t.count,
                                    t.pct(t.done), t.steps_pct) + (tr(" · {0} por recoger", t.ready) if t.ready else ""), f)
            head.addWidget(title, 1)
            go = QPushButton(tr("Abrir"))
            go.setProperty("flat", True)
            go.clicked.connect(lambda _=False, k=tab: self._show(tab=k))
            head.addWidget(go)
            self.body.addLayout(head)
            grid = QGridLayout()
            grid.setHorizontalSpacing(f.px(12))
            grid.setVerticalSpacing(f.px(4))
            for row, (sub, sub_name) in enumerate(ms.subs.get(tab) or []):
                s = groups.get((tab, sub))
                if s is None:
                    continue
                grid.addWidget(label(sub_name, f.body, theme.TEXT), row, 0)
                grid.addWidget(_stack(s, f.px(8)), row, 1)
                grid.addWidget(label(f"{s.done}/{s.count} ({s.pct(s.done):.0f}%)", f.body, theme.TEXT), row, 2,
                               Qt.AlignRight)
                grid.addWidget(label(tr("en curso {0} · sin empezar {1}", s.active, s.new) +
                                     (f" · {s.ready} ★" if s.ready else ""), f.small,
                                     theme.GOLD if s.ready else theme.MUTED), row, 3)
            grid.setColumnStretch(1, 1)
            self.body.addLayout(grid)
        tips = ms.recommend(name, faction, now)[:self.cols * 2]
        if tips:
            self.body.addWidget(SectionTitle(tr("Fáciles para avanzar (sin ir a un sitio concreto)"), f))
            self._want_icons([m for m, _ in tips])
            grid = QGridLayout()
            grid.setSpacing(f.px(10))
            for i, (m, p) in enumerate(tips):
                grid.addWidget(self._card(m, p, faction, now), i // self.cols, i % self.cols)
            for c in range(self.cols):
                grid.setColumnStretch(c, 1)
            self.body.addLayout(grid)

    def _faction(self, name):
        side = self.ctl.settings.get("characters", {}).get(name, {}).get("faction")
        if not side:
            letter = (self.ctl.settings.get("gear", {}).get(name) or {}).get("faction")
            side = {"A": "asmodian", "E": "elyos"}.get(letter)
        return side or self.ctl.crafting_faction()

    # --- one card, like the game's ---
    def _card(self, m, p, faction, now):
        f = self.fonts
        mission_name, text, where = m.view(faction)
        band = "ready" if p.ready else "done" if p.done else None
        title = mission_name + (f" {roman(p.step)}" if p.total > 1 else "")
        sub = tr("Paso {0}/{1}", p.step, p.total)
        if self.search.text().strip() or self.tab in (SUMMARY, EXPIRING):
            sub += " · " + self.missions.tabs.get(m.tab, m.tab)
        card = GameCard(f, title, band, sub)
        card.band.title.setToolTip(title)
        desc = label(step_text(m, text, p.target), f.body, theme.TEXT if not p.done else theme.MUTED)
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignHCenter)
        desc.setMinimumHeight(f.px(30))
        card.body.addWidget(desc)
        count = QLabel()
        count.setTextFormat(Qt.RichText)
        count.setFont(f.name)
        count.setAlignment(Qt.AlignHCenter)
        lit = theme.GAIN if p.done else theme.GOLD if p.ready else theme.ACCENT
        count.setText(f"<span style='color:{lit.name()}'>{_amount(m, p.count)}</span>"
                      f"<span style='color:{theme.TEXT.name()}'>/{_amount(m, p.target)}</span>")
        count.setStyleSheet("background: transparent;")
        count.setAttribute(Qt.WA_TransparentForMouseEvents)
        card.body.addWidget(count)
        card.body.addWidget(Meter(p.ratio, theme.GAIN if p.done else theme.ACCENT, height=f.px(4),
                                  glow=not p.done and p.ratio > 0))
        rewards = parse_rewards(m.rewards)
        if rewards:
            row = QHBoxLayout()
            row.setSpacing(f.px(5))
            row.addStretch(1)
            steps = tr(" (sumando los {0} pasos)", p.total) if p.total > 1 else ""
            for n, name in rewards[:4]:
                pix = None if name.startswith("Title:") else icon_pixmap(self.ctl, name, f.px(TILE), self.devicePixelRatioF())
                # no icon (yet): the short name over the count, inside the tile
                text = short_name(name)
                if n and pix is None:
                    text = (text if name in SHORT else text.split()[0]) + f"\n{compact(n)}"
                row.addWidget(RewardTile(f, pix, compact(n) if pix is not None else "", done=p.done, size=f.px(TILE),
                                         tip=(f"{n}× {name}" if n else name) + steps, text=text,
                                         color=reward_color(name)))
            if len(rewards) > 4:
                more = label(tr("+{0}", len(rewards) - 4), f.small, theme.MUTED)
                more.setToolTip(rewards_text(m.rewards))
                more.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                row.addWidget(more)
            row.addStretch(1)
            card.body.addLayout(row)
        places = " · ".join(WHERE.get(w, w.split(":", 1)[-1].replace("-", " ").title()) for w in where)
        if places and not p.done:
            place = label(places, f.small, theme.MUTED, elide=True)
            place.setAlignment(Qt.AlignHCenter)
            card.body.addWidget(place)
        if p.done:
            when = time.strftime("%d/%m/%Y", time.localtime(p.at)) if p.at else ""
            card.set_footer(tr("Completado: {0}", when) if when else tr("Completado"))
            card.set_dim(True)
        else:
            if p.ready:
                tip = tr("Cóbrala en la ventana {0} del juego: AionDex no puede cobrarla por ti.", self.window_name)
            else:
                tip = tr("Te faltan {0} para el paso {1}", _amount(m, max(0, p.target - p.count)), p.step)
            text = "Collect Rewards" + (f" ({p.ready})" if p.ready > 1 else "")
            card.set_footer(text, lambda _=False, c=card, t=text: self._collect(c, t), enabled=p.ready > 0)
            card.footer.setToolTip(tip)
        steps = " / ".join(_amount(m, s) for s in m.steps)
        card.setToolTip(tr("{0}\n{1}\nPasos: {2}", mission_name, text if p.done else step_text(m, m.view(faction)[1],
                                                                                              p.target), steps)
                        + (tr("\nRecompensas (todos los pasos): {0}", rewards_text(m.rewards)) if m.rewards else ""))
        return card

    def _collect(self, card, text):
        """«Collect Rewards» only says where: the game's window (AionDex cannot collect for you)."""
        msg = tr("Cóbrala en la ventana {0} del juego", self.window_name)
        try:
            card.footer.setText(msg)
        except RuntimeError:                    # the card was drawn again meanwhile
            return
        QToolTip.showText(QCursor.pos(), msg)

        def back():
            try:
                card.footer.setText(text)
            except RuntimeError:
                pass
        QTimer.singleShot(3500, back)


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
        p.drawRoundedRect(r, 2, 2)
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


# --- the rewards' icons (shared with Eventos) ---
def parse_rewards(text):
    """metabot's rewards («2,940× Oath Coin 5,280× Abyss Points», «Title: A , B 36,000× Kina (Bound)») as
    [(count text, name)]; a title (an achievement's) is one entry with no count."""
    text = (text or "").strip()
    if not text:
        return []
    out = []
    first = re.search(r"\d[\d,]*×", text)
    head = text[:first.start()].strip(" ,") if first else text
    if head:
        out.append(("", head))
    for n, name in re.findall(r"(\d[\d,]*)×\s*(.+?)(?=\s+\d[\d,]*×|$)", text):
        out.append((n, name.strip(" ,")))
    return out


def compact(count):
    """A tile's count as the game writes it: 590, 4.0K, 18.0K, 1.2M."""
    try:
        n = int(str(count).replace(",", "").replace(".", ""))
    except ValueError:
        return str(count)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n) if count else ""


def roman(n):
    """The step numeral the game puts after a mission's name (Purify Reshanta III)."""
    out = ""
    for value, digits in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += digits
            n -= value
    return out


def step_text(m, text, target):
    """The mission's text with the count of the step being done, as the game writes it («Hunt 600 monsters in
    Abyss» at step 3; the database has step 1's). Unchanged when step 1's count is not in it."""
    first = m.steps[0] if m.steps else None
    if not first or target == first:
        return text
    old, new = first, target
    if m.unit == "min" and "hour" in text.lower() and first % 60 == 0:
        old, new = first // 60, target // 60
    if not re.search(rf"(?<![\d,.]){old}(?![\d,.])", text):
        return text
    out = re.sub(rf"(?<![\d,.]){old}(?![\d,.])", f"{new:,}", text, count=1)
    return re.sub(rf"(?<![\d,.]){new:,} time\b", f"{new:,} times", out) if new > 1 else out


def _amount(m, n):
    """A count as the game writes it on the card: minutes as hours (1 h, 1 h 30, 45 min), big numbers with
    separators."""
    if m.unit == "min":
        h, mins = divmod(int(n), 60)
        return f"{h} h {mins:02d}" if h and mins else f"{h} h" if h else f"{mins} min"
    return thousands(f"{n:,}")


def short_name(name):
    """A reward's name as it fits in a tile: a known short form, else its first words without the «(Bound)»."""
    if name.startswith("Title:"):
        return tr("Título")
    if name in SHORT:
        return SHORT[name]
    plain = re.sub(r"\s*\(.*?\)", "", name).strip()
    words = plain.split()
    return " ".join(w[:7] for w in words[:2]) if words else "?"


def reward_color(name):
    if name.startswith("Title:"):
        return theme.PURPLE
    if any(c in name for c in CURRENCY):
        return theme.GOLD
    return theme.ACCENT


def rewards_text(text):
    """metabot's rewards ("2,940× Oath Coin 5,280× Abyss Points") with a separator between the items."""
    parts = re.split(r"\s+(?=\d[\d,]*×)", (text or "").strip())
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


_ = QFrame                      # kept for the pages that import it from here
