"""Eventos page: the events running now on the left (picture, name, time left), and the chosen one on the right as
a sheet rather than a wall of text: a banner (picture, title in the game's serif, its short lines, the time left on a
bar), what to do as short cards with chips (each day, each week, once, per server), how many coins you can get as
number tiles (attendance, missions, total, what the shop costs, what is left), the attendance days as reward tiles
(the big days in gold), the weekly missions as small mission cards with your progress, the shop as a compact table,
and the rest (other tables, notes) folded. From NC's official notice (link in the banner); item icons are metabot's
(missionswindow.item_icons). Item grades are not in the event data, so shop names take a grade's color only when the
name says it."""
import re
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, \
    QWidget

from .. import APP_NAME
from ..events import at, weeks_left, weeks_total
from ..status import countdown, next_at, next_weekly, DAY, DAILY_PHASE
from . import theme
from .design import Card, Chip, GameCard, Meter, PageHeader, RewardTile, Stat, serif_font
from .itemicons import icon_pixmap, item_icons
from .missionswindow import _clear, compact, short_name
from .theme import Fonts
from .widgets import SectionTitle, label
from ..i18n import thousands, tr

LIST_W = 236                 # the list of events
TILE = 44
CADENCE = ((r"cada d[ií]a|diari|every day|daily", tr("Cada día"), theme.ACCENT),
           (r"cada semana|por semana|semanal|every week|weekly|per week|a week", tr("Cada semana"), theme.ACCENT_GLOW),
           (r"una (sola )?vez|\bonce\b", tr("Una vez"), theme.GOLD),
           (r"por servidor|per server", tr("Por servidor"), theme.PURPLE))
GRADES = ("Mythic", "Unique", "Legend", "Epic", "Rare", "Special")


def _when(iso):
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(at(iso)))


def _day(iso):
    return time.strftime("%d/%m", time.localtime(at(iso)))


def _n(value):
    return thousands(f"{value:,}") if isinstance(value, int) else str(value)


def _coin(name):
    return (name or "").replace(" (Event)", "")


def cadence(text):
    """The chips of a «what to do» card: each day / each week / once / per server, from its words («once» only when
    it is not once a day or a week)."""
    low = (text or "").lower()
    out = [(name, color) for pattern, name, color in CADENCE if re.search(pattern, low)]
    names = [n for n, _ in out]
    if tr("Una vez") in names and (tr("Cada día") in names or tr("Cada semana") in names):
        out = [(n, c) for n, c in out if n != tr("Una vez")]
    return out


def grade_of(name):
    """An item's grade when its name says it («Transfer Stone (Unique)»), else None."""
    return next((g for g in GRADES if f"({g})" in (name or "")), None)


