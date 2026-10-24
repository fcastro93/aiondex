"""The design system: one stylesheet for the AionDex window (the hub) and the widgets its pages share. The look is
AION 2's own, so the app reads as the game's companion: near-black with a faint blue glow, dark slate panels with
crisp thin borders (small radii), ivory text, a serif for titles, cyan for progress and what is active (the game's
selected tab has a cyan-white lens flare under it), gold for currency and what matters, grade colors on names.

Widgets here: Card, Hero, Stat, Chip, Toggle, NavItem, Meter, Divider (the game's rule with a diamond), GameTabs (the
game's tab bar with the glow), GameCard (a mission-style card: ornament, title band, body, footer bar), ProgressRing
(the game's circular progress), RewardTile (an icon with its count), ChipGroup + SearchBox (filters), PageHeader,
KeyValue (a tooltip row). Rules the pages follow:

- one thing first: a page opens with its hero (the next step, the one number) and the rest under it;
- cards, not paragraphs: a fact per row, a chip for its state, a bar for a level, an icon where the game shows one;
- the game's words stay the game's (names of skills, items, dungeons are never translated);
- filters at the top of any list longer than a screen; nothing the game already shows on its own screen."""
from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, \
    QRadialGradient
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import theme
from .widgets import label
from ..i18n import tr

NAVY = theme.WINDOW_BG.name()
SURFACE = theme.SURFACE.name()
SURFACE_2 = theme.SURFACE_2.name()
LINE = theme.LINE.name()
LINE_SOFT = theme.LINE_SOFT.name()
INK = theme.TEXT.name()
MUTED = theme.MUTED.name()
ACCENT = theme.ACCENT.name()
GOLD = theme.GOLD.name()
GAIN = theme.GAIN.name()
WARN = theme.WARN.name()
RAIL_BG = "#07090e"
INPUT_BG = "#0a0d14"
INPUT_LINE = "#2a3444"
GOLD_LINE = "#5a4a1e"

