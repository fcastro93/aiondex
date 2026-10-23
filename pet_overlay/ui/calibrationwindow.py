"""Calibrar el minimapa (Ajustes > Minimapa: open_minimap_calibration()): markers over the game window that you drag
onto the minimap's 4 corners. Only the markers and the panel catch the mouse: clicks anywhere else reach the game.
"""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme
from ..i18n import tr

_user32 = ctypes.windll.user32
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
COLORS = {"mm_": "#ff5cf0"}
R = 11
SHORT = {"mm_tl": "↖", "mm_tr": "↗", "mm_br": "↘", "mm_bl": "↙"}
MINIMAP = ("mm_tl", "mm_tr", "mm_br", "mm_bl")
MINIMAP_TEXT = (tr("<b>Calibrar el minimapa</b><br>Arrastra cada esquina rosa a la esquina del mapa del minimapa "
                   "del juego (solo el mapa: sin el título de la zona ni los botones de alrededor). La ruta se "
                   "dibuja solo dentro de ese recuadro.<br><br>Se guarda para este tamaño de la ventana del juego. "
                   "<b>Restablecer</b> vuelve a donde AionDex lo encuentra solo."))


def _color(name):
    for prefix, color in COLORS.items():
        if name.startswith(prefix):
            return QColor(color)
    return QColor("#ffffff")


class CalibrationWindow(QWidget):
    def __init__(self, client, points, on_save, names, text, defaults, outline=False, panel_at=(0.22, 0.35)):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.client = client                 # the game's client area (real pixels)
        self.points = dict(points)           # name -> (fx, fy)
        self.on_save = on_save
        self.names = list(names)
        self.defaults = defaults             # "Restablecer": (w, h) -> {name: (fx, fy)}
        self.outline = outline               # the markers are the corners of an area: draw it
        self.panel_at = panel_at
        self.dragging = None
        self.font = QFont("Segoe UI", 9, QFont.Bold)
        panel = QWidget(self)
        panel.setObjectName("CalPanel")
        panel.setStyleSheet(f"QWidget#CalPanel {{ background: rgba(11,18,34,235); border: 1px solid #2ec0f2; "
                            f"border-radius: 10px; }} QLabel {{ color: {theme.TEXT.name()}; }} "
                            f"QPushButton {{ background: {theme.ACCENT.name()}; color: #06101f; border: none; "
                            f"border-radius: 6px; padding: 6px 12px; font-weight: 600; }}")
        col = QVBoxLayout(panel)
        col.setContentsMargins(14, 12, 14, 12)
        text = QLabel(text)
        text.setWordWrap(True)
        text.setFixedWidth(330)
        col.addWidget(text)
        row = QHBoxLayout()
        for label, action in ((tr("Guardar"), self._save), (tr("Restablecer"), self._reset), (tr("Cancelar"), self.close)):
            button = QPushButton(label)
            button.clicked.connect(action)
            row.addWidget(button)
        col.addLayout(row)
        panel.adjustSize()
        self.panel = panel

    def showEvent(self, event):
        super().showEvent(event)
        # over the game in real pixels, once Qt has shown it (placed earlier, Qt puts back its own size)
        QTimer.singleShot(0, self._place)
        QTimer.singleShot(200, self._place)

    def _place(self):
        left, top, right, bottom = self.client
        _user32.SetWindowPos(int(self.winId()), wintypes.HWND(-1), left, top, right - left, bottom - top, 0x0040)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.panel.move(round(self.width() * self.panel_at[0]), round(self.height() * self.panel_at[1]))
        self.update()

    def _px(self, name):
        fx, fy = self.points[name]
        return QPointF(fx * self.width(), fy * self.height())

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.outline:
            corners = QPolygonF([self._px(n) for n in self.names])
            p.setPen(QPen(QColor(255, 92, 240, 230), 2, Qt.DashLine))
            p.setBrush(QColor(255, 92, 240, 40))
            p.drawPolygon(corners)
        for name in self.names:
            c, color = self._px(name), _color(name)
            p.setPen(QPen(QColor(5, 10, 20, 230), 4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, R, R)
            p.setPen(QPen(color, 2.2))
            p.drawEllipse(c, R, R)
            p.drawLine(c + QPointF(-R - 4, 0), c + QPointF(R + 4, 0))
            p.drawLine(c + QPointF(0, -R - 4), c + QPointF(0, R + 4))
            path = QPainterPath()
            text = SHORT.get(name, name)
            inward = c.x() > self.width() * 0.9                 # by the screen's right edge: the label on the left
            path.addText(c + QPointF(-R - 3 - 9 * len(text) if inward else R + 3, -R + 1), self.font, text)
            p.setPen(QPen(QColor(5, 10, 20, 230), 3.4))
            p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawPath(path)
        p.end()

    def mousePressEvent(self, event):
        pos = event.position()
        hit = min(self.names, key=lambda n: (self._px(n) - pos).manhattanLength())
        if (self._px(hit) - pos).manhattanLength() <= R * 2.2:
            self.dragging = hit

    def mouseMoveEvent(self, event):
        if self.dragging:
            pos = event.position()
            self.points[self.dragging] = (min(1.0, max(0.0, pos.x() / self.width())),
                                          min(1.0, max(0.0, pos.y() / self.height())))
            self.update()

    def mouseReleaseEvent(self, _event):
        self.dragging = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def _save(self):
        self.on_save(self.points)
        self.close()

    def _reset(self):
        left, top, right, bottom = self.client
        self.points = dict(self.defaults(right - left, bottom - top))
        self.update()

    def hit_area(self):
        """For tests: where the markers are, in widget pixels."""
        return {n: QRectF(self._px(n).x() - R, self._px(n).y() - R, 2 * R, 2 * R) for n in self.names}
