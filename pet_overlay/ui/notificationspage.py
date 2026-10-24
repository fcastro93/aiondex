"""Sistema › Notificaciones, the page of the AionDex window: the whole history (the HUD shows the last few), one
row per notice with its icon, title, text and when, grouped by day, with filters by what they are about (the
Tracker, Status, your pets, missions and achievements, problems) and a search. A click opens the page the notice is
about (a pet: its map). Opening the page marks everything read."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from . import theme
from .design import ChipGroup, PageHeader, SearchBox
from .notifications_panel import KIND_LABEL, ago
from .toasts import kind_color
from .widgets import label, pet_pixmap
from ..i18n import tr

GROUPS = (("all", tr("Todas"), lambda n: True),
          ("tracker", "Tracker", lambda n: n.kind == "tracker"),
          ("status", "Status", lambda n: n.kind == "status"),
          ("pets", tr("Pets"), lambda n: n.kind in ("progress", "levelup", "unlock", "sync")),
          ("progress", tr("Misiones y logros"), lambda n: n.page in ("missions", "achievements", "events")),
          ("warn", tr("Problemas"), lambda n: n.kind == "warn"),
          ("other", tr("Otros"), lambda n: n.kind in ("info", "zone", "combat")
           and n.page not in ("missions", "achievements", "events")))
ROW_STYLE = ("QFrame#NoticeRow { background: rgba(18,22,31,0.9); border: 1px solid #1e2532; border-radius: 5px; }"
             "QFrame#NoticeRow:hover { border-color: #46506a; }"
             "QFrame#NoticeRow[unread=\"true\"] { border-left: 3px solid %s; }")


def _day_title(ts, now):
    today = time.localtime(now)
    then = time.localtime(ts)
    if (then.tm_year, then.tm_yday) == (today.tm_year, today.tm_yday):
        return tr("Hoy")
    if (then.tm_year, then.tm_yday) == (today.tm_year, today.tm_yday - 1):
        return tr("Ayer")
    return time.strftime("%d/%m", then)


class _Row(QFrame):
    """One notice: a colored mark, the pet's portrait when it is about one, title, text, and when."""

    def __init__(self, ctl, fonts, notice, parent=None):
        super().__init__(parent)
        self.setObjectName("NoticeRow")
        self.ctl = ctl
        self.notice = notice
        color = kind_color(notice.kind)
        self.setStyleSheet(ROW_STYLE % color.name())
        self.setProperty("unread", not notice.read)
        f = fonts
        lay = QHBoxLayout(self)
        lay.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
        lay.setSpacing(f.px(10))
        if notice.pet_id is not None:
            px = pet_pixmap(ctl.icons_dir, notice.pet_id, f.px(34), self.devicePixelRatioF())
            if px is not None:
                icon = QLabel()
                icon.setPixmap(px)
                icon.setFixedSize(f.px(34), f.px(34))
                icon.setAttribute(Qt.WA_TransparentForMouseEvents)
                lay.addWidget(icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(f.px(2))
        head = QHBoxLayout()
        head.setSpacing(f.px(8))
        title_color = theme.GOLD if notice.kind in ("levelup", "unlock", "tracker") else \
            theme.COMBAT if notice.kind == "warn" else theme.TEXT
        title = label(notice.title, f.name, title_color)
        title.setWordWrap(True)
        head.addWidget(title, 1)
        head.addWidget(label(time.strftime("%H:%M", time.localtime(notice.updated)), f.small, theme.MUTED), 0,
                       Qt.AlignTop)
        col.addLayout(head)
        if notice.body:
            body = label(notice.body, f.body, theme.TEXT)
            body.setWordWrap(True)
            col.addWidget(body)
        meta = f"{KIND_LABEL.get(notice.kind, tr('Aviso'))} · {ago(notice.updated)}"
        if notice.page or notice.pet_id is not None:
            meta += tr(" · clic para abrir")
        col.addWidget(label(meta, f.small, color if not notice.read else theme.MUTED))
        lay.addLayout(col, 1)
        target = notice.page or (notice.pet_id is not None)
        if target:
            self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self.rect().contains(event.position().toPoint()):
            return
        n = self.notice
        if n.pet_id is not None and n.page in (None, "collection", "map"):
            self.ctl.open_map(n.pet_id)
        elif n.page:
            self.ctl.open_hub(n.page)


class NotificationsPage(QWidget):
    live = True                  # the hub refreshes it with the rest (every 10 s and on each new notice)

    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        f = fonts
        root = QVBoxLayout(self)
        root.setContentsMargins(f.px(22), f.px(16), f.px(22), f.px(16))
        root.setSpacing(f.px(10))
        self.header = PageHeader(f, tr("Notificaciones"),
                                 tr("Lo importante que pasó: avisos del Tracker y de Status, tus pets, misiones y "
                                    "logros, problemas. Los globos de cada +1 no se guardan."))
        read_all = QPushButton(tr("Marcar todo como leído"))
        read_all.setProperty("flat", True)
        read_all.clicked.connect(lambda: (ctl.notifications.mark_all_read(), self.refresh()))
        self.header.add_right(read_all)
        clear = QPushButton(tr("Limpiar historial"))
        clear.setProperty("ghost", True)
        clear.clicked.connect(lambda: (ctl.clear_notifications(), self.refresh()))
        self.header.add_right(clear)
        root.addWidget(self.header)
        bar = QHBoxLayout()
        bar.setSpacing(f.px(10))
        self.filter = ChipGroup(f, [(k, t) for k, t, _ in GROUPS], "all")
        self.filter.changed.connect(self.refresh)
        bar.addWidget(self.filter, 1)
        self.search = SearchBox(f, tr("Buscar en las notificaciones…"))
        self.search.changed.connect(lambda _t: self.refresh())
        bar.addWidget(self.search)
        root.addLayout(bar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.list = QVBoxLayout(holder)
        self.list.setContentsMargins(0, 0, f.px(6), 0)
        self.list.setSpacing(f.px(6))
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        self._stamp = None

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        QTimer.singleShot(600, self.ctl.notifications.mark_all_read)   # you are looking at them now

    def refresh(self):
        from .upgradeswindow import _clear
        f, center = self.fonts, self.ctl.notifications
        chosen = self.filter.selected()
        pick = next((fn for k, _, fn in GROUPS if k == chosen), GROUPS[0][2])
        query = self.search.text().strip().lower()
        notices = [n for n in center.recent(len(center.history))
                   if pick(n) and (not query or query in f"{n.title} {n.body}".lower())]
        stamp = (tuple((n.id, n.updated, n.read) for n in notices), self.filter.selected(), query)
        if stamp == self._stamp:
            return
        self._stamp = stamp
        _clear(self.list)
        for key, text, fn in GROUPS:
            n = sum(1 for x in center.history if fn(x))
            self.filter.set_label(key, f"{text} ({n})" if n else text)
        if not notices:
            empty = label(tr("Nada por aquí.") if query or chosen != "all" else
                          tr("Sin notificaciones todavía: aquí irán los avisos del Tracker y de Status, tus pets al "
                             "máximo, las misiones y los logros que termines, y cualquier problema."),
                          f.body, theme.MUTED)
            empty.setWordWrap(True)
            self.list.addWidget(empty)
            self.list.addStretch(1)
            return
        now = time.time()
        day = None
        for n in notices:
            title = _day_title(n.updated, now)
            if title != day:
                day = title
                head = label(title.upper(), f.section, theme.MUTED)
                head.setContentsMargins(f.px(4), f.px(6), 0, 0)
                self.list.addWidget(head)
            self.list.addWidget(_Row(self.ctl, f, n))
        self.list.addStretch(1)