HUB_STYLE = f"""
QWidget#Hub {{ background: {NAVY}; }}
QStackedWidget#Pages {{ background: qradialgradient(cx:0.5, cy:-0.3, radius:1.3, fx:0.5, fy:-0.3,
    stop:0 #141c2e, stop:0.45 #090c13, stop:1 {NAVY}); }}
QFrame#NavRail {{ background: {RAIL_BG}; border-right: 1px solid #1c2330; }}
QFrame#RightRail {{ background: {RAIL_BG}; border-left: 1px solid #1c2330; }}
QLabel#RailTitle {{ color: {INK}; }}
QLabel#RailMuted {{ color: {MUTED}; }}
QFrame#HudSlot {{ background: {SURFACE}; border: 1px solid #343c4e; border-radius: 8px; }}
QLabel#NavGroup {{ color: #6b6a5e; font-weight: 700; letter-spacing: 1px; padding: 6px 14px 0px 14px; }}
QPushButton[nav="true"] {{ background: transparent; color: {MUTED}; border: none; border-radius: 4px;
    padding: 4px 10px; text-align: left; font-size: 10pt; }}
QPushButton[nav="true"]:hover {{ background: rgba(255,255,255,0.05); color: {INK}; }}
QPushButton[nav="true"]:checked {{ background: #181610; color: {INK}; }}
QPushButton[nav="true"][accent="true"]:checked {{ border-left: 3px solid {GOLD}; padding-left: 7px; }}
QLabel#PageTitle {{ color: {INK}; }}
QLabel#PageSub {{ color: {MUTED}; }}
QFrame#Card {{ background: rgba(15,19,27,0.94); border: 1px solid {LINE}; border-radius: 6px; }}
QFrame#Hero {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a1a22, stop:1 #10141d);
    border: 1px solid {GOLD_LINE}; border-radius: 8px; }}
QFrame#Row {{ background: rgba(18,22,31,0.9); border: 1px solid {LINE_SOFT}; border-radius: 5px; }}
QFrame#Row:hover {{ border-color: #46506a; }}
QFrame#Tile {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 6px; }}
QFrame#Tile[hot="true"] {{ border-color: {GOLD}; }}
QFrame#Pill {{ background: #12161f; border: 1px solid {LINE_SOFT}; border-radius: 5px; }}
QFrame#Stat {{ background: rgba(18,22,31,0.9); border: 1px solid {LINE_SOFT}; border-radius: 6px; }}
QFrame#GameCard {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #151a24, stop:1 #0d1118);
    border: 1px solid #2a3140; border-radius: 4px; }}
QFrame#GameCard[dim="true"] {{ background: #0b0e14; border-color: #1c222c; }}
QFrame#Divider {{ background: {LINE}; max-height: 1px; min-height: 1px; border: none; }}
QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f3d273, stop:1 #c99d33);
    color: #1b1505; border: 1px solid #8a6d1f; border-radius: 4px; padding: 6px 16px; font-weight: 600; }}
QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffe08a, stop:1 #d9ad3f); }}
QPushButton:pressed {{ background: #b8923a; }}
QPushButton:disabled {{ background: #1c212b; color: #4e5869; border-color: #262e3d; }}
QPushButton[flat="true"] {{ background: transparent; color: {ACCENT}; border: none; padding: 3px 8px; }}
QPushButton[flat="true"]:hover {{ background: rgba(58,209,245,0.10); }}
QPushButton[flat="true"]:disabled {{ color: #2c3a58; background: transparent; }}
QPushButton[ghost="true"] {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3a4150, stop:1 #262c38);
    color: {INK}; border: 1px solid #4a5264; padding: 6px 14px; }}
QPushButton[ghost="true"]:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #475062, stop:1 #2f3644);
    border-color: {GOLD}; }}
QPushButton[ghost="true"]:disabled {{ background: #161a22; color: #4e5869; border-color: #262e3d; }}
QPushButton[tab="true"] {{ background: transparent; color: {MUTED}; border: none;
    border-bottom: 2px solid transparent; border-radius: 0; padding: 6px 14px; font-weight: 600; }}
QPushButton[tab="true"]:hover {{ color: {INK}; }}
QPushButton[tab="true"]:checked {{ color: {INK}; border-bottom: 2px solid {ACCENT}; }}
QPushButton[tab="true"]:disabled {{ color: #3a4560; }}
QPushButton[link="true"] {{ background: transparent; border: none; color: {MUTED}; padding: 2px 4px; }}
QPushButton[link="true"]:hover {{ color: {INK}; }}
QPushButton[glyph="true"] {{ background: transparent; color: {MUTED}; border: none; border-radius: 4px;
    padding: 4px 6px; font-family: 'Segoe Fluent Icons', 'Segoe MDL2 Assets'; }}
QPushButton[glyph="true"]:hover {{ background: rgba(255,255,255,0.07); color: {INK}; }}
QPushButton[chip="true"] {{ background: transparent; color: {MUTED}; border: 1px solid {INPUT_LINE};
    border-radius: 11px; padding: 2px 10px; font-weight: 500; }}
QPushButton[chip="true"]:hover {{ color: {INK}; border-color: #46506a; }}
QPushButton[chip="true"]:checked {{ color: {ACCENT}; border-color: {ACCENT}; background: rgba(58,209,245,0.10); }}
QPushButton[chip="true"][gold="true"]:checked {{ color: {GOLD}; border-color: {GOLD}; background: rgba(233,196,92,0.10); }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{ background: {INPUT_BG}; color: {INK};
    border: 1px solid {INPUT_LINE}; border-radius: 4px; padding: 5px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit[search="true"] {{ padding-left: 10px; border-radius: 13px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {INPUT_BG}; color: {INK}; selection-background-color: #243041;
    border: 1px solid {INPUT_LINE}; }}
QCheckBox {{ color: {INK}; spacing: 6px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 3px; border: 1px solid #3a4a70; background: {INPUT_BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {INPUT_LINE}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4f80; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {INPUT_LINE}; border-radius: 4px; min-width: 30px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 6px 14px; border: none;
    border-bottom: 2px solid transparent; margin-right: 2px; }}
QTabBar::tab:selected {{ color: {INK}; border-bottom: 2px solid {ACCENT}; }}
QTabWidget::pane {{ border: none; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
    padding: 4px 8px; font-weight: 600; }}
QTableView, QTreeView, QListView {{ background: transparent; color: {INK}; gridline-color: {LINE_SOFT};
    border: 1px solid {LINE_SOFT}; alternate-background-color: rgba(255,255,255,0.025);
    selection-background-color: rgba(58,209,245,0.16); selection-color: {INK}; }}
QTableView::item:hover, QTreeView::item:hover {{ background: rgba(255,255,255,0.04); }}
QToolTip {{ background: #0b0e15; color: {INK}; border: 1px solid #3a4256; padding: 5px 7px; border-radius: 3px; }}
QMenu {{ background: #0b0e15; border: 1px solid #3a4256; border-radius: 6px; padding: 5px; color: {INK}; }}
QMenu::item {{ padding: 6px 22px 6px 8px; border-radius: 4px; }}
QMenu::item:selected {{ background: #243041; }}
QMenu::separator {{ height: 1px; background: #3a4256; margin: 4px 8px; }}
QSplitter::handle {{ background: {LINE}; }}
QProgressBar {{ background: rgba(255,255,255,0.08); border: none; border-radius: 3px; height: 8px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
"""


