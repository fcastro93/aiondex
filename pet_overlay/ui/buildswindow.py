"""Builds window: your skill setups kept on your PC beyond the game's 3 presets (builds.py): save the preset you
have on, bring in another player's from their official profile, share or paste a code, and "Usar build": the
changes from your preset to it, in the Skill window's order (and, when it is on, AionDex makes them itself). Its
second tab is the Afinador of the macro (ui/tunerwindow.py), part of the builds."""
import time
import webbrowser

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QStackedWidget, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..armory import shugo_url
from ..builds import Builds, class_of_build, entry_levels
from ..skills import SLOT_KEYS
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label

ICON = 34
SIDE_STYLE = """
QFrame#BuildRow { background: #101a30; border: 1px solid #1a2643; border-radius: 8px; }
QFrame#BuildRow[selected="true"] { border-color: #2ec0f2; background: #13233f; }
QFrame#Panel { background: #0f1830; border: 1px solid #1a2643; border-radius: 10px; }
QPushButton[flat="true"] { background: transparent; color: #2ec0f2; padding: 4px 8px; }
QPushButton[flat="true"]:hover { color: #7fe0ff; }
"""
SOURCE_TEXT = {"own": ("Tu preset", "#7bd88f"), "player": ("Perfil oficial", "#c38bff"), "code": ("Código", "#f0c24e")}
KIND_TITLE = (("active", "Activas"), ("passive", "Pasivas"), ("stigma", "Estigmas"))


class _Relay(QObject):
    """The macro's news from its thread to the window."""
    progress = Signal(str)
    done = Signal(bool, str)


def _ago(ts):
    days = (time.time() - (ts or 0)) / 86400
    return "hoy" if days < 1 else "ayer" if days < 2 else f"hace {days:.0f} días"


class BuildsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.builds = ctl.builds
        self.fonts = Fonts(1.0)
        self.selected = None
        self.plan_for = None              # build id whose "Usar build" plan is shown
        self.waiting = None               # (name, server, since) of a player profile being fetched
        self.macro_text = ""              # what the build macro is doing / did
        self.running = False
        self._relay = _Relay()
        self._relay.progress.connect(self._macro_progress)
        self._relay.done.connect(self._macro_done)
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — Builds")
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + SIDE_STYLE)
        self.resize(1320, 900)
        f = self.fonts

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(0)
        tabs = QHBoxLayout()
        tabs.setContentsMargins(16, 0, 16, 4)
        tabs.setSpacing(6)
        self.tab_buttons = {}
        for key, text in (("builds", "Builds"), ("afinador", "Afinador del macro")):
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            tabs.addWidget(button)
            self.tab_buttons[key] = button
        tabs.addStretch(1)
        outer.addLayout(tabs)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        page = QWidget()
        self.stack.addWidget(page)
        self.tuner_page = None            # the Afinador, made the first time its tab opens

        root = QVBoxLayout(page)
        root.setContentsMargins(16, 6, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(34, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("Builds", f.name_big, theme.TEXT))
        self.subtitle = label("", f.small, theme.MUTED)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.cls = QComboBox()
        self.cls.currentIndexChanged.connect(lambda _: self.refresh())
        head.addWidget(self.cls)
        save = QPushButton("Guardar mi preset")
        save.setToolTip("Guarda el preset que tienes puesto en el juego (sus puntos, especialidades, estigmas y barra)")
        save.clicked.connect(self._save_preset)
        head.addWidget(save)
        imp = QPushButton("Importar de jugador")
        imp.clicked.connect(lambda: self.import_row.setVisible(not self.import_row.isVisible()))
        head.addWidget(imp)
        paste = QPushButton("Pegar código")
        paste.setToolTip("Importa la build cuyo código copiaste (de AionDex de otro jugador)")
        paste.clicked.connect(self._paste_code)
        head.addWidget(paste)
        root.addLayout(head)

        self.import_row = QFrame()
        self.import_row.setObjectName("Panel")
        row = QHBoxLayout(self.import_row)
        row.setContentsMargins(12, 8, 12, 8)
        row.addWidget(label("Jugador", f.small, theme.MUTED))
        self.import_name = QLineEdit()
        self.import_name.setPlaceholderText("Nombre del personaje")
        self.import_name.returnPressed.connect(self._import_player)
        row.addWidget(self.import_name, 1)
        row.addWidget(label("Servidor", f.small, theme.MUTED))
        self.import_server = QLineEdit()
        self.import_server.setFixedWidth(70)
        row.addWidget(self.import_server)
        go = QPushButton("Traer build")
        go.clicked.connect(self._import_player)
        row.addWidget(go)
        self.import_status = label("Del perfil oficial de NCSOFT (lo mismo que muestra shugo.gg): sus skills, niveles "
                                   "y estigmas equipados. No trae especialidades ni barra.", f.small, theme.MUTED)
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
        for k, button in self.tab_buttons.items():
            button.setChecked(k == key)
        self.setWindowTitle(f"{APP_NAME} — {'Afinador del macro' if key == 'afinador' else 'Builds'}")

    def select(self, build_id, plan=False):
        """Show one build (and its plan, "Usar build")."""
        if self.builds.get(build_id):
            self.selected = build_id
            self.plan_for = build_id if plan else None
            self.refresh()

    # --- the list ---
    def _fill_classes(self):
        mine = self._my_class()
        classes = sorted(set(self.builds.classes()) | ({mine} if mine else set()))
        self.cls.blockSignals(True)
        self.cls.clear()
        self.cls.addItem("Todas las clases", None)
        for c in classes:
            self.cls.addItem(c, c)
        self.cls.setCurrentIndex(max(0, self.cls.findData(mine)) if mine else 0)
        self.cls.blockSignals(False)

    def _my_class(self):
        own = self.ctl.tuner.own
        return class_of_build({"skills": {str(b): None for b in own}}) if own else None

    def refresh(self):
        f = self.fonts
        builds = self.builds.list(self.cls.currentData())
        self.subtitle.setText(f"Guardadas en tu PC · {len(self.builds.items)} "
                              f"{'build' if len(self.builds.items) == 1 else 'builds'}")
        _clear(self.list)
        if not builds:
            hint = label("Aún no hay builds de esta clase. Guarda tu preset (el que tienes puesto en el juego), "
                         "importa la de un jugador o pega un código.", f.small, theme.MUTED)
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
        top.addWidget(label(b.get("name") or "Build", f.name, theme.TEXT, elide=True), 1)
        text, color = SOURCE_TEXT.get(b.get("source"), ("", "#8a93a6"))
        top.addWidget(label(text, f.small, QColor(color)))
        col.addLayout(top)
        col.addWidget(label(f"{class_of_build(b)} · {b.get('origin', '')} · {_ago(b.get('at'))}", f.small, theme.MUTED,
                            elide=True))
        return row

    def _select(self, build_id):
        self.selected = build_id
        self.plan_for = None
        self.refresh()

    # --- one build ---
    def _show_detail(self):
        f = self.fonts
        _clear(self.detail)
        b = self.builds.get(self.selected) if self.selected else None
        if b is None:
            self.detail.addStretch(1)
            return
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(b.get("name") or "Build", f.name_big, theme.TEXT))
        titles.addWidget(label(f"{class_of_build(b)} · {b.get('origin', '')} · guardada {_ago(b.get('at'))}",
                               f.small, theme.MUTED))
        head.addLayout(titles, 1)
        self.detail.addLayout(head)
        head = QHBoxLayout()
        head.setSpacing(4)
        use = QPushButton("Usar build")
        use.setToolTip("Lo que hay que cambiar de tu preset actual para tener esta build")
        use.clicked.connect(lambda: self._plan(b["id"]))
        head.addWidget(use)
        for text, action in (("Copiar código", lambda: self._copy_code(b)), ("Renombrar", lambda: self._rename(b)),
                             ("Borrar", lambda: self._delete(b))):
            button = QPushButton(text)
            button.setProperty("flat", True)
            button.clicked.connect(action)
            head.addWidget(button)
        if b.get("player"):
            shugo = QPushButton("Ver en shugo.gg")
            shugo.setProperty("flat", True)
            name, server = b["player"]
            shugo.clicked.connect(lambda: webbrowser.open(shugo_url(name, server,
                                                                    self.ctl.server_name(server) or "zikel")))
            head.addWidget(shugo)
        head.addStretch(1)
        self.detail.addLayout(head)
        if self.plan_for == b["id"]:
            self.detail.addWidget(self._plan_panel(b))
        self.detail.addWidget(self._skills_panel(b))
        if b.get("bar"):
            self.detail.addWidget(self._bar_panel(b))
            self.detail.addWidget(self._macro_panel(b))
        elif b.get("source") == "player":
            self.detail.addWidget(label("El perfil oficial no publica la barra ni las especialidades de un jugador.",
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
        groups = {}
        for sid, entry in b.get("skills", {}).items():
            points, specs, total = entry_levels(entry)
            groups.setdefault(self.builds.kind_of(sid) or "active", []).append((int(sid), points, total, specs))
        for kind, title in KIND_TITLE:
            rows = sorted(groups.get(kind, []), key=lambda r: (r[0] not in equipped, r[0]))
            if not rows:
                continue
            col.addWidget(SectionTitle(title, f))
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(6)
            for i, (sid, points, total, specs) in enumerate(rows):
                cell = QHBoxLayout()
                cell.setSpacing(6)
                cell.addWidget(self._icon(sid, faded=kind == "stigma" and sid not in equipped))
                text = QVBoxLayout()
                text.setSpacing(0)
                text.addWidget(label(self.builds.name_of(sid), f.small, theme.TEXT, elide=True))
                extra = (f"Lv {points}" + (f" (total {total})" if total and total != points else "") if points
                         else f"Lv total {total}")
                extra += f" · espec. {'·'.join(map(str, specs))}" if specs else ""
                if kind == "stigma":
                    extra += " · equipado" if sid in equipped else " · no equipado"
                text.addWidget(label(extra, f.small, theme.MUTED))
                cell.addLayout(text, 1)
                holder = QWidget()
                holder.setLayout(cell)
                holder.setFixedWidth(250)
                grid.addWidget(holder, i // 3, i % 3)
            col.addLayout(grid)
        return panel

    def _bar_panel(self, b):
        """The bar as the game's Skill window draws it: one column per key, priority 0 at the bottom."""
        f = self.fonts
        panel = QFrame()
        panel.setObjectName("Panel")
        col = QVBoxLayout(panel)
        col.setContentsMargins(14, 10, 14, 12)
        col.addWidget(SectionTitle("Barra (como en la ventana Skill: prioridad 0 abajo)", f))
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        by_slot = {slot: prio for slot, _, prio in b["bar"]}
        rows = 4
        for c, slot in enumerate(SLOT_KEYS):
            column = c + (1 if c >= 4 else 0) + (1 if c >= 8 else 0)            # gaps like the game's groups
            for r in range(rows):
                prio = by_slot.get(slot, [])
                cell = self._icon(prio[r]) if r < len(prio) else _empty_cell()
                if r < len(prio):
                    cell.setToolTip(f"{SLOT_KEYS[slot]} · prioridad {r}: {self.builds.name_of(prio[r])}")
                grid.addWidget(cell, rows - 1 - r, column, Qt.AlignCenter)
            grid.addWidget(label(SLOT_KEYS[slot], f.name, theme.ACCENT), rows, column, Qt.AlignCenter)
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
        col.addWidget(SectionTitle("Macro del juego", f))
        steps = b.get("macro") or []
        if steps:
            row = QHBoxLayout()
            row.setSpacing(8)
            keys = {p: SLOT_KEYS.get(slot) for slot, _, prio in (b.get("bar") or []) for p in prio[:1]}
            for n, base, name, delay in steps:
                cell = QVBoxLayout()
                cell.setSpacing(2)
                cell.addWidget(self._icon(base), 0, Qt.AlignCenter)
                key = keys.get(base)
                cell.addWidget(label(f"{n}. {name}" + (f" ({key})" if key else ""), f.small, theme.TEXT), 0,
                               Qt.AlignCenter)
                cell.addWidget(label(f"{delay if delay is not None else '?'} ms", f.small, theme.MUTED), 0,
                               Qt.AlignCenter)
                row.addLayout(cell)
            row.addStretch(1)
            col.addLayout(row)
        else:
            hint = label("Sin macro guardado. En el juego abre la ventana Skill con el preset de esta build, pulsa "
                         "«Macro» y luego «Leer macro del juego».", f.small, theme.MUTED)
            hint.setWordWrap(True)
            col.addWidget(hint)
        buttons = QHBoxLayout()
        read = QPushButton("Leer macro del juego")
        read.setToolTip("Con la ventana Macro del juego abierta: lee sus pasos (si no caben todos, baja la lista del "
                        "juego y vuelve a leer: se agregan)")
        read.clicked.connect(lambda: self._read_macro(b))
        buttons.addWidget(read)
        if steps:
            tuner = QPushButton("Pasarlo al Afinador")
            tuner.setProperty("flat", True)
            tuner.setToolTip("Pone este macro como el macro del juego del preset activo en el Afinador")
            tuner.clicked.connect(lambda: self._macro_to_tuner(b))
            buttons.addWidget(tuner)
            clear = QPushButton("Borrar macro")
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
            self.subtitle.setText("No pude leer la pantalla del juego (¿está abierto y sin minimizar?).")
            return
        if not steps:
            self.subtitle.setText("No veo la ventana Macro: en la ventana Skill del juego pulsa «Macro» y vuelve a "
                                  "intentarlo.")
            return
        merged = macroread.merge(b.get("macro"), steps)
        self._set_macro(b, merged)
        self.subtitle.setText(f"Macro leído: {len(merged)} pasos. Si tiene más, baja la lista del juego y vuelve a "
                              f"leer.")

    def _set_macro(self, b, steps):
        b["macro"] = [list(s) for s in steps]
        self.builds.save()
        self._show_detail()

    def _macro_to_tuner(self, b):
        done = self.ctl.macro_to_tuner(b)
        self.subtitle.setText(done)

    def _plan_panel(self, b):
        f = self.fonts
        panel = QFrame()
        panel.setObjectName("Panel")
        col = QVBoxLayout(panel)
        col.setContentsMargins(14, 10, 14, 12)
        tuner = self.ctl.tuner
        preset = tuner.active_preset or 1
        if not tuner.own:
            col.addWidget(label("Entra al juego con tu personaje con AionDex abierto: necesito tus skills para comparar.",
                                f.small, theme.WARN))
            return panel
        steps = self.builds.plan(b, tuner.own, tuner.presets.get(preset, []))
        col.addWidget(SectionTitle(f"Para usarla en tu preset {preset} (el que tienes puesto)", f))
        if not steps:
            col.addWidget(label("Tu preset ya es esta build.", f.name, theme.GAIN))
            return panel
        section = None
        for step in steps:
            if step.section != section:
                section = step.section
                col.addWidget(label(section, f.small, theme.MUTED))
            line = label(f"•  {step.text}", f.body, theme.TEXT if step.kind != "info" else theme.MUTED)
            line.setWordWrap(True)
            col.addWidget(line)
        col.addWidget(label("Abre la ventana Skill del juego (pestañas Mastery y Stigma) y hazlo en este orden: bajar "
                            "puntos primero libera los que piden las subidas.", f.small, theme.MUTED))
        col.addLayout(self._macro_row(b, steps, preset))
        if self.macro_text:
            status = label(self.macro_text, f.small, theme.ACCENT if self.running else theme.TEXT)
            status.setWordWrap(True)
            col.addWidget(status)
        return panel

    def _macro_row(self, b, steps, preset):
        """Aplicar en el juego (the macro does the points and the bar; the specialities stay for you)."""
        from ..buildmacro import is_admin
        f = self.fonts
        row = QHBoxLayout()
        doable = [s for s in steps if s.kind in ("points", "place", "clear")]
        if not is_admin():
            note = label("Para que AionDex haga los cambios por ti, ábrelo como administrador (el juego corre como "
                         "administrador y Windows no deja que un programa normal haga clic en él).", f.small, theme.WARN)
            note.setWordWrap(True)
            row.addWidget(note, 1)
            again = QPushButton("Reiniciar como administrador")
            again.clicked.connect(self.ctl.restart_as_admin)
            row.addWidget(again)
            return row
        apply = QPushButton("Detener (Esc)" if self.running else f"Aplicar en el juego ({len(doable)} cambios)")
        apply.setEnabled(bool(doable) or self.running)
        apply.setToolTip("Con la ventana Skill del juego abierta: AionDex hace los cambios de puntos y de la barra, "
                         "comprobando cada uno; mover el ratón o Esc lo detiene. Las especialidades quedan para ti.")
        apply.clicked.connect(lambda: self._apply(b["id"], steps, preset))
        row.addWidget(apply)
        found = self.ctl.macro_layout()
        calibrated = bool(found and found[2])
        cal = QPushButton("Calibrar menú" if not calibrated else "Calibrar menú (hecho)")
        cal.setProperty("flat", True)
        cal.setToolTip("Arrastra unos marcadores sobre los botones y celdas del menú Skill: así los clics caen justo "
                       "ahí con tu resolución.")
        cal.clicked.connect(self.ctl.open_calibration)
        row.addWidget(cal)
        row.addStretch(1)
        always = QCheckBox("Abrir siempre como administrador")
        always.setToolTip("AionDex se abre siempre así (al abrirlo tú, al reiniciarse y tras actualizarse) sin que "
                          "Windows pregunte: usa una tarea programada de Windows. También hace que la ruta del minimapa "
                          "se mueva con tus teclas.")
        always.setChecked(bool(self.ctl.settings.get("run_as_admin")))
        always.toggled.connect(self._always_admin)
        row.addWidget(always)
        return row

    def _always_admin(self, on):
        from .. import elevation
        self.ctl.settings["run_as_admin"] = on
        self.ctl.settings.save()
        if on:
            elevation.create_task()          # this AionDex is elevated: the task can be made with no prompt
        else:
            elevation.delete_task()

    def _apply(self, build_id, steps, preset):
        if self.running:
            self.macro_text = "Pulsa Esc o mueve el ratón para detenerlo."
            self._show_detail()
            return
        error = self.ctl.apply_build(steps, None, self._relay.progress.emit, self._relay.done.emit)
        if error:
            self.macro_text = error if error != "admin" else "Hace falta abrir AionDex como administrador."
        else:
            self.running = True
            self.macro_text = "Empezando: no muevas el ratón (Esc lo detiene)…"
        self._show_detail()

    def _macro_progress(self, text):
        self.macro_text = text
        self._show_detail()

    def _macro_done(self, ok, text):
        self.running = False
        self.macro_text = text
        self._show_detail()

    # --- actions ---
    def _plan(self, build_id):
        self.plan_for = None if self.plan_for == build_id else build_id
        self._show_detail()

    def _save_preset(self):
        tuner = self.ctl.tuner
        who = self.ctl.character_name() or self.ctl.settings.get("last_character")
        preset = tuner.active_preset or 1
        if not tuner.own or not who:
            self.subtitle.setText("Entra al juego con tu personaje con AionDex abierto: el juego manda tus skills al "
                                  "entrar y al cambiar de preset.")
            return
        name, ok = QInputDialog.getText(self, "Guardar build", "Nombre de la build:", text=f"{who} · preset {preset}")
        if not ok:
            return
        b = self.builds.add(self.builds.from_preset(name.strip() or f"{who} · preset {preset}", who, preset, tuner.own,
                                               tuner.presets.get(preset, [])))
        self.selected = b["id"]
        self._fill_classes()
        self.refresh()

    def _paste_code(self):
        b = Builds.from_code(QApplication.clipboard().text())
        if b is None:
            self.subtitle.setText("En el portapapeles no hay un código de build (empieza por AIONDEX-BUILD:).")
            return
        b = self.builds.add(b)
        self.selected = b["id"]
        self._fill_classes()
        self.refresh()

    def _copy_code(self, b):
        QApplication.clipboard().setText(Builds.to_code(b))
        self.subtitle.setText(f"Código de «{b.get('name')}» copiado: pégalo en Discord; con AionDex se importa con "
                              f"«Pegar código».")

    def _rename(self, b):
        name, ok = QInputDialog.getText(self, "Renombrar build", "Nombre:", text=b.get("name") or "")
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
            self.import_status.setText("Escribe el nombre del personaje y su servidor (número, el tuyo es "
                                       f"{self._my_server() or '—'}).")
            return
        self.ctl.armory.want(name, server, refresh=True)
        self.waiting = (name, server, time.time())
        self.import_status.setText(f"Buscando a {name} en el perfil oficial…")
        self._poll.start()

    def _check_import(self):
        if not self.waiting:
            self._poll.stop()
            return
        name, server, since = self.waiting
        armory = self.ctl.armory
        if armory.loading(name) and time.time() - since < 30:
            return
        self._poll.stop()
        self.waiting = None
        profile = armory.cached(name, server)
        if profile is None or not profile.skill_list:
            self.import_status.setText(armory.error(name) or f"No encontré a {name} en el servidor {server}.")
            return
        b = self.builds.add(Builds.from_profile(f"{profile.name} · {profile.class_name or ''}".strip(" ·"), profile,
                                                profile.server_name or ""))
        self.selected = b["id"]
        self.import_status.setText(f"Build de {profile.name} guardada.")
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
    def _icon(self, sid, faded=False):
        lbl = QLabel()
        lbl.setFixedSize(ICON, ICON)
        path = self.ctl.tuner.icons_dir / f"{sid}.png"
        pix = QPixmap(str(path)) if path.exists() else QPixmap()
        if not pix.isNull():
            dpr = self.devicePixelRatioF()
            pix = pix.scaled(QSize(ICON, ICON) * dpr, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
            lbl.setPixmap(pix)
        else:
            lbl.setStyleSheet("background: #1a2643; border-radius: 6px;")
        if faded:
            lbl.setStyleSheet(lbl.styleSheet() + "opacity: 0.4;")
            lbl.setEnabled(False)
        lbl.setToolTip(self.builds.name_of(sid))
        return lbl

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
    cell = QLabel()
    cell.setFixedSize(ICON, ICON)
    cell.setStyleSheet("background: #121c33; border: 1px solid #1f2b47; border-radius: 6px;")
    return cell


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.hide()
            w.deleteLater()
        elif item.layout():
            _clear(item.layout())
