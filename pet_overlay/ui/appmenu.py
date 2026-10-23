"""The app menu (switch between feature modules, exit) and its icons."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu

from . import theme
from ..i18n import tr

GLYPH_WINDOW = "\ue8a7"      # OpenInNewWindow
GLYPH_EXIT = ""
GLYPH_WORLD = "\ue909"       # World
GLYPH_PEOPLE = ""       # People in Segoe MDL2 / Fluent
GLYPH_RESTART = ""     # Refresh

MENU_STYLE = f"""
QMenu {{ background: #0d1628; border: 1px solid #24345a; border-radius: 9px; padding: 5px;
    color: {theme.TEXT.name()}; font-family: 'Segoe UI'; font-size: 10pt; }}
QMenu::item {{ padding: 6px 22px 6px 8px; border-radius: 6px; }}
QMenu::item:selected {{ background: #1d3157; }}
QMenu::item:checked {{ color: {theme.GOLD.name()}; }}
QMenu::indicator {{ width: 0; height: 0; }}
QMenu::icon {{ padding-left: 6px; }}
QMenu::separator {{ height: 1px; background: #24345a; margin: 4px 8px; }}
QMenu::right-arrow {{ width: 8px; height: 8px; padding-right: 8px; }}
"""
# modules shown together in a submenu (a "›" row that opens to the side), at the place of the first one
GROUPS = ((tr("Personaje"), "character", ("upgrades", "arcana", "builds")),
          (tr("Progreso"), "progress", ("missions", "achievements", "passes", "events")),
          (tr("Profesiones y Market"), "professions", ("crafting", "market", "leveling", "gathering")))


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
    """Modules (exclusive, the active one highlighted; some in a submenu of their own), the players of your group,
    Reiniciar and Salir."""
    menu = styled_menu(parent)
    menu.addAction(glyph_icon(GLYPH_WINDOW, theme.GOLD, fonts.icons_family), tr("Abrir la ventana de AionDex"),
                   ctl.open_hub)
    menu.addSeparator()
    group = QActionGroup(menu)
    group.setExclusive(True)
    grouped = {key: g for g in GROUPS for key in g[2]}
    done = set()

    def module_action(module, owner):
        text = module.menu_label() if hasattr(module, "menu_label") else module.title
        act = QAction(module.icon(), text, owner, checkable=True, checked=module is ctl.active_module)
        act.triggered.connect(lambda _=False, key=module.key: ctl.switch_module(key))
        group.addAction(act)
        return act

    for module in ctl.modules:
        g = grouped.get(module.key)
        if g is None:
            menu.addAction(module_action(module, menu))
            continue
        if g[0] in done:
            continue
        done.add(g[0])
        members = [m for m in ctl.modules if m.key in g[2]]
        active = next((m for m in members if m is ctl.active_module), None)
        sub = styled_menu(menu)
        sub.setTitle(g[0])                           # the module on is the gold one inside (a longer title cut)
        sub.setIcon(members[0].icon())
        for m in members:
            sub.addAction(module_action(m, sub))
        entry = menu.addMenu(sub)
        if active:                                   # the group of the module on: in gold like a checked row
            font = entry.font()
            font.setBold(True)
            entry.setFont(font)
    menu.addSeparator()
    menu.addAction(glyph_icon(GLYPH_WORLD, theme.ACCENT, fonts.icons_family), tr("Mapa del mundo"), ctl.open_world_map)
    menu.addAction(glyph_icon(GLYPH_PEOPLE, theme.ACCENT, fonts.icons_family), tr("Jugadores de tu party / force"),
                   ctl.open_players)
    restart = menu.addAction(glyph_icon(GLYPH_RESTART, theme.MUTED, fonts.icons_family), tr("Reiniciar"),
                             ctl.restart_app)
    restart.setToolTip(tr("Cierra AionDex y lo vuelve a abrir (por si algo se quedó trabado)"))
    menu.addAction(glyph_icon(GLYPH_EXIT, theme.MUTED, fonts.icons_family), tr("Salir"), ctl.quit)
    return menu
