"""Tracker page: every boss and event on a calendar (Día: a timeline by hours with what is on now; Semana: the next
seven days side by side) with filters, and the list of cards like the game's boss tooltip (name, time left, loot
highlights). The bell on a row or a card marks it: AionDex tells you a few minutes before it comes back."""
import time
import webbrowser

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..gear import verdict
from ..modules.tracker import when_text
from ..tracker import (LEADS, REGIONS, countdown, image_file, local_day, occurrence_status, occurrences,
                       server_clock)
from . import theme
from .design import Chip, ChipGroup, Divider, GameTabs, PageHeader, SearchBox, serif_font
from .theme import Fonts
from .widgets import SectionTitle, label
from ..i18n import tr

PAGE_TABS = [("calendar", tr("Calendario")), ("list", tr("Lista"))]
VIEWS = [("day", tr("Día")), ("week", tr("Semana"))]
TABS = [("marked", tr("Marcados")), ("field", tr("Jefes de campo")), ("abyss", tr("Abismo")), ("event", tr("Eventos"))]
CATEGORIES = [("all", tr("Todo")), ("event", tr("Eventos")), ("abyss", tr("Abismo")), ("field", tr("Jefes de campo"))]
CAT_CHIP = {"event": tr("Evento"), "abyss": tr("Abismo"), "field": tr("Jefe de campo")}
CAT_COLOR = {"event": theme.ACCENT, "abyss": theme.PURPLE, "field": theme.COMBAT}
LIVE = {"en curso": "gain", "vivo": "gain", "quizá": "warn", "debería estar": "warn"}   # row highlight by state
MAP_NAMES = {1010: "Verteron (Elyos)", 1110: "Altgard (Asmodian)", 20: "Chaotic Lower Reshanta"}
MAP_SIDE = {1010: "elyos", 1110: "asmodian"}
DAY_SHORT = (tr("lun"), tr("mar"), tr("mié"), tr("jue"), tr("vie"), tr("sáb"), tr("dom"))
DAYS_AHEAD = 6                # the calendar goes from today to six days ahead (the week view: those seven days)
REPEAT = 5                    # this many times in a day or more: one line in the week («cada hora»)
ROW_ICON = 24
# item rank -> rarity colour, the game's (metabot uses the same; gold and teal checked against the game's tooltip)
RANK_COLOR = {0: "#C3C3C3", 1: "#19E048", 2: "#379AFF", 3: "#FFD02B", 4: "#FF6C00", 6: "#00FFD8"}
PORTRAIT = 64
ITEM_ICON = 30
GRADE_ES = {"Common": tr("Común"), "Rare": tr("Raro"), "Epic": tr("Épico"), "Unique": tr("Único"), "Special": tr("Especial")}
VERDICT_MARK = {"upgrade": (tr("↑ mejora"), "#7bd88f"), "base": (tr("≈ mejor base"), "#e0a050"), "no": ("", "#8a93a8"),
                "class": (tr("otra clase"), "#8a93a8"), "level": (tr("más nivel"), "#8a93a8"),
                "faction": (tr("otra facción"), "#8a93a8")}
CHIP_STYLE = ("color: {c}; background: rgba({r},{g},{b},0.14); border: 1px solid rgba({r},{g},{b},0.55); "
              "border-radius: 8px; padding: 0px 6px;")
DROPS_SHOWN = 5
CARD_STYLE = """
QFrame#Card { background: #1D2029; border: 1px solid #2C3140; border-radius: 6px; }
QFrame#CardHead { border-top-left-radius: 6px; border-top-right-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3A4150, stop:1 #2C3140); }
QPushButton[bell="true"] { background: transparent; border: 1px solid #4A5468; border-radius: 6px; padding: 4px 10px;
    color: #C9D1E0; font-weight: 600; }
QPushButton[bell="true"]:checked { background: #3B2F12; border-color: #FFD02B; color: #FFD02B; }
"""
GLYPH_SKULL = ""        # warning-ish emblem for bosses (Segoe MDL2)
GLYPH_EVENT = ""
GLYPH_BELL = ""
GLYPH_PREV, GLYPH_NEXT = "", ""


def _rgba(color, alpha):
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


CAL_STYLE = f"""
QFrame#CalRow {{ background: {_rgba(theme.SURFACE, 0.85)}; border: 1px solid {theme.LINE_SOFT.name()};
    border-radius: 4px; }}
QFrame#CalRow:hover {{ background: {_rgba(theme.SURFACE_2, 0.95)}; border-color: {theme.LINE.name()}; }}
QFrame#CalRow[past="true"] {{ background: transparent; }}
QFrame#CalRow[live="gain"] {{ border-left: 3px solid {theme.GAIN.name()}; background: {_rgba(theme.GAIN, 0.08)}; }}
QFrame#CalRow[live="warn"] {{ border-left: 3px solid {theme.WARN.name()}; background: {_rgba(theme.WARN, 0.08)}; }}
QFrame#CalHour {{ border: none; border-top: 1px solid {theme.LINE_SOFT.name()}; }}
QFrame#CalDay {{ background: {_rgba(theme.SURFACE, 0.75)}; border: 1px solid {theme.LINE_SOFT.name()};
    border-radius: 4px; }}
QFrame#CalDay[today="true"] {{ background: {_rgba(theme.SURFACE_2, 0.95)}; border: 1px solid {_rgba(theme.ACCENT, 0.55)}; }}
QFrame#CalDayHead:hover {{ background: {_rgba(theme.TEXT, 0.05)}; border-radius: 3px; }}
QFrame#WeekRow {{ background: transparent; border: none; border-left: 2px solid {theme.LINE.name()}; }}
QFrame#WeekRow:hover {{ background: {_rgba(theme.TEXT, 0.06)}; }}
QFrame#WeekRow[cat="event"] {{ border-left-color: {theme.ACCENT.name()}; }}
QFrame#WeekRow[cat="abyss"] {{ border-left-color: {theme.PURPLE.name()}; }}
QFrame#WeekRow[cat="field"] {{ border-left-color: {theme.COMBAT.name()}; }}
QFrame#WeekRow[live="gain"] {{ background: {_rgba(theme.GAIN, 0.12)}; }}
QFrame#WeekRow[live="warn"] {{ background: {_rgba(theme.WARN, 0.12)}; }}
QPushButton[calbell="true"] {{ background: transparent; color: {theme.MUTED.name()}; border: 1px solid {theme.LINE.name()};
    border-radius: 4px; padding: 2px 6px; font-weight: 400; font-family: 'Segoe Fluent Icons', 'Segoe MDL2 Assets'; }}
QPushButton[calbell="true"]:hover {{ color: {theme.TEXT.name()}; border-color: {theme.MUTED.name()}; }}
QPushButton[calbell="true"]:checked {{ color: {theme.GOLD.name()}; border-color: {theme.GOLD_DARK.name()};
    background: {_rgba(theme.GOLD, 0.12)}; }}
QPushButton[calnav="true"] {{ background: transparent; color: {theme.TEXT.name()}; border: 1px solid {theme.LINE.name()};
    border-radius: 4px; padding: 3px 7px; font-weight: 400; font-family: 'Segoe Fluent Icons', 'Segoe MDL2 Assets'; }}
QPushButton[calnav="true"]:hover {{ border-color: {theme.ACCENT.name()}; color: {theme.ACCENT.name()}; }}
QPushButton[calnav="true"]:disabled {{ color: {theme.LINE.name()}; border-color: {theme.LINE_SOFT.name()}; }}
"""


