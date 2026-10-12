"""Overlay panel of the Notificaciones module: history of notices, newest first."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import theme
from .toasts import kind_color
from .widgets import label, pet_pixmap


KIND_LABEL = {"progress": "Progreso de pet", "levelup": "Subida de nivel", "unlock": "Pet nuevo",
              "zone": "Zona", "sync": "Colección", "info": "Aviso", "warn": "Atención",
              "combat": "Combate", "tracker": "Tracker"}


def ago(ts, now=None):
    seconds = int((now or time.time()) - ts)
    if seconds < 60:
        return "ahora"
    if seconds < 3600:
        return f"hace {seconds // 60} min"
    if seconds < 86400:
        return f"hace {seconds // 3600} h"
    return f"hace {seconds // 86400} d"


class NotificationsPanel(QWidget):
    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(2))

    def render(self, notices, total, unread_ids):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        f = self.fonts
        if not notices:
            self.content.addWidget(label("Sin notificaciones todavía", f.body, theme.MUTED))
        for n in notices:
            self.content.addWidget(self._row(n, n.id in unread_ids))
        if total > len(notices):
            more = label(f"y {total - len(notices)} más (expande para ver más)", f.small, theme.MUTED)
            more.setContentsMargins(f.px(4), f.px(2), 0, 0)
            self.content.addWidget(more)
        if notices:
            clear = label("Limpiar historial", f.small, theme.ACCENT)
            clear.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            clear.setCursor(Qt.PointingHandCursor)
            clear.on_click = self.ctl.notifications.clear     # shell runs on_click on a plain click
            clear.setContentsMargins(f.px(4), f.px(6), 0, 0)
            self.content.addWidget(clear, 0, Qt.AlignLeft)
        self.adjustSize()

    def _row(self, n, unread):
        f = self.fonts
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(f.px(4), f.px(4), f.px(4), f.px(4))
        lay.setSpacing(f.px(8))
        dot = label("●", f.small, kind_color(n.kind) if unread else theme.BORDER)
        lay.addWidget(dot, 0, Qt.AlignTop)
        if n.pet_id is not None:
            px = pet_pixmap(self.ctl.icons_dir, n.pet_id, f.px(26), self.devicePixelRatioF())
            if px is not None:
                icon = QLabel()
                icon.setPixmap(px)
                icon.setAttribute(Qt.WA_TransparentForMouseEvents)
                lay.addWidget(icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(f.px(1))
        title_color = theme.GOLD if n.kind in ("levelup", "unlock") else theme.TEXT
        title = label(n.title, f.name, title_color)
        title.setWordWrap(True)
        col.addWidget(title)
        if n.body:
            body = label(n.body, f.small, theme.TEXT)
            body.setWordWrap(True)
            col.addWidget(body)
        meta = f"{KIND_LABEL.get(n.kind, 'Aviso')} · {time.strftime('%H:%M', time.localtime(n.updated))} · {ago(n.updated)}"
        col.addWidget(label(meta, f.small, kind_color(n.kind) if unread else theme.MUTED))
        lay.addLayout(col, 1)
        if n.pet_id is not None:
            row.on_click = lambda pid=n.pet_id: self.ctl.open_map(pid)
        return row
