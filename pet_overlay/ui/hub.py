"""The AionDex window (v2): every feature in one place. A sidebar with the features in groups, the pages in the
middle (the detail windows of before, embedded: Mejoras, Arcanas, Builds, Crafting, the maps...), and on the right
the HUD: while this window is open the overlay leaves its spot on the screen and sits docked in the top-right corner
here (it is the same always-on-top window, moved: its hotkeys and its click-through keep working), with a card under
it that says where you are and what comes next. Close the window and the HUD goes back where you had it.

Pages are made on first use and kept (a page hidden by the stack keeps its state: tabs, filters, scroll). The
Controller's open_* methods all end in show_page(), so the tray, the HUD and the pages themselves open the same
window."""
import time

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from . import theme
from .design import HUB_STYLE, Card, Divider, Hero, Meter, NavItem, Stat, hub_font
from .mapwindow import badge_label
from .theme import Fonts
from .widgets import label
from ..gear import CLASS_OF_JOB
from ..i18n import tr

GLYPH_HOME = "\ue80f"
GLYPH_STATUS = "\ue7ba"
GLYPH_DPS = "\ue945"
GLYPH_COLLECTION = "\ue8b7"
GLYPH_EXCLUSIVE = "\ue735"
GLYPH_MAP = "\ue707"
GLYPH_WORLD = ""
GLYPH_TRACKER = "\ue823"
GLYPH_PEOPLE = "\ue716"
GLYPH_ARENA = "\ue7c1"
GLYPH_DIAG = "\ue9d9"
GLYPH_SETTINGS = "\ue713"
GLYPH_BELL = "\ue7e7"
RAIL_WIDTH = 214
HUD_MARGIN = 12

# the sidebar: (group, [(nav key, label, glyph or None for the module's own icon, Controller method, kwargs)])
NAV = (
    (tr("Ahora"), (("home", tr("Inicio"), GLYPH_HOME, "open_home", {}),)),
    (tr("Personaje"), (("upgrades", tr("Mejoras"), None, "open_upgrades", {}),
                       ("arcana", tr("Arcanas"), None, "open_arcana", {}),
                       ("builds", tr("Builds"), None, "open_builds", {}),
                       ("status", "Status", GLYPH_STATUS, "open_status", {}),
                       ("dps", "DPS", GLYPH_DPS, "open_dps_details", {}))),
    (tr("Progreso"), (("missions", "Season Missions", None, "open_missions", {}),
                      ("achievements", tr("Logros"), None, "open_achievements", {}),
                      ("events", tr("Eventos"), None, "open_events", {}),
                      ("passes", tr("Pase"), None, "open_panel_page", {"key": "passes"}))),
    (tr("Pets"), (("pets", tr("Pets"), None, "open_panel_page", {"key": "pets"}),
                  ("collection", tr("Colección"), GLYPH_COLLECTION, "open_collection", {}),
                  ("exclusive", tr("Exclusivos"), GLYPH_EXCLUSIVE, "open_exclusive_pets", {}),
                  ("map", tr("Mapa de pets"), GLYPH_MAP, "open_map", {}))),
    (tr("Mundo"), (("world", tr("Mapa del mundo"), GLYPH_WORLD, "open_world_map", {}),
                   ("tracker", "Tracker", GLYPH_TRACKER, "open_tracker", {}),
                   ("players", tr("Jugadores"), GLYPH_PEOPLE, "open_players", {}),
                   ("arena", "Arena", GLYPH_ARENA, "open_arena", {}))),
    (tr("Profesiones"), (("crafting", "Crafting", None, "open_crafting", {"tab": "crafting"}),
                         ("market", "Market", None, "open_crafting", {"tab": "market"}),
                         ("leveling", tr("Levear"), None, "open_crafting", {"tab": "leveling"}),
                         ("gathering", "Gathering", None, "open_crafting", {"tab": "gathering"}))),
    (tr("Sistema"), (("notifications", tr("Notificaciones"), GLYPH_BELL, "open_panel_page", {"key": "notifications"}),
                     ("diagnostics", tr("Diagnóstico"), GLYPH_DIAG, "open_diagnostics", {}),
                     ("settings", tr("Ajustes"), GLYPH_SETTINGS, "open_panel_page", {"key": "settings"}))),
)


