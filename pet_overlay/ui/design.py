"""The v2 design system: one stylesheet for the AionDex window (the hub) and the widgets its pages share - cards,
a hero, stat tiles, nav items, chips, toggles. The look is the app's: navy surfaces, sky accents, gold for what
matters, a quiet muted grey for the rest. Rules the pages follow:

- one thing first: a page opens with its hero (the next step, the one number) and the rest under it;
- cards, not paragraphs: a fact per row, a chip for its state, a bar for a level;
- the game's words stay the game's (names of skills, items, dungeons are never translated);
- nothing the game already shows on its own screen."""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme
from .widgets import label

NAVY = "#0b1222"
SURFACE = "#121c33"
SURFACE_2 = "#16213d"
LINE = "#1f2b47"
LINE_SOFT = "#1a2643"
INK = theme.TEXT.name()
MUTED = theme.MUTED.name()
ACCENT = theme.ACCENT.name()
GOLD = theme.GOLD.name()
GAIN = theme.GAIN.name()
WARN = theme.WARN.name()

HUB_STYLE = f"""
QWidget#Hub {{ background: {NAVY}; }}
QFrame#NavRail {{ background: #0d1628; border-right: 1px solid {LINE}; }}
QFrame#RightRail {{ background: #0d1628; border-left: 1px solid {LINE}; }}
QLabel#NavGroup {{ color: #56627c; font-weight: 700; letter-spacing: 1px; padding: 7px 14px 1px 14px; }}
QPushButton[nav="true"] {{ background: transparent; color: {MUTED}; border: none; border-radius: 8px;
    padding: 4px 10px; text-align: left; font-size: 10pt; }}
QPushButton[nav="true"]:hover {{ background: rgba(255,255,255,0.05); color: {INK}; }}
QPushButton[nav="true"]:checked {{ background: #13264a; color: {INK}; }}
QPushButton[nav="true"][accent="true"]:checked {{ border-left: 3px solid {ACCENT}; padding-left: 7px; }}
QLabel#PageTitle {{ color: {INK}; }}
QLabel#PageSub {{ color: {MUTED}; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#Hero {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1b2440, stop:1 #121c33);
    border: 1px solid #3a3218; border-radius: 12px; }}
QFrame#Row {{ background: #101a30; border: 1px solid {LINE_SOFT}; border-radius: 8px; }}
QFrame#Row:hover {{ border-color: #2c4170; }}
QFrame#Tile {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#Tile[hot="true"] {{ border-color: {GOLD}; }}
QFrame#Pill {{ background: #101a30; border: 1px solid {LINE_SOFT}; border-radius: 8px; }}
QFrame#Stat {{ background: #101a30; border: 1px solid {LINE_SOFT}; border-radius: 10px; }}
QFrame#Divider {{ background: {LINE}; max-height: 1px; min-height: 1px; border: none; }}
QPushButton {{ background: {ACCENT}; color: #06101f; border: none; border-radius: 7px; padding: 7px 16px;
    font-weight: 600; }}
QPushButton:hover {{ background: #3fd3ff; }}
QPushButton:pressed {{ background: #1fa8d8; }}
QPushButton:disabled {{ background: #18233c; color: #56627c; }}
QPushButton[flat="true"] {{ background: transparent; color: {ACCENT}; padding: 3px 8px; }}
QPushButton[flat="true"]:hover {{ background: rgba(46,192,242,0.10); }}
QPushButton[flat="true"]:disabled {{ color: #2c3a58; background: transparent; }}
QPushButton[ghost="true"] {{ background: transparent; color: {INK}; border: 1px solid #24345a; padding: 6px 14px; }}
QPushButton[ghost="true"]:hover {{ border-color: {ACCENT}; color: {INK}; background: rgba(46,192,242,0.06); }}
QPushButton[tab="true"] {{ background: #0f1830; color: {MUTED}; border: 1px solid #24345a; border-radius: 8px;
    padding: 6px 16px; }}
QPushButton[tab="true"]:checked {{ background: #13264a; color: {INK}; border-color: {ACCENT}; }}
QPushButton[tab="true"]:disabled {{ color: #3a4560; border-color: #1a2440; }}
QPushButton[link="true"] {{ background: transparent; border: none; color: {MUTED}; padding: 2px 4px; }}
QPushButton[link="true"]:hover {{ color: {INK}; }}
QPushButton[icon="true"] {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
    padding: 4px 6px; font-family: 'Segoe Fluent Icons', 'Segoe MDL2 Assets'; }}
QPushButton[icon="true"]:hover {{ background: rgba(255,255,255,0.07); color: {INK}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{ background: #0d1628; color: {INK};
    border: 1px solid #24345a; border-radius: 6px; padding: 5px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #0d1628; color: {INK}; selection-background-color: #1d3157;
    border: 1px solid #24345a; }}
QCheckBox {{ color: {INK}; spacing: 6px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid #3a4a70; background: #0d1628; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2a3a60; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4f80; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2a3a60; border-radius: 4px; min-width: 30px; }}
QTabBar::tab {{ background: #0f1830; color: {MUTED}; padding: 6px 14px; border: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {SURFACE}; color: {INK}; }}
QToolTip {{ background: #0d1628; color: {INK}; border: 1px solid #24345a; padding: 5px 7px; border-radius: 4px; }}
QMenu {{ background: #0d1628; border: 1px solid #24345a; border-radius: 9px; padding: 5px; color: {INK}; }}
QMenu::item {{ padding: 6px 22px 6px 8px; border-radius: 6px; }}
QMenu::item:selected {{ background: #1d3157; }}
QMenu::separator {{ height: 1px; background: #24345a; margin: 4px 8px; }}
QSplitter::handle {{ background: {LINE}; }}
QProgressBar {{ background: rgba(255,255,255,0.08); border: none; border-radius: 4px; height: 8px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
"""


