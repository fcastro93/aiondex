"""Eventos window: the events running now on the left (banner, dates, time left), and the chosen one on the right:
what to do, how many coins you can get, the attendance days, the weekly missions with their rewards, the shop with
prices and limits, the other tables and the notes, from NC's official notice (link at the top)."""
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..events import at, weeks_left, weeks_total
from ..status import countdown, next_at, next_weekly, DAY, DAILY_PHASE
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .trackerwindow import CARD_STYLE
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label
from ..i18n import thousands, tr

BANNER_W = 300


def _when(iso):
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(at(iso)))


def _n(value):
    return thousands(f"{value:,}") if isinstance(value, int) else str(value)


class EventsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.chosen = None
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Eventos", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + CARD_STYLE)
        self.resize(1240, 880)
        f = self.fonts
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(tr("Eventos"), f.name_big, theme.TEXT))
        titles.addWidget(label(tr("Los eventos en marcha, de los avisos oficiales de AION 2 Global. Las horas, en "
                                  "la de tu PC."), f.small, theme.MUTED))
        head.addLayout(titles, 1)
        root.addLayout(head)
        body = QHBoxLayout()
        body.setSpacing(14)
        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFixedWidth(BANNER_W + 34)
        left.setFrameShape(QFrame.NoFrame)
        self.list_box = QWidget()
        self.list_col = QVBoxLayout(self.list_box)
        self.list_col.setContentsMargins(0, 0, 8, 0)
        self.list_col.setSpacing(10)
        left.setWidget(self.list_box)
        body.addWidget(left)
        right = QScrollArea()
        right.setWidgetResizable(True)
        right.setFrameShape(QFrame.NoFrame)
        self.detail_box = QWidget()
        self.detail = QVBoxLayout(self.detail_box)
        self.detail.setContentsMargins(4, 0, 8, 0)
        self.detail.setSpacing(8)
        right.setWidget(self.detail_box)
        body.addWidget(right, 1)
        root.addLayout(body, 1)
        self._images = QTimer(self, interval=2000, timeout=self._images_arrived)
        self._missing = False

    # --- data ---
    def refresh(self):
        events = self.ctl.events.running()
        if self.chosen not in {e.get("id") or e["title"] for e in events}:
            self.chosen = (events[0].get("id") or events[0]["title"]) if events else None
        self._fill_list(events)
        self._fill_detail(next((e for e in events if (e.get("id") or e["title"]) == self.chosen), None))

    def _choose(self, key):
        self.chosen = key
        self.refresh()

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                EventsWindow._clear(item.layout())

    def _banner(self, url, width):
        """The event's picture (downloaded once), or None while it downloads."""
        path = self.ctl.armory.icon(url) if url else None
        if not path:
            self._missing = bool(url)
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

    # --- the list ---
    def _fill_list(self, events):
        f, now = self.fonts, time.time()
        self._clear(self.list_col)
        self._missing = False
        for e in events:
            key = e.get("id") or e["title"]
            card = QFrame()
            card.setObjectName("Card")
            chosen = key == self.chosen
            card.setStyleSheet("QFrame#Card { background: %s; border: 1px solid %s; border-radius: 10px; }"
                               % ("#13264a" if chosen else "#0f1830", theme.ACCENT.name() if chosen else "#1c2944"))
            col = QVBoxLayout(card)
            col.setContentsMargins(10, 10, 10, 10)
            col.setSpacing(4)
            pix = self._banner(e.get("image"), BANNER_W - 22)
            if pix is not None:
                img = label("", f.small, theme.TEXT)
                img.setPixmap(pix)
                col.addWidget(img)
            col.addWidget(label(e.get("title_es") or e["title"], f.name, theme.GOLD if chosen else theme.TEXT))
            if e.get("title_es") and e.get("title_es") != e["title"]:
                col.addWidget(label(e["title"], f.small, theme.MUTED))
            end = at(e["end"])
            col.addWidget(label(tr("Termina en {0} · {1}", countdown(end - now), _when(e['end'])), f.small,
                                theme.WARN if end - now < 2 * DAY else theme.MUTED))
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
        self._clear(self.detail)
        if e is None:
            self.detail.addStretch(1)
            return
        title = label(e.get("title_es") or e["title"], f.name_big, theme.TEXT)
        self.detail.addWidget(title)
        dates = tr("Del {0} al {1}", _when(e['start']), _when(e['end'])) if e.get("start") else tr("Hasta el {0}", _when(e['end']))
        row = QHBoxLayout()
        row.addWidget(label(tr("{0} · termina en {1}", dates, countdown(at(e['end']) - now)), f.body, theme.MUTED), 1)
        if e.get("url"):
            link = QPushButton(tr("Aviso oficial"))
            link.setProperty("flat", True)
            link.clicked.connect(lambda _=False, u=e["url"]: webbrowser.open(u))
            row.addWidget(link)
        self.detail.addLayout(row)
        if e.get("summary"):
            text = label(e["summary"], f.body, theme.TEXT)
            text.setWordWrap(True)
            self.detail.addWidget(text)
        if not e.get("curated"):
            note = label(tr("AionDex aún no tiene los detalles de este evento: ábrelo en el aviso oficial. La "
                            "próxima versión los incluirá."), f.body, theme.WARN)
            note.setWordWrap(True)
            self.detail.addWidget(note)
        for block in e.get("blocks") or []:
            kind = block.get("type")
            self.detail.addWidget(SectionTitle(block.get("title", ""), f))
            if block.get("where"):
                where = label(block["where"], f.small, theme.ACCENT)
                where.setWordWrap(True)
                self.detail.addWidget(where)
            getattr(self, f"_block_{kind}", self._block_unknown)(block, e, now)
            if block.get("note"):
                note = label(block["note"], f.small, theme.MUTED)
                note.setWordWrap(True)
                self.detail.addWidget(note)
        self.detail.addStretch(1)

    def _grid(self, columns, rows, stretch=None, align_right=(), wrap=True):
        f = self.fonts
        frame = QFrame()
        frame.setObjectName("Card")
        grid = QGridLayout(frame)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        for c, text in enumerate(columns):
            grid.addWidget(label(text, f.small, theme.MUTED), 0, c, Qt.AlignRight if c in align_right else Qt.AlignLeft)
        for r, cells in enumerate(rows, start=1):
            for c, (text, color) in enumerate(cells):
                cell = label(text, f.body, color)
                cell.setWordWrap(c == stretch and wrap)
                grid.addWidget(cell, r, c, (Qt.AlignRight if c in align_right else Qt.AlignLeft) | Qt.AlignTop)
        if stretch is not None:
            grid.setColumnStretch(stretch, 1)
        self.detail.addWidget(frame)

    def _block_steps(self, block, e, now):
        f = self.fonts
        for title, text in block.get("items", []):
            frame = QFrame()
            frame.setObjectName("Card")
            col = QVBoxLayout(frame)
            col.setContentsMargins(12, 8, 12, 8)
            col.setSpacing(2)
            col.addWidget(label(title, f.name, theme.GOLD))
            body = label(text, f.body, theme.TEXT)
            body.setWordWrap(True)
            col.addWidget(body)
            self.detail.addWidget(frame)

    def _block_plan(self, block, e, now):
        """What the event pays in all and what is left of it, against what the shop asks."""
        weeks, left = weeks_total(e), weeks_left(e, now)
        rows, total = [], 0
        for name, amount, how in block.get("sources", []):
            n = amount * weeks if how == "weekly" else amount
            total += n
            rows.append([(name, theme.TEXT), (tr("{0} × {1} semanas", _n(amount), weeks) if how == "weekly" else tr("una vez"),
                                              theme.MUTED), (_n(n), theme.GOLD)])
        rows.append([(tr("Total del evento"), theme.TEXT), ("", theme.MUTED), (_n(total), theme.GOLD)])
        self._grid([tr("De dónde"), "", tr("Monedas")], rows, align_right=(2,))
        shop = block.get("once_shop", 0) + block.get("weekly_shop", 0) * weeks + block.get("per_character_shop", 0)
        text = (tr("Comprar todo lo que tiene límite cuesta {0} (con un personaje): te sobrarían {1}.", _n(shop), _n(total - shop)) if total >= shop else
                tr("Comprar todo lo que tiene límite cuesta {0}: no alcanza para todo, elige.", _n(shop)))
        text += (tr(" Quedan {0} {1} de misiones (las semanales se renuevan en {2}).", left, tr("semana") if left == 1 else tr("semanas"), countdown(next_weekly(now) - now)))
        line = label(text, self.fonts.body, theme.TEXT)
        line.setWordWrap(True)
        self.detail.addWidget(line)

    def _block_attendance(self, block, e, now):
        f = self.fonts
        frame = QFrame()
        frame.setObjectName("Card")
        grid = QGridLayout(frame)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(8)
        per_row = 7
        same = len({item for _, item, _ in block.get("days", [])}) == 1      # one item every day: named once
        for i, (day, item, amount) in enumerate(block.get("days", [])):
            cell = QFrame()
            big = amount >= max(a for _, _, a in block["days"]) and len({a for _, _, a in block["days"]}) > 1
            cell.setStyleSheet("background: %s; border-radius: 8px;" % ("#2a2410" if big else "#13203a"))
            col = QVBoxLayout(cell)
            col.setContentsMargins(8, 6, 8, 6)
            col.setSpacing(0)
            col.addWidget(label(tr("Día {0}", day), f.small, theme.MUTED))
            col.addWidget(label(f"×{_n(amount)}", f.name, theme.GOLD if big else theme.TEXT))
            if not same:
                name = label(item, f.small, theme.TEXT)
                name.setWordWrap(True)
                col.addWidget(name)
            grid.addWidget(cell, i // per_row, i % per_row)
        self.detail.addWidget(frame)
        if same and block.get("days"):
            self.detail.addWidget(label(tr("Cada día: {0}", block['days'][0][1]), f.small, theme.TEXT))
        reset = next_at(now, DAY, DAILY_PHASE)
        self.detail.addWidget(label(tr("Siguiente día de asistencia en {0}", countdown(reset - now)), f.small,
                                    theme.MUTED))

    def _block_missions(self, block, e, now):
        ms = self.ctl.missions
        who = self.ctl.missions_character(ms)
        rows, done, ready = [], 0, 0
        for row in block.get("rows", []):
            name, what, reward, amount = row[:4]
            mid = row[4].get("id") if len(row) > 4 and isinstance(row[4], dict) else None
            cells = [(name, theme.TEXT), (what, theme.TEXT),
                     (f"{_n(amount)} {reward.replace(' (Event)', '')}", theme.GOLD)]
            if mid in ms.db and who:
                p = ms.progress(who, mid, now)
                done += p.done
                ready += p.ready
                cells.append(("cobrada ✓" if p.done else tr("{0}/{1} · ¡cobrar!", p.count, p.target) if p.ready else
                              f"{p.count}/{p.target}" if p.known else "—",
                              theme.MUTED if p.done else theme.GAIN if p.ready else theme.TEXT))
            else:
                cells.append(("—", theme.MUTED))
            rows.append(cells)
        total = sum(r[3] for r in block.get("rows", []))
        self._grid([tr("Misión"), tr("Qué hacer"), tr("Recompensa"), f"{who}" if who else tr("Tu progreso")], rows, stretch=1,
                   wrap=False)
        bits = [tr("{0} misiones · {1} por semana · se renuevan en {2}", len(rows), _n(total), countdown(next_weekly(now) - now))]
        if who:
            bits.append(f"{who}: {done}/{len(rows)} cobradas" + (tr(", {0} por cobrar en el juego", ready) if ready else ""))
        self.detail.addWidget(label(" · ".join(bits), self.fonts.small, theme.MUTED))

    def _block_shop(self, block, e, now):
        currency = (block.get("currency") or "").replace(" (Event)", "")
        rows = [[(item, theme.TEXT), (f"{_n(cost)} {currency}" if currency else _n(cost), theme.GOLD),
                 (limit, theme.TEXT), (f"Lv {level}" if level else "", theme.MUTED), (notes, theme.MUTED)]
                for item, cost, limit, level, notes in block.get("rows", [])]
        self._grid(block.get("columns", ["Objeto", "Precio", "Límite", "Nivel", "Notas"]), rows, stretch=4)

    def _block_table(self, block, e, now):
        rows = [[(str(c), theme.GOLD if i == 0 else theme.TEXT) for i, c in enumerate(r)] for r in block.get("rows", [])]
        self._grid(block.get("columns", []), rows, stretch=len(block.get("columns", [])) - 1)

    def _block_notes(self, block, e, now):
        for text in block.get("items", []):
            line = label("• " + text, self.fonts.body, theme.TEXT)
            line.setWordWrap(True)
            self.detail.addWidget(line)

    def _block_unknown(self, block, e, now):
        pass