def zone_name(ctl):
    """The zone you are in, by the game's name (its slug is what the Controller keeps)."""
    slug = ctl.current_zone
    if not slug:
        return ""
    zone = ctl.mapdata.zones.get(slug) if getattr(ctl, "mapdata", None) and ctl.mapdata.available else None
    return (zone or {}).get("name") or slug


class PanelPage(QWidget):
    """A page made of a module's own panel (the one the HUD draws, a bigger copy): Pase, Notificaciones, Ajustes,
    Pets. The HUD's click protocol (a plain click runs the first ancestor's on_click) is kept here."""

    def __init__(self, ctl, module, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.module = module
        self.panel = type(module.panel)(ctl, fonts)
        root = QVBoxLayout(self)
        root.setContentsMargins(fonts.px(22), fonts.px(16), fonts.px(22), fonts.px(16))
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        head.addWidget(label(module.title, fonts.name_big, theme.TEXT), 1)
        root.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, fonts.px(8), 0, 0)
        self.panel.setMaximumWidth(fonts.px(720))
        lay.addWidget(self.panel, 0, Qt.AlignTop | Qt.AlignLeft)
        lay.addStretch(1)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        self._press = None

    def refresh(self):
        """The module draws its data into this page's panel (its render feeds the panel), then gets its own back."""
        module, mine = self.module, self.panel
        hud_panel = module.panel
        module.panel = mine
        try:
            module.render(False)
        except Exception:                      # noqa: BLE001 - a panel drawn for the HUD may miss something here
            import logging
            logging.getLogger(__name__).exception("panel page %s", module.key)
        finally:
            module.panel = hud_panel

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        if self._press is None:
            return
        moved = (event.globalPosition().toPoint() - self._press).manhattanLength()
        self._press = None
        if moved > 4:
            return
        widget = self.childAt(event.position().toPoint())
        while widget is not None and not hasattr(widget, "on_click"):
            widget = widget.parentWidget()
        if widget is not None:
            widget.on_click()
            QTimer.singleShot(0, self.refresh)


