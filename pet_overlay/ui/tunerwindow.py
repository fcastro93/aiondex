"""Afinador window, laid out like the game: your quickslot bar per preset, the in-game macro (one key + delay per
step), your skills; then the external macro, the test and what to change."""
import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..diagnostics import ago
from ..tuner import (BINDINGS, CALIBRATION_SECONDS, ROLES, MacroEvent, MacroLine, is_mouse,
                     key_events, test_noise)
from . import theme
from .appmenu import styled_menu
from .mapwindow import STYLE, badge_label
from .skilltiles import BarGrid, SkillTile, tile_for
from .theme import Fonts
from .widgets import SectionTitle, label
from ..i18n import N_, tr

EXTRA_STYLE = f"""
QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f3d273, stop:1 #c99d33);
    color: #1b1505; border: 1px solid #8a6d1f; border-radius: 6px; padding: 6px 16px; font-weight: 600; }}
QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffe08a, stop:1 #d9ad3f); }}
QPushButton:pressed {{ background: #b8923a; }}
QPushButton:disabled {{ background: #232834; color: #56627c; border-color: #2b3242; }}
QPushButton[flat="true"] {{ background: transparent; color: {theme.ACCENT.name()}; padding: 2px 6px; }}
QPushButton[flat="true"]:disabled {{ color: #2c3a58; }}
QPushButton[tab="true"] {{ background: transparent; color: {theme.MUTED.name()}; border: none;
    border-bottom: 2px solid transparent; border-radius: 0; padding: 6px 14px; font-weight: 600; }}
QPushButton[tab="true"]:hover {{ color: {theme.TEXT.name()}; }}
QPushButton[tab="true"]:checked {{ color: {theme.TEXT.name()}; border-bottom: 2px solid {theme.GOLD.name()}; }}
QPushButton[tab="true"]:disabled {{ color: #3a4560; }}
QSpinBox {{ background: #10131b; color: {theme.TEXT.name()}; border: 1px solid #363e52; border-radius: 6px;
    padding: 3px 6px; }}
QCheckBox {{ color: {theme.TEXT.name()}; }}
"""
KEY_CHOICES = ([N_("Clic izquierdo"), N_("Clic derecho"), N_("Botón central"), N_("Botón lateral 4"),
                N_("Botón lateral 5")]
               + [f"F{i}" for i in range(1, 13)] + list("1234567890") + ["Q", "E", "R", "T", "F", N_("Espacio")])


def _stored(text):
    """The key a combo's text names: its mouse buttons and Espacio are shown in the app's language, kept in Spanish."""
    return next((k for k in KEY_CHOICES if tr(k) == text), text)


GLYPH_KEYBOARD, GLYPH_MOUSE, GLYPH_WAIT = "\uE765", "\uE962", "\uE916"
LEVEL_COLOR = {"bad": theme.COMBAT, "warn": theme.WARN, "ok": theme.GAIN, "info": theme.ACCENT}
BAR_TILE = 48
STEP_TILE = 56
SKILL_TILE = 46
SKILLS_PER_ROW = 10
COMPARE_COLUMNS = 4          # "Antes" + the latest tests
SIDE_WIDTH = 500             # the test and its results, right of the bar and the macro
NARROW = 1180                # narrower (the AionDex window): the test goes below them, the bar does not fit beside


class TunerWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.tuner = ctl.tuner
        self.fonts = Fonts(1.0)
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Afinador de macros", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE)
        self.resize(1300, 880)

        root = self.root = QBoxLayout(QBoxLayout.LeftToRight, self)
        self._narrow = False
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(16)

        # left: bar, macro, skills ---------------------------------------------------------------------
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left = QWidget()
        self.left = QVBoxLayout(left)
        self.left.setSpacing(8)
        left_scroll.setWidget(left)
        root.addWidget(left_scroll, 1)

        # right: test & results -----------------------------------------------------------------------
        right_frame = QFrame()
        right_frame.setObjectName("Side")
        right_frame.setFixedWidth(SIDE_WIDTH)
        self.right_frame = right_frame
        right = QVBoxLayout(right_frame)
        right.setContentsMargins(14, 12, 10, 12)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        title = QVBoxLayout()
        title.setSpacing(0)
        title.addWidget(label(tr("Afinador de macros"), self.fonts.name_big, theme.TEXT))
        self.subtitle = label("", self.fonts.small, theme.MUTED)
        title.addWidget(self.subtitle)
        head.addLayout(title, 1)
        right.addLayout(head)
        self.test_button = QPushButton(tr("Iniciar prueba"))
        self.test_button.clicked.connect(self._toggle_test)
        right.addWidget(self.test_button)
        self.test_status = label(tr("Pelea 30-60 s usando tus macros como siempre y pulsa «Detener»."), self.fonts.small,
                                 theme.MUTED)
        self.test_status.setWordWrap(True)
        right.addWidget(self.test_status)
        self.calib_button = QPushButton(tr("¿Cuántos ms poner? Calibrar retrasos"))
        self.calib_button.setProperty("flat", True)
        self.calib_button.clicked.connect(self._open_calibration)
        right.addWidget(self.calib_button, 0, Qt.AlignLeft)
        calib_holder = QWidget()
        self.calib = QVBoxLayout(calib_holder)
        self.calib.setContentsMargins(0, 0, 0, 0)
        self.calib.setSpacing(6)
        right.addWidget(calib_holder)
        results_scroll = QScrollArea()
        results_scroll.setWidgetResizable(True)
        results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.results = QVBoxLayout(holder)
        self.results.setSpacing(6)
        results_scroll.setWidget(holder)
        right.addWidget(results_scroll, 1)
        root.addWidget(right_frame)

        self._timer = QTimer(self, interval=500, timeout=self._tick)
        self.rebuild()
        self._update_test_button()

    def resizeEvent(self, event):
        """Side by side when the bar, the macro and the test fit; else the test and its results below (in the AionDex
        window the left column was cut off: the bar alone takes ~680 px)."""
        super().resizeEvent(event)
        narrow = self.width() < NARROW
        if narrow:
            self.right_frame.setMaximumHeight(max(260, int(self.height() * 0.42)))
        if narrow == self._narrow:
            return
        self._narrow = narrow
        self.root.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)
        if narrow:
            self.right_frame.setMinimumWidth(0)
            self.right_frame.setMaximumWidth(16777215)
        else:
            self.right_frame.setMaximumHeight(16777215)
            self.right_frame.setFixedWidth(SIDE_WIDTH)

    # --- left side ----------------------------------------------------------------------------------
    def rebuild(self):
        _clear(self.left)
        f = self.fonts
        tuner = self.tuner
        setup = tuner.setup
        char = self.ctl.character_name()
        self.subtitle.setText(tr("Personaje: {0}", char) if char else tr("Entra con un personaje para ver tus habilidades"))

        # presets + where the data comes from
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        for n in (1, 2, 3):
            tab = QPushButton(f"Preset {n}" + (tr(" · en uso") if n == tuner.active_preset else ""))
            tab.setProperty("tab", True)
            tab.setCheckable(True)
            tab.setChecked(n == tuner.preset)
            tab.setEnabled(bool(tuner.presets.get(n)) or not tuner.presets)
            if tuner.presets and not tuner.presets.get(n):
                tab.setToolTip(tr("Preset vacío en el juego"))
            tab.clicked.connect(lambda _=False, n=n: self._select_preset(n))
            tabs.addWidget(tab)
        tabs.addStretch(1)
        self.left.addLayout(tabs)
        if tuner.loadout_at:
            source = (tr("Leído del juego {0}. El juego lo reenvía al entrar con el personaje y en algunos "
                         "teletransportes; si cambias la barra o subes habilidades y aquí no se actualiza, ve a "
                         "«Cambiar de personaje» y vuelve a entrar.", ago(time.time() - tuner.loadout_at)))
        else:
            source = (tr("Tus habilidades y tu barra se leen del juego al entrar con el personaje. Si AionDex se "
                         "abrió con el juego ya en marcha, ve a «Cambiar de personaje» y vuelve a entrar una vez."))
        hint = label(source, f.small, theme.MUTED)
        hint.setWordWrap(True)
        self.left.addWidget(hint)

        steps = {line.slot: i for i, line in enumerate(setup.ingame, 1) if line.slot is not None}
        if tuner.bar:
            self.left.addWidget(SectionTitle(tr("Tu barra rápida — preset {0}", tuner.preset), f))
            bar_row = QHBoxLayout()
            bar_row.addWidget(BarGrid(tuner, tuner.bar, steps, self._toggle_slot, f, BAR_TILE))
            bar_row.addStretch(1)
            self.left.addLayout(bar_row)
            help_text = label(tr("Cada tecla lanza la primera habilidad de su columna que no esté en recarga, "
                                 "empezando por la fila 0. Clic en una tecla para añadirla a tu macro o quitarla."), f.small,
                              theme.MUTED)
            help_text.setWordWrap(True)
            self.left.addWidget(help_text)

        self.left.addWidget(SectionTitle(tr("Tu macro del juego (K → Macro) — preset {0}", tuner.preset), f))
        if not setup.ingame:
            empty = label(tr("El juego guarda tu macro en tu PC y no lo envía, así que AionDex no puede leerlo: haz "
                             "clic en las teclas de la barra en el mismo orden que tus pasos de K → Macro y pon sus "
                             "retrasos."),
                          f.small, theme.MUTED)
            empty.setWordWrap(True)
            self.left.addWidget(empty)
        else:
            note = label(tr("Déjalo como tu macro del juego (mismas teclas y orden) y escribe el retraso de cada "
                            "paso: ese número no viaja por la red."), f.small, theme.MUTED)
            note.setWordWrap(True)
            self.left.addWidget(note)
        options = None
        for i, line in enumerate(setup.ingame):
            if line.slot is not None:
                self.left.addWidget(self._step_row(i, line, len(setup.ingame)))
            else:
                options = options or tuner.skill_options()
                self.left.addWidget(self._manual_row(i, line, options))
        add = QPushButton(tr("+ Añadir paso"))
        add.setProperty("flat", True)
        add.clicked.connect(lambda: self._add_step_menu(add))
        self.left.addWidget(add, 0, Qt.AlignLeft)

        self.left.addWidget(SectionTitle(tr("Tus teclas del juego (Esc → Ajustes → Teclas)"), f))
        self.left.addWidget(self._bindings_editor(setup))
        self.left.addWidget(SectionTitle(tr("Tu macro externo (Synapse, iCUE, G HUB…)"), f))
        self.left.addWidget(self._external_question(setup))
        if setup.uses_external:
            self.left.addWidget(self._external_editor(setup))
        repeat = QCheckBox(tr("Se repite mientras mantengo el botón"))
        repeat.setChecked(setup.external_repeat)
        repeat.toggled.connect(lambda on: self._edit(lambda s: setattr(s, "external_repeat", on), rebuild=False))
        self.left.addWidget(repeat)
        queue = QCheckBox(tr("Tengo activada la cola de habilidades (Skill Queue) en el juego"))
        queue.setChecked(setup.skill_queue)
        queue.toggled.connect(lambda on: self._edit(lambda s: setattr(s, "skill_queue", on), rebuild=False))
        self.left.addWidget(queue)

        groups = tuner.loadout_groups()
        if groups:
            self.left.addWidget(SectionTitle(tr("Tus habilidades"), f))
            for title, skills in groups:
                self.left.addWidget(label(title, f.name, theme.TEXT))
                grid = QGridLayout()
                grid.setSpacing(5)
                for k, own in enumerate(skills):
                    grid.addWidget(tile_for(tuner, own.base, SKILL_TILE, key=tuner.key_of(own.base)),
                                   k // SKILLS_PER_ROW, k % SKILLS_PER_ROW)
                row = QHBoxLayout()
                row.addLayout(grid)
                row.addStretch(1)
                self.left.addLayout(row)
        self.left.addStretch(1)
        self._show_report(tuner.last_report)

    def _select_preset(self, n):
        self.tuner.select_preset(n)
        self.rebuild()

    def _toggle_slot(self, slot):
        def change(s):
            idx = next((i for i, line in enumerate(s.ingame) if line.slot == slot), None)
            if idx is not None:
                s.ingame.pop(idx)
            else:
                q = next(q for q in self.tuner.bar if q.slot == slot)
                s.ingame.append(MacroLine(list(q.priority), 10, slot))
        self._edit(change)

    def _step_row(self, i, line, count):
        """One step like the game's Macro window: number, the key's skill, what fires if it is on cooldown, delay."""
        f, tuner = self.fonts, self.tuner
        box = QFrame()
        box.setObjectName("Step")
        box.setStyleSheet("QFrame#Step { background: #141822; border-radius: 8px; }")
        row = QHBoxLayout(box)
        row.setContentsMargins(12, 8, 10, 8)
        row.setSpacing(12)
        row.addWidget(label(str(i + 1), f.name_big, theme.GOLD))
        key = tuner.slot_key(line.slot)
        if line.skills:
            row.addWidget(tile_for(tuner, line.skills[0], STEP_TILE, key=key))
        else:
            row.addWidget(SkillTile(STEP_TILE, empty=True, key=key))
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(label(tuner.skill_name(line.skills[0]) if line.skills else tr("Tecla vacía"), f.name, theme.TEXT))
        if len(line.skills) > 1:
            alt = label(tr("Si está en recarga: ") + " › ".join(tuner.skill_name(s) for s in line.skills[1:]),
                        f.small, theme.MUTED)
            alt.setWordWrap(True)
            info.addWidget(alt)
        delay_row = QHBoxLayout()
        delay_row.addWidget(label(tr("Retraso"), f.small, theme.MUTED))
        delay = QSpinBox()
        delay.setRange(0, 2000)
        delay.setSuffix(" ms")
        delay.setValue(line.delay_ms)
        delay.valueChanged.connect(lambda v: self._edit(lambda s: setattr(s.ingame[i], "delay_ms", v), rebuild=False))
        delay_row.addWidget(delay)
        delay_row.addStretch(1)
        info.addLayout(delay_row)
        row.addLayout(info, 1)
        for text, enabled, move in (("▲", i > 0, -1), ("▼", i < count - 1, 1)):
            button = QPushButton(text)
            button.setProperty("flat", True)
            button.setEnabled(enabled)
            button.clicked.connect(lambda _=False, m=move: self._move(i, m))
            row.addWidget(button)
        remove = QPushButton(tr("Quitar"))
        remove.setProperty("flat", True)
        remove.clicked.connect(lambda: self._edit(lambda s: s.ingame.pop(i)))
        row.addWidget(remove)
        return box

    def _move(self, i, delta):
        def change(s):
            j = i + delta
            s.ingame[i], s.ingame[j] = s.ingame[j], s.ingame[i]
        self._edit(change)

    def _manual_row(self, i, line, options):
        """A step written by hand (when the bar was not read from the game)."""
        box = QFrame()
        box.setObjectName("Step")
        box.setStyleSheet("QFrame#Step { background: #141822; border-radius: 8px; }")
        grid = QGridLayout(box)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.addWidget(label(tr("Paso {0}", i + 1), self.fonts.name, theme.TEXT), 0, 0)
        for slot in range(4):
            combo = QComboBox()
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            combo.addItem("—", None)
            for sid, text in options:
                combo.addItem(text, sid)
            current = line.skills[slot] if slot < len(line.skills) else None
            if current is not None:
                idx = combo.findData(current)
                if idx < 0:
                    combo.addItem(f"#{current}", current)
                    idx = combo.count() - 1
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(lambda _, c=combo, k=slot: self._set_line_skill(i, k, c.currentData()))
            grid.addWidget(combo, 0 if slot < 2 else 1, 1 + slot % 2)
        delay = QSpinBox()
        delay.setRange(0, 2000)
        delay.setSuffix(" ms")
        delay.setValue(line.delay_ms)
        delay.valueChanged.connect(lambda v: self._edit(lambda s: setattr(s.ingame[i], "delay_ms", v), rebuild=False))
        grid.addWidget(label(tr("Retraso"), self.fonts.small, theme.MUTED), 1, 0)
        grid.addWidget(delay, 1, 3)
        remove = QPushButton(tr("Quitar"))
        remove.setProperty("flat", True)
        remove.clicked.connect(lambda: self._edit(lambda s: s.ingame.pop(i)))
        grid.addWidget(remove, 0, 3, Qt.AlignRight)
        return box

    def _add_step_menu(self, button):
        menu = styled_menu(self)
        used = {line.slot for line in self.tuner.setup.ingame}
        for q in self.tuner.bar:
            if q.slot in used:
                continue
            names = " › ".join(self.tuner.skill_name(s) for s in q.priority)
            action = menu.addAction(f"{self.tuner.slot_label(q.slot)}: {names}")
            action.triggered.connect(lambda _=False, q=q: self._toggle_slot(q.slot))
        if self.tuner.bar:
            menu.addSeparator()
        manual = menu.addAction(tr("Escribirlo a mano"))
        manual.triggered.connect(lambda: self._edit(lambda s: s.ingame.append(MacroLine([], 10))))
        menu.popup(button.mapToGlobal(QPoint(0, button.height())))

    def _set_line_skill(self, i, slot, sid):
        def change(s):
            skills = s.ingame[i].skills + [None] * 4
            skills[slot] = sid
            s.ingame[i].skills = [x for x in skills[:4] if x is not None]
        self._edit(change, rebuild=False)

    # --- external macro, one row per event like the mouse software -----------------------------------
    def _external_editor(self, setup):
        f = self.fonts
        box = QFrame()
        box.setObjectName("Step")
        box.setStyleSheet("QFrame#Step { background: #141822; border-radius: 8px; }")
        col = QVBoxLayout(box)
        col.setContentsMargins(12, 8, 12, 10)
        col.setSpacing(4)
        if not setup.events:
            hint = label(tr("Cópialo tal cual lo tienes en tu programa del ratón: cada «pulsar», «soltar» y espera, "
                            "en el mismo orden."), f.small, theme.MUTED)
            hint.setWordWrap(True)
            col.addWidget(hint)
        for i, ev in enumerate(setup.events):
            col.addWidget(self._event_row(i, ev, len(setup.events)))
        buttons = QHBoxLayout()
        add_key = QPushButton(tr("+ Tecla (pulsar y soltar)"))
        add_key.setProperty("flat", True)
        add_key.clicked.connect(self._add_key)
        add_wait = QPushButton(tr("+ Espera"))
        add_wait.setProperty("flat", True)
        add_wait.clicked.connect(lambda: self._edit(lambda s: s.events.append(MacroEvent("wait", ms=10))))
        buttons.addWidget(add_key)
        buttons.addWidget(add_wait)
        buttons.addStretch(1)
        col.addLayout(buttons)
        keys = setup.keys()
        if keys:
            col.addWidget(label(tr("Qué hace cada tecla en el juego"), f.name, theme.TEXT))
            for key in keys:
                row = QHBoxLayout()
                row.addWidget(self._glyph(GLYPH_MOUSE if is_mouse(key) else GLYPH_KEYBOARD))
                row.addWidget(label(key, f.body, theme.TEXT))
                role = QComboBox()
                for code, text in ROLES.items():
                    role.addItem(text, code)
                role.setCurrentIndex(max(0, role.findData(setup.role(key))))
                role.currentIndexChanged.connect(
                    lambda _, k=key, c=role: self._edit(lambda s: s.roles.__setitem__(k, c.currentData())))
                bound = setup.bound_role(key)
                if bound:
                    role.setEnabled(False)
                    role.setToolTip(tr("Según tus teclas del juego (arriba)"))
                row.addWidget(role)
                if bound:
                    row.addWidget(label(tr("según tus teclas del juego"), f.small, theme.MUTED))
                row.addStretch(1)
                col.addLayout(row)
            cycle = setup.cycle_ms()
            text = tr("Un ciclo dura {0} ms", cycle) + (tr(" (≈{0:.0f} por segundo)", 1000 / cycle) if cycle else "")
            if len(keys) >= 2:
                text += tr("; entre {0} y {1} esperas {2} ms", keys[0], keys[1], setup.main_gap())
            col.addWidget(label(text + ".", f.small, theme.MUTED))
        return box

    def _external_question(self, setup):
        f = self.fonts
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        char = self.ctl.character_name() or tr("este personaje")
        if setup.uses_external is None:
            row.addWidget(label(tr("¿Usas un macro externo con {0}?", char), f.name, theme.GOLD))
        else:
            row.addWidget(label(tr("Con {0}: ", char) + (tr("uso macro externo + el del juego") if setup.uses_external else
                                                  tr("solo uso el macro del juego")), f.body, theme.TEXT))
        for value, text in ((True, tr("Sí, uso macro externo")), (False, tr("No, solo el del juego"))):
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.setChecked(setup.uses_external is value)
            button.clicked.connect(lambda _=False, v=value: self._edit(lambda s: setattr(s, "uses_external", v)))
            row.addWidget(button)
        row.addStretch(1)
        return box

    def _bindings_editor(self, setup):
        """The two keys the game has for "Macro" and for "Quick Use 1" (the mouse slot), as in its settings."""
        f = self.fonts
        box = QFrame()
        box.setObjectName("Step")
        box.setStyleSheet("QFrame#Step { background: #141822; border-radius: 8px; }")
        grid = QGridLayout(box)
        grid.setContentsMargins(12, 8, 12, 10)
        grid.setHorizontalSpacing(8)
        for r, (role, title) in enumerate(BINDINGS.items()):
            grid.addWidget(label(title, f.body, theme.TEXT), r, 0)
            keys = (setup.bindings.get(role) or ["", ""]) + ["", ""]
            for c in range(2):
                combo = QComboBox()
                combo.setEditable(True)
                combo.addItems([""] + [tr(k) for k in KEY_CHOICES])
                combo.setCurrentText(tr(keys[c]) if keys[c] else "")
                combo.setMinimumContentsLength(12)
                combo.lineEdit().setPlaceholderText(tr("sin asignar"))
                combo.activated.connect(lambda _, rl=role, i=c, cb=combo: self._set_binding(rl, i, _stored(cb.currentText())))
                combo.lineEdit().editingFinished.connect(
                    lambda rl=role, i=c, cb=combo: self._set_binding(rl, i, _stored(cb.currentText())))
                grid.addWidget(combo, r, 1 + c)
        hint = label(tr("AionDex no puede leer tus teclas del juego: ponlas igual que en el juego y sabrá qué hace "
                        "cada tecla de tu macro externo (por ejemplo, F10 = macro y F9 = tu clic)."), f.small, theme.MUTED)
        hint.setWordWrap(True)
        grid.addWidget(hint, len(BINDINGS), 0, 1, 3)
        grid.setColumnStretch(0, 1)
        return box

    def _set_binding(self, role, index, key):
        key = key.strip()
        current = (self.tuner.setup.bindings.get(role) or ["", ""]) + ["", ""]
        if current[index] == key:
            return

        def change(s):
            keys = (s.bindings.get(role) or ["", ""]) + ["", ""]
            keys[index] = key
            s.bindings[role] = keys[:2]
        self._edit(change)

    def _glyph(self, glyph, color=theme.ACCENT):
        font = QFont(self.fonts.icons_family)
        font.setPixelSize(16)
        icon = label(glyph, font, color)
        icon.setFixedWidth(22)
        return icon

    def _event_row(self, i, ev, count):
        f = self.fonts
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        number = label(str(i + 1), f.small, theme.MUTED)
        number.setFixedWidth(18)
        lay.addWidget(number)
        if ev.kind == "wait":
            lay.addWidget(self._glyph(GLYPH_WAIT, theme.GOLD))
            spin = QSpinBox()
            spin.setRange(0, 5000)
            spin.setSuffix(" ms")
            spin.setValue(ev.ms)
            spin.valueChanged.connect(lambda v: self._edit(lambda s: setattr(s.events[i], "ms", v), rebuild=False))
            lay.addWidget(spin)
        else:
            lay.addWidget(self._glyph(GLYPH_MOUSE if is_mouse(ev.key) else GLYPH_KEYBOARD))
            key = QComboBox()
            key.setEditable(True)
            key.addItems([tr(k) for k in KEY_CHOICES])
            key.setCurrentText(tr(ev.key))
            key.setMinimumContentsLength(12)
            key.activated.connect(lambda _: self._rename_key(i, _stored(key.currentText())))
            key.lineEdit().editingFinished.connect(lambda: self._rename_key(i, _stored(key.currentText())))
            lay.addWidget(key)
            direction = QPushButton(tr("↓ pulsar") if ev.kind == "down" else tr("↑ soltar"))
            direction.setProperty("tab", True)
            direction.clicked.connect(lambda: self._edit(
                lambda s: setattr(s.events[i], "kind", "up" if s.events[i].kind == "down" else "down")))
            lay.addWidget(direction)
        lay.addStretch(1)
        for text, enabled, move in (("▲", i > 0, -1), ("▼", i < count - 1, 1)):
            button = QPushButton(text)
            button.setProperty("flat", True)
            button.setEnabled(enabled)
            button.clicked.connect(lambda _=False, m=move: self._edit(lambda s: _swap(s.events, i, i + m)))
            lay.addWidget(button)
        remove = QPushButton("✕")
        remove.setProperty("flat", True)
        remove.clicked.connect(lambda: self._edit(lambda s: s.events.pop(i)))
        lay.addWidget(remove)
        return row

    def _add_key(self):
        def change(s):
            used = set(s.keys())
            key = next(k for k in ["Clic izquierdo"] + KEY_CHOICES[5:] if k not in used)
            if s.events and s.events[-1].kind != "wait":
                s.events.append(MacroEvent("wait", ms=40))
            s.events.extend(key_events(key))
        self._edit(change)

    def _rename_key(self, i, new):
        """Renaming a key renames its press and its release (and keeps what it does in the game)."""
        new = new.strip()
        old = self.tuner.setup.events[i].key
        if not new or new == old:
            return

        def change(s):
            for ev in s.events:
                if ev.key == old:
                    ev.key = new
            if old in s.roles:
                s.roles.setdefault(new, s.roles.pop(old))
        self._edit(change)

    def _edit(self, change, rebuild=True):
        change(self.tuner.setup)
        self.tuner.setup.suggested = False
        self.tuner.save()
        if rebuild:
            self.rebuild()

    # --- test -------------------------------------------------------------------------------------
    def _toggle_test(self):
        if self.tuner.recording:
            report = self.tuner.stop()
            self._timer.stop()
            self.rebuild()               # the test may have picked the preset you actually used
            self._show_report(report)
            self._update_test_button()
        else:
            self.tuner.start()
            self._timer.start()
            self.test_button.setText(tr("Detener y analizar"))
            _clear(self.results)

    def _update_test_button(self):
        n = len(self.tuner.tests())
        self.test_button.setText(tr("Iniciar prueba («Antes»)") if not n else tr("Probar luego del ajuste (Prueba {0})", n))

    def _tick(self):
        s = self.tuner.session
        if not s:
            return
        rejected = len(s.presses) - s.fired
        c = self.tuner.calibration
        if c and c.get("running"):
            fought = self.tuner.fight_seconds()
            pause = tr(" · SIN MANÁ: pausada, recupéralo y sigue") if self.tuner.out_of_mana() else ""
            self.test_status.setText(tr("Prueba con {0} ms: {1:.0f} de {2} s peleando · {3} lanzamientos", self.tuner.calibration_value(), fought, CALIBRATION_SECONDS, s.fired)
                                     + (tr(" · {0} rechazados", rejected) if rejected else "") + pause)
            if fought >= CALIBRATION_SECONDS:
                self._stop_run()
            return
        self.test_status.setText(tr("Grabando… {0} s · {1} lanzamientos", int(s.duration), s.fired)
                                 + (tr(" · {0} rechazados", rejected) if rejected else ""))

    # --- before / after -------------------------------------------------------------------------------
    def _comparison(self, tests):
        """Columns: "Antes" and the latest tests; rows: settings, rates, losses and each skill per minute."""
        f, tuner = self.fonts, self.tuner
        cols = list(enumerate(tests))
        if len(cols) > COMPARE_COLUMNS:
            cols = cols[:1] + cols[-(COMPARE_COLUMNS - 1):]
        self.results.addWidget(SectionTitle(tr("Antes y después"), f))
        table = QWidget()
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)
        for c, (i, _) in enumerate(cols, 1):
            grid.addWidget(label(_test_label(i), f.name, theme.GOLD if c == len(cols) else theme.TEXT), 0, c,
                           Qt.AlignRight)

        noise, measured = test_noise(tests)

        def row(r, title, values, better=None, fmt=str, threshold=None):
            """Each test against "Antes": green = better beyond chance, orange = worse beyond chance."""
            grid.addWidget(label(title, f.small, theme.MUTED), r, 0)
            base = values[0]
            limit = noise if threshold is None else threshold
            for c, v in enumerate(values, 1):
                color = theme.TEXT
                if better and c > 1 and v is not None and base is not None:
                    if better == "less":
                        delta = (base - v)
                        significant = abs(delta) > 0.02           # rejections: two points of share
                    else:
                        delta = (v - base) / base if base else 0
                        significant = abs(delta) > limit
                    if significant:
                        color = theme.GAIN if delta > 0 else theme.WARN
                grid.addWidget(label("—" if v is None else fmt(v), f.body, color), r, c, Qt.AlignRight)

        configs = [t["config"] for _, t in cols]
        grid.addWidget(label(tr("Ajustes (ms)"), f.small, theme.MUTED), 1, 0)
        for c, text in enumerate(configs, 1):
            cell = label(text.replace(" · ", "\n"), f.small, theme.TEXT)
            cell.setAlignment(Qt.AlignRight)
            grid.addWidget(cell, 1, c, Qt.AlignRight)
        row(2, tr("Habilidades/s"), [t["casts_per_s"] for _, t in cols], "more", lambda v: f"{v:.2f}")
        row(3, tr("Daño/s"), [t["dps"] for _, t in cols], "more", lambda v: f"{v / 1000:.1f}K",
            threshold=max(0.4, noise))       # damage swings much more than casts (crits, long cooldowns)
        row(4, "Rechazos", [t["rejected"] for _, t in cols], "less", lambda v: f"{100 * v:.0f}%")
        row(5, tr("Clic"), [t["click_share"] for _, t in cols], None, lambda v: f"{100 * v:.0f}%")
        row(6, "Ping", [t["ping"] for _, t in cols], None, lambda v: f"{v:.0f} ms")
        row(7, tr("Peleando"), [t["fight_s"] for _, t in cols], None, lambda v: f"{v:.0f} s")
        skills = []
        for _, t in cols:
            for sid, where in t.get("where", {}).items():
                if sid not in skills:
                    skills.append(sid)
        r = 8
        if skills:
            grid.addWidget(label(tr("Por minuto"), f.small, theme.MUTED), r, 0)
            r += 1
        def rate(test, sid):
            return 60 * test["skills"].get(sid, 0) / test["fight_s"] if test["fight_s"] else None

        for sid in skills:
            # each skill's own chance swing, from tests repeated with the same settings
            swings = [abs(rate(a, sid) - rate(b, sid)) / max(rate(a, sid), rate(b, sid))
                      for i, a in enumerate(tests) for b in tests[i + 1:]
                      if a["config"] == b["config"] and max(rate(a, sid) or 0, rate(b, sid) or 0)]
            limit = max([0.5, 2 * noise] + swings)
            row(r, tuner.skill_name(int(sid)), [rate(t, sid) for _, t in cols], "more", lambda v: f"{v:.1f}",
                threshold=limit)
            r += 1
        grid.setColumnStretch(0, 1)
        self.results.addWidget(table)
        source = (tr("con los mismos ajustes, tus pruebas variaron") if measured else tr("dos pruebas iguales suelen "
                                                                                     "variar"))
        hint = label(tr("Cada prueba se compara con «Antes»: verde = mejor, naranja = peor, blanco = igual dentro "
                        "del azar ({0} un {1:.0f}%). Para afinar más el margen, repite una prueba sin cambiar nada. "
                        "Compara peleas parecidas (mismo objetivo, 30–60 s).", source, 100 * noise), f.small, theme.MUTED)
        hint.setWordWrap(True)
        self.results.addWidget(hint)
        reset = QPushButton(tr("Empezar una comparación nueva"))
        reset.setProperty("flat", True)
        reset.clicked.connect(self._reset_tests)
        self.results.addWidget(reset, 0, Qt.AlignLeft)

    def _mana_section(self, report):
        f, m = self.fonts, report.mana
        self.results.addWidget(SectionTitle(tr("Maná"), f))
        if m.seconds_to_empty is None:
            verdict, color = tr("Tu maná aguanta: recuperas al menos lo que gastas."), theme.GAIN
        elif m.seconds_to_empty < 90 or m.low_share > 0.1:
            verdict, color = tr("Te quedas sin maná en unos {0:.0f} s de pelea seguida.", m.seconds_to_empty), theme.COMBAT
        else:
            verdict, color = (tr("Pierdes maná poco a poco: aguantas unos {0:.0f} min de pelea seguida.", m.seconds_to_empty / 60), theme.TEXT)
        summary = label(tr("{0} Gastas {1:.0f} MP/min y recuperas {2:.0f} (máximo {3}).", verdict, m.spent_per_min, m.gained_per_min, m.max_mp) + (tr(" Sin maná: {0:.0f} s (no cuentan en la prueba).", report.low_mp_s)
                                                   if report.low_mp_s >= 1 else ""), f.small, color)
        summary.setWordWrap(True)
        self.results.addWidget(summary)
        table = QWidget()
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        for col, head in enumerate(("", tr("Habilidad"), "MP", tr("Veces/min"), "MP/min", tr("Daño/MP"))):
            grid.addWidget(label(head, f.small, theme.MUTED), 0, col, Qt.AlignLeft if col < 2 else Qt.AlignRight)
        for r, (sid, cost, per_min, mp_min, dmg_mp) in enumerate(m.skills[:8], 1):
            grid.addWidget(tile_for(self.tuner, sid, 22, border=False), r, 0)
            grid.addWidget(label(self.tuner.skill_name(sid), f.body, theme.TEXT, elide=True), r, 1)
            grid.addWidget(label(f"{-cost:+d}", f.body, theme.GAIN if cost < 0 else theme.TEXT), r, 2, Qt.AlignRight)
            grid.addWidget(label(f"{per_min:.1f}", f.small, theme.MUTED), r, 3, Qt.AlignRight)
            grid.addWidget(label(f"{-mp_min:+.0f}", f.small, theme.GAIN if mp_min < 0 else theme.MUTED), r, 4,
                           Qt.AlignRight)
            grid.addWidget(label(f"{dmg_mp:.0f}" if dmg_mp is not None else "—", f.small, theme.MUTED), r, 5,
                           Qt.AlignRight)
        grid.setColumnStretch(1, 1)
        self.results.addWidget(table)

    def _reset_tests(self):
        self.tuner.reset_tests()
        self._update_test_button()
        self._show_report(self.tuner.last_report)

    # --- delay calibration ----------------------------------------------------------------------------
    def _open_calibration(self):
        self.tuner.calibration = None
        self._show_calibration()

    def _close_calibration(self):
        if self.tuner.recording:
            self.tuner.cancel()
            self._timer.stop()
            self.test_status.setText(tr("Prueba cancelada."))
        self.tuner.calibration = None
        self.test_button.setEnabled(True)
        self.calib_button.setVisible(True)
        _clear(self.calib)

    def _kind_label(self, kind):
        setup = self.tuner.setup
        keys = setup.keys()
        if kind == "hold":
            return tr("Cuánto mantener {0} (macro externo)", setup.key_name('macro'))
        if kind == "hold_attack":
            return tr("Cuánto mantener {0} (macro externo)", setup.key_name('click'))
        if kind == "gap":
            return tr("Espera entre {0} y {1} (macro externo)", keys[0], keys[1]) if len(keys) >= 2 else tr("Espera entre teclas")
        return tr("Retraso de los pasos del macro del juego")

    def _kind_where(self, kind):
        setup = self.tuner.setup
        keys = setup.keys()
        if kind in ("hold", "hold_attack"):
            key = setup.key_name("macro" if kind == "hold" else "click")
            return tr("en la espera entre «{0} ↓ pulsar» y «{1} ↑ soltar» de tu macro externo", key, key)
        if kind == "gap":
            pair = f"{keys[0]} y {keys[1]}" if len(keys) >= 2 else tr("tus dos teclas")
            return (tr("en la espera entre {0} de tu macro externo (la que va justo después de soltar {1})", pair, keys[0] if keys else tr("la primera")))
        return tr("en el retraso de TODOS los pasos de K → Macro")

    def _show_calibration(self):
        f, tuner = self.fonts, self.tuner
        _clear(self.calib)
        self.calib_button.setVisible(False)
        c = tuner.calibration
        self.test_button.setEnabled(c is None)
        box = QFrame()
        box.setObjectName("Calib")
        box.setStyleSheet("QFrame#Calib { background: #141822; border: 1px solid #363e52; border-radius: 8px; }")
        col = QVBoxLayout(box)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(6)

        def text(value, font=f.small, color=theme.TEXT):
            lbl = label(value, font, color)
            lbl.setWordWrap(True)
            col.addWidget(lbl)
            return lbl

        if c is None:
            col.addWidget(label(tr("Calibrar retrasos"), f.name, theme.TEXT))
            ping = tuner.ping_ms
            text(tr("Pruebas cortas con distintos valores contra el mismo objetivo; AionDex mide lo que se lanza, "
                    "lo que se pierde y tu daño en cada una, y te dice el mejor valor.")
                 + (tr(" Tu ping medido: {0:.0f} ms.", ping) if ping else ""), color=theme.MUTED)
            kinds = tuner.calibration_kinds()
            first = tuner.calibration_first()
            if first == "hold" and tuner.last_report and tuner.last_report.starved:
                text(tr("Tu última prueba: el macro del juego no pasa del paso 1. Empieza por «{0}»: hasta que "
                        "llegue a todos los pasos, el resto de valores no cambia nada.", self._kind_label('hold')), color=theme.WARN)
            if len(kinds) > 1:
                every = QPushButton(tr("Calibrar todo, en orden (recomendado)"))
                every.clicked.connect(lambda: self._choose_kind("all"))
                col.addWidget(every)
            for kind in kinds:
                button = QPushButton(self._kind_label(kind))
                button.setProperty("tab", True)
                button.clicked.connect(lambda _=False, k=kind: self._choose_kind(k))
                col.addWidget(button)
            if not kinds:
                text(tr("Añade antes los pasos de tu macro del juego o tu macro externo."), color=theme.MUTED)
            cancel = QPushButton(tr("Cancelar"))
            cancel.setProperty("flat", True)
            cancel.clicked.connect(self._close_calibration)
            col.addWidget(cancel, 0, Qt.AlignLeft)
            self.calib.addWidget(box)
            return

        stage = len(c["done"]) + 1
        stages = stage + len(c["queue"])
        title = self._kind_label(c["kind"])
        col.addWidget(label(tr("Calibrar ({0} de {1}): {2}", stage, stages, title) if stages > 1 else tr("Calibrar: {0}", title),
                            f.name, theme.TEXT))
        for kind, value in c["done"]:
            text(f"✓ {self._kind_label(kind)}: {value} ms", color=theme.GAIN)
        value = tuner.calibration_value()
        runs = c["runs"]
        if runs:
            col.addWidget(self._runs_table(runs))
        if value is not None:
            setup = tuner.setup
            macro_key, attack_key = setup.key_name("macro"), setup.key_name("click")
            how = (tr("usa tu macro externo como siempre (pulsa {0} y {1})", macro_key, attack_key) if setup.uses_external else
                   tr("mantén {0} y usa {1} como en tus peleas", macro_key, attack_key))
            text(tr("Prueba {0} de {1}: pon {2} ms {3}. Luego pulsa «Empezar», {4} y pelea {5} s contra el mismo "
                    "objetivo (un muñeco de entrenamiento es lo ideal). AionDex mide aparte lo que sale de cada "
                    "tecla y para solo.", len(runs) + 1, len(c['values']), value, self._kind_where(c['kind']), how, CALIBRATION_SECONDS))
            text(tr("Si te quedas sin maná, la prueba se pausa sola: espera a recuperarlo y sigue pegando; ese rato "
                    "no cuenta."), color=theme.MUTED)
            start = QPushButton(tr("Empezar prueba con {0} ms", value))
            start.setEnabled(not c.get("running"))
            start.clicked.connect(self._start_run)
            col.addWidget(start)
        else:
            best, reason = tuner.calibration_result()
            if best:
                col.addWidget(label(tr("Pon {0} ms", best.value), f.name_big, theme.GAIN))
                text(reason[0].upper() + reason[1:])
                follow = tr(" y seguir con «{0}»", self._kind_label(c['queue'][0])) if c["queue"] else ""
                apply = QPushButton(tr("Guardar {0} ms{1}", best.value, follow))
                apply.clicked.connect(lambda: self._apply(best.value))
                col.addWidget(apply)
                text(tr("Ponlo también en el juego o en tu programa del ratón: AionDex no puede cambiarlo por ti."),
                     color=theme.MUTED)
            else:
                text(reason, color=theme.WARN)
                retry = QPushButton(tr("Repetir con valores más altos"))
                retry.clicked.connect(self._retry_higher)
                col.addWidget(retry)
        close = QPushButton(tr("Cerrar calibración"))
        close.setProperty("flat", True)
        close.clicked.connect(self._close_calibration)
        col.addWidget(close, 0, Qt.AlignLeft)
        self.calib.addWidget(box)

    def _runs_table(self, runs):
        from ..tuner import run_problem
        f = self.fonts
        table = QWidget()
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        for col, head in enumerate((tr("Valor"), tr("Ataque/min"), "Macro/min", tr("Hab./s"), tr("Rech."), "")):
            grid.addWidget(label(head, f.small, theme.MUTED), 0, col, Qt.AlignRight if 0 < col < 5 else Qt.AlignLeft)
        for r, run in enumerate(runs, 1):
            problem = run_problem(run)
            color = theme.WARN if problem else theme.TEXT
            cells = (f"{run.value} ms", f"{run.attack_per_min:.0f}", f"{run.macro_per_min:.0f}",
                     f"{run.casts_per_s:.2f}", f"{100 * run.rejected:.0f}%", problem)
            for col, value in enumerate(cells):
                grid.addWidget(label(value, f.small if col == 5 else f.body, color), r, col,
                               Qt.AlignRight if 0 < col < 5 else Qt.AlignLeft)
        return table

    def _retry_higher(self):
        c = self.tuner.calibration
        top = max(c["values"])
        c["values"] = [top * 2, top * 3, top * 4]
        c["runs"] = []
        self._show_calibration()

    def _choose_kind(self, kind):
        self.tuner.start_calibration(kind)
        self._show_calibration()

    def _start_run(self):
        self.tuner.start_run()
        self._timer.start()
        _clear(self.results)
        self._show_calibration()

    def _stop_run(self):
        report = self.tuner.stop()
        self._timer.stop()
        self._show_report(report)
        self._show_calibration()

    def _apply(self, value):
        self.tuner.apply_calibration(value)
        self.tuner.next_calibration()
        self.rebuild()
        if self.tuner.calibration and not self.tuner.calibration["queue"] and self.tuner.calibration_value() is None:
            self._close_calibration()
            self.test_status.setText(tr("Calibración guardada. Ahora haz una prueba normal con los valores nuevos: "
                                        "se compara con «Antes» y verás la revisión del maná."))
            return
        self._show_calibration()

    def _show_report(self, report):
        _clear(self.results)
        if report is None:
            return
        f = self.fonts
        gap = tr(" · uno cada {0:.0f} ms", report.median_gap_ms) if report.median_gap_ms else ""
        rejected = tr(" · {0} rechazados", report.rejected) if report.rejected else ""
        ping = tr(" · tu ping {0:.0f} ms", report.ping_ms) if report.ping_ms else ""
        self.test_status.setText(tr("Prueba de {0} s ({1} s peleando) · {2} lanzamientos{3}{4}{5}", int(report.duration), int(report.fight_s), report.total, rejected, gap, ping))
        tests = self.tuner.tests()
        if len(tests) >= 2:
            self._comparison(tests)
        self.results.addWidget(SectionTitle(tr("Qué cambiar"), f))
        for finding in report.findings:
            box = QFrame()
            box.setObjectName("Finding")
            color = LEVEL_COLOR.get(finding.level, theme.MUTED)
            box.setStyleSheet(f"QFrame#Finding {{ background: #141822; border-left: 3px solid {color.name()}; "
                              f"border-radius: 4px; }}")
            col = QVBoxLayout(box)
            col.setContentsMargins(10, 6, 8, 6)
            text = label(finding.text, f.name, theme.TEXT)
            text.setWordWrap(True)
            col.addWidget(text)
            if finding.advice:
                advice = label(finding.advice, f.small, theme.MUTED)
                advice.setWordWrap(True)
                col.addWidget(advice)
            self.results.addWidget(box)
        if report.mana:
            self._mana_section(report)
        self.results.addWidget(SectionTitle(tr("Lanzado frente a posible"), f))
        table = QWidget()
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        for col, head in enumerate(("", tr("Habilidad"), tr("Dónde"), tr("Lanzada"), tr("Posible"), tr("Rech."))):
            grid.addWidget(label(head, f.small, theme.MUTED), 0, col, Qt.AlignLeft if col < 3 else Qt.AlignRight)
        for r, row in enumerate(report.rows, 1):
            short = row.possible and row.fired / row.possible < 0.6
            color = theme.COMBAT if row.where and not row.fired else theme.WARN if short else theme.TEXT
            grid.addWidget(tile_for(self.tuner, row.skill, 24, border=False), r, 0)
            grid.addWidget(label(self.tuner.skill_name(row.skill), f.body, color, elide=True), r, 1)
            grid.addWidget(label(row.where or tr("fuera"), f.small, theme.MUTED), r, 2)
            grid.addWidget(label(str(row.fired), f.name, color), r, 3, Qt.AlignRight)
            grid.addWidget(label(f"~{row.possible}" if row.possible else "—", f.small, theme.MUTED), r, 4,
                           Qt.AlignRight)
            grid.addWidget(label(str(row.rejected) if row.rejected else "", f.small, theme.WARN), r, 5, Qt.AlignRight)
        grid.setColumnStretch(1, 1)
        self.results.addWidget(table)
        note = label(tr("«Posible»: veces que su recarga permitía lanzarla mientras peleabas. «—»: sin recarga."),
                     f.small, theme.MUTED)
        note.setWordWrap(True)
        self.results.addWidget(note)
        self.results.addStretch(1)


def _swap(items, i, j):
    items[i], items[j] = items[j], items[i]


def _test_label(i):
    return tr("Antes") if i == 0 else tr("Prueba {0}", i)


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
