"""The app menu (switch between feature modules, exit) and its icons."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu

from . import theme

GLYPH_EXIT = ""

MENU_STYLE = f"""
QMenu {{ background: #0d1628; border: 1px solid #24345a; border-radius: 9px; padding: 5px;
    color: {theme.TEXT.name()}; font-family: 'Segoe UI'; font-size: 10pt; }}
QMenu::item {{ padding: 6px 22px 6px 8px; border-radius: 6px; }}
QMenu::item:selected {{ background: #1d3157; }}
QMenu::item:checked {{ color: {theme.GOLD.name()}; }}
QMenu::indicator {{ width: 0; height: 0; }}
QMenu::icon {{ padding-left: 6px; }}
QMenu::separator {{ height: 1px; background: #24345a; margin: 4px 8px; }}
"""


def styled_menu(parent=None):
    menu = QMenu(parent)
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    menu.setStyleSheet(MENU_STYLE)
    return menu


def paw_icon(color=None, size=64):
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(color or theme.ACCENT)
    s = size / 64
    p.drawEllipse(QRectF(16 * s, 28 * s, 32 * s, 28 * s))
    for cx, cy in ((14, 22), (26, 12), (38, 12), (50, 22)):
        p.drawEllipse(QPointF(cx * s, cy * s), 7 * s, 8 * s)
    p.end()
    return QIcon(px)


def glyph_icon(glyph, color, family, size=64):
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    font = QFont(family)
    font.setPixelSize(int(size * 0.7))
    p.setFont(font)
    p.setPen(color)
    p.drawText(px.rect(), Qt.AlignCenter, glyph)
    p.end()
    return QIcon(px)


def build_app_menu(ctl, fonts, parent=None):
    """Modules (exclusive, the active one highlighted) and Salir."""
    menu = styled_menu(parent)
    group = QActionGroup(menu)
    group.setExclusive(True)
    for module in ctl.modules:
        text = module.menu_label() if hasattr(module, "menu_label") else module.title
        act = QAction(module.icon(), text, menu, checkable=True, checked=module is ctl.active_module)
        act.triggered.connect(lambda _=False, key=module.key: ctl.switch_module(key))
        group.addAction(act)
        menu.addAction(act)
    menu.addSeparator()
    menu.addAction(glyph_icon(GLYPH_EXIT, theme.MUTED, fonts.icons_family), "Salir", ctl.quit)
    return menu