class NowPanel(QWidget):
    """Under the docked HUD, flat (no box): who you play and where, your two numbers, the Tracker's next
    activities, what is unread. A divider between sections carries the structure."""

    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        lay = QVBoxLayout(self)
        lay.setContentsMargins(fonts.px(6), fonts.px(4), fonts.px(6), 0)
        lay.setSpacing(fonts.px(6))
        self.who = label("", hub_font(fonts, 11, QFont.DemiBold), theme.TEXT, elide=True)
        lay.addWidget(self.who)
        self.where = label("", fonts.small, theme.MUTED, elide=True)
        lay.addWidget(self.where)
        row = QHBoxLayout()
        row.setSpacing(fonts.px(8))
        self.il = Stat(fonts, "—", tr("Nivel de objeto"), theme.GOLD)
        self.cp = Stat(fonts, "—", "CP", theme.ACCENT)
        row.addWidget(self.il, 1)
        row.addWidget(self.cp, 1)
        lay.addLayout(row)
        lay.addWidget(Divider())
        self.next_title = label(tr("Lo siguiente"), fonts.name, theme.TEXT)
        lay.addWidget(self.next_title)
        self.next_rows = QVBoxLayout()
        self.next_rows.setSpacing(fonts.px(2))
        lay.addLayout(self.next_rows)
        self.tracker_link = QPushButton(tr("Abrir Tracker"))
        self.tracker_link.setProperty("flat", True)
        self.tracker_link.clicked.connect(ctl.open_tracker)
        lay.addWidget(self.tracker_link, 0, Qt.AlignLeft)
        lay.addWidget(Divider())
        self.unread = QPushButton("")
        self.unread.setProperty("flat", True)
        self.unread.clicked.connect(lambda: ctl.open_panel_page("notifications"))
        lay.addWidget(self.unread, 0, Qt.AlignLeft)

    def refresh(self):
        from .upgradeswindow import _clear
        ctl, f = self.ctl, self.fonts
        name = ctl.character_name() or ctl.settings.get("last_character")
        ch = ctl.character
        if ch:
            cls = CLASS_OF_JOB.get(getattr(ch, "job", None), "")
            self.who.setText(ch.name + (f" · {cls}" if cls else "") + (tr(" · Nv. {0}", ch.level) if getattr(ch, "level", 0) else ""))
        else:
            self.who.setText(name or tr("Sin personaje"))
        self.where.setText(zone_name(ctl) or (tr("Entra al juego con AionDex abierto") if not ch else tr("Zona: aún no la sé")))
        il = cp = None
        try:
            me = ctl.upgrades_me(name) if name else None
            if me is not None:
                il, cp = me.item_level, me.cp
        except Exception:                       # noqa: BLE001 - Mejoras' business
            pass
        self.il.value.setText(f"{il:,}" if il else "—")
        self.cp.value.setText(f"{cp:,}" if cp else "—")
        _clear(self.next_rows)
        rows = ctl.tracker.upcoming()[:3]
        if rows:
            from ..modules.tracker import when_text
            now = time.time()
            for entry, st in rows:
                text, color = when_text(st, now)
                line = QHBoxLayout()
                line.addWidget(label(entry.name, f.body, theme.TEXT, elide=True), 1)
                line.addWidget(label(text, f.small, color))
                self.next_rows.addLayout(line)
        else:
            self.next_rows.addWidget(label(tr("Nada marcado en el Tracker"), f.small, theme.MUTED))
        n = ctl.notifications.unread
        self.unread.setText(tr("{0} avisos sin leer", n) if n != 1 else tr("1 aviso sin leer"))
        self.unread.setVisible(bool(n))