class _Fold(QWidget):
    """A section folded under its title (▸ / ▾), open or closed as it was left."""

    def __init__(self, fonts, title, opened, on_toggle, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(fonts.px(6))
        self.title = title
        self.button = QPushButton()
        self.button.setProperty("link", True)
        self.button.setFont(fonts.name)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(self._toggle)
        lay.addWidget(self.button, 0, Qt.AlignLeft)
        self.inner = QWidget()
        self.body = QVBoxLayout(self.inner)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(fonts.px(6))
        lay.addWidget(self.inner)
        self.on_toggle = on_toggle
        self._set(opened)

    def _set(self, opened):
        self.opened = opened
        self.inner.setVisible(opened)
        self.button.setText(("▾  " if opened else "▸  ") + self.title)

    def _toggle(self):
        self._set(not self.opened)
        self.on_toggle(self.title, self.opened)


class EventsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.chosen = None
        self._opened = set()                     # folded sections left open
        self._into = None                        # the layout the blocks are drawn into
        self.setWindowTitle(tr("{0} — Eventos", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.resize(1240, 880)
        f = self.fonts
        root = QVBoxLayout(self)
        root.setContentsMargins(f.px(22), f.px(16), f.px(22), f.px(16))
        root.setSpacing(f.px(10))
        root.addWidget(PageHeader(f, tr("Eventos"), tr("Los eventos en marcha, de los avisos oficiales de AION 2 "
                                                         "Global. Las horas, en la de tu PC.")))
        body = QHBoxLayout()
        body.setSpacing(f.px(14))
        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFixedWidth(f.px(LIST_W) + f.px(12))
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left.setFrameShape(QFrame.NoFrame)
        self.list_box = QWidget()
        self.list_col = QVBoxLayout(self.list_box)
        self.list_col.setContentsMargins(0, 0, f.px(8), 0)
        self.list_col.setSpacing(f.px(10))
        left.setWidget(self.list_box)
        body.addWidget(left)
        right = QScrollArea()
        right.setWidgetResizable(True)
        right.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right.setFrameShape(QFrame.NoFrame)
        self.detail_scroll = right
        self.detail_box = QWidget()
        self.detail = QVBoxLayout(self.detail_box)
        self.detail.setContentsMargins(0, 0, f.px(8), 0)
        self.detail.setSpacing(f.px(10))
        right.setWidget(self.detail_box)
        body.addWidget(right, 1)
        root.addLayout(body, 1)
        self._images = QTimer(self, interval=2000, timeout=self._images_arrived)
        self._missing = False
        self._icons_later = QTimer(self, singleShot=True, interval=400, timeout=self._icons_arrived)
        _, relay = item_icons(ctl)
        relay.arrived.connect(self._icons_later.start)

    # --- data ---
    def refresh(self):
        events = self.ctl.events.running()
        if self.chosen not in {e.get("id") or e["title"] for e in events}:
            self.chosen = (events[0].get("id") or events[0]["title"]) if events else None
        self._fill_list(events)
        self._fill_detail(next((e for e in events if (e.get("id") or e["title"]) == self.chosen), None))

    def show_tab(self, key):
        """An event by its id (from a notice or a test)."""
        self._choose(key)

    def _choose(self, key):
        self.chosen = key
        self.refresh()
        self.detail_scroll.verticalScrollBar().setValue(0)

    def _banner(self, url, width):
        """The event's picture (downloaded once), or None while it downloads."""
        path = self.ctl.armory.icon(url) if url else None
        if not path:
            self._missing = self._missing or bool(url)
            return None
        pix = QPixmap(str(path))
        if pix.isNull():
            return None
        dpr = self.devicePixelRatioF()
        pix = pix.scaledToWidth(round(width * dpr), Qt.SmoothTransformation)
        pix.setDevicePixelRatio(dpr)
        return pix

    def _images_arrived(self):
        if not self._missing:
            self._images.stop()
        self.refresh()

    def _icons_arrived(self):
        if self.isVisible():
            self.refresh()

    def _icon(self, name, size):
        return icon_pixmap(self.ctl, name, size, self.devicePixelRatioF()) if name else None

    def _want(self, e):
        """Ask for the icons of the event's items (attendance, missions, shop, its coin)."""
        names = {e.get("currency")}
        for b in e.get("blocks") or []:
            names |= {d[1] for d in b.get("days", [])} | {r[0] for r in b.get("rows", []) if b.get("type") == "shop"}
            names.add(b.get("currency"))
        icons, _ = item_icons(self.ctl)
        icons.want(sorted(n for n in names if n and n not in icons.map))

    # --- the list ---
    def _fill_list(self, events):
        f, now = self.fonts, time.time()
        _clear(self.list_col)
        self._missing = False
        for e in events:
            key = e.get("id") or e["title"]
            chosen = key == self.chosen
            card = QFrame()
            card.setObjectName("Tile")
            card.setProperty("hot", chosen)
            col = QVBoxLayout(card)
            col.setContentsMargins(f.px(8), f.px(8), f.px(8), f.px(10))
            col.setSpacing(f.px(4))
            pix = self._banner(e.get("image"), f.px(LIST_W) - f.px(26))
            if pix is not None:
                img = QLabel()
                img.setPixmap(pix)
                img.setAttribute(Qt.WA_TransparentForMouseEvents)
                col.addWidget(img)
            name = label(e.get("title_es") or e["title"], f.name, theme.GOLD if chosen else theme.TEXT, elide=True)
            col.addWidget(name)
            if e.get("title_es") and e.get("title_es") != e["title"]:
                col.addWidget(label(e["title"], f.small, theme.MUTED, elide=True))
            end = at(e["end"])
            soon = end - now < 2 * DAY
            col.addWidget(label(tr("Termina en {0} · {1}", countdown(end - now), _day(e["end"])), f.small,
                                theme.WARN if soon else theme.MUTED))
            if e.get("start"):
                start = at(e["start"])
                col.addWidget(Meter((now - start) / max(1, end - start), theme.WARN if soon else theme.MUTED,
                                    height=f.px(3)))
            card.setCursor(Qt.PointingHandCursor)
            card.mousePressEvent = lambda _e, k=key: self._choose(k)
            self.list_col.addWidget(card)
        if not events:
            self.list_col.addWidget(label(tr("No hay eventos en marcha."), f.body, theme.MUTED))
        self.list_col.addStretch(1)
        if self._missing and not self._images.isActive():
            self._images.start()

    # --- the chosen event ---
    def _fill_detail(self, e):
        f, now = self.fonts, time.time()
        _clear(self.detail)
        if e is None:
            self.detail.addStretch(1)
            return
        self._want(e)
        self.detail.addWidget(self._head(e, now))
        if not e.get("curated"):
            note = label(tr("AionDex aún no tiene los detalles de este evento: ábrelo en el aviso oficial. La "
                            "próxima versión los incluirá."), f.body, theme.WARN)
            note.setWordWrap(True)
            self.detail.addWidget(note)
        for block in e.get("blocks") or []:
            kind = block.get("type")
            if kind in ("table", "notes"):        # the rest, folded
                title = block.get("title", "")
                fold = _Fold(f, title, title in self._opened, self._fold_toggled)
                self._into = fold.body
                self._where(block)
                getattr(self, f"_block_{kind}")(block, e, now)
                self._note(block)
                self.detail.addWidget(fold)
                continue
            self._into = self.detail
            self.detail.addWidget(SectionTitle(block.get("title", ""), f))
            self._where(block)
            getattr(self, f"_block_{kind}", self._block_unknown)(block, e, now)
            if kind not in ("plan",):
                self._note(block)
        self.detail.addStretch(1)

    def _fold_toggled(self, title, opened):
        (self._opened.add if opened else self._opened.discard)(title)

    def _where(self, block):
        if block.get("where"):
            where = label(block["where"], self.fonts.small, theme.ACCENT)
            where.setWordWrap(True)
            self._into.addWidget(where)

    def _note(self, block):
        if block.get("note"):
            note = label(block["note"], self.fonts.small, theme.MUTED)
            note.setWordWrap(True)
            self._into.addWidget(note)

    def _head(self, e, now):
        """The banner: the picture, the title in serif, the event's short lines, the time left on a bar, the notice."""
        f = self.fonts
        hero = QFrame()
        hero.setObjectName("Hero")
        lay = QHBoxLayout(hero)
        lay.setContentsMargins(f.px(12), f.px(12), f.px(16), f.px(12))
        lay.setSpacing(f.px(14))
        pix = self._banner(e.get("image"), f.px(200))
        if pix is not None:
            img = QLabel()
            img.setPixmap(pix)
            img.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(img, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(f.px(4))
        top = QHBoxLayout()
        top.setSpacing(f.px(6))
        if e.get("kind"):
            top.addWidget(Chip(e["kind"], theme.GOLD, f))
        top.addStretch(1)
        if e.get("url"):
            link = QPushButton(tr("Aviso oficial"))
            link.setProperty("flat", True)
            link.clicked.connect(lambda _=False, u=e["url"]: webbrowser.open(u))
            top.addWidget(link)
        col.addLayout(top)
        title = label(e.get("title_es") or e["title"], serif_font(f, 17), theme.TEXT)
        title.setWordWrap(True)
        if e.get("summary"):
            title.setToolTip(e["summary"])
            title.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        col.addWidget(title)
        for line in e.get("headline") or []:
            row = label("◆  " + line, f.small, theme.TEXT)
            row.setWordWrap(True)
            col.addWidget(row)
        if not e.get("headline") and e.get("summary"):
            text = label(e["summary"], f.small, theme.TEXT)
            text.setWordWrap(True)
            col.addWidget(text)
        col.addStretch(1)
        end = at(e["end"])
        soon = end - now < 2 * DAY
        dates = tr("{0} → {1}", _when(e["start"]), _when(e["end"])) if e.get("start") else _when(e["end"])
        when = QHBoxLayout()
        when.addWidget(label(tr("Termina en {0}", countdown(end - now)), f.name, theme.WARN if soon else theme.TEXT))
        when.addStretch(1)
        when.addWidget(label(dates, f.small, theme.MUTED))
        col.addLayout(when)
        if e.get("start"):
            start = at(e["start"])
            col.addWidget(Meter((now - start) / max(1, end - start), theme.WARN if soon else theme.ACCENT,
                                height=f.px(5), glow=True))
        lay.addLayout(col, 1)
        return hero

    # --- blocks ---
    def _block_steps(self, block, e, now):
        """«Qué hacer»: a short card per step, two per row, with its chips."""
        f = self.fonts
        grid = QGridLayout()
        grid.setSpacing(f.px(8))
        for i, (title, text) in enumerate(block.get("items", [])):
            card = Card(f)
            card.body.setSpacing(f.px(4))
            card.body.addWidget(label(title, f.name, theme.GOLD))
            chips = cadence(f"{title} {text}")
            if chips:
                row = QHBoxLayout()
                row.setSpacing(f.px(5))
                for name, color in chips:
                    row.addWidget(Chip(name, color, f))
                row.addStretch(1)
                card.body.addLayout(row)
            body = label(text, f.small, theme.TEXT)
            body.setWordWrap(True)
            card.body.addWidget(body)
            card.body.addStretch(1)
            grid.addWidget(card, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._into.addLayout(grid)

    def _block_plan(self, block, e, now):
        """What the event pays in all, against what the shop asks: number tiles."""
        f = self.fonts
        weeks, left = weeks_total(e), weeks_left(e, now)
        total = 0
        tiles = QGridLayout()
        tiles.setSpacing(f.px(8))
        cells = []
        for name, amount, how in block.get("sources", []):
            n = amount * weeks if how == "weekly" else amount
            total += n
            cells.append((_n(n), name + (f" · {_n(amount)} × {weeks}" if how == "weekly" else ""), theme.GOLD,
                          tr("{0} por semana, {1} semanas", _n(amount), weeks) if how == "weekly" else tr("una vez")))
        cells.append((_n(total), tr("Total del evento"), theme.GOLD, _coin(block.get("currency"))))
        shop = block.get("once_shop", 0) + block.get("weekly_shop", 0) * weeks + block.get("per_character_shop", 0)
        if shop:
            cells.append((_n(shop), tr("comprar todo lo que tiene límite"), theme.TEXT, block.get("note", "")))
            cells.append((_n(abs(total - shop)), tr("te sobran") if total >= shop else tr("te faltan"),
                          theme.GAIN if total >= shop else theme.WARN,
                          tr("Con un personaje. Lo que sobre, en lo que no tiene límite.") if total >= shop else
                          tr("No alcanza para todo: elige.")))
        per_row = 3
        for i, (value, name, color, tip) in enumerate(cells):
            tiles.addWidget(Stat(f, value, name, color, tip=tip), i // per_row, i % per_row)
        for c in range(per_row):
            tiles.setColumnStretch(c, 1)
        self._into.addLayout(tiles)
        line = label(tr("Quedan {0} {1} de misiones · se renuevan en {2}", left,
                        tr("semana") if left == 1 else tr("semanas"), countdown(next_weekly(now) - now)),
                     f.small, theme.MUTED)
        self._into.addWidget(line)

    def _block_attendance(self, block, e, now):
        """The attendance days as reward tiles (the big days in gold), seven per row."""
        f = self.fonts
        days = block.get("days", [])
        if not days:
            return
        amounts = {a for _, _, a in days}
        top = max(amounts)
        same = len({item for _, item, _ in days}) == 1      # one item every day: named once
        frame = Card(f)
        grid = QGridLayout()
        grid.setHorizontalSpacing(f.px(8))
        grid.setVerticalSpacing(f.px(8))
        size = f.px(TILE)
        for i, (day, item, amount) in enumerate(days):
            # the big days: the most of the one item, or each seventh day of a calendar of different items
            big = (amount == top and len(amounts) > 1) if same else day % 7 == 0
            cell = QVBoxLayout()
            cell.setSpacing(f.px(2))
            pix = self._icon(item, size)
            count = compact(amount) if amount >= 1000 else f"×{amount}"
            # no icon yet: the amount in the tile when the item is the same every day, else its short name
            text = count if same else short_name(_coin(item))
            tile = RewardTile(f, pix, count if pix is not None else "", size=size, tip=f"{_n(amount)}× {item}",
                              text=text, color=theme.GOLD if big else None)
            cell.addWidget(tile, 0, Qt.AlignHCenter)
            day = tr("Día {0}", day) + ("" if pix is not None or same else f" · {count}")
            day_label = label(day, f.small, theme.GOLD if big else theme.MUTED)
            day_label.setAlignment(Qt.AlignHCenter)
            cell.addWidget(day_label)
            grid.addLayout(cell, i // 7, i % 7)
        for c in range(min(7, len(days))):
            grid.setColumnStretch(c, 1)
        frame.body.addLayout(grid)
        self._into.addWidget(frame)
        reset = next_at(now, DAY, DAILY_PHASE)
        bits = [tr("Cada día: {0}", _coin(days[0][1]))] if same else []
        bits.append(tr("siguiente día en {0}", countdown(reset - now)))
        self._into.addWidget(label(" · ".join(bits), f.small, theme.TEXT))

    def _block_missions(self, block, e, now):
        """The event's weekly missions as small mission cards with your progress."""
        f = self.fonts
        ms = self.ctl.missions
        who = self.ctl.missions_character(ms)
        rows = block.get("rows", [])
        done = ready = 0
        grid = QGridLayout()
        grid.setSpacing(f.px(8))
        per_row = 3
        size = f.px(34)
        for i, row in enumerate(rows):
            name, what, reward, amount = row[:4]
            mid = row[4].get("id") if len(row) > 4 and isinstance(row[4], dict) else None
            p = ms.progress(who, mid, now) if who and mid in ms.db else None
            if p is not None:
                done += p.done
                ready += p.ready > 0
            band = "ready" if p is not None and p.ready else "done" if p is not None and p.done else None
            card = GameCard(f, name.replace("[Event] ", ""), band)
            text = label(what, f.small, theme.TEXT if not (p and p.done) else theme.MUTED)
            text.setWordWrap(True)
            text.setAlignment(Qt.AlignHCenter)
            card.body.addWidget(text)
            line = QHBoxLayout()
            line.addStretch(1)
            pix = self._icon(reward, size)
            line.addWidget(RewardTile(f, pix, str(amount) if pix is not None else "", done=bool(p and p.done),
                                      size=size, tip=f"{amount}× {reward}", text=f"+{amount}", color=theme.GOLD))
            if p is not None and not p.done:
                count = label(f"{_n(p.count)}/{_n(p.target)}", f.name, theme.GOLD if p.ready else theme.ACCENT)
                line.addWidget(count, 0, Qt.AlignVCenter)
            line.addStretch(1)
            card.body.addLayout(line)
            if p is None:
                card.set_footer(tr("sin lectura"))
            elif p.done:
                card.set_footer(tr("cobrada ✓"))
                card.set_dim(True)
            elif p.ready:
                card.set_footer(tr("por cobrar en el juego"), color=theme.GOLD)
            else:
                card.set_footer(tr("en curso") if p.count else tr("sin empezar"))
            grid.addWidget(card, i // per_row, i % per_row)
        for c in range(per_row):
            grid.setColumnStretch(c, 1)
        self._into.addLayout(grid)
        total = sum(r[3] for r in rows)
        bits = [tr("{0} misiones · {1} por semana · se renuevan en {2}", len(rows), _n(total),
                   countdown(next_weekly(now) - now))]
        if who:
            bits.append(tr("{0}: {1}/{2} cobradas", who, done, len(rows))
                        + (tr(", {0} por cobrar en el juego", ready) if ready else ""))
        self._into.addWidget(label(" · ".join(bits), f.small, theme.MUTED))

    def _block_shop(self, block, e, now):
        """The shop as a compact table: icon and name (in its grade's color when known), price, limit, level; the
        notes in each row's tooltip."""
        f = self.fonts
        currency = _coin(block.get("currency"))
        card = Card(f)
        grid = QGridLayout()
        grid.setHorizontalSpacing(f.px(12))
        grid.setVerticalSpacing(f.px(5))
        heads = (tr("Objeto"), currency or tr("Precio"), tr("Límite"), tr("Nv."))
        for c, text in enumerate(heads):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignRight if c in (1, 3) else Qt.AlignLeft)
        size = f.px(20)
        for r, (item, cost, limit, level, notes) in enumerate(block.get("rows", []), start=1):
            cell = QHBoxLayout()
            cell.setSpacing(f.px(6))
            pix = self._icon(item, size)
            icon = QLabel()
            icon.setFixedSize(size, size)
            if pix is not None:
                icon.setPixmap(pix)
            cell.addWidget(icon)
            name = label(item + ("  ⓘ" if notes else ""), f.body, theme.grade_color(grade_of(item)), elide=True)
            if notes:
                name.setToolTip(notes)
                name.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            cell.addWidget(name, 1)
            grid.addLayout(cell, r, 0)
            grid.addWidget(label(_n(cost), f.name, theme.GOLD), r, 1, Qt.AlignRight)
            grid.addWidget(label(limit, f.small, theme.TEXT), r, 2)
            grid.addWidget(label(str(level or ""), f.small, theme.MUTED), r, 3, Qt.AlignRight)
        grid.setColumnStretch(0, 1)
        card.body.addLayout(grid)
        self._into.addWidget(card)

    def _block_table(self, block, e, now):
        f = self.fonts
        card = Card(f)
        grid = QGridLayout()
        grid.setHorizontalSpacing(f.px(12))
        grid.setVerticalSpacing(f.px(5))
        columns = block.get("columns", [])
        for c, text in enumerate(columns):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c)
        for r, row in enumerate(block.get("rows", []), start=1):
            for c, value in enumerate(row):
                cell = label(str(value), f.body if c == 0 else f.small, theme.GOLD if c == 0 else theme.TEXT)
                cell.setWordWrap(c == len(row) - 1)
                grid.addWidget(cell, r, c, Qt.AlignTop)
        if columns:
            grid.setColumnStretch(len(columns) - 1, 1)
        card.body.addLayout(grid)
        self._into.addWidget(card)

    def _block_notes(self, block, e, now):
        for text in block.get("items", []):
            line = label("◆  " + text, self.fonts.small, theme.TEXT)
            line.setWordWrap(True)
            self._into.addWidget(line)

    def _block_unknown(self, block, e, now):
        pass
