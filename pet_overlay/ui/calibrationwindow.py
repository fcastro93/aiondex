"""Calibrar menú: markers over the game window that you drag onto the Skill window's buttons, tabs and first grid
cells, so the build macro (buildmacro.py) clicks exactly there at any resolution or interface size. Only the markers
and the panel catch the mouse: clicks anywhere else reach the game (to switch tabs, open the Skill window...)."""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..buildmacro import REFERENCE, default_layout
from . import theme

_user32 = ctypes.windll.user32
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
COLORS = {"preset": "#ffd34d", "tab": "#7bd88f", "grid": "#2ec0f2", "passive": "#c38bff", "bar": "#ff8a3d",
          "reset": "#ff5c5c", "level": "#ffd34d"}
R = 11
SHORT = {"preset1": "P1", "preset2": "P2", "preset3": "P3", "tab_mastery": "Mastery", "tab_stigma": "Stigma",
         "grid_first": "A1", "grid_right": "A2", "grid_below": "A6", "passive_first": "Pas", "bar_1": "1",
         "bar_2": "2", "bar_5": "5", "bar_q": "Q", "bar_1_up": "1↑", "reset": "Reset", "level_up": "Subir"}


def _color(name):
    for prefix, color in COLORS.items():
        if name.startswith(prefix):
            return QColor(color)
    return QColor("#ffffff")


class CalibrationWindow(QWidget):
    def __init__(self, client, points, on_save):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.client = client                 # the game's client area (real pixels)
        self.points = dict(points)           # name -> (fx, fy)
        self.on_save = on_save
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
        text = QLabel("<b>Calibrar el menú de skills</b><br>Abre la ventana Skill del juego (K) y arrastra cada "
                      "marcador encima de su botón o celda (los clics fuera de los marcadores van al juego).<br><br>"
                      "<b>P1 P2 P3</b> los presets · <b>Mastery</b> y <b>Stigma</b> las pestañas · <b>A1</b> la "
                      "1ª skill activa, <b>A2</b> la de su derecha, <b>A6</b> la de debajo de A1 · <b>Pas</b> la 1ª "
                      "pasiva · en la barra, la fila de abajo de las teclas <b>1</b>, <b>2</b>, <b>5</b> y <b>Q</b>, "
                      "y <b>1↑</b> una fila más arriba de la 1 · <b>Reset</b> la flecha circular · <b>Subir</b> el "
                      "botón dorado de subir nivel.")
        text.setWordWrap(True)
        text.setFixedWidth(330)
        col.addWidget(text)
        row = QHBoxLayout()
        for label, action in (("Guardar", self._save), ("Restablecer", self._reset), ("Cancelar", self.close)):
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
        self.panel.move(round(self.width() * 0.22), round(self.height() * 0.35))
        self.update()

    def _px(self, name):
        fx, fy = self.points[name]
        return QPointF(fx * self.width(), fy * self.height())

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for name in REFERENCE:
            c, color = self._px(name), _color(name)
            p.setPen(QPen(QColor(5, 10, 20, 230), 4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, R, R)
            p.setPen(QPen(color, 2.2))
            p.drawEllipse(c, R, R)
            p.drawLine(c + QPointF(-R - 4, 0), c + QPointF(R + 4, 0))
            p.drawLine(c + QPointF(0, -R - 4), c + QPointF(0, R + 4))
            path = QPainterPath()
            path.addText(c + QPointF(R + 3, -R + 1), self.font, SHORT.get(name, name))
            p.setPen(QPen(QColor(5, 10, 20, 230), 3.4))
            p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawPath(path)
        p.end()

    def mousePressEvent(self, event):
        pos = event.position()
        hit = min(REFERENCE, key=lambda n: (self._px(n) - pos).manhattanLength())
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
        self.points = default_layout(right - left, bottom - top)
        self.update()

    def hit_area(self):
        """For tests: where the markers are, in widget pixels."""
        return {n: QRectF(self._px(n).x() - R, self._px(n).y() - R, 2 * R, 2 * R) for n in REFERENCE}