def hub_font(fonts, pt, weight=QFont.Normal):
    """A font of the hub's family at pt points, scaled like the rest."""
    return fonts._make(fonts.title.family(), pt, weight)


def serif_font(fonts, pt, weight=QFont.DemiBold):
    """The game's title serif at pt points."""
    return fonts._make(fonts.display_family, pt, weight)


class Chip(QLabel):
    """A small rounded tag in a color: a state («Sirve», «3 short»), a kind, a place."""

    def __init__(self, text, color, fonts, tip="", parent=None):
        super().__init__(text, parent)
        c = QColor(color)
        self.setFont(fonts.small)
        self.setStyleSheet(f"color: {c.name()}; background: rgba({c.red()},{c.green()},{c.blue()},0.12); "
                           f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.5); border-radius: 3px; "
                           f"padding: 0px 6px;")
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
        big = label(title, fonts.display, color or theme.TEXT)
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


class Divider(QWidget):
    """The game's rule: a thin gold line fading at both ends, with a small diamond in the middle."""

    def __init__(self, parent=None, plain=False):
        super().__init__(parent)
        self.plain = plain
        self.setFixedHeight(9)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, y = self.width(), self.height() / 2
        gold = QColor(theme.GOLD)
        edge = QColor(gold)
        edge.setAlpha(0)
        mid = QColor(gold)
        mid.setAlpha(60 if self.plain else 150)
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0, edge)
        grad.setColorAt(0.5, mid)
        grad.setColorAt(1.0, edge)
        p.setPen(QPen(grad, 1))
        p.drawLine(QPointF(0, y), QPointF(w, y))
        if not self.plain:
            p.setPen(Qt.NoPen)
            p.setBrush(gold)
            d = 3.0
            p.drawPolygon([QPointF(w / 2 - d, y), QPointF(w / 2, y - d), QPointF(w / 2 + d, y), QPointF(w / 2, y + d)])


