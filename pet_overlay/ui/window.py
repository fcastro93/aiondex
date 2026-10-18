"""Overlay shell: translucent panel with the app menu button, lock/compact controls, the active module's
panel, a banner for notices and a footer. Feature content lives in pet_overlay/modules."""
import time

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import APP_NAME
from . import theme, winapi
from .appmenu import build_app_menu
from .mapwindow import badge_label
from .widgets import label

GLYPH_CHEVRON = ""
GLYPH_BELL = ""


class GlyphButton(QLabel):
    clicked = Signal()

    def __init__(self, glyph, fonts, tooltip, parent=None):
        super().__init__(glyph, parent)
        self.setFont(fonts.glyph)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"color: {theme.MUTED.name()}; padding: 0 3px;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

    def enterEvent(self, _):
        self.setStyleSheet(f"color: {theme.TEXT.name()}; padding: 0 3px;")

    def leaveEvent(self, _):
        self.setStyleSheet(f"color: {theme.MUTED.name()}; padding: 0 3px;")


class MenuButton(QWidget):
    """Logo + module title + chevron; opens the app menu."""
    clicked = Signal()

    def __init__(self, fonts, dpr, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Menú")
        self.setAttribute(Qt.WA_StyledBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(fonts.px(3), fonts.px(2), fonts.px(6), fonts.px(2))
        lay.setSpacing(fonts.px(6))
        lay.addWidget(badge_label(fonts.px(22), dpr))
        self.title = label("", fonts.title, theme.TEXT)
        lay.addWidget(self.title)
        chevron = label(GLYPH_CHEVRON, fonts.glyph, theme.MUTED)
        chevron.setFont(fonts._make(fonts.icons_family, 7))
        lay.addWidget(chevron)
        self._hover(False)

    def set_title(self, text):
        self.title.setText(text)

    def _hover(self, on):
        bg = "rgba(255,255,255,0.07)" if on else "transparent"
        self.setStyleSheet(f"MenuButton {{ background: {bg}; border-radius: 6px; }}")

    def enterEvent(self, _):
        self._hover(True)

    def leaveEvent(self, _):
        self._hover(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            event.accept()          # do not start a window drag
            self.clicked.emit()


class OverlayWindow(QWidget):
    moved = Signal()            # position or size changed (notification bubbles follow it)

    def __init__(self, controller, fonts):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.ctl = controller
        self.fonts = fonts
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle(APP_NAME)
        self.setFixedWidth(fonts.px(340))
        self._drag_from = None
        self._press_at = None
        self._anchor = None              # where you put it (placed or dragged): a resize never moves it from there
        self._resized_at = 0.0
        self.panels = []

        root = QVBoxLayout(self)
        m = fonts.px(10)
        root.setContentsMargins(m - fonts.px(3), fonts.px(6), m, fonts.px(8))
        root.setSpacing(fonts.px(2))

        header = QHBoxLayout()
        header.setSpacing(fonts.px(6))
        self.menu_button = MenuButton(fonts, self.devicePixelRatioF())
        self.menu_button.clicked.connect(self.open_menu)
        header.addWidget(self.menu_button)
        self.dot = QLabel("●")
        self.dot.setFont(fonts.small)
        header.addWidget(self.dot)
        self.summary = label("", fonts.small, theme.MUTED, elide=True)   # gives way to the title
        header.addWidget(self.summary, 1)
        self.btn_bell = GlyphButton(GLYPH_BELL, fonts, "Notificaciones")
        self.btn_bell.clicked.connect(lambda: controller.switch_module("notifications"))
        self.unread = label("", fonts.badge, theme.GOLD)
        header.addWidget(self.btn_bell)
        header.addWidget(self.unread)
        self.btn_compact = GlyphButton(theme.GLYPH_EXPAND, fonts, "Expandir / compactar")
        self.btn_compact.clicked.connect(controller.toggle_compact)
        self.btn_lock = GlyphButton(theme.GLYPH_UNLOCK, fonts, "Bloquear (los clics pasan al juego)")
        self.btn_lock.clicked.connect(controller.toggle_lock)
        header.addWidget(self.btn_compact)
        header.addWidget(self.btn_lock)
        root.addLayout(header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(fonts.px(3), 0, 0, 0)
        root.addLayout(self.body)

        self.footer = label("", fonts.small, theme.MUTED)
        self.footer.setWordWrap(True)
        self.footer.setContentsMargins(fonts.px(3), 0, 0, 0)
        root.addWidget(self.footer)
        root.addStretch(1)                       # room left while the window waits to shrink (see _fit)
        self._shrink = QTimer(self, singleShot=True, interval=1500, timeout=self._shrink_now)

    # --- modules ------------------------------------------------------------------------
    def add_panel(self, panel):
        self.panels.append(panel)
        self.body.addWidget(panel)
        panel.hide()

    def show_module(self, module):
        for panel in self.panels:
            panel.setVisible(panel is module.panel)
        self.menu_button.set_title(module.title)
        self.setWindowTitle(f"{APP_NAME} — {module.title}")

    def update_view(self, view, compact):
        self.summary.setText(view.summary)
        self.dot.setStyleSheet(f"color: {view.status_color.name()};")
        self.dot.setToolTip(view.status)
        self.btn_compact.setText(theme.GLYPH_EXPAND if compact else theme.GLYPH_COLLAPSE)
        self.footer.setText(view.footer)
        self.footer.setVisible(bool(view.footer))
        self._fit()

    def _fit(self):
        """Grow at once, shrink only after 1.5 s of being smaller: a panel whose height changes every second (the DPS
        in a fight) no longer resizes the window each time (on some PCs a resize of a see-through window blinks)."""
        want = self.sizeHint().height()
        if want >= self.height() or not self.isVisible():
            self._shrink.stop()
            self._resize_to(want)
        elif not self._shrink.isActive():
            self._shrink.start()

    def _shrink_now(self):
        self._resize_to(self.sizeHint().height())

    def _resize_to(self, height):
        if height != self.height():
            self._resized_at = time.time()
            self.resize(self.width(), height)
        self._hold()

    def _hold(self):
        """Back where you put it, if something moved it while it changed size (2026-10-18, a user: a panel growing
        and shrinking over and over crept the overlay up the screen) and it fits there."""
        if self._anchor is None or self._drag_from is not None or self.pos() == self._anchor:
            return
        screen = QGuiApplication.screenAt(self._anchor)
        if screen is not None and screen.availableGeometry().contains(QRect(self._anchor, self.size())):
            self.move(self._anchor)

    def set_unread(self, count):
        self.unread.setText(str(count) if count else "")
        self.unread.setVisible(bool(count))
        self.btn_bell.setToolTip(f"Notificaciones ({count} sin leer)" if count else "Notificaciones")

    def open_menu(self):
        menu = build_app_menu(self.ctl, self.fonts, self)
        menu.exec(self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height() + self.fonts.px(4))))

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._drag_from is None and self._anchor is not None and self.pos() != self._anchor and \
                time.time() - self._resized_at < 1.0:
            QTimer.singleShot(0, self._hold)     # moved by a resize, not by you
        self.moved.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.moved.emit()

    # --- painting & interaction ----------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self.fonts.px(10)
        p.setBrush(theme.panel_color(self.ctl.settings["opacity"]))
        if self.ctl.settings["locked"]:
            p.setPen(QPen(theme.BORDER, 1))
        else:
            pen = QPen(theme.ACCENT, 1.2, Qt.DashLine)
            pen.setDashPattern([4, 3])
            p.setPen(pen)
        p.drawRoundedRect(r, radius, radius)

    def apply_lock(self, locked):
        winapi.set_click_through(int(self.winId()), locked)
        self.btn_lock.setText(theme.GLYPH_LOCK if locked else theme.GLYPH_UNLOCK)
        self.btn_lock.setToolTip("Desbloquear" if locked else "Bloquear (los clics pasan al juego)")
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_at = event.globalPosition().toPoint()
            self._drag_from = self._press_at - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, event):
        if self._drag_from is None:
            return
        self._drag_from = None
        moved = (event.globalPosition().toPoint() - self._press_at).manhattanLength()
        if moved > 4:
            self._anchor = self.pos()
            self.ctl.remember_position(self.pos())
            return
        # A plain click (not a drag) runs the clicked widget's action, if it has one.
        widget = self.childAt(event.position().toPoint())
        while widget is not None and not hasattr(widget, "on_click"):
            widget = widget.parentWidget()
        if widget is not None:
            widget.on_click()

    def nativeEvent(self, event_type, message):
        watch = getattr(self.ctl, "blink_watch", None)
        if watch is not None and watch.active and event_type == b"windows_generic_MSG":
            watch.on_native(message)
        hotkey = winapi.hotkey_id_from_native(event_type, message)
        if hotkey is not None:
            self.ctl.on_hotkey(hotkey)
            return True, 0
        return super().nativeEvent(event_type, message)

    def place(self, pos):
        """Move to pos if it is on a visible screen, else to the default spot."""
        size = self.sizeHint()
        if pos is not None:
            point = QPoint(*pos)
            for screen in QGuiApplication.screens():
                if screen.availableGeometry().adjusted(-size.width() // 2, 0, 0, -40).contains(point):
                    self.move(point)
                    self._anchor = self.pos()
                    return
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.right() - size.width() - self.fonts.px(40), area.top() + self.fonts.px(160))
        self._anchor = self.pos()