def hub_font(fonts, pt, weight=QFont.Normal):
    """A font of the hub's family at pt points, scaled like the rest."""
    return fonts._make(fonts.title.family(), pt, weight)


class Chip(QLabel):
    """A small rounded tag in a color: a state («Sirve», «3 short»), a kind, a place."""

    def __init__(self, text, color, fonts, tip="", parent=None):
        super().__init__(text, parent)
        c = QColor(color)
        self.setFont(fonts.small)
        self.setStyleSheet(f"color: {c.name()}; background: rgba({c.red()},{c.green()},{c.blue()},0.12); "
                           f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.5); border-radius: 8px; "
                           f"padding: 0px 7px;")
        if tip:
            self.setToolTip(tip)
        else:
            self.setAttribute(Qt.WA_TransparentForMouseEvents)


class Card(QFrame):
    """A surface with a title row (text + optional right-side widgets) and a body layout."""

    def __init__(self, fonts, title="", parent=None, name="Card"):
        super().__init__(parent)
        self.setObjectName(name)
        self.fonts = fonts
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(fonts.px(14), fonts.px(10), fonts.px(14), fonts.px(12))
        self.body.setSpacing(fonts.px(6))
        self.head = None
        if title:
            self.head = QHBoxLayout()
            self.head.setSpacing(fonts.px(8))
            self.title = label(title.upper(), fonts.section, theme.MUTED)
            self.head.addWidget(self.title, 1)
            self.body.addLayout(self.head)

    def add_action(self, widget):
        if self.head is not None:
            self.head.addWidget(widget, 0, Qt.AlignRight)


