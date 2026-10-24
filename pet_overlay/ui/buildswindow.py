"""Builds window: your skill setups kept on your PC beyond the game's 3 presets (builds.py): save the preset you
have on, bring in another player's from their official profile, share or paste a code, and «Comparar Mastery» /
«Comparar Stigma»: once you copied it by hand in the game's Skill window, what of your preset in use matches it and
what does not, one tab at a time. AION 2 drops simulated clicks (SendInput and posted ones; it only takes the moves:
2026-10-22), so AionDex no longer applies a build itself. Its second tab is the Afinador of the macro
(ui/tunerwindow.py), part of the builds."""
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QComboBox, QStackedWidget, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..armory import shugo_url
from ..builds import Builds, bonus_now, character_of, class_of_build, class_of_job, entry_levels, game_level
from ..skills import SLOT_KEYS
from . import theme
from .design import GameTabs, PageHeader
from .mapwindow import STYLE
from .skilltiles import ACTIVE_BORDER, PASSIVE_BORDER, SkillTile
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label
from ..i18n import tr

ICON = 40                      # a skill tile: big enough for its «Lv. N» like the game's
BAR_ICON = 34                  # the bar's and the macro's tiles: 12 keys must fit the detail column
CELL = 230                     # a skill cell's least width in the skills panel
SIDE_STYLE = f"""
QFrame#BuildRow {{ background: {theme.SURFACE.name()}; border: 1px solid {theme.LINE.name()}; border-radius: 5px; }}
QFrame#BuildRow[selected="true"] {{ border-color: {theme.ACCENT.name()}; background: {theme.SURFACE_2.name()}; }}
QFrame#Panel {{ background: {theme.SURFACE.name()}; border: 1px solid {theme.LINE.name()}; border-radius: 6px; }}
QLabel#PresetChip {{ background: transparent; color: {theme.GOLD.name()}; border: 1px solid {theme.GOLD_DARK.name()};
    border-radius: 3px; padding: 0 5px; }}
QLabel#PresetChip[active="true"] {{ background: {theme.GOLD.name()}; color: {theme.WINDOW_BG.name()};
    border-color: {theme.GOLD.name()}; }}
QPushButton[flat="true"] {{ background: transparent; color: {theme.ACCENT.name()}; border: none; padding: 3px 6px; }}
QPushButton[flat="true"]:hover {{ color: {theme.ACCENT_GLOW.name()}; }}
QPushButton[ghost="true"] {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {theme.SURFACE_2.name()},
    stop:1 {theme.SURFACE.name()}); color: {theme.TEXT.name()}; border: 1px solid {theme.LINE.name()};
    padding: 6px 14px; }}
QPushButton[ghost="true"]:hover {{ border-color: {theme.GOLD.name()}; }}
"""
SOURCE_TEXT = {"own": (tr("Tu preset"), theme.GAIN), "player": (tr("Perfil oficial"), theme.ASMODIAN),
               "code": (tr("Código"), theme.GOLD)}
KIND_TITLE = (("active", tr("Activas")), ("passive", tr("Pasivas")), ("stigma", tr("Estigmas")))


def _ago_exact(ts):
    mins = max(0, (time.time() - (ts or 0)) / 60)
    if mins < 1:
        return tr("hace un momento")
    if mins < 60:
        return tr("hace {0:.0f} min", mins)
    return tr("hace {0:.0f} h", mins / 60) if mins < 48 * 60 else tr("hace {0:.0f} días", mins / 1440)


def _ago(ts):
    days = (time.time() - (ts or 0)) / 86400
    return tr("hoy") if days < 1 else tr("ayer") if days < 2 else tr("hace {0:.0f} días", days)


class BuildsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.builds = ctl.builds
        self.fonts = Fonts(1.0)
        self.selected = None
        self.compare = None               # (build id, "mastery" | "stigma") of the comparison shown
        self.waiting = None               # (name, server, since, build id or None) of a player profile being fetched
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — Builds")
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + SIDE_STYLE)
        self.resize(1320, 900)
        f = self.fonts

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(0)
        self.tabs = GameTabs(f, [("builds", "Builds"), ("afinador", tr("Afinador del macro"))], "builds")
        self.tabs.changed.connect(self.show_tab)
        tabs = QHBoxLayout()
        tabs.setContentsMargins(16, 0, 16, 4)
        tabs.addWidget(self.tabs)
        outer.addLayout(tabs)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        page = QWidget()
        self.stack.addWidget(page)
        self.tuner_page = None            # the Afinador, made the first time its tab opens

        root = QVBoxLayout(page)
        root.setContentsMargins(16, 6, 16, 12)
        root.setSpacing(10)
        self.header = PageHeader(f, "Builds", " ")
        self.subtitle = self.header.subtitle              # the status line (what was saved, imported, read)
        self.cls = QComboBox()
        self.cls.setToolTip(tr("Clase de las builds de la lista (por defecto, la de tu personaje)"))
        self.cls.currentIndexChanged.connect(lambda _: self.refresh())
        self.header.add_right(self.cls)
        save = QPushButton(tr("Guardar mi preset"))
        save.setToolTip(tr("Guarda el preset que tienes puesto en el juego (sus puntos, especialidades, estigmas y "
                           "barra)"))
        save.clicked.connect(self._save_preset)
        self.header.add_right(save)
        imp = QPushButton(tr("Importar de jugador"))
        imp.setProperty("ghost", True)
        imp.clicked.connect(lambda: self.import_row.setVisible(not self.import_row.isVisible()))
        self.header.add_right(imp)
        paste = QPushButton(tr("Pegar código"))
        paste.setProperty("ghost", True)
        paste.setToolTip(tr("Importa la build cuyo código copiaste (de AionDex de otro jugador)"))
        paste.clicked.connect(self._paste_code)
        self.header.add_right(paste)
        root.addWidget(self.header)
        self._focus = None                 # the character the class list was picked for (Controller.focus_character)
        self._grids = []                   # [(grid, skill cells)] of the build drawn: placed again on a resize
        self._drawn_cols = None

        self.import_row = QFrame()
        self.import_row.setObjectName("Panel")
        row = QHBoxLayout(self.import_row)
        row.setContentsMargins(12, 8, 12, 8)
        row.addWidget(label(tr("Jugador"), f.small, theme.MUTED))
        self.import_name = QLineEdit()
        self.import_name.setPlaceholderText(tr("Nombre del personaje"))
        self.import_name.returnPressed.connect(self._import_player)
        row.addWidget(self.import_name, 1)
        row.addWidget(label(tr("Servidor"), f.small, theme.MUTED))
        self.import_server = QLineEdit()
        self.import_server.setFixedWidth(70)
        row.addWidget(self.import_server)
        go = QPushButton(tr("Traer build"))
        go.clicked.connect(self._import_player)
        row.addWidget(go)
        meter = QPushButton(tr("Builds de notmeter"))
        meter.setProperty("flat", True)
        meter.setToolTip(tr("Las builds y equipos que corren los jugadores de cada clase (notmeter, lo recomienda "
                            "la guía de kanonxo): ábrelo, elige una y tráela con el nombre del jugador"))
        meter.clicked.connect(lambda: webbrowser.open("https://notmeter.com/?view=setup-guide"))
        row.addWidget(meter)
        self.import_status = label(tr("Del perfil oficial de NCSOFT (lo mismo que muestra shugo.gg): sus skills, "
                                      "niveles y estigmas equipados. No trae especialidades ni barra."), f.small, theme.MUTED)
        self.import_status.setWordWrap(True)
        row.addWidget(self.import_status, 2)
        self.import_row.hide()
        root.addWidget(self.import_row)

        body = QHBoxLayout()
        body.setSpacing(12)
        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFixedWidth(330)
        holder = QWidget()
        self.list = QVBoxLayout(holder)
        self.list.setContentsMargins(0, 0, 6, 0)
        self.list.setSpacing(6)
        left.setWidget(holder)
        body.addWidget(left)
        right = QScrollArea()
        right.setWidgetResizable(True)
        self.detail_scroll = right
        holder = QWidget()
        self.detail = QVBoxLayout(holder)
        self.detail.setContentsMargins(0, 0, 6, 0)
        self.detail.setSpacing(10)
        right.setWidget(holder)
        body.addWidget(right, 1)
        root.addLayout(body, 1)
        self._poll = QTimer(self, interval=500, timeout=self._check_import)
        self._fill_classes()
        self.show_tab("builds")

    # --- tabs ---
    def show_tab(self, key):
        """"builds" or "afinador" (the Afinador of the macro, inside)."""
        if key == "afinador":
            if self.tuner_page is None:
                from .tunerwindow import TunerWindow
                self.tuner_page = TunerWindow(self.ctl)
                self.tuner_page.setWindowFlags(Qt.Widget)
                self.stack.addWidget(self.tuner_page)
                self.ctl.tuner_window = self.tuner_page
            self.tuner_page.rebuild()
            self.stack.setCurrentWidget(self.tuner_page)
        else:
            self.stack.setCurrentIndex(0)
        self.tabs.set_current(key)
        self.setWindowTitle(f"{APP_NAME} — {tr('Afinador del macro') if key == 'afinador' else 'Builds'}")

    def select(self, build_id, plan=False):
        """Show one build (plan: with its Mastery comparison)."""
        if self.builds.get(build_id):
            self.selected = build_id
            self.compare = (build_id, "mastery") if plan else None
            self.refresh()

    # --- the list ---
    def _fill_classes(self):
        mine = self._my_class()
        classes = sorted(set(self.builds.classes()) | ({mine} if mine else set()))
        self._focus = self.ctl.focus_character()
        self.cls.blockSignals(True)
        self.cls.clear()
        self.cls.addItem(tr("Todas las clases"), None)
        for c in classes:
            self.cls.addItem(c, c)
        self.cls.setCurrentIndex(max(0, self.cls.findData(mine)) if mine else 0)
        self.cls.blockSignals(False)

    def _my_class(self):
        """The class the list opens on: that of the character every page talks about (Controller.focus_character:
        the one chosen in the AionDex window's picker, else the one in play, else the last one played) - its skills
        when it is the one in play, else its job as the game sent it."""
        name = self.ctl.focus_character()
        if name and name == self.ctl.character_name() and self._tuner_class():
            return self._tuner_class()
        job = (self.ctl.settings.get("characters", {}).get(name) or {}).get("job") if name else None
        return class_of_job(job) or self._tuner_class()

    def _tuner_class(self):
        """The class of the skills the Afinador holds (the character in play, or the last one read)."""
        own = self.ctl.tuner.own
        return class_of_build({"skills": {str(b): None for b in own}}) if own else None

    def refresh(self):
        f = self.fonts
        focus = self.ctl.focus_character()
        if focus != self._focus:                   # the picker changed: the list opens on that character's class
            self._focus = focus
            self._fill_classes()
        builds = self.builds.list(self.cls.currentData())
        self.subtitle.setText(tr("Guardadas en tu PC · {0} {1}", len(self.builds.items), 'build' if len(self.builds.items) == 1 else 'builds'))
        _clear(self.list)
        if not builds:
            hint = label(tr("Aún no hay builds de esta clase. Guarda tu preset (el que tienes puesto en el juego), "
                            "importa la de un jugador o pega un código."), f.small, theme.MUTED)
            hint.setWordWrap(True)
            self.list.addWidget(hint)
        for b in builds:
            self.list.addWidget(self._row(b))
        self.list.addStretch(1)
        if self.selected is None or not self.builds.get(self.selected):
            self.selected = builds[0]["id"] if builds else None
        self._show_detail()

    def _row(self, b):
        f = self.fonts
        row = QFrame()
        row.setObjectName("BuildRow")
        row.setProperty("selected", b["id"] == self.selected)
        row.setCursor(Qt.PointingHandCursor)
        row.mousePressEvent = lambda e, i=b["id"]: self._select(i)
        col = QVBoxLayout(row)
        col.setContentsMargins(10, 7, 10, 7)
        col.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(4)
        top.addWidget(label(b.get("name") or "Build", f.name, theme.TEXT, elide=True), 1)
        for number in self._presets_of(b):
            top.addWidget(self._preset_chip(number))
        text, color = SOURCE_TEXT.get(b.get("source"), ("", theme.MUTED))
        top.addWidget(label(text, f.small, color))
        col.addLayout(top)
        col.addWidget(label(f"{class_of_build(b)} · {b.get('origin', '')} · {_ago(b.get('at'))}", f.small, theme.MUTED,
                            elide=True))
        return row

    def _presets_of(self, b):
        """The presets (1-3) of the character in play that have this build on."""
        tuner = self.ctl.tuner
        if class_of_build(b) != self._tuner_class():
            return []
        return Builds.in_presets(b, tuner.presets, tuner.own, tuner.active_preset)

    def _preset_chip(self, number):
        chip = QLabel(str(number))
        chip.setObjectName("PresetChip")
        chip.setFont(self.fonts.small)
        active = number == self.ctl.tuner.active_preset
        chip.setProperty("active", active)
        who = self.ctl.character_name() or self.ctl.settings.get("last_character") or ""
        chip.setToolTip(tr("Puesta en el preset {0} de {1}", number, who) + (tr(" (el que tienes puesto)") if active else ""))
        return chip

    def _select(self, build_id):
        self.selected = build_id
        self.compare = None
        self.refresh()

    # --- one build ---
    def _show_detail(self):
        """The selected build, drawn again where you were in it (the scroll stays: rebuilding it moved the focus to the
        last button and Qt scrolled down to it)."""
        bar = self.detail_scroll.verticalScrollBar()
        where = bar.value()
        self._draw_detail()
        QTimer.singleShot(0, lambda: bar.setValue(min(where, bar.maximum())))

    def _draw_detail(self):
        f = self.fonts
        _clear(self.detail)
        self._grids = []
        b = self.builds.get(self.selected) if self.selected else None
        if b is None:
            self.detail.addStretch(1)
            return
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_row.addWidget(label(b.get("name") or "Build", f.name_big, theme.TEXT))
        for number in self._presets_of(b):
            name_row.addWidget(self._preset_chip(number))
        name_row.addStretch(1)
        for text, action in ((tr("Copiar código"), lambda: self._copy_code(b)), (tr("Renombrar"), lambda: self._rename(b)),
                             (tr("Borrar"), lambda: self._delete(b))):
            button = QPushButton(text)
            button.setProperty("flat", True)
            button.clicked.connect(action)
            name_row.addWidget(button)
        if b.get("player"):
            shugo = QPushButton(tr("Ver en shugo.gg"))
            shugo.setProperty("flat", True)
            name, server = b["player"]
            shugo.clicked.connect(lambda: webbrowser.open(shugo_url(name, server,
                                                                    self.ctl.server_name(server) or "zikel")))
            name_row.addWidget(shugo)
        titles.addLayout(name_row)
        titles.addWidget(label(tr("{0} · {1} · guardada {2}", class_of_build(b), b.get('origin', ''), _ago(b.get('at'))),
                               f.small, theme.MUTED))
        head.addLayout(titles, 1)
        self.detail.addLayout(head)
        head = QHBoxLayout()
        head.setSpacing(4)
        for part, text, tip in (("mastery", tr("Comparar Mastery"),
                                 tr("Compara tu preset (el que tienes puesto) con la build en la pestaña Mastery del "
                                    "juego: puntos y especialidades de activas y pasivas, y la barra")),
                                ("stigma", tr("Comparar Stigma"),
                                 tr("Compara tu preset con la build en la pestaña Stigma: puntos, especialidades y en "
                                    "qué tecla va cada estigma"))):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setCheckable(True)
            button.setChecked(self.compare == (b["id"], part))
            button.clicked.connect(lambda _=False, k=part: self._compare(b["id"], k))
            head.addWidget(button)
        update = QPushButton(tr("Actualizar"))
        update.setToolTip(tr("Vuelve a leer las skills de esta build como están ahora: las del preset donde la tienes "
                             "puesta (niveles, especialidades y barra), o el perfil oficial del jugador"))
        update.clicked.connect(lambda: self._update(b))
        head.addWidget(update)
        head.addStretch(1)
        self.detail.addLayout(head)
        if self.compare and self.compare[0] == b["id"]:
            self.detail.addWidget(self._compare_panel(b, self.compare[1]))
        self.detail.addWidget(self._skills_panel(b))
        if b.get("bar"):
            self.detail.addWidget(self._bar_panel(b))
            self.detail.addWidget(self._macro_panel(b))
        elif b.get("source") == "player":
            self.detail.addWidget(label(tr("El perfil oficial no publica la barra ni las especialidades de un "
                                           "jugador."),
                                        f.small, theme.MUTED))
        self.detail.addStretch(1)
        self._want_icons(b)

    def _skills_panel(self, b):
        f = self.fonts
        panel = QFrame()
        panel.setObjectName("Panel")
        col = QVBoxLayout(panel)
        col.setContentsMargins(14, 10, 14, 12)
        equipped = set(b.get("equipped") or [])
        bonus = self._bonus(b)
        groups = {}
        for sid, entry in b.get("skills", {}).items():
            points, specs, _ = entry_levels(entry)
            level = game_level(entry, bonus.get(int(sid)))
            groups.setdefault(self.builds.kind_of(sid) or "active", []).append((int(sid), points, level, specs))
        # 2.5.0: the level drawn is the one the game shows (points + the bonus of Daevanion, gear and Arcana, the
        # character's as it is now); 2.4 drew the points first, and every maxed skill read «Lv 10», their cap
        if bonus:
            text = tr("Nivel: el que muestra el juego con esta build hoy, sus puntos más el bonus que {0} tiene ahora "
                      "(Daevanion, equipo y arcanas).", character_of(b))
        elif b.get("source") == "player":
            text = tr("Nivel: el del perfil oficial del jugador (sus puntos más su bonus de Daevanion, equipo y "
                      "arcanas).")
        else:
            text = tr("Nivel: el que muestra el juego, puntos más el bonus de Daevanion, equipo y arcanas, como estaba "
                      "al guardarla.")
        note = label(text, f.small, theme.MUTED)
        note.setWordWrap(True)
        col.addWidget(note)
        for kind, title in KIND_TITLE:
            rows = sorted(groups.get(kind, []), key=lambda r: (r[0] not in equipped, r[0]))
            if not rows:
                continue
            col.addWidget(SectionTitle(title, f))
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(6)
            cols = self._drawn_cols = self._cols()
            holders = []
            for sid, points, level, specs in rows:
                cell = QHBoxLayout()
                cell.setContentsMargins(0, 0, 0, 0)
                cell.setSpacing(8)
                cell.addWidget(self._icon(sid, faded=kind == "stigma" and sid not in equipped, level=level or None))
                text = QVBoxLayout()
                text.setSpacing(0)
                name_row = QHBoxLayout()
                name_row.setSpacing(6)
                name_row.addWidget(label(self.builds.name_of(sid), f.small, theme.TEXT, elide=True), 1)
                name_row.addWidget(label(f"Lv {level}" if level else "—", f.name, theme.GOLD))
                text.addLayout(name_row)
                if points is not None and level and level > points:
                    extra = (tr("1 punto + {0} de bonus", level - points) if points == 1 else
                             tr("{0} puntos + {1} de bonus", points, level - points))
                elif points is not None:
                    extra = tr("1 punto") if points == 1 else tr("{0} puntos", points)
                else:
                    extra = tr("nivel del perfil oficial")
                extra += tr(" · espec. {0}", "·".join(map(str, specs))) if specs else ""
                if kind == "stigma":
                    extra += tr(" · equipado") if sid in equipped else tr(" · no equipado")
                text.addWidget(label(extra, f.small, theme.MUTED, elide=True))
                cell.addLayout(text, 1)
                holder = QWidget()
                holder.setLayout(cell)
                holder.setMinimumWidth(CELL)
                holders.append(holder)
            self._place(grid, holders, cols)
            self._grids.append((grid, holders))
            col.addLayout(grid)
        return panel

    @staticmethod
    def _place(grid, holders, cols):
        """The skill cells in rows of cols, the columns sharing the width."""
        for holder in holders:
            grid.removeWidget(holder)
        for c in range(grid.columnCount()):
            grid.setColumnStretch(c, 0)
        for i, holder in enumerate(holders):
            grid.addWidget(holder, i // cols, i % cols)
        for c in range(cols):
            grid.setColumnStretch(c, 1)

    def _cols(self):
        """Skill cells per row: as many as the detail column holds (the window's width less the list and the
        margins: two in the AionDex window, three on a wide screen)."""
        avail = self.width() - 404
        return max(1, min(3, (avail - 14) // (CELL + 14)))

    def resizeEvent(self, event):
        """Another number of skill columns fits now: the cells drawn move (drawing the build again would show it
        for a moment with its new cells not laid out yet, and loses the scroll)."""
        super().resizeEvent(event)
        cols = self._cols()
        if self._grids and cols != self._drawn_cols:
            self._drawn_cols = cols
            for grid, holders in self._grids:
                self._place(grid, holders, cols)

    def _bonus(self, b):
        """{base: the bonus its character has now} of one of your builds ({} for the rest): builds.bonus_now."""
        tuner = self.ctl.tuner
        return bonus_now(b, self.ctl.settings, self.ctl.character_name(), tuner.own)

    def _levels(self, b):
        """{base: the level the game shows} of a build's skills (builds.game_level), for the bar and the macro."""
        bonus = self._bonus(b)
        return {int(sid): game_level(entry, bonus.get(int(sid))) or None
                for sid, entry in (b.get("skills") or {}).items()}

    def _bar_panel(self, b):
        """The bar as the game's Skill window draws it: one column per key, priority 0 at the bottom."""
        f = self.fonts
        panel = QFrame()
        panel.setObjectName("Panel")
        col = QVBoxLayout(panel)
        col.setContentsMargins(14, 10, 14, 12)
        col.addWidget(SectionTitle(tr("Barra (como en la ventana Skill: prioridad 0 abajo)"), f))
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        by_slot = {slot: prio for slot, _, prio in b["bar"]}
        levels = self._levels(b)
        rows = 4
        for c, slot in enumerate(SLOT_KEYS):
            column = c + (1 if c >= 4 else 0) + (1 if c >= 8 else 0)            # gaps like the game's groups
            for r in range(rows):
                prio = by_slot.get(slot, [])
                cell = (self._icon(prio[r], level=levels.get(prio[r]), size=BAR_ICON) if r < len(prio)
                        else _empty_cell())
                if r < len(prio):
                    cell.setToolTip(tr("{0} · prioridad {1}: {2}", tr(SLOT_KEYS[slot]), r, self.builds.name_of(prio[r])))
                grid.addWidget(cell, rows - 1 - r, column, Qt.AlignCenter)
            grid.addWidget(label(tr(SLOT_KEYS[slot]), f.name, theme.ACCENT), rows, column, Qt.AlignCenter)
        for r in range(rows):
            grid.addWidget(label(str(r), f.small, theme.MUTED), rows - 1 - r, 14, Qt.AlignCenter)
        grid.setColumnMinimumWidth(4, 10)
        grid.setColumnMinimumWidth(9, 10)
        col.addLayout(grid)
        return panel

    def _macro_panel(self, b):
        """The game's skill macro of this build (read from its Macro window), and passing it to the Afinador."""
        f = self.fonts
        panel = QFrame()
        panel.setObjectName("Panel")
        col = QVBoxLayout(panel)
        col.setContentsMargins(14, 10, 14, 12)
        col.addWidget(SectionTitle(tr("Macro del juego"), f))
        steps = b.get("macro") or []
        if steps:
            row = QHBoxLayout()
            row.setSpacing(8)
            keys = {p: SLOT_KEYS.get(slot) for slot, _, prio in (b.get("bar") or []) for p in prio[:1]}
            levels = self._levels(b)
            for n, base, name, delay in steps:
                cell = QVBoxLayout()
                cell.setSpacing(2)
                cell.addWidget(self._icon(base, level=levels.get(base), size=BAR_ICON), 0, Qt.AlignCenter)
                key = keys.get(base)
                cell.addWidget(label(f"{n}. {name}" + (f" ({key})" if key else ""), f.small, theme.TEXT), 0,
                               Qt.AlignCenter)
                cell.addWidget(label(f"{delay if delay is not None else '?'} ms", f.small, theme.MUTED), 0,
                               Qt.AlignCenter)
                row.addLayout(cell)
            row.addStretch(1)
            col.addLayout(row)
        else:
            hint = label(tr("Sin macro guardado. En el juego abre la ventana Skill con el preset de esta build, "
                            "pulsa «Macro» y luego «Leer macro del juego»."), f.small, theme.MUTED)
            hint.setWordWrap(True)
            col.addWidget(hint)
        buttons = QHBoxLayout()
        read = QPushButton(tr("Leer macro del juego"))
        read.setToolTip(tr("Con la ventana Macro del juego abierta: lee sus pasos (si no caben todos, baja la lista "
                           "del juego y vuelve a leer: se agregan)"))
        read.clicked.connect(lambda: self._read_macro(b))
        buttons.addWidget(read)
        if steps:
            tuner = QPushButton(tr("Pasarlo al Afinador"))
            tuner.setProperty("flat", True)
            tuner.setToolTip(tr("Pone este macro como el macro del juego del preset activo en el Afinador"))
            tuner.clicked.connect(lambda: self._macro_to_tuner(b))
            buttons.addWidget(tuner)
            clear = QPushButton(tr("Borrar macro"))
            clear.setProperty("flat", True)
            clear.clicked.connect(lambda: self._set_macro(b, []))
            buttons.addWidget(clear)
        buttons.addStretch(1)
        col.addLayout(buttons)
        return panel

    def _read_macro(self, b):
        from .. import macroread
        steps = self.ctl.read_game_macro(b)
        if steps is None:
            self.subtitle.setText(tr("No pude leer la pantalla del juego (¿está abierto y sin minimizar?)."))
            return
        if not steps:
            self.subtitle.setText(tr("No veo la ventana Macro: en la ventana Skill del juego pulsa «Macro» y vuelve "
                                     "a intentarlo."))
            return
        merged = macroread.merge(b.get("macro"), steps)
        self._set_macro(b, merged)
        self.subtitle.setText(tr("Macro leído: {0} pasos. Si tiene más, baja la lista del juego y vuelve a leer.", len(merged)))

    def _set_macro(self, b, steps):
        b["macro"] = [list(s) for s in steps]
        self.builds.save()
        self._show_detail()

    def _macro_to_tuner(self, b):
        done = self.ctl.macro_to_tuner(b)
        self.subtitle.setText(done)

    def _compare_panel(self, b, part):
        """Your preset in use against the build, one tab of the game's Skill window: what is right and what is not."""
        f = self.fonts
        panel = QFrame()
        panel.setObjectName("Panel")
        col = QVBoxLayout(panel)
        col.setContentsMargins(14, 10, 14, 12)
        tuner = self.ctl.tuner
        preset = tuner.active_preset or 1
        title = tr("Mastery") if part == "mastery" else tr("Stigma")
        if not tuner.own:
            col.addWidget(label(tr("Entra al juego con tu personaje con AionDex abierto: necesito tus skills para "
                                   "comparar."), f.small, theme.WARN))
            return panel
        wrong, checked = self.builds.compare(b, part, tuner.own, tuner.presets.get(preset, []),
                                             tuner.spec_slots.get(preset))
        col.addWidget(SectionTitle(tr("{0}: tu preset {1} contra «{2}»", title, preset, b.get("name") or "Build"), f))
        if not wrong:
            col.addWidget(label(tr("✓ Todo coincide ({0} cosas revisadas)", checked), f.name, theme.GAIN))
        else:
            col.addWidget(label(tr("✗ {0} cosa{1} no coincide{2} (de {3} revisadas):", len(wrong),
                                   "s" if len(wrong) != 1 else "", "n" if len(wrong) != 1 else "", checked),
                                f.name, theme.WARN))
            section = None
            for step in wrong:
                if step.section != section:
                    section = step.section
                    col.addWidget(label(section + (tr(" (su número en la lista del juego, de arriba abajo)")
                                                   if section == tr("Especialidades") else ""), f.small, theme.MUTED))
                line = label(f"•  {step.text}", f.body, theme.TEXT if step.kind != "info" else theme.MUTED)
                line.setWordWrap(True)
                col.addWidget(line)
        read = tr("Lo que tengo del juego: leído {0}.", _ago_exact(tuner.loadout_at)) if tuner.loadout_at else ""
        hint = label(read + tr(" Si cambiaste algo y no se ve, cambia de preset en el juego y vuelve al tuyo (el juego "
                               "manda entonces todo: puntos, especialidades y barra) y compara de nuevo."),
                     f.small, theme.MUTED)
        hint.setWordWrap(True)
        col.addWidget(hint)
        again = QPushButton(tr("Comparar de nuevo"))
        again.setProperty("flat", True)
        again.clicked.connect(self._show_detail)
        row = QHBoxLayout()
        row.addWidget(again)
        row.addStretch(1)
        col.addLayout(row)
        return panel

    # --- actions ---
    def _compare(self, build_id, part):
        self.compare = None if self.compare == (build_id, part) else (build_id, part)
        self._show_detail()

    def _save_preset(self):
        tuner = self.ctl.tuner
        who = self.ctl.character_name() or self.ctl.settings.get("last_character")
        preset = tuner.active_preset or 1
        if not tuner.own or not who:
            self.subtitle.setText(tr("Entra al juego con tu personaje con AionDex abierto: el juego manda tus "
                                     "skills al entrar y al cambiar de preset."))
            return
        name, ok = QInputDialog.getText(self, tr("Guardar build"), tr("Nombre de la build:"), text=f"{who} · preset {preset}")
        if not ok:
            return
        b = self.builds.add(self.builds.from_preset(name.strip() or f"{who} · preset {preset}", who, preset, tuner.own,
                                               tuner.presets.get(preset, [])))
        self.selected = b["id"]
        self._fill_classes()
        self.refresh()

    def _update(self, b):
        """«Actualizar»: the build as it is now - from the preset that has it on (the one in use: levels,
        specializations and bar; another: its bar, the levels when you put it on), or a player's official profile."""
        if b.get("source") == "player" and b.get("player"):
            name, server = b["player"]
            self.ctl.armory.want(name, server, refresh=True)
            self.waiting = (name, server, time.time(), b["id"])
            self.subtitle.setText(tr("Leyendo de nuevo el perfil oficial de {0}…", name))
            self._poll.start()
            return
        tuner = self.ctl.tuner
        who = self.ctl.character_name() or self.ctl.settings.get("last_character") or ""
        on = self._presets_of(b)
        if not on and b.get("source") == "own":      # changed in the game since: the preset it was saved from
            on = [n for n in tuner.presets if b.get("origin") == f"{who} · preset {n}"]
        if not on:
            self.subtitle.setText(tr("«{0}» no está puesta en ningún preset: cópiala en el juego («Comparar» te dice qué falta) y luego "
                                     "Actualizar.", b.get("name") or "Build"))
            return
        preset = tuner.active_preset if tuner.active_preset in on else on[0]
        fresh = self.builds.from_preset(b.get("name") or "", who, preset, tuner.own, tuner.presets.get(preset, []))
        if preset != tuner.active_preset:          # the game sends a preset's levels only while it is in use
            fresh = {k: fresh[k] for k in ("equipped", "bar")}
        self.builds.refreshed(b, fresh)
        self.subtitle.setText(tr("«{0}» actualizada con el preset {1}.", b.get("name") or "Build", preset)
                              + ("" if preset == tuner.active_preset else
                                 tr(" Sus niveles se leen cuando pongas ese preset en el juego.")))
        self.refresh()

    def _paste_code(self):
        b = Builds.from_code(QApplication.clipboard().text())
        if b is None:
            self.subtitle.setText(tr("En el portapapeles no hay un código de build (empieza por AIONDEX-BUILD:)."))
            return
        b = self.builds.add(b)
        self.selected = b["id"]
        self._fill_classes()
        self.refresh()

    def _copy_code(self, b):
        QApplication.clipboard().setText(Builds.to_code(b))
        self.subtitle.setText(tr("Código de «{0}» copiado: pégalo en Discord; con AionDex se importa con «Pegar "
                                 "código».", b.get('name')))

    def _rename(self, b):
        name, ok = QInputDialog.getText(self, tr("Renombrar build"), tr("Nombre:"), text=b.get("name") or "")
        if ok:
            self.builds.rename(b["id"], name)
            self.refresh()

    def _delete(self, b):
        self.builds.delete(b["id"])
        self.selected = None
        self.refresh()

    def _import_player(self):
        name = self.import_name.text().strip()
        try:
            server = int(self.import_server.text().strip() or 0)
        except ValueError:
            server = 0
        if not name or not server:
            self.import_status.setText(tr("Escribe el nombre del personaje y su servidor (número, el tuyo es {0}).", self._my_server() or '—'))
            return
        self.ctl.armory.want(name, server, refresh=True)
        self.waiting = (name, server, time.time(), None)
        self.import_status.setText(tr("Buscando a {0} en el perfil oficial…", name))
        self._poll.start()

    def _check_import(self):
        if not self.waiting:
            self._poll.stop()
            return
        name, server, since, build_id = self.waiting
        armory = self.ctl.armory
        if armory.loading(name) and time.time() - since < 30:
            return
        self._poll.stop()
        self.waiting = None
        profile = armory.cached(name, server)
        if profile is None or not profile.skill_list:
            (self.subtitle if build_id else self.import_status).setText(
                armory.error(name) or tr("No encontré a {0} en el servidor {1}.", name, server))
            return
        if build_id and self.builds.get(build_id):
            b = self.builds.refreshed(self.builds.get(build_id), Builds.from_profile("", profile, profile.server_name or ""))
            self.subtitle.setText(tr("«{0}» actualizada con el perfil oficial de {1}.", b.get("name") or "Build", profile.name))
            self.refresh()
            return
        b = self.builds.add(Builds.from_profile(f"{profile.name} · {profile.class_name or ''}".strip(" ·"), profile,
                                                profile.server_name or ""))
        self.selected = b["id"]
        self.import_status.setText(tr("Build de {0} guardada.", profile.name))
        self._fill_classes()
        self.cls.setCurrentIndex(max(0, self.cls.findData(class_of_build(b))))
        self.refresh()

    def profile_ready(self, name):
        if self.waiting and self.waiting[0] == name:
            self._check_import()

    def _my_server(self):
        c = self.ctl.character
        return c.server if c else None

    # --- icons ---
    def _icon(self, sid, faded=False, level=None, size=ICON):
        """A skill tile like the game's: its icon, «Lv. N» (the level the game shows) and the frame of its kind
        (orange for actives and stigmas, green for passives); dim for a stigma the build does not equip."""
        kind = self.builds.kind_of(sid)
        path = self.ctl.tuner.icons_dir / f"{sid}.png"
        name = self.builds.name_of(sid)
        initials = "".join(w[0] for w in name.split()[:2]).upper()
        return SkillTile(size, icon=str(path) if path.exists() else None, level=level, dim=faded, initials=initials,
                         border=PASSIVE_BORDER if kind == "passive" else ACTIVE_BORDER if kind else None,
                         tooltip=name + (f" · Lv {level}" if level else ""))

    def _want_icons(self, b):
        ids = [int(s) for s in b.get("skills", {})] + [p for _, _, prio in (b.get("bar") or []) for p in prio]
        missing = [s for s in ids if not (self.ctl.tuner.icons_dir / f"{s}.png").exists()]
        if missing and hasattr(self.ctl, "fetch_skill_icons"):
            self.ctl.fetch_skill_icons(missing, on_done=lambda *_: self.isVisible() and self._show_detail())

    def showEvent(self, event):
        super().showEvent(event)
        if not self.import_server.text():
            self.import_server.setText(str(self._my_server() or ""))


def _empty_cell():
    return SkillTile(BAR_ICON, empty=True)


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.hide()
            w.deleteLater()
        elif item.layout():
            _clear(item.layout())