class HomePage(QWidget):
    """Inicio: the one next step across the app, your numbers, and the Dashboard's cards (what needs you now)."""

    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        root = QVBoxLayout(self)
        root.setContentsMargins(fonts.px(22), fonts.px(16), fonts.px(22), fonts.px(16))
        root.setSpacing(fonts.px(12))
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        head.addWidget(label(tr("Inicio"), fonts.name_big, theme.TEXT), 1)
        self.stamp = label("", fonts.small, theme.MUTED)
        head.addWidget(self.stamp)
        root.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(0, 0, fonts.px(6), 0)
        self.body.setSpacing(fonts.px(12))
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        self._press = None

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def refresh(self):
        from .upgradeswindow import _clear
        from ..status import ALERTS
        f = self.fonts
        ctl = self.ctl
        _clear(self.body)
        self.stamp.setText(time.strftime("%d/%m · %H:%M"))
        name = ctl.character_name() or ctl.settings.get("last_character")
        now = time.time()
        # the one next step (the same the HUD and Mejoras show)
        nxt = ctl.next_step(name)
        if nxt is not None:
            eyebrow = {"upgrades": tr("Tu siguiente paso · Mejoras"), "arcana": tr("Tu siguiente paso · Arcanas"),
                       "tracker": tr("Lo siguiente en el Tracker")}.get(nxt.source, tr("Tu siguiente paso"))
            self.body.addWidget(Hero(f, eyebrow, nxt.title, nxt.summary, (nxt.action, nxt.open)))
        else:
            self.body.addWidget(Hero(f, tr("Todo al día"), tr("Entra al juego con AionDex abierto y te digo qué hacer."),
                                     "", (tr("Ajustes"), lambda: ctl.open_panel_page("settings"))))
        # your numbers (the official profile moved by your changes: no plan is built for this)
        row = QHBoxLayout()
        row.setSpacing(f.px(10))
        ch = ctl.character
        il = cp = None
        try:
            me = ctl.upgrades_me(name) if name else None
            if me is not None:
                il, cp = me.item_level, me.cp
        except Exception:                       # noqa: BLE001
            pass
        for value, title, color in ((ch.name if ch else (name or "—"), tr("Personaje"), theme.TEXT),
                                    (f"{il:,}" if il else "—", tr("Nivel de objeto"), theme.GOLD),
                                    (f"{cp:,}" if cp else "—", "CP", theme.ACCENT),
                                    (zone_name(ctl) or "—", tr("Zona"), theme.TEXT),
                                    (str(ctl.notifications.unread or 0), tr("Sin leer"), theme.MUTED)):
            row.addWidget(Stat(f, value, title, color), 1)
        self.body.addLayout(row)
        grid = QGridLayout()
        grid.setHorizontalSpacing(f.px(12))
        # this week: season missions, achievements to claim, the Pass
        week = Card(f, tr("Esta semana"))
        try:
            done, total = ctl.missions.week_summary(name, now) if name and ctl.missions.available else (0, 0)
        except Exception:                       # noqa: BLE001
            done, total = 0, 0
        if total:
            line = QHBoxLayout()
            line.addWidget(label(tr("Season Missions semanales"), f.body, theme.TEXT), 1)
            line.addWidget(label(f"{done} / {total}", f.name, theme.GAIN if done == total else theme.TEXT))
            week.body.addLayout(line)
            week.body.addWidget(Meter(done / total, "#7bd88f" if done == total else "#f0c24e", height=7))
        for model, text, opener in ((ctl.missions, tr("misiones con recompensa por cobrar"), ctl.open_missions),
                                    (ctl.achievements, tr("logros con recompensa por cobrar"), ctl.open_achievements)):
            try:
                n = len(model.ready(name, now)) if name and model.available else 0
            except Exception:                   # noqa: BLE001
                n = 0
            if n:
                link = QPushButton(f"{n} {text}")
                link.setProperty("flat", True)
                link.clicked.connect(opener)
                week.body.addWidget(link, 0, Qt.AlignLeft)
        if not total:
            week.body.addWidget(label(tr("Entra al juego: leo tus misiones y tus logros."), f.small, theme.MUTED))
        week.body.addStretch(1)
        grid.addWidget(week, 0, 0)
        # what needs you now: Status alerts, the Tracker's next activities, pets to farm here
        now_card = Card(f, tr("Ahora mismo"))
        alerts = [(key, ctl.status.targets(key, now)) for key in ALERTS]
        alerts = [(key, who) for key, who in alerts if who]
        for key, who in alerts[:3]:
            line = QHBoxLayout()
            line.addWidget(label(ALERTS[key][0], f.body, theme.COMBAT), 1)
            line.addWidget(label(", ".join(who[:3]), f.small, theme.MUTED))
            now_card.body.addLayout(line)
        rows = ctl.tracker.upcoming()[:3]
        if rows:
            from ..modules.tracker import when_text
            for entry, st in rows:
                text, color = when_text(st, now)
                line = QHBoxLayout()
                line.addWidget(label(entry.name, f.body, theme.TEXT, elide=True), 1)
                line.addWidget(label(text, f.small, color))
                now_card.body.addLayout(line)
        try:
            zone, nearby, _ = ctl.nearby_pets()
            left = [p for p, _ in nearby if not p.is_max]
        except Exception:                       # noqa: BLE001
            zone, left = None, []
        if zone and left:
            link = QPushButton(tr("{0} pets por subir en {1}", len(left), zone.get("name", "")))
            link.setProperty("flat", True)
            link.clicked.connect(lambda: ctl.open_panel_page("pets"))
            now_card.body.addWidget(link, 0, Qt.AlignLeft)
        if not alerts and not rows and not left:
            now_card.body.addWidget(label(tr("Nada pendiente ahora mismo."), f.small, theme.MUTED))
        now_card.body.addStretch(1)
        grid.addWidget(now_card, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)
        self.body.addStretch(1)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        if self._press is None:
            return
        moved = (event.globalPosition().toPoint() - self._press).manhattanLength()
        self._press = None
        if moved > 4:
            return
        widget = self.childAt(event.position().toPoint())
        while widget is not None and not hasattr(widget, "on_click"):
            widget = widget.parentWidget()
        if widget is not None:
            widget.on_click()