class _Click(QFrame):
    """A frame that runs on_click when clicked (its labels let the mouse through; its buttons keep their clicks)."""

    def __init__(self, on_click, name, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            QTimer.singleShot(0, self.on_click)       # after the event: the click may rebuild the page
        else:
            super().mouseReleaseEvent(event)


class _NowLine(QWidget):
    """«ahora 13:04»: a cyan pill and a line across the timeline, glowing like the game's selected tab."""

    def __init__(self, fonts, parent=None):
        super().__init__(parent)
        self.fonts = fonts
        self.text = ""
        self.setFixedHeight(fonts.px(18))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_text(self, text):
        if text != self.text:
            self.text = text
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(self.fonts.badge)
        h, w = self.height(), self.width()
        tw = p.fontMetrics().horizontalAdvance(self.text) + self.fonts.px(14)
        pill = QRectF(1.5, 2.5, tw, h - 5)
        y = h / 2
        line = QLinearGradient(pill.right(), 0, w, 0)
        line.setColorAt(0, QColor(theme.ACCENT_GLOW))
        edge = QColor(theme.ACCENT)
        edge.setAlpha(25)
        line.setColorAt(1, edge)
        p.setPen(QPen(line, 1.6))
        p.drawLine(QPointF(pill.right(), y), QPointF(w, y))
        glow = QColor(theme.ACCENT)
        glow.setAlpha(70)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        r = pill.height() / 2
        p.drawRoundedRect(pill.adjusted(-1.5, -1.5, 1.5, 1.5), r + 1.5, r + 1.5)
        p.setBrush(theme.ACCENT)
        p.drawRoundedRect(pill, r, r)
        p.setPen(theme.WINDOW_BG)
        p.drawText(pill, Qt.AlignCenter, self.text)


class TrackerWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.tracker = ctl.tracker
        self.fonts = Fonts(1.0)
        f = self.fonts
        self.page_tab = "calendar"
        self.view = "day"
        self.day_offset = 0
        self.list_tab = "marked" if self.tracker.config.get("marked") else "field"
        self._status_labels = []          # list cards: (entry, QLabel) refreshed by tick()
        self._cal_states = []             # calendar rows: (entry, start, end, QLabel) refreshed by tick()
        self._cal_sig = None              # what the calendar on screen shows: rebuilt only when that changes
        self._cal_checked = 0.0
        self._first_cal = True
        self._now_line = None
        self._scroll_target = None
        self._scroll_gen = 0
        self._subtitle = None
        self._images = {}                 # path -> QPixmap of the downloaded images
        self._wanted = set()              # images the rows and cards on screen use
        self._gear_name = None            # the character the drops are compared with (the page's character)
        self._gear_state = None           # its saved gear
        self._verdicts = {}               # drop name -> [(piece, Item, Verdict)] for the current refresh
        self._glyph = QFont(f.icons_family)
        self._glyph.setPixelSize(f.px(14))
        self.setObjectName("TrackerPage")
        self.setWindowTitle(tr("{0} — Tracker de jefes y eventos", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(CARD_STYLE + CAL_STYLE)
        self.resize(1180, 860)

        root = QVBoxLayout(self)
        root.setContentsMargins(f.px(22), f.px(16), f.px(22), f.px(10))
        root.setSpacing(f.px(8))
        self.header = PageHeader(f, tr("Tracker de jefes y eventos"))
        self.header.add_right(label(tr("Servidor:"), f.small, theme.MUTED))
        self.region = QComboBox()
        for rid, (text, _, _) in REGIONS.items():
            self.region.addItem(text, rid)
        self.region.setCurrentIndex(max(0, self.region.findData(self.tracker.region)))
        self.region.currentIndexChanged.connect(lambda _: self._set("region", self.region.currentData()))
        self.header.add_right(self.region)
        self.header.add_right(label(tr("Avisarme"), f.small, theme.MUTED))
        self.lead = QComboBox()
        for m in LEADS:
            self.lead.addItem(tr("{0} min antes", m), m)
        self.lead.setCurrentIndex(max(0, self.lead.findData(self.tracker.lead)))
        self.lead.currentIndexChanged.connect(lambda _: self._set("lead", self.lead.currentData()))
        self.header.add_right(self.lead)
        root.addWidget(self.header)

        bar = QHBoxLayout()
        bar.setSpacing(f.px(8))
        self.tabs = GameTabs(f, PAGE_TABS, self.page_tab)
        self.tabs.changed.connect(self.show_tab)
        bar.addWidget(self.tabs, 1)
        boss_map = QPushButton(tr("Mapa de jefes"))
        boss_map.setProperty("ghost", True)
        boss_map.setToolTip(tr("El Mapa del mundo con los jefes del Tracker sobre su punto y su estado en vivo"))
        boss_map.clicked.connect(lambda: self.ctl.open_boss_map())
        bar.addWidget(boss_map, 0, Qt.AlignVCenter)
        root.addLayout(bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_calendar())
        self.stack.addWidget(self._build_list())
        root.addWidget(self.stack, 1)

        foot = QHBoxLayout()
        for c in self.tracker.credits or [{"text": tr("Horarios, jefes y botín: metabot.gg"),
                                           "url": "https://metabot.gg/en/aion-2"}]:
            credit = QPushButton(c["text"])
            credit.setProperty("flat", True)
            credit.clicked.connect(lambda _=False, url=c["url"]: webbrowser.open(url))
            foot.addWidget(credit)
        foot.addStretch(1)
        root.addLayout(foot)

        self._timer = QTimer(self, interval=1000, timeout=self.tick)
        self.refresh()

    def _build_calendar(self):
        f = self.fonts
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, f.px(2), 0, 0)
        v.setSpacing(f.px(8))
        top = QHBoxLayout()
        top.setSpacing(f.px(8))
        self.view_tabs = GameTabs(f, VIEWS, self.view, sub=True)
        self.view_tabs.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.view_tabs.changed.connect(self._set_view)
        top.addWidget(self.view_tabs)
        top.addSpacing(f.px(8))
        self.prev_day = QPushButton(GLYPH_PREV)
        self.next_day = QPushButton(GLYPH_NEXT)
        for button, step, tip in ((self.prev_day, -1, tr("Día anterior")), (self.next_day, 1, tr("Día siguiente"))):
            button.setProperty("calnav", True)
            button.setFont(self._glyph)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, s=step: self._go_day(self.day_offset + s))
        top.addWidget(self.prev_day)
        self.day_title = label("", serif_font(f, 11), theme.TEXT)
        top.addWidget(self.day_title)
        top.addWidget(self.next_day)
        self.today_btn = QPushButton(tr("Hoy"))
        self.today_btn.setProperty("flat", True)
        self.today_btn.clicked.connect(lambda: self._go_day(0))
        top.addWidget(self.today_btn)
        top.addStretch(1)
        self.cal_search = SearchBox(f, tr("Buscar jefe, zona u objeto…"))
        self.cal_search.setMinimumWidth(f.px(230))
        self._cal_search_timer = QTimer(self, singleShot=True, interval=250,
                                        timeout=lambda: self._refresh_calendar(scroll=self._scroll_mode()))
        self.cal_search.changed.connect(lambda _: self._cal_search_timer.start())
        top.addWidget(self.cal_search)
        v.addLayout(top)

        chips = QHBoxLayout()
        chips.setSpacing(f.px(12))
        self.marked_chip = ChipGroup(f, [("marked", tr("Solo marcados"),
                                          tr("Solo lo que marcaste con la campana (de eso te aviso)"))],
                                     multi=True, gold=True)
        self.marked_chip.changed.connect(lambda: self._refresh_calendar(scroll=self._scroll_mode()))
        chips.addWidget(self.marked_chip)
        self.cat_chips = ChipGroup(f, CATEGORIES, "all")
        self.cat_chips.changed.connect(lambda: self._refresh_calendar(scroll=self._scroll_mode()))
        chips.addWidget(self.cat_chips, 1)
        self.legend = label("", f.small, theme.MUTED)
        chips.addWidget(self.legend)
        v.addLayout(chips)

        self.cal_scroll = QScrollArea()
        self.cal_scroll.setWidgetResizable(True)
        self.cal_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cal_holder = QWidget()
        self.cal_body = QVBoxLayout(self.cal_holder)
        self.cal_body.setContentsMargins(0, 0, f.px(8), f.px(8))
        self.cal_body.setSpacing(0)
        self.cal_scroll.setWidget(self.cal_holder)
        v.addWidget(self.cal_scroll, 1)
        return page

    def _build_list(self):
        f = self.fonts
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, f.px(2), 0, 0)
        v.setSpacing(f.px(8))
        bar = QHBoxLayout()
        bar.setSpacing(f.px(10))
        self.list_tabs = GameTabs(f, TABS, self.list_tab, sub=True)
        self.list_tabs.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.list_tabs.changed.connect(self._show_list_tab)
        bar.addWidget(self.list_tabs)
        bar.addStretch(1)
        self.upgrades_only = ChipGroup(f, [("up", tr("Solo con mejoras"))], multi=True, gold=True)
        self.upgrades_only.changed.connect(self.refresh)
        bar.addWidget(self.upgrades_only)
        self.search = SearchBox(f, tr("Buscar jefe, zona u objeto…"))
        self.search.setMinimumWidth(f.px(230))
        self._search_timer = QTimer(self, singleShot=True, interval=250, timeout=self.refresh)
        self.search.changed.connect(lambda _: self._search_timer.start())
        bar.addWidget(self.search)
        v.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(0, 0, f.px(8), 0)
        self.body.setSpacing(10)
        scroll.setWidget(holder)
        v.addWidget(scroll, 1)
        return page

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _set(self, key, value):
        self.tracker.set_option(key, value)
        self.refresh()

    def show_tab(self, key):
        """«calendar» / «list», a calendar view («day» / «week») or a tab of the list («marked», «field»...)."""
        if key in ("day", "week"):
            self.view, key = key, "calendar"
        elif key in dict(TABS):
            self.list_tab, key = key, "list"
        if key not in ("calendar", "list"):
            return
        self.page_tab = key
        self.tabs.set_current(key)
        self.view_tabs.set_current(self.view)
        self.list_tabs.set_current(self.list_tab)
        self.stack.setCurrentIndex(0 if key == "calendar" else 1)
        self._update_subtitle(time.time())
        if key == "calendar":
            self._refresh_calendar(scroll=self._scroll_mode())
        else:
            self._refresh_list()

    def _show_list_tab(self, key):
        self.list_tab = key
        self.refresh()

    # --- content ---------------------------------------------------------------------------------------
    def refresh(self):
        self._update_subtitle(time.time())
        if self.page_tab == "calendar":
            self._refresh_calendar(scroll="now" if self._first_cal else "keep")
        else:
            self._refresh_list()

    def _update_subtitle(self, now):
        region = self.tracker.region
        name = REGIONS.get(region, REGIONS["global-nae"])[0]
        there = server_clock(region, now).strftime("%H:%M")
        mine = time.strftime("%H:%M", time.localtime(now))
        parts = [tr("{0}: allí son las {1}", name, there)]
        if there != mine:
            parts.append(tr("tu hora {0}", mine))
        text = " · ".join(parts)
        if text != self._subtitle:
            self._subtitle = text
            self.header.set_subtitle(text)

    def _fetch_missing(self):
        missing = [p for p in self._wanted if self._image(p) is None]
        if missing:
            self.ctl.fetch_tracker_images(sorted(missing), self._images_arrived)

    # --- calendar ----------------------------------------------------------------------------------------
    def _scroll_mode(self):
        return "now" if self.view == "day" and self.day_offset == 0 else "top"

    def _set_view(self, key):
        self.view = key
        self._refresh_calendar(scroll=self._scroll_mode())

    def _go_day(self, offset):
        self.view = "day"
        self.day_offset = max(0, min(DAYS_AHEAD, offset))
        self.view_tabs.set_current("day")
        self._refresh_calendar(scroll=self._scroll_mode())

    def _refresh_calendar(self, force=True, scroll="keep"):
        """Build the calendar (Día or Semana) with the filters; force=False: only when what it shows changed."""
        now = time.time()
        self._cal_checked = now
        tracker = self.tracker
        if self.view == "day":
            ws, we = local_day(now, self.day_offset)
        else:
            ws, we = local_day(now)[0], local_day(now, DAYS_AHEAD)[1]
        today = ws <= now < we
        timed, unknown = tracker.schedule(ws, we, now)
        query = self.cal_search.text().strip().lower()
        marked_only = "marked" in self.marked_chip.selected()

        def keep(e):
            return (not marked_only or tracker.marked(e.id)) and (not query or query in _haystack(e))
        timed = [r for r in timed if keep(r[0])]
        unknown = [u for u in unknown if keep(u[0])] if today else []
        hidden_unknown = len(unknown) if self.view == "week" else 0      # the week has no row for them
        if self.view == "week":
            unknown = []
        counts = {k: set() for k, _ in CATEGORIES}
        for e in [r[0] for r in timed] + [u[0] for u in unknown]:
            counts[_category(e)].add(e.id)
            counts["all"].add(e.id)
        for k, text in CATEGORIES:
            self.cat_chips.set_label(k, f"{text} ({len(counts[k])})")
        cat = self.cat_chips.selected() or "all"
        if cat != "all":
            timed = [r for r in timed if _category(r[0]) == cat]
            unknown = [u for u in unknown if _category(u[0]) == cat]
            hidden_unknown = hidden_unknown if cat == "field" else 0
        marker = sum(1 for r in timed if r[1] <= now) if today else -1
        sig = (self.view, self.day_offset, round(ws), tracker.region, marked_only, cat, query, marker, hidden_unknown,
               tuple((r[0].id, round(r[1]), r[3].state, tracker.marked(r[0].id)) for r in timed),
               tuple((u[0].id, tracker.maybe_alive(u[0], now), tracker.marked(u[0].id)) for u in unknown))
        if not force and sig == self._cal_sig:
            return
        self._cal_sig = sig
        self._first_cal = False
        old = self.cal_scroll.verticalScrollBar().value()
        _clear(self.cal_body)
        self._cal_states = []
        self._now_line = None
        self._scroll_target = None
        self._wanted = set()
        self._update_day_nav(now, ws, we)
        if not timed and not unknown:
            self.cal_body.addWidget(self._empty_note(marked_only))
        elif self.view == "day":
            self._build_day(timed, unknown, ws, we, now, cat)
        else:
            self._build_week(timed, now)
        if hidden_unknown:
            self.cal_body.addSpacing(self.fonts.px(8))
            self.cal_body.addWidget(self._hint(tr("{0} jefes de campo sin hora conocida: están en la vista Día de hoy.",
                                                  hidden_unknown)))
        self.cal_body.addStretch(1)
        self._tick_calendar(now)
        self._fetch_missing()
        self._scroll_gen += 1
        gen = self._scroll_gen
        QTimer.singleShot(60, lambda: self._apply_scroll(gen, scroll, old))

    def _apply_scroll(self, gen, mode, old):
        if gen != self._scroll_gen:
            return
        bar = self.cal_scroll.verticalScrollBar()
        if mode == "keep":
            bar.setValue(old)
            return
        target = self._scroll_target if mode == "now" else None
        if target is None:
            bar.setValue(0)
            return
        try:
            y = target.mapTo(self.cal_holder, QPoint(0, 0)).y()
        except RuntimeError:               # rebuilt meanwhile
            return
        bar.setValue(max(0, y - self.fonts.px(48)))

    def _update_day_nav(self, now, ws, we):
        day = self.view == "day"
        self.prev_day.setVisible(day)
        self.next_day.setVisible(day)
        self.prev_day.setEnabled(self.day_offset > 0)
        self.next_day.setEnabled(self.day_offset < DAYS_AHEAD)
        self.today_btn.setVisible(day and self.day_offset > 0)
        if day:
            text = _date(ws)
            if self.day_offset == 0:
                text = f"{tr('Hoy')} · {text}"
            elif self.day_offset == 1:
                text = f"{tr('Mañana')} · {text}"
        else:
            text = f"{_date(ws)} – {_date(we - 1)}"
        self.day_title.setText(text)
        region = self.tracker.region
        differs = server_clock(region, now).strftime("%H:%M") != time.strftime("%H:%M", time.localtime(now))
        legend = ""
        if differs:
            legend = tr("Tu hora; en gris, la del servidor") if day else tr("Horas en tu reloj")
        self.legend.setText(legend)

    def _empty_note(self, marked_only):
        if marked_only and not self.tracker.config.get("marked"):
            text = tr("Aún no marcaste nada: pulsa la campana de una fila y AionDex te avisará antes de que salga.")
        elif self.view == "day":
            text = tr("Nada este día con estos filtros.")
        else:
            text = tr("Nada esta semana con estos filtros.")
        note = label(text, self.fonts.body, theme.MUTED)
        note.setWordWrap(True)
        note.setContentsMargins(0, self.fonts.px(12), 0, 0)
        return note

    def _build_day(self, timed, unknown, ws, we, now, cat):
        """A timeline by hours (empty hours folded into one line), «ahora» where now is, then the field bosses with no
        known time (today only)."""
        f = self.fonts
        today = ws <= now < we
        buckets = {}
        for r in timed:
            buckets.setdefault(time.localtime(max(r[1], ws)).tm_hour, []).append(r)
        now_hour = time.localtime(now).tm_hour if today else None
        t0 = time.localtime(ws)
        if timed:
            h = 0
            while h < 24:
                if h in buckets:
                    j = h + 1
                else:
                    j = h
                    while j < 24 and j not in buckets:
                        j += 1
                hts = time.mktime((t0.tm_year, t0.tm_mon, t0.tm_mday, h, 0, 0, 0, 0, -1))
                current = now_hour is not None and h <= now_hour < j
                past = now_hour is not None and j <= now_hour
                self.cal_body.addWidget(self._hour_block(hts, buckets.get(h), j, current, past, now))
                h = j
        else:
            self.cal_body.addWidget(self._hint(tr("Nada con hora este día con estos filtros.")))
        if unknown:
            self.cal_body.addSpacing(f.px(12))
            self.cal_body.addWidget(SectionTitle(tr("Jefes de campo sin hora conocida") + f" · {len(unknown)}", f))
            self.cal_body.addWidget(self._hint(tr("Su hora la manda el propio juego: abre el mapa (M) → monstruos de "
                                                  "campo y AionDex la recuerda; también apunta las muertes que ve.")))
            maps = {}
            for e, st in unknown:
                maps.setdefault(e.map_id, []).append((e, st))
            side = self.ctl.location_faction() or self.ctl.character_faction()
            for map_id in sorted(maps, key=lambda m: (0 if MAP_SIDE.get(m) == side else 1, m)):
                title = label(MAP_NAMES.get(map_id, f"Mapa {map_id}"), f.name, theme.TEXT)
                title.setContentsMargins(0, f.px(8), 0, f.px(4))
                self.cal_body.addWidget(title)
                grid = QGridLayout()
                grid.setHorizontalSpacing(f.px(6))
                grid.setVerticalSpacing(f.px(4))
                rows = sorted(maps[map_id], key=lambda u: (not self.tracker.maybe_alive(u[0], now), u[0].name))
                for i, (e, st) in enumerate(rows):
                    grid.addWidget(self._cal_row(e, None, None, st, now, unknown=True), i // 2, i % 2)
                grid.setColumnStretch(0, 1)
                grid.setColumnStretch(1, 1)
                self.cal_body.addLayout(grid)
        elif not today and cat in ("all", "field"):
            self.cal_body.addSpacing(f.px(10))
            self.cal_body.addWidget(self._hint(tr("Los jefes de campo reaparecen según cuándo los matan: su hora solo "
                                                  "se conoce para hoy.")))

    def _hour_block(self, hts, rows, end_hour, current, past, now):
        f = self.fonts
        block = QFrame()
        block.setObjectName("CalHour")
        lay = QHBoxLayout(block)
        lay.setContentsMargins(0, f.px(6), 0, f.px(6))
        lay.setSpacing(f.px(12))
        side = QWidget()
        side.setFixedWidth(f.px(58))
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        mine = time.strftime("%H:%M", time.localtime(hts))
        col.addWidget(label(mine, serif_font(f, 11), theme.ACCENT if current else theme.MUTED if past else theme.TEXT))
        there = server_clock(self.tracker.region, hts).strftime("%H:%M")
        if there != mine:
            col.addWidget(label(there, f.small, theme.MUTED))
        col.addStretch(1)
        lay.addWidget(side)
        right = QVBoxLayout()
        right.setSpacing(f.px(4))
        if rows:
            placed = False
            for r in rows:
                if current and not placed and r[1] > now:
                    right.addWidget(self._make_now_line(now))
                    placed = True
                right.addWidget(self._cal_row(*r, now=now))
            if current and not placed:
                right.addWidget(self._make_now_line(now))
        else:
            if current:
                right.addWidget(self._make_now_line(now))
            text = tr("Nada hasta las {0}", f"{end_hour:02d}:00") if end_hour < 24 else tr("Nada más este día")
            right.addWidget(label(text, f.small, theme.MUTED))
        lay.addLayout(right, 1)
        return block

    def _make_now_line(self, now):
        self._now_line = _NowLine(self.fonts)
        self._now_line.set_text(f"{tr('ahora')} {time.strftime('%H:%M', time.localtime(now))}")
        if self._scroll_target is None:
            self._scroll_target = self._now_line
        return self._now_line

    def _cal_row(self, entry, start, end, st, now, unknown=False):
        """A row of the day: time, picture, name and zone, category, live state, Mapa, bell; a click opens its card."""
        f = self.fonts
        cat = _category(entry)
        past = st.state == "pasado"
        live = "" if unknown else LIVE.get(st.state, "")
        marked = self.tracker.marked(entry.id)
        row = _Click(lambda e=entry: self._open_entry(e), "CalRow")
        row.setProperty("live", live)
        row.setProperty("past", past)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(f.px(8), f.px(3), f.px(6), f.px(3))
        lay.setSpacing(f.px(8))
        if not unknown:
            when = label(_span(start, end), f.name, theme.MUTED if past else theme.TEXT)
            when.setFixedWidth(f.px(84))
            lay.addWidget(when)
        lay.addWidget(self._row_icon(entry, cat))
        lay.addWidget(label(entry.name, f.name, theme.MUTED if past else theme.GOLD if marked else theme.TEXT))
        lay.addWidget(label(entry.sub, f.small, theme.MUTED, elide=True), 1)
        if unknown:
            maybe = self.tracker.maybe_alive(entry, now)
            state = label(tr("¿vivo?") if maybe else tr("abre la lista del mapa"), f.name if maybe else f.small,
                          theme.WARN if maybe else theme.MUTED)
        else:
            lay.addWidget(Chip(CAT_CHIP[cat], CAT_COLOR[cat], f))
            state = label("", f.name, theme.TEXT)
            self._cal_states.append((entry, start, end, state))
        state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        state.setMinimumWidth(f.px(72))
        lay.addWidget(state)
        if entry.kind != "event" and self.ctl.boss_on_map(entry):
            where = QPushButton(tr("Mapa"))
            where.setProperty("flat", True)
            where.setToolTip(tr("Abre el Mapa del mundo en este jefe, con su estado y tu posición"))
            where.clicked.connect(lambda _=False, e=entry: self.ctl.open_boss_map(e))
            lay.addWidget(where)
        lay.addWidget(self._bell(entry))
        tip = [entry.name, entry.sub, st.detail, tr("Clic: su ficha en la Lista")]
        row.setToolTip("\n".join(x for x in tip if x))
        if live and self._scroll_target is None:
            self._scroll_target = row
        return row

    def _row_icon(self, entry, cat):
        if entry.image:
            return self._tile(entry.image, ROW_ICON, theme.LINE, radius=4, fill_alpha=255)
        glyph = label(GLYPH_EVENT if entry.kind == "event" else GLYPH_SKULL, self._glyph, CAT_COLOR[cat])
        glyph.setFixedSize(ROW_ICON, ROW_ICON)
        glyph.setAlignment(Qt.AlignCenter)
        return glyph

    def _bell(self, entry):
        on = self.tracker.marked(entry.id)
        bell = QPushButton(GLYPH_BELL)
        bell.setProperty("calbell", True)
        bell.setCheckable(True)
        bell.setChecked(on)
        bell.setFont(self._glyph)
        bell.setCursor(Qt.PointingHandCursor)
        bell.setToolTip(tr("Te aviso antes de que salga · clic para dejar de avisar") if on
                        else tr("Avisarme antes de que salga"))
        bell.clicked.connect(lambda _=False, e=entry: self._toggle(e))
        return bell

    def _build_week(self, timed, now):
        f = self.fonts
        row = QHBoxLayout()
        row.setSpacing(f.px(6))
        for i in range(DAYS_AHEAD + 1):
            ds, de = local_day(now, i)
            items = [r for r in timed if r[1] < de and (i == 0 or r[1] >= ds)]
            row.addWidget(self._week_column(i, ds, items, now), 1)
        self.cal_body.addLayout(row)

    def _week_column(self, i, ds, items, now):
        """One day of the week: its name (a click opens it hour by hour), the things at a time, then the ones that
        repeat all day folded into one line each."""
        f = self.fonts
        col = QFrame()
        col.setObjectName("CalDay")
        col.setProperty("today", i == 0)
        v = QVBoxLayout(col)
        v.setContentsMargins(f.px(6), f.px(6), f.px(6), f.px(8))
        v.setSpacing(f.px(3))
        t = time.localtime(ds)
        head = _Click(lambda o=i: self._go_day(o), "CalDayHead")
        head.setToolTip(tr("Ver este día hora por hora"))
        hv = QVBoxLayout(head)
        hv.setContentsMargins(f.px(2), 0, 0, 0)
        hv.setSpacing(0)
        title = tr("Hoy") if i == 0 else tr("Mañana") if i == 1 else DAY_SHORT[t.tm_wday].capitalize()
        hv.addWidget(label(title, serif_font(f, 11), theme.ACCENT if i == 0 else theme.TEXT))
        hv.addWidget(label(_date(ds) if i < 2 else f"{t.tm_mday:02d}/{t.tm_mon:02d}", f.small, theme.MUTED))
        v.addWidget(head)
        v.addWidget(Divider(plain=True))
        groups = {}
        for r in items:
            groups.setdefault(r[0].id, []).append(r)
        for r in items:
            if len(groups[r[0].id]) < REPEAT:
                v.addWidget(self._week_row(r[0], [r], now))
        repeats = [g for g in groups.values() if len(g) >= REPEAT]
        if repeats:
            v.addSpacing(f.px(6))
            v.addWidget(label(tr("Todo el día").upper(), f.section, theme.MUTED))
            for g in repeats:
                v.addWidget(self._week_row(g[0][0], g, now))
        if not items:
            v.addWidget(label("—", f.small, theme.MUTED))
        v.addStretch(1)
        return col

    def _week_row(self, entry, occ, now):
        """A compact line of the week: time and name (one occurrence), or name and «cada hora» (one that repeats)."""
        f = self.fonts
        cat = _category(entry)
        live = next((LIVE[r[3].state] for r in occ if r[3].state in LIVE), "")
        past = all(r[3].state == "pasado" for r in occ)
        marked = self.tracker.marked(entry.id)
        row = _Click(lambda e=entry: self._open_entry(e), "WeekRow")
        row.setProperty("cat", cat)
        row.setProperty("live", live)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(f.px(5), f.px(2), f.px(2), f.px(2))
        lay.setSpacing(f.px(5))
        name_color = theme.MUTED if past else theme.GOLD if marked else theme.TEXT
        starts = [r[1] for r in occ]
        if len(occ) == 1:
            lay.addWidget(label(_hhmm(occ[0][1]), f.small, theme.MUTED), 0, Qt.AlignTop)
            name = label(entry.name, f.small, name_color)
            name.setWordWrap(True)
            lay.addWidget(name, 1)
            when = _span(occ[0][1], occ[0][2])
        else:
            text = QVBoxLayout()
            text.setSpacing(0)
            name = label(entry.name, f.small, name_color)
            name.setWordWrap(True)
            text.addWidget(name)
            text.addWidget(label(_cadence(starts), f.small, theme.MUTED))
            lay.addLayout(text, 1)
            when = " · ".join(_hhmm(s) for s in starts)
        if marked:
            bell = label(GLYPH_BELL, self.fonts.glyph, theme.GOLD)
            lay.addWidget(bell, 0, Qt.AlignTop)
        tip = [entry.name, entry.sub, when, tr("Te aviso antes de que salga") if marked else "",
               tr("Clic: su ficha en la Lista")]
        row.setToolTip("\n".join(x for x in tip if x))
        return row

    def _state_text(self, entry, start, end, now):
        st = occurrence_status(start, end, now, entry.maybe) if entry.slots else self.tracker.status(entry, now)
        if st.state == "pasado":
            return tr("pasó"), theme.MUTED
        return when_text(st, now)

    def _tick_calendar(self, now):
        for entry, start, end, lbl in self._cal_states:
            text, color = self._state_text(entry, start, end, now)
            if lbl.text() != text:
                lbl.setText(text)
            c = color.name()
            if getattr(lbl, "_color", None) != c:
                lbl._color = c
                lbl.setStyleSheet(f"color: {c}; background: transparent;")
        if self._now_line is not None:
            self._now_line.set_text(f"{tr('ahora')} {time.strftime('%H:%M', time.localtime(now))}")

    def _open_entry(self, entry):
        """A calendar row clicked: its card in the Lista (its tab, searched by name)."""
        self.list_tab = _category(entry)
        self.search.blockSignals(True)
        self.search.setText(entry.name)
        self.search.blockSignals(False)
        self.upgrades_only.blockSignals(True)
        self.upgrades_only.set_selected("up", False)
        self.upgrades_only.blockSignals(False)
        self.show_tab("list")

    # --- list --------------------------------------------------------------------------------------------
    def _refresh_list(self):
        self.list_tabs.set_current(self.list_tab)
        _clear(self.body)
        self._status_labels = []
        self._wanted = set()
        f, tracker = self.fonts, self.tracker
        self._load_gear()
        query = self.search.text().strip().lower()
        entries = [e for e in tracker.entries if not query or query in _haystack(e)]
        if "up" in self.upgrades_only.selected() and self._gear_state:
            entries = [e for e in entries if self._entry_upgrades(e)]
        if query and not _in_tab(self.list_tab, entries, tracker):
            elsewhere = [f"{text} ({n})" for key, text in TABS if (n := len(_in_tab(key, entries, tracker)))]
            self.body.addWidget(label(tr("Nada coincide con «{0}» en esta pestaña", self.search.text().strip())
                                      + (tr("; sí en: {0}.", ', '.join(elsewhere)) if elsewhere else "."), f.body, theme.MUTED))
        if self.list_tab == "marked":
            rows = _in_tab("marked", entries, tracker)
            if not rows and not query:
                self.body.addWidget(label(tr("Aún no marcaste nada. Busca en las otras pestañas y pulsa «Avisarme»."),
                                          f.body, theme.MUTED))
            self._grid(rows)
        elif self.list_tab == "field":
            maps = {}
            for e in _in_tab("field", entries, tracker):
                maps.setdefault(e.map_id, []).append(e)
            if not tracker.states:
                self.body.addWidget(self._hint(tr("Los tiempos de los jefes de campo los manda el propio juego: "
                                                  "abre una vez el mapa (M) → monstruos de campo y AionDex los "
                                                  "recuerda; también apunta las muertes que ve.")))
            side = self.ctl.location_faction() or self.ctl.character_faction()
            order = sorted(maps, key=lambda m: (0 if MAP_SIDE.get(m) == side else 1, m))
            for map_id in order:
                self.body.addWidget(SectionTitle(MAP_NAMES.get(map_id, f"Mapa {map_id}"), f))
                self._grid(sorted(maps[map_id], key=lambda e: self._sort_key(e)))
        elif self.list_tab == "abyss":
            self._grid(sorted(_in_tab("abyss", entries, tracker), key=self._sort_key))
        else:
            self._grid(sorted(_in_tab("event", entries, tracker), key=self._sort_key), events=True)
        self.body.addStretch(1)
        self._tick_list(time.time())
        self._fetch_missing()

    def _images_arrived(self, _=None):
        if self.isVisible():
            self.refresh()

    def _image(self, path):
        if not path:
            return None
        self._wanted.add(path)
        if path not in self._images:
            file = image_file(self.ctl.data_dir, path)
            pix = QPixmap(str(file)) if file.exists() else None
            if pix is None or pix.isNull():
                return None
            self._images[path] = pix
        return self._images[path]

    def _tile(self, path, size, color, radius=5, fill_alpha=55):
        """An image on a rounded tile tinted with its rarity colour (like the game's icons), or the empty tile."""
        dpr = self.devicePixelRatioF()
        canvas = QPixmap(round(size * dpr), round(size * dpr))
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(Qt.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(0.5, 0.5, size - 1, size - 1)
        shape = QPainterPath()
        shape.addRoundedRect(rect, radius, radius)
        tint = QColor(color)
        tint.setAlpha(fill_alpha)
        p.fillPath(shape, tint)
        img = self._image(path)
        if img is not None:
            p.setClipPath(shape)
            scaled = img.scaled(round(size * dpr), round(size * dpr), Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            x = (size - scaled.width() / dpr) / 2
            y = (size - scaled.height() / dpr) / 2
            p.drawPixmap(QRectF(x, y, scaled.width() / dpr, scaled.height() / dpr), scaled,
                         QRectF(0, 0, scaled.width(), scaled.height()))
            p.setClipping(False)
        border = QColor(color)
        border.setAlpha(170)
        p.setPen(QPen(border, 1))
        p.drawPath(shape)
        p.end()
        tile = QLabel()
        tile.setPixmap(canvas)
        tile.setFixedSize(size, size)
        tile.setAttribute(Qt.WA_TransparentForMouseEvents)
        tile.setStyleSheet("background: transparent;")
        return tile

    def _sort_key(self, entry):
        st = self.tracker.status(entry)
        if st.state in ("vivo", "en curso"):
            return 0
        return st.start or float("inf")

    def _grid(self, entries, events=False):
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for i, entry in enumerate(entries):
            grid.addWidget(self._card(entry), i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)

    def _hint(self, text):
        hint = label(text, self.fonts.small, theme.MUTED)
        hint.setWordWrap(True)
        return hint

    def _achievement(self, entry):
        """«Logro pendiente: título» when the game has a field-boss achievement for this boss you have not claimed."""
        if entry.kind == "event":
            return ""
        ctl = self.ctl
        ms = ctl.achievements
        name = ctl.missions_character(ms)
        if not ms.available or not name:
            return ""
        for m in ms.db.values():
            if "fieldboss" not in (m.where or []):
                continue
            names = {m.name, (m.asmodian or {}).get("name", "")}
            if entry.name not in names and entry.name not in (m.desc or "") and \
                    entry.name not in (m.asmodian or {}).get("desc", ""):
                continue
            p = ms.progress(name, m.id)
            if p.done:
                return ""
            return tr("Logro por cobrar: {0}", m.rewards.split(" Superior")[0] if m.rewards else m.name) if p.ready \
                else tr("Logro pendiente: {0}", m.rewards.split(" Superior")[0] if m.rewards else m.name)
        return ""

    def _card(self, entry):
        """A card like the game's boss tooltip: header with emblem and name, time left, loot highlights."""
        f = self.fonts
        card = QFrame()
        card.setObjectName("Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(0, 0, 0, 10)
        col.setSpacing(4)

        head = QFrame()
        head.setObjectName("CardHead")
        row = QHBoxLayout(head)
        row.setContentsMargins(12, 10, 12, 10)
        if entry.image:
            row.addWidget(self._tile(entry.image, PORTRAIT, "#4A5468", radius=8, fill_alpha=255))
        else:
            emblem_font = QFont(f.icons_family)
            emblem_font.setPixelSize(26)
            row.addWidget(label(GLYPH_EVENT if entry.kind == "event" else GLYPH_SKULL, emblem_font,
                                QColor("#C9D1E0")))
        names = QVBoxLayout()
        names.setSpacing(0)
        title_font = QFont(f.name_big)
        title_font.setPixelSize(19)
        names.addWidget(label(entry.name, title_font, QColor("#FFFFFF"), elide=True))
        sub = " · ".join(x for x in (tr("Nv. {0}", entry.level) if entry.level else "", entry.sub) if x)
        if sub:
            names.addWidget(label(sub, f.small, QColor("#AEB6C6"), elide=True))
        pending = self._achievement(entry)
        if pending:
            names.addWidget(label(pending, f.small, theme.GOLD, elide=True))
        row.addLayout(names, 1)
        if entry.kind != "event" and self.ctl.boss_on_map(entry):
            where = QPushButton(tr("Mapa"))
            where.setProperty("bell", True)
            where.setToolTip(tr("Abre el Mapa del mundo en este jefe, con su estado y tu posición"))
            where.clicked.connect(lambda _=False, e=entry: self.ctl.open_boss_map(e))
            row.addWidget(where)
        bell = QPushButton(tr("Avisarme"))
        bell.setProperty("bell", True)
        bell.setCheckable(True)
        bell.setChecked(self.tracker.marked(entry.id))
        bell.setText(tr("Te aviso") if bell.isChecked() else tr("Avisarme"))
        bell.clicked.connect(lambda _=False, e=entry: self._toggle(e))
        row.addWidget(bell)
        col.addWidget(head)

        body = QWidget()                  # margins on a container: a label's own margins get reset when restyled
        inner = QVBoxLayout(body)
        inner.setContentsMargins(14, 4, 14, 0)
        inner.setSpacing(4)
        col.addWidget(body)
        status = label("", f.name, theme.TEXT)
        status.setWordWrap(True)
        inner.addWidget(status)
        self._status_labels.append((entry, status))
        upgrades = self._entry_upgrades(entry)
        if upgrades:
            mine = label(tr("↑ Para {0}: mejora tu {1}", self._gear_name, ', '.join(upgrades).lower()), f.small,
                         theme.GAIN)
            mine.setWordWrap(True)
            inner.addWidget(mine)

        if entry.kind == "event":
            nexts = [s for s, _ in occurrences(entry.slots, "kst" if entry.clock == "kst" else self.tracker.region,
                                                time.time()) if s > time.time()][:4]
            if nexts:
                times = " · ".join(time.strftime("%H:%M", time.localtime(s)) for s in nexts)
                more = label(tr("Próximas (tu hora): {0}", times), f.small, theme.MUTED)
                inner.addWidget(more)
        if entry.respawn_min:
            again = label(tr("Reaparece {0} después de morir", _duration(entry.respawn_min)), f.small, theme.MUTED)
            inner.addWidget(again)
        if entry.drops:
            inner.addSpacing(4)
            inner.addWidget(label(tr("Botín destacado"), f.small, QColor("#89B7BF")))
            for drop in entry.drops[:DROPS_SHOWN]:
                color = RANK_COLOR.get(drop.get("rank"), "#D6DAE3")
                line = QWidget()
                lay = QHBoxLayout(line)
                lay.setContentsMargins(0, 1, 0, 1)
                lay.setSpacing(8)
                lay.addWidget(self._tile(drop.get("icon"), ITEM_ICON, color))
                lay.addWidget(label(_drop_text(drop), f.body, QColor(color), elide=True), 1)
                verdicts = self._drop_verdicts(drop)
                chip = self._chip(verdicts)
                if chip is not None:
                    lay.addWidget(chip)
                if drop.get("items"):
                    line.setToolTip(self._pieces_tip(drop, verdicts))
                elif verdicts:
                    _, item, v = verdicts[0]
                    line.setToolTip(tr("<b>{0}</b> · nivel de objeto {1} · {2}<br>{3}", item.name, item.level, GRADE_ES.get(item.grade, item.grade), v.text))
                inner.addWidget(line)
            if len(entry.drops) > DROPS_SHOWN:
                rest = label(tr("y {0} más", len(entry.drops) - DROPS_SHOWN), f.small, theme.MUTED)
                rest.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                rest.setToolTip("<br>".join(_drop_text(d) for d in entry.drops[DROPS_SHOWN:]))
                inner.addWidget(rest)
        return card

    def _pieces_tip(self, drop, verdicts=()):
        """The pieces of a set with their icon and chance (one of them drops), and what each would do for you."""
        by_name = {piece: v for piece, _, v in verdicts}
        rows = []
        for piece in drop["items"]:
            file = image_file(self.ctl.data_dir, piece["icon"]) if piece.get("icon") else None
            icon = f'<img src="{file.as_uri()}" width="22" height="22">' if file and file.exists() else ""
            v = by_name.get(piece["name"])
            mark, color = VERDICT_MARK.get(v.kind, ("", "")) if v else ("", "")
            detail = (f" ({v.mine.name} +{v.mine_enchant}: {v.mine_level})" if v and v.mine and v.kind == "upgrade"
                      else "")
            rows.append(f"<tr><td>{icon}</td><td>{piece['name']}</td><td align='right'>&nbsp;{piece['chance']:g}%</td>"
                        f"<td style='color:{color}'>&nbsp;{mark}{detail}</td></tr>")
        head = tr("<b>Sale una de estas piezas</b>")
        if verdicts:
            head += tr(" · comparado con el equipo de {0}", self._gear_name)
        return f"{head}<table>{''.join(rows)}</table>"

    # --- your gear -------------------------------------------------------------------------------------
    def _load_gear(self):
        """The drops are compared with the gear of the page's character (the AionDex window's picker), when AionDex
        has read it."""
        saved = self.ctl.settings.get("gear", {})
        name = self.ctl.focus_character()
        self._gear_name = name if name in saved else None
        self._gear_state = saved.get(name) if self._gear_name else None
        self._verdicts = {}
        has = self._gear_state is not None
        self.upgrades_only.setVisible(has)
        if not has:
            self.upgrades_only.blockSignals(True)
            self.upgrades_only.set_selected("up", False)
            self.upgrades_only.blockSignals(False)
            return
        self.upgrades_only.set_label("up", tr("Solo con mejoras para {0}", name))
        when = time.strftime("%d/%m %H:%M", time.localtime(self._gear_state.get("at", 0)))
        self.upgrades_only.buttons["up"].setToolTip(
            tr("Solo los jefes que sueltan algo mejor que lo que lleva tu personaje") + "\n" +
            tr("Equipo de {0} leído el {1}. Se actualiza al entrar con el personaje (los anillos, el cinturón y demás "
               "piezas que no se ven solo al entrar; lo visible, cada minuto).", name, when))

    def _drop_verdicts(self, drop):
        """[(piece name, Item, Verdict)] of the equipment in a drop (each piece of a set), vs your gear."""
        if not self._gear_state:
            return []
        if drop["name"] not in self._verdicts:
            db, gear = self.ctl.gear_db, self._gear_state
            out = []
            for name in [i["name"] for i in drop.get("items", [])] or [drop["name"]]:
                item = db.named(name, gear.get("faction"))
                if item is not None:
                    out.append((name, item, verdict(item, gear, db)))
            self._verdicts[drop["name"]] = out
        return self._verdicts[drop["name"]]

    def _entry_upgrades(self, entry):
        """The slots (Casco, Anillo...) that something this boss drops would upgrade for you."""
        slots = []
        for drop in entry.drops:
            for _, _, v in self._drop_verdicts(drop):
                if v.kind == "upgrade" and v.slot not in slots:
                    slots.append(v.slot)
        return slots

    def _chip(self, verdicts):
        ups = sum(1 for *_, v in verdicts if v.kind == "upgrade")
        bases = sum(1 for *_, v in verdicts if v.kind == "base")
        if not verdicts or not (ups or bases):
            return None
        if len(verdicts) == 1:
            text = tr("↑ Mejora") if ups else tr("≈ Mejor base")
        else:
            text = tr("↑ {0} de {1}", ups, len(verdicts)) if ups else tr("≈ {0} de {1}", bases, len(verdicts))
        color = QColor(VERDICT_MARK["upgrade" if ups else "base"][1])
        chip = QLabel(text)
        chip.setFont(self.fonts.small)
        chip.setStyleSheet(CHIP_STYLE.format(c=color.name(), r=color.red(), g=color.green(), b=color.blue()))
        chip.setAttribute(Qt.WA_TransparentForMouseEvents)
        return chip

    def _toggle(self, entry):
        self.tracker.toggle(entry.id)
        self.refresh()
        self.ctl.refresh()

    def tick(self):
        now = time.time()
        self._update_subtitle(now)
        if self.page_tab == "calendar":
            if now - self._cal_checked >= 15:
                self._refresh_calendar(force=False)
            self._tick_calendar(now)
        else:
            self._tick_list(now)

    def _tick_list(self, now):
        for entry, status in self._status_labels:
            st = self.tracker.status(entry, now)
            if st.state == "vivo":
                text, color = tr("Vivo") + (tr(" · desde hace {0}", countdown(now - st.start)) if st.start else ""), theme.GAIN
            elif st.state == "en curso":
                text, color = tr("En curso · termina en {0}", countdown(st.end - now)), theme.GAIN
            elif st.state == "quizá":
                text, color = tr("Quizá ahora · hasta dentro de {0} (no sale todas las horas)", countdown(st.end - now)), \
                    theme.WARN
            elif st.state == "debería estar":
                text, color = tr("Ya debería haber salido (sin confirmar)"), theme.WARN
            elif st.start is not None:
                text = tr("Sale en {0} · {1}", countdown(st.start - now), st.detail)
                color = theme.TEXT
            else:
                text, color = st.detail, theme.MUTED
            status.setText(text)
            status.setStyleSheet(f"color: {color.name()}; background: transparent;")


def _category(entry):
    """The calendar's category of an entry: «event», «abyss» (Abyss bosses and Argo's field slot) or «field»."""
    if entry.kind == "event":
        return "event"
    if entry.kind == "abyss" or entry.map_id == 20:
        return "abyss"
    return "field"


def _hhmm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def _span(start, end):
    return f"{_hhmm(start)}–{_hhmm(end)}" if end and end > start else _hhmm(start)


def _date(ts):
    t = time.localtime(ts)
    return f"{DAY_SHORT[t.tm_wday]} {t.tm_mday:02d}/{t.tm_mon:02d}"


def _cadence(starts):
    """«cada hora», «cada 3 h» or «8 veces» for the starts of something that repeats in a day."""
    gaps = {round(b - a) for a, b in zip(starts, starts[1:])}
    if len(gaps) == 1:
        gap = gaps.pop()
        if gap == 3600:
            return tr("cada hora")
        if gap % 3600 == 0:
            return tr("cada {0} h", gap // 3600)
    return tr("{0} veces", len(starts))


def _in_tab(tab, entries, tracker):
    """The entries a tab shows (marked ones in the order they come)."""
    if tab == "marked":
        return [e for e, _ in tracker.upcoming() if e in entries]
    if tab == "field":
        return [e for e in entries if e.kind == "field" and e.map_id != 20]
    if tab == "abyss":
        return [e for e in entries if e.kind == "abyss" or (e.kind == "field" and e.map_id == 20)]
    return [e for e in entries if e.kind == "event"]


def _drop_text(drop):
    chance = drop.get("chance")
    return drop["name"] + (f"  ({chance:g}%)" if isinstance(chance, (int, float)) and chance < 100 else "")


def _duration(minutes):
    h, m = divmod(minutes, 60)
    return f"{h} h" + (f" {m} min" if m else "") if h else f"{m} min"


def _haystack(entry):
    """What the search looks at: name, zone and every drop (and the pieces of each set)."""
    parts = [entry.name, entry.sub]
    for d in entry.drops:
        parts.append(d["name"])
        parts.extend(i["name"] for i in d.get("items", []))
    return " ".join(parts).lower()


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()            # gone at once, not when the deferred delete runs: no blink
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
