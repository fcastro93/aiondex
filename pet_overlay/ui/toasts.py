"""Notification bubbles shown beside the overlay (never on top of it): max 3 stacked, 5 s each."""
from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, \
    QVariantAnimation
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..paths import ASSETS_DIR
from . import theme
from .widgets import label, pet_pixmap

DURATION_MS = 5000
MAX_VISIBLE = 3
GAP = 10          # between overlay and bubbles
SPACING = 6       # between bubbles
TAIL = 7          # size of the bubble's pointer toward the overlay

KIND_COLOR = {"progress": theme.GAIN, "levelup": theme.GOLD, "unlock": theme.GOLD, "zone": theme.ACCENT,
              "sync": theme.ACCENT, "info": theme.ACCENT, "warn": theme.WARN,
              "combat": theme.COMBAT, "tracker": theme.GOLD, "status": theme.WARN}


def kind_color(kind):
    return KIND_COLOR.get(kind, theme.ACCENT)


class Toast(QWidget):
    def __init__(self, notice, fonts, icon, manager):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.fonts = fonts
        self.manager = manager
        self.notice = notice
        self.tail_side = "left"
        self.remaining = 1.0
        self.closing = False
        self.setFixedWidth(fonts.px(290) + TAIL)

        row = QHBoxLayout(self)
        row.setContentsMargins(fonts.px(14) + TAIL, fonts.px(9), fonts.px(12) + TAIL, fonts.px(11))
        row.setSpacing(fonts.px(10))
        self.icon = QLabel()
        self.icon.setFixedSize(fonts.px(34), fonts.px(34))
        row.addWidget(self.icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(fonts.px(1))
        self.title = label("", fonts.name, theme.TEXT)
        self.title.setWordWrap(True)
        self.body = label("", fonts.small, theme.TEXT)
        self.body.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.body)
        row.addLayout(col, 1)
        self.set_notice(notice, icon)

        self._timer = QTimer(self, singleShot=True, timeout=lambda: self.manager.dismiss(self))
        self._countdown = QVariantAnimation(self, startValue=1.0, endValue=0.0, duration=DURATION_MS)
        self._countdown.valueChanged.connect(self._tick)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_done = None
        self._fade.finished.connect(lambda: self._fade_done and self._fade_done())
        self._move = QPropertyAnimation(self, b"pos", self)
        self._move.setDuration(180)
        self._move.setEasingCurve(QEasingCurve.OutCubic)

    def set_notice(self, notice, icon=None):
        self.notice = notice
        self.title.setText(notice.title)
        self.title.setStyleSheet(f"color: {(theme.GOLD if notice.kind in ('levelup', 'unlock') else theme.TEXT).name()};"
                                 " background: transparent;")
        self.body.setText(notice.body)
        self.body.setVisible(bool(notice.body))
        if icon is not None:
            self.icon.setPixmap(icon)
        self.adjustSize()
        self.update()

    def start(self):
        self.setWindowOpacity(0.0)
        self.show()
        self._fade_to(1.0, 160)
        self.restart()

    def restart(self):
        """Show for the full duration again (a merged update arrived)."""
        self._timer.start(DURATION_MS)
        self._countdown.stop()
        self._countdown.start()

    def close_animated(self):
        if self.closing:
            return
        self.closing = True
        self._timer.stop()
        self._fade_to(0.0, 260, on_done=self.deleteLater)

    def glide_to(self, point, animate=True):
        if not animate or not self.isVisible():
            self._move.stop()
            self.move(point)
            return
        self._move.stop()
        self._move.setStartValue(self.pos())
        self._move.setEndValue(point)
        self._move.start()

    def _fade_to(self, value, ms, on_done=None):
        self._fade.stop()
        self._fade.setDuration(ms)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(value)
        self._fade_done = on_done
        self._fade.start()

    def _tick(self, value):
        self.remaining = value
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = kind_color(self.notice.kind)
        body = QRectF(self.rect()).adjusted(TAIL + 0.5, 0.5, -TAIL - 0.5, -0.5)
        radius = self.fonts.px(10)

        path = QPainterPath()
        path.addRoundedRect(body, radius, radius)
        ty = min(body.top() + self.fonts.px(22), body.center().y())
        tail = QPainterPath()
        if self.tail_side == "left":
            tail.moveTo(body.left() + 1, ty - TAIL)
            tail.lineTo(body.left() - TAIL + 1, ty)
            tail.lineTo(body.left() + 1, ty + TAIL)
        else:
            tail.moveTo(body.right() - 1, ty - TAIL)
            tail.lineTo(body.right() + TAIL - 1, ty)
            tail.lineTo(body.right() - 1, ty + TAIL)
        tail.closeSubpath()
        shape = path.united(tail)

        p.fillPath(shape, QColor(*theme.PANEL_RGB, 242))
        border = QColor(color)
        border.setAlpha(150)
        p.setPen(QPen(border, 1))
        p.drawPath(shape)

        # accent stripe and the 5-second countdown line
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        stripe = QRectF(body.left() + self.fonts.px(5), body.top() + self.fonts.px(9), 3,
                        body.height() - self.fonts.px(18))
        p.drawRoundedRect(stripe, 1.5, 1.5)
        line = QColor(color)
        line.setAlpha(170)
        p.setBrush(line)
        w = (body.width() - 2 * radius) * self.remaining
        p.drawRoundedRect(QRectF(body.left() + radius, body.bottom() - 4, w, 2), 1, 1)