class HubWindow(QWidget):
    moved = Signal()

    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        f = self.fonts
        self.setObjectName("Hub")
        self.setStyleSheet(HUB_STYLE)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(ctl.app_icon)
        self.setMinimumSize(f.px(1100), f.px(700))
        self.pages = {}                  # page key -> widget in the stack
        self.items = {}                  # nav key -> NavItem
        self.current = None              # nav key shown
        self._page_of_nav = {}           # nav key -> page key

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # --- the sidebar ---
        rail = QFrame()
        rail.setObjectName("NavRail")
        rail.setFixedWidth(f.px(RAIL_WIDTH))
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(f.px(10), f.px(12), f.px(10), f.px(10))
        rl.setSpacing(f.px(2))
        brand = QHBoxLayout()
        brand.setSpacing(f.px(8))
        brand.addWidget(badge_label(f.px(28), self.devicePixelRatioF()))
        brand.addWidget(label(APP_NAME, f.name_big, theme.TEXT), 1)
        rl.addLayout(brand)
        rl.addSpacing(f.px(6))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        nav = QVBoxLayout(holder)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(f.px(1))
        modules = {m.key: m for m in ctl.modules}
        for group, entries in NAV:
            g = QLabel(group.upper())
            g.setObjectName("NavGroup")
            g.setFont(f.section)
            nav.addWidget(g)
            for key, text, glyph, method, kwargs in entries:
                item = NavItem(glyph or "", text, f)
                if glyph is None and key in modules:
                    icon = modules[key].icon()
                    if isinstance(icon, QIcon):
                        px = icon.pixmap(QSize(f.px(16), f.px(16)) * self.devicePixelRatioF())
                        px.setDevicePixelRatio(self.devicePixelRatioF())
                        item.glyph.setPixmap(px)
                item.clicked.connect(lambda _=False, m=method, kw=kwargs: getattr(ctl, m)(**kw))
                nav.addWidget(item)
                self.items[key] = item
        nav.addStretch(1)
        scroll.setWidget(holder)
        rl.addWidget(scroll, 1)
        foot = QHBoxLayout()
        foot.addWidget(label(f"v{__version__}", f.small, theme.MUTED), 1)
        hud = QPushButton(tr("HUD"))
        hud.setProperty("flat", True)
        hud.setToolTip(tr("Mostrar u ocultar el HUD (el overlay)"))
        hud.clicked.connect(lambda: ctl.toggle_visible("menú"))
        foot.addWidget(hud)
        rl.addLayout(foot)
        root.addWidget(rail)
        # --- the pages ---
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.stack, 1)
        # --- the right rail: the HUD's place (the overlay sits exactly on the slot) and the Now panel ---
        right = QFrame()
        right.setObjectName("RightRail")
        right.setFixedWidth(ctl.window.width() + f.px(HUD_MARGIN) * 2)
        self.right = right
        rr = QVBoxLayout(right)
        rr.setContentsMargins(f.px(HUD_MARGIN), f.px(HUD_MARGIN), f.px(HUD_MARGIN), f.px(HUD_MARGIN))
        rr.setSpacing(f.px(12))
        self.hud_slot = QFrame()
        self.hud_slot.setObjectName("HudSlot")
        self.hud_slot.setFixedSize(ctl.window.width(), ctl.window.height())
        slot = QVBoxLayout(self.hud_slot)
        slot.setContentsMargins(f.px(12), f.px(10), f.px(12), f.px(10))
        self.hud_hidden = label(tr("HUD oculto"), f.small, theme.MUTED)
        slot.addWidget(self.hud_hidden, 0, Qt.AlignTop)
        self.hud_show = QPushButton(tr("Mostrar el HUD"))
        self.hud_show.setProperty("flat", True)
        self.hud_show.clicked.connect(lambda: ctl.toggle_visible("menú"))
        slot.addWidget(self.hud_show, 0, Qt.AlignLeft | Qt.AlignTop)
        slot.addStretch(1)
        rr.addWidget(self.hud_slot, 0, Qt.AlignTop)
        self.now = NowPanel(ctl, f)
        rr.addWidget(self.now)
        rr.addStretch(1)
        root.addWidget(right)
        self._timer = QTimer(self, interval=10_000, timeout=self.refresh)
        geo = ctl.settings.get("hub_geometry")
        if isinstance(geo, list) and len(geo) == 4:
            self.setGeometry(*geo)
        else:
            from PySide6.QtGui import QGuiApplication
            area = QGuiApplication.primaryScreen().availableGeometry()
            w, h = min(f.px(1500), area.width() - 40), min(f.px(940), area.height() - 60)
            self.setGeometry(area.x() + (area.width() - w) // 2, area.y() + (area.height() - h) // 2, w, h)

    # --- pages ---
    def show_page(self, page, widget, nav=None):
        """Show widget as the page `page` (added on first use); nav: the sidebar entry to light (page by default)."""
        if page not in self.pages:
            self.pages[page] = widget
            self.stack.addWidget(widget)          # re-parents the window into the stack (a child widget now)
        self.stack.setCurrentWidget(widget)
        self.current = nav or page
        self._page_of_nav[self.current] = page
        for key, item in self.items.items():
            item.setChecked(key == self.current)
        if not self.isVisible():
            self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def page(self, key):
        return self.pages.get(key)

    def refresh(self):
        """The badges and the Now card; the current page if it is one of ours (the pages refresh themselves)."""
        if not self.isVisible():
            return
        unread = self.ctl.notifications.unread
        if "notifications" in self.items:
            self.items["notifications"].set_badge(unread)
        try:
            from ..modules.diagnostics import open_problems
            self.items["diagnostics"].set_badge(len(open_problems(self.ctl)))
        except Exception:                       # noqa: BLE001
            pass
        self.now.refresh()
        hidden = not self.ctl.window.isVisible()
        self.hud_hidden.setVisible(hidden)
        self.hud_show.setVisible(hidden)
        page = self.stack.currentWidget()
        if isinstance(page, (PanelPage, HomePage)):
            page.refresh()

    # --- the HUD's place ---
    def hud_anchor(self):
        """Where the HUD goes while this window is open: the slot's top-left, in screen pixels."""
        return self.hud_slot.mapToGlobal(QPoint(0, 0))

    def hud_resized(self, size):
        self.hud_slot.setFixedSize(size)

    # --- window events ---
    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()
        self.refresh()
        QTimer.singleShot(0, self.ctl.hub_shown)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()
        self.ctl.settings["hub_geometry"] = [self.x(), self.y(), self.width(), self.height()]
        self.ctl._save_settings()
        self.ctl.hub_hidden()

    def closeEvent(self, event):
        event.ignore()                           # the pages keep their state: hide, do not destroy
        self.hide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            if self.isMinimized():
                self.ctl.hub_hidden()
            elif self.isVisible():
                QTimer.singleShot(0, self.ctl.hub_shown)

    def moveEvent(self, event):
        super().moveEvent(event)
        self.moved.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.moved.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)