class Hero(QFrame):
    """The one thing first: an eyebrow («TU SIGUIENTE PASO»), a big title, a line under it and a button."""

    def __init__(self, fonts, eyebrow, title, line="", action=None, color=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Hero")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(fonts.px(18), fonts.px(14), fonts.px(18), fonts.px(14))
        lay.setSpacing(fonts.px(14))
        text = QVBoxLayout()
        text.setSpacing(fonts.px(4))
        text.addWidget(label(eyebrow.upper(), fonts.section, theme.GOLD))
        big = label(title, hub_font(fonts, 14, QFont.DemiBold), color or theme.TEXT)
        big.setWordWrap(True)
        text.addWidget(big)
        if line:
            sub = label(line, fonts.body, theme.MUTED)
            sub.setWordWrap(True)
            text.addWidget(sub)
        lay.addLayout(text, 1)
        self.button = None
        if action:
            self.button = QPushButton(action[0])
            self.button.setMinimumWidth(fonts.px(140))
            self.button.clicked.connect(action[1])
            lay.addWidget(self.button, 0, Qt.AlignVCenter)


class Stat(QFrame):
    """A number with its name under it (and a color for what it means)."""

    def __init__(self, fonts, value, name, color=None, tip="", parent=None):
        super().__init__(parent)
        self.setObjectName("Stat")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(fonts.px(12), fonts.px(8), fonts.px(12), fonts.px(8))
        lay.setSpacing(0)
        self.value = label(str(value), hub_font(fonts, 15, QFont.DemiBold), color or theme.TEXT)
        lay.addWidget(self.value)
        lay.addWidget(label(name, fonts.small, theme.MUTED))
        if tip:
            self.setToolTip(tip)


class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFixedHeight(1)


class Meter(QWidget):
    """A thin bar from 0 to 1 with an optional mark, painted (crisp on any background)."""

    def __init__(self, ratio, color, mark=None, height=6, parent=None):
        super().__init__(parent)
        self.ratio, self.color, self.mark = max(0.0, min(1.0, ratio)), QColor(color), mark
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(theme.BAR_BG)
        p.drawRoundedRect(r, radius, radius)
        if self.ratio > 0:
            p.setBrush(self.color)
            p.drawRoundedRect(QRectF(r.x(), r.y(), max(r.height(), r.width() * self.ratio), r.height()), radius, radius)
        if self.mark is not None and 0 < self.mark < 1:
            x = r.x() + r.width() * self.mark
            p.setPen(QPen(theme.TEXT, 1))
            p.drawLine(QRectF(x, r.y() - 1, 0, r.height() + 2).topLeft(), QRectF(x, r.y() - 1, 0, r.height() + 2).bottomLeft())


class Toggle(QWidget):
    """A switch: on/off with a short slide."""
    toggled = Signal(bool)

    def __init__(self, on=False, parent=None):
        super().__init__(parent)
        self._on = bool(on)
        self._pos = 1.0 if on else 0.0
        self.setFixedSize(QSize(36, 20))
        self.setCursor(Qt.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def isChecked(self):
        return self._on

    def setChecked(self, on):
        on = bool(on)
        if on != self._on:
            self._on = on
            self._slide()

    def _slide(self):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if self._on else 0.0)
        self._anim.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on = not self._on
            self._slide()
            self.toggled.emit(self._on)

    def get_knob(self):
        return self._pos

    def set_knob(self, value):
        self._pos = value
        self.update()

    from PySide6.QtCore import Property
    knob = Property(float, get_knob, set_knob)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg = QColor(theme.ACCENT) if self._on else QColor("#24345a")
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.x() + 3 + (r.width() - d - 6) * self._pos
        p.setBrush(QColor("#06101f") if self._on else QColor("#8a93a8"))
        p.drawEllipse(QRectF(x, r.y() + 3, d, d))


class NavItem(QPushButton):
    """A sidebar entry: a glyph, a label and a badge (unread count) on the right."""

    def __init__(self, glyph, text, fonts, parent=None):
        super().__init__(parent)
        self.setProperty("nav", True)
        self.setProperty("accent", True)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.fonts = fonts
        lay = QHBoxLayout(self)
        lay.setContentsMargins(fonts.px(8), 0, fonts.px(8), 0)
        lay.setSpacing(fonts.px(10))
        self.glyph = label(glyph, fonts.glyph, theme.MUTED)
        self.glyph.setFixedWidth(fonts.px(16))
        lay.addWidget(self.glyph)
        self.text_label = label(text, fonts.body, theme.MUTED, elide=True)
        lay.addWidget(self.text_label, 1)
        self.badge = label("", fonts.badge, theme.GOLD)
        self.badge.hide()
        lay.addWidget(self.badge)
        self.setMinimumHeight(fonts.px(27))
        self.toggled.connect(self._paint_state)
        self._paint_state(False)

    def set_badge(self, count):
        self.badge.setText(str(count) if count else "")
        self.badge.setVisible(bool(count))

    def _paint_state(self, on):
        color = theme.TEXT if on else theme.MUTED
        self.glyph.setStyleSheet(f"color: {(theme.ACCENT if on else theme.MUTED).name()}; background: transparent;")
        self.text_label.setStyleSheet(f"color: {color.name()}; background: transparent;")

    def enterEvent(self, event):
        if not self.isChecked():
            self.text_label.setStyleSheet(f"color: {theme.TEXT.name()}; background: transparent;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._paint_state(self.isChecked())
        super().leaveEvent(event)


def fade_in(widget, ms=160):
    """A short fade when a page comes up (the stack switch is otherwise a cut)."""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def done():
        widget.setGraphicsEffect(None)
    anim.finished.connect(done)
    anim.start(QPropertyAnimation.DeleteWhenStopped)
    widget._fade = anim