class Meter(QWidget):
    """A thin bar from 0 to 1 with an optional mark, painted (crisp on any background). The game's bars are cyan on a
    dark track; a glow at the fill's end when `glow`."""

    def __init__(self, ratio, color=None, mark=None, height=6, glow=False, parent=None):
        super().__init__(parent)
        self.ratio, self.color, self.mark = max(0.0, min(1.0, ratio)), QColor(color or theme.ACCENT), mark
        self.glow = glow
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_ratio(self, ratio):
        self.ratio = max(0.0, min(1.0, ratio))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(2.0, r.height() / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1a212c"))
        p.drawRoundedRect(r, radius, radius)
        if self.ratio > 0:
            fill = QRectF(r.x(), r.y(), max(r.height(), r.width() * self.ratio), r.height())
            grad = QLinearGradient(fill.topLeft(), fill.topRight())
            grad.setColorAt(0, self.color.darker(125))
            grad.setColorAt(1, self.color)
            p.setBrush(grad)
            p.drawRoundedRect(fill, radius, radius)
            if self.glow:
                g = QRadialGradient(fill.right(), fill.center().y(), r.height() * 2.2)
                c = QColor(theme.ACCENT_GLOW)
                c.setAlpha(170)
                g.setColorAt(0, c)
                c.setAlpha(0)
                g.setColorAt(1, c)
                p.setBrush(g)
                p.drawEllipse(QPointF(fill.right(), fill.center().y()), r.height() * 2.2, r.height() * 2.2)
        if self.mark is not None and 0 < self.mark < 1:
            x = r.x() + r.width() * self.mark
            p.setPen(QPen(theme.TEXT, 1))
            p.drawLine(QPointF(x, r.y() - 1), QPointF(x, r.bottom() + 1))


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
        bg = QColor(theme.ACCENT) if self._on else QColor("#363e52")
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
        self.setMinimumHeight(fonts.px(25))
        self.toggled.connect(self._paint_state)
        self._paint_state(False)

    def set_badge(self, count):
        self.badge.setText(str(count) if count else "")
        self.badge.setVisible(bool(count))

    def _paint_state(self, on):
        color = theme.TEXT if on else theme.MUTED
        self.glyph.setStyleSheet(f"color: {(theme.GOLD if on else theme.MUTED).name()}; background: transparent;")
        self.text_label.setStyleSheet(f"color: {color.name()}; background: transparent;")

    def enterEvent(self, event):
        if not self.isChecked():
            self.text_label.setStyleSheet(f"color: {theme.TEXT.name()}; background: transparent;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._paint_state(self.isChecked())
        super().leaveEvent(event)


# --- the game's own pieces -------------------------------------------------------------------------------------
class GameTabs(QWidget):
    """The game's tab bar: plain labels, the chosen one in ivory with a cyan-white lens flare under it (Season
    Mission, Crafting, Subscribe...). tabs: [(key, label)]; `changed(key)` when the player picks one; `sub` for the
    smaller second row (Weekly / Season)."""
    changed = Signal(str)

    def __init__(self, fonts, tabs, current=None, sub=False, parent=None):
        super().__init__(parent)
        self.fonts = fonts
        self.sub = sub
        self.tabs = [(k, t) for k, t in tabs]
        self.labels = {k: t for k, t in tabs}
        self.enabled_keys = {k for k, _ in tabs}
        self.current = current if current is not None else (tabs[0][0] if tabs else None)
        self.hover = None
        self.font_ = fonts._make(fonts.family, 8.5 if sub else 10, QFont.DemiBold)
        self.setFixedHeight(fonts.px(28 if sub else 38))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.pad = fonts.px(14 if sub else 18)

    def keys(self):
        return [k for k, _ in self.tabs]

    def set_label(self, key, text):
        self.labels[key] = text
        self.update()

    def set_enabled(self, key, on):
        if on:
            self.enabled_keys.add(key)
        else:
            self.enabled_keys.discard(key)
        self.update()

    def set_current(self, key, emit=False):
        if key not in self.labels:
            return
        self.current = key
        self.update()
        if emit:
            self.changed.emit(key)

    def _boxes(self):
        fm = QFontMetrics(self.font_)
        x = 0
        out = []
        for k, _ in self.tabs:
            w = fm.horizontalAdvance(self.labels[k]) + self.pad * 2
            out.append((k, QRectF(x, 0, w, self.height())))
            x += w
        return out

    def sizeHint(self):
        boxes = self._boxes()
        return QSize(int(boxes[-1][1].right()) if boxes else 0, self.height())

    def mouseMoveEvent(self, event):
        key = next((k for k, r in self._boxes() if r.contains(event.position())), None)
        if key != self.hover:
            self.hover = key
            self.update()

    def leaveEvent(self, _):
        self.hover = None
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        key = next((k for k, r in self._boxes() if r.contains(event.position())), None)
        if key is not None and key in self.enabled_keys and key != self.current:
            self.current = key
            self.update()
            self.changed.emit(key)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(self.font_)
        h = self.height()
        base = QColor(theme.LINE)
        p.setPen(QPen(base, 1))
        p.drawLine(QPointF(0, h - 0.5), QPointF(self.width(), h - 0.5))
        for key, r in self._boxes():
            on = key == self.current
            if on:
                cx, cy = r.center().x(), h - 1
                glow = QRadialGradient(cx, cy, r.width() * 0.7)
                c = QColor(theme.ACCENT_GLOW)
                c.setAlpha(120 if not self.sub else 90)
                glow.setColorAt(0, c)
                c2 = QColor(theme.ACCENT)
                c2.setAlpha(40)
                glow.setColorAt(0.45, c2)
                c2.setAlpha(0)
                glow.setColorAt(1, c2)
                p.setPen(Qt.NoPen)
                p.setBrush(glow)
                p.drawRect(QRectF(r.x() - r.width() * 0.2, h * 0.45, r.width() * 1.4, h * 0.55))
                line = QLinearGradient(r.x() - r.width() * 0.2, 0, r.right() + r.width() * 0.2, 0)
                edge = QColor(theme.ACCENT)
                edge.setAlpha(0)
                core = QColor(theme.ACCENT_GLOW)
                line.setColorAt(0, edge)
                line.setColorAt(0.5, core)
                line.setColorAt(1, edge)
                p.setPen(QPen(line, 2))
                p.drawLine(QPointF(r.x() - r.width() * 0.2, h - 1), QPointF(r.right() + r.width() * 0.2, h - 1))
            if key not in self.enabled_keys:
                color = QColor("#3a4560")
            elif on:
                color = QColor("#ffffff")
            elif key == self.hover:
                color = theme.TEXT
            else:
                color = theme.MUTED
            p.setPen(color)
            p.drawText(r, Qt.AlignCenter, self.labels[key])


class Ornament(QWidget):
    """The game's small emblem over a card: a diamond with a wing at each side."""

    def __init__(self, color=None, width=46, parent=None):
        super().__init__(parent)
        self.color = QColor(color or "#b9b19a")
        self.setFixedSize(width, 12)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        d = 3.5
        p.drawPolygon([QPointF(cx - d, cy), QPointF(cx, cy - d), QPointF(cx + d, cy), QPointF(cx, cy + d)])
        for sign in (-1, 1):
            grad = QLinearGradient(cx + sign * 6, cy, cx + sign * (w / 2 - 1), cy)
            c = QColor(self.color)
            grad.setColorAt(0, c)
            c.setAlpha(0)
            grad.setColorAt(1, c)
            p.setPen(QPen(grad, 1.2))
            p.drawLine(QPointF(cx + sign * 6, cy), QPointF(cx + sign * (w / 2 - 1), cy))
            p.drawLine(QPointF(cx + sign * 7, cy - 2.5), QPointF(cx + sign * 13, cy - 2.5))


class _Band(QWidget):
    """The title band of a GameCard: a vertical gradient of its color, the ornament, the title in serif."""

    def __init__(self, fonts, title, color, sub="", parent=None):
        super().__init__(parent)
        self.color = QColor(color) if color else None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(fonts.px(10), fonts.px(6), fonts.px(10), fonts.px(6))
        lay.setSpacing(fonts.px(2))
        lay.addWidget(Ornament("#d9d2b8" if color else "#8b8a7e"), 0, Qt.AlignHCenter)
        self.title = label(title, serif_font(fonts, 11), theme.TEXT, elide=True)
        self.title.setAlignment(Qt.AlignHCenter)
        lay.addWidget(self.title)
        self.sub = None
        if sub:
            self.sub = label(sub, fonts.small, theme.TEXT if color else theme.MUTED)
            self.sub.setAlignment(Qt.AlignHCenter)
            lay.addWidget(self.sub)

    def paintEvent(self, _):
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        if self.color is None:
            grad.setColorAt(0, QColor("#2a2f3a"))
            grad.setColorAt(1, QColor("#161a22"))
        else:
            grad.setColorAt(0, self.color.lighter(120))
            grad.setColorAt(1, self.color.darker(140))
        p.fillRect(self.rect(), grad)


class GameCard(QFrame):
    """A mission-style card like the game's: an ornament and the title on a colored band (band: None grey, "done"
    green, "ready" gold, "warn" red), a body layout, and a footer bar («Collect Rewards»: a button or a line)."""

    def __init__(self, fonts, title, band=None, sub="", parent=None):
        super().__init__(parent)
        self.setObjectName("GameCard")
        self.fonts = fonts
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)
        color = {"done": theme.DONE_BAND, "ready": theme.READY_BAND, "warn": QColor("#5a2e1e")}.get(band)
        self.band = _Band(fonts, title, color, sub)
        root.addWidget(self.band)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(fonts.px(12), fonts.px(10), fonts.px(12), fonts.px(10))
        self.body.setSpacing(fonts.px(6))
        root.addLayout(self.body, 1)
        self.footer = None
        self._root = root

    def set_dim(self, on):
        self.setProperty("dim", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)

    def set_footer(self, text, action=None, enabled=True, color=None):
        """A bar at the bottom: a button when action is given (enabled or greyed), else a line of text."""
        if self.footer is not None:
            self.footer.setParent(None)
            self.footer.deleteLater()
        if action is not None:
            btn = QPushButton(text)
            btn.setProperty("ghost", True)
            btn.setEnabled(enabled)
            btn.clicked.connect(action)
            btn.setMinimumHeight(self.fonts.px(28))
            self.footer = btn
        else:
            lbl = label(text, self.fonts.small, color or theme.MUTED)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(f"color: {(color or theme.MUTED).name()}; background: #0b0e14; padding: 6px;")
            lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.footer = lbl
        self._root.addWidget(self.footer)


class ProgressRing(QWidget):
    """The game's circular progress («All 50% 84/167»): a cyan arc on a dark ring, a big text and a small one."""

    def __init__(self, fonts, ratio, big, small="", size=96, color=None, title="", parent=None):
        super().__init__(parent)
        self.fonts = fonts
        self.ratio = max(0.0, min(1.0, ratio))
        self.big, self.small, self.title = big, small, title
        self.color = QColor(color or theme.ACCENT)
        self.setFixedSize(size, size)

    def set(self, ratio, big, small=""):
        self.ratio, self.big, self.small = max(0.0, min(1.0, ratio)), big, small
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = min(self.width(), self.height())
        stroke = max(4.0, s * 0.07)
        r = QRectF(stroke, stroke, s - 2 * stroke, s - 2 * stroke)
        p.setPen(QPen(QColor("#1b222d"), stroke))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0a0d13"))
        p.drawEllipse(r.adjusted(stroke / 2, stroke / 2, -stroke / 2, -stroke / 2))
        if self.ratio > 0:
            span = -int(360 * 16 * self.ratio)
            glow = QColor(self.color)
            glow.setAlpha(70)
            p.setPen(QPen(glow, stroke * 1.9, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(r, 90 * 16, span)
            p.setPen(QPen(self.color, stroke, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(r, 90 * 16, span)
        parts = [t for t in (self.title, self.big, self.small) if t]
        scale = max(1.0, self.fonts.scale)
        big_font = self.fonts._make(self.fonts.family, s * 0.17 / scale, QFont.DemiBold)
        small_font = self.fonts._make(self.fonts.family, s * 0.095 / scale)
        sizes = []
        total = 0
        for t in parts:
            f = big_font if t == self.big else small_font
            fh = QFontMetrics(f).height()
            sizes.append((t, f, fh))
            total += fh
        y = (s - total) / 2
        for t, f, fh in sizes:
            p.setFont(f)
            p.setPen(self.color if t == self.big else theme.TEXT if t == self.title else theme.MUTED)
            p.drawText(QRectF(0, y, s, fh), Qt.AlignCenter, t)
            y += fh


class RewardTile(QWidget):
    """An icon tile with its count in the corner, like the game's reward rows; dim with a check when collected."""

    def __init__(self, fonts, pixmap=None, count="", done=False, size=44, tip="", text="", color=None, parent=None):
        super().__init__(parent)
        self.fonts = fonts
        self.pixmap, self.count, self.done, self.text = pixmap, count, done, text
        self.color = QColor(color) if color else None
        self.setFixedSize(size, size)
        if tip:
            self.setToolTip(tip)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        s = self.width()
        r = QRectF(0.5, 0.5, s - 1, s - 1)
        path = QPainterPath()
        path.addRoundedRect(r, 3, 3)
        grad = QLinearGradient(0, 0, 0, s)
        grad.setColorAt(0, QColor("#1c222d"))
        grad.setColorAt(1, QColor("#0e1219"))
        p.fillPath(path, grad)
        scale = max(1.0, self.fonts.scale)
        if self.pixmap is not None and not self.pixmap.isNull():
            p.setClipPath(path)
            inset = s * 0.08
            p.drawPixmap(QRectF(inset, inset, s - 2 * inset, s - 2 * inset).toRect(), self.pixmap)
            p.setClipping(False)
        elif self.text:
            p.setPen(self.color or theme.TEXT)
            p.setFont(self.fonts._make(self.fonts.family, s * 0.2 / scale, QFont.DemiBold))
            p.drawText(r.adjusted(2, 2, -2, -2), Qt.AlignCenter | Qt.TextWordWrap, self.text)
        if self.done:
            p.fillPath(path, QColor(0, 0, 0, 150))
            p.setPen(QPen(QColor("#e6e2d2"), 2.2, Qt.SolidLine, Qt.RoundCap))
            p.drawPolyline([QPointF(s * 0.3, s * 0.52), QPointF(s * 0.45, s * 0.68), QPointF(s * 0.72, s * 0.34)])
        if self.count:
            font = self.fonts._make(self.fonts.family, s * 0.2 / scale, QFont.DemiBold)
            p.setFont(font)
            fm = QFontMetrics(font)
            w = fm.horizontalAdvance(self.count) + 6
            box = QRectF(s - w - 2, s - fm.height() - 1, w, fm.height())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(box, 2, 2)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, self.count)
        p.setPen(QPen(self.color.darker(120) if self.color else QColor("#2f3747"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


class ChipGroup(QWidget):
    """A row of filter chips: one chosen (exclusive) or any number (multi). chips: [(key, label)] or
    [(key, label, tooltip)]; `changed` when the choice changes; selected() -> key | set of keys."""
    changed = Signal()

    def __init__(self, fonts, chips, current=None, multi=False, gold=False, parent=None):
        super().__init__(parent)
        self.multi = multi
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(fonts.px(6))
        self.buttons = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(not multi)
        for chip in chips:
            key, text = chip[0], chip[1]
            btn = QPushButton(text)
            btn.setProperty("chip", True)
            if gold:
                btn.setProperty("gold", True)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFont(fonts.small)
            if len(chip) > 2 and chip[2]:
                btn.setToolTip(chip[2])
            self.group.addButton(btn)
            self.buttons[key] = btn
            lay.addWidget(btn)
            btn.toggled.connect(lambda _on, k=key: self._toggled(k))
        if current is not None:
            for k in (current if multi and not isinstance(current, str) else [current]):
                if k in self.buttons:
                    self.buttons[k].setChecked(True)
        elif not multi and self.buttons:
            next(iter(self.buttons.values())).setChecked(True)
        lay.addStretch(1)

    def _toggled(self, key):
        if self.multi or self.buttons[key].isChecked():
            self.changed.emit()

    def selected(self):
        if self.multi:
            return {k for k, b in self.buttons.items() if b.isChecked()}
        return next((k for k, b in self.buttons.items() if b.isChecked()), None)

    def set_selected(self, key, on=True):
        if key in self.buttons:
            self.buttons[key].setChecked(on)

    def set_label(self, key, text):
        if key in self.buttons:
            self.buttons[key].setText(text)


class SearchBox(QLineEdit):
    """A search field with the game's rounded look; `changed(text)` as you type."""
    changed = Signal(str)

    def __init__(self, fonts, placeholder="", parent=None):
        super().__init__(parent)
        self.setProperty("search", True)
        self.setPlaceholderText(placeholder or tr("Buscar…"))
        self.setClearButtonEnabled(True)
        self.setFont(fonts.body)
        self.setMinimumWidth(fonts.px(160))
        self.textChanged.connect(lambda t: self.changed.emit(t.strip()))


class PageHeader(QWidget):
    """A page's head: the badge, the title in the game's serif, a line under it, and a right side for actions."""

    def __init__(self, fonts, title, subtitle="", parent=None):
        super().__init__(parent)
        from .mapwindow import badge_label
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(fonts.px(4))
        row = QHBoxLayout()
        row.setSpacing(fonts.px(10))
        row.addWidget(badge_label(fonts.px(28), self.devicePixelRatioF()))
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = label(title, fonts.name_big, theme.TEXT)
        col.addWidget(self.title)
        self.subtitle = label(subtitle, fonts.small, theme.MUTED)
        self.subtitle.setVisible(bool(subtitle))
        col.addWidget(self.subtitle)
        row.addLayout(col, 1)
        self.right = QHBoxLayout()
        self.right.setSpacing(fonts.px(8))
        row.addLayout(self.right)
        root.addLayout(row)
        root.addWidget(Divider(plain=True))

    def set_subtitle(self, text):
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))

    def add_right(self, widget):
        self.right.addWidget(widget)

    def add_picker(self, widget):
        """The character picker (charpicker.CharacterPicker): first on the right, before the page's own buttons."""
        self.right.insertWidget(0, widget)


class KeyValue(QWidget):
    """A row of the game's tooltips: the name at the left, the value at the right, in their colors."""

    def __init__(self, fonts, key, value, key_color=None, value_color=None, font=None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(fonts.px(8))
        lay.addWidget(label(key, font or fonts.body, key_color or theme.TEXT, elide=True), 1)
        lay.addWidget(label(value, font or fonts.body, value_color or theme.TEXT))


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