class ToastManager(QObject):
    def __init__(self, center, anchor, fonts, icons_dir, enabled):
        """anchor: the overlay window (bubbles sit beside it); enabled(): settings switch."""
        super().__init__()
        self.anchor = anchor
        self.fonts = fonts
        self.icons_dir = icons_dir
        self.enabled = enabled
        self.toasts = []                       # newest first
        center.posted.connect(self._posted)
        center.updated.connect(self._updated)

    # --- events -------------------------------------------------------------------------------
    def _posted(self, notice):
        if not self.enabled():
            return
        toast = Toast(notice, self.fonts, self._icon(notice), self)
        self.toasts.insert(0, toast)
        while len(self.toasts) > MAX_VISIBLE:
            self.dismiss(self.toasts[-1], relayout=False)
        self.relayout(new=toast)
        toast.start()

    def _updated(self, notice):
        toast = next((t for t in self.toasts if t.notice.id == notice.id and not t.closing), None)
        if toast is None:
            self._posted(notice)
            return
        toast.set_notice(notice)
        toast.restart()
        self.relayout()

    def dismiss(self, toast, relayout=True):
        if toast in self.toasts:
            self.toasts.remove(toast)
        toast.close_animated()
        if relayout:
            self.relayout()

    # --- placement ------------------------------------------------------------------------
    def relayout(self, new=None, animate=True):
        if not self.toasts:
            return
        frame = self.anchor.frameGeometry()
        screen = (QGuiApplication.screenAt(frame.center()) or QGuiApplication.primaryScreen()).availableGeometry()
        width = self.toasts[0].width()
        left_x = frame.left() - GAP - width + TAIL
        right_x = frame.right() + GAP - TAIL
        prefer_left = frame.center().x() > screen.center().x()
        if prefer_left and left_x < screen.left():
            prefer_left = False
        elif not prefer_left and right_x + width > screen.right():
            prefer_left = True
        x = left_x if prefer_left else right_x
        y = max(screen.top(), frame.top())
        for toast in self.toasts:
            toast.tail_side = "right" if prefer_left else "left"
            if y + toast.height() > screen.bottom():
                y = screen.bottom() - toast.height()
            toast.glide_to(QPoint(x, y), animate=animate and toast is not new)
            toast.update()
            y += toast.height() + SPACING

    def _icon(self, notice):
        size = self.fonts.px(34)
        dpr = self.anchor.devicePixelRatioF()
        if notice.pet_id is not None:
            px = pet_pixmap(self.icons_dir, notice.pet_id, size, dpr)
            if px is not None:
                return _rounded(px, size, dpr)
        px = QPixmap(str(ASSETS_DIR / "badge.png")).scaled(size * dpr, size * dpr, Qt.KeepAspectRatio,
                                                            Qt.SmoothTransformation)
        px.setDevicePixelRatio(dpr)
        return px


def _rounded(px, size, dpr):
    out = QPixmap(int(size * dpr), int(size * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    p.setClipPath(clip)
    p.drawPixmap(QPointF(0, 0), px)
    p.end()
    return out
