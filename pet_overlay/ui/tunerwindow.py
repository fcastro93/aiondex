"""Afinador window, laid out like the game: your quickslot bar per preset, the in-game macro (one key + delay per
step), your skills; then the external macro, the test and what to change."""
import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..diagnostics import ago
from ..tuner import ExternalStep, MacroLine
from . import theme
from .appmenu import styled_menu
from .mapwindow import STYLE, badge_label
from .skilltiles import BarGrid, SkillTile, tile_for
from .theme import Fonts
from .widgets import SectionTitle, label

EXTRA_STYLE = f"""
QPushButton {{ background: {theme.ACCENT.name()}; color: #06101f; border: none; border-radius: 6px;
    padding: 6px 14px; font-weight: 600; }}
QPushButton:hover {{ background: #3fd3ff; }}
QPushButton[flat="true"] {{ background: transparent; color: {theme.ACCENT.name()}; padding: 2px 6px; }}
QPushButton[flat="true"]:disabled {{ color: #2c3a58; }}
QPushButton[tab="true"] {{ background: #0f1830; color: {theme.MUTED.name()}; border: 1px solid #24345a;
    border-radius: 8px; padding: 6px 16px; }}
QPushButton[tab="true"]:checked {{ background: #13264a; color: {theme.TEXT.name()}; border-color: {theme.ACCENT.name()}; }}
QPushButton[tab="true"]:disabled {{ color: #3a4560; border-color: #1a2440; }}
QSpinBox {{ background: #0d1628; color: {theme.TEXT.name()}; border: 1px solid #24345a; border-radius: 6px;
    padding: 3px 6px; }}
QCheckBox {{ color: {theme.TEXT.name()}; }}
"""
EXTERNAL_KEYS = [("click_izq", "Clic izquierdo"), ("click_der", "Clic derecho"), ("macro", "Tecla del macro"),
                 ("otra", "Otra tecla")]
LEVEL_COLOR = {"bad": theme.COMBAT, "warn": theme.WARN, "ok": theme.GAIN, "info": theme.ACCENT}
BAR_TILE = 48
STEP_TILE = 56
SKILL_TILE = 46
SKILLS_PER_ROW = 10


class TunerWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.tuner = ctl.tuner
        self.fonts = Fonts(1.0)
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — Afinador de macros")
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE)
        self.resize(1300, 880)

        root = QHBoxLayout(self)
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
        right_frame.setFixedWidth(440)
        right = QVBoxLayout(right_frame)
        right.setContentsMargins(14, 12, 10, 12)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        title = QVBoxLayout()
        title.setSpacing(0)
        title.addWidget(label("Afinador de macros", self.fonts.name_big, theme.TEXT))
        self.subtitle = label("", self.fonts.small, theme.MUTED)
        title.addWidget(self.subtitle)
        head.addLayout(title, 1)
        right.addLayout(head)
        self.test_button = QPushButton("Iniciar prueba")
        self.test_button.clicked.connect(self._toggle_test)
        right.addWidget(self.test_button)
        self.test_status = label("Pelea 30-60 s usando tus macros como siempre y pulsa «Detener».", self.fonts.small,
                                 theme.MUTED)
        self.test_status.setWordWrap(True)
        right.addWidget(self.test_status)
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

    # --- left side ----------------------------------------------------------------------------------
    def rebuild(self):
        _clear(self.left)
        f = self.fonts
        tuner = self.tuner
        setup = tuner.setup
        char = self.ctl.character_name()
        self.subtitle.setText(f"Personaje: {char}" if char else "Entra con un personaje para ver tus habilidades")

        # presets + where the data comes from
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        for n in (1, 2, 3):
            tab = QPushButton(f"Preset {n}" + (" · en uso" if n == tuner.active_preset else ""))
            tab.setProperty("tab", True)
            tab.setCheckable(True)
            tab.setChecked(n == tuner.preset)
            tab.setEnabled(bool(tuner.presets.get(n)) or not tuner.presets)
            if tuner.presets and not tuner.presets.get(n):
                tab.setToolTip("Preset vacío en el juego")
            tab.clicked.connect(lambda _=False, n=n: self._select_preset(n))
            tabs.addWidget(tab)
        tabs.addStretch(1)
        self.left.addLayout(tabs)
        if tuner.loadout_at:
            source = (f"Leído del juego {ago(time.time() - tuner.loadout_at)}. El juego lo reenvía al entrar con el "
                      f"personaje y en algunos teletransportes; si cambias la barra o subes habilidades y aquí no se "
                      f"actualiza, ve a «Cambiar de personaje» y vuelve a entrar.")
        else:
            source = ("Tus habilidades y tu barra se leen del juego al entrar con el personaje. Si AionDex se abrió "
                      "con el juego ya en marcha, ve a «Cambiar de personaje» y vuelve a entrar una vez.")
        hint = label(source, f.small, theme.MUTED)
        hint.setWordWrap(True)
        self.left.addWidget(hint)

        steps = {line.slot: i for i, line in enumerate(setup.ingame, 1) if line.slot is not None}
        if tuner.bar:
            self.left.addWidget(SectionTitle(f"Tu barra rápida — preset {tuner.preset}", f))
            bar_row = QHBoxLayout()
            bar_row.addWidget(BarGrid(tuner, tuner.bar, steps, self._toggle_slot, f, BAR_TILE))
            bar_row.addStretch(1)
            self.left.addLayout(bar_row)
            help_text = label("Cada tecla lanza la primera habilidad de su columna que no esté en recarga, empezando "
                              "por la fila 0. Clic en una tecla para añadirla a tu macro o quitarla.", f.small,
                              theme.MUTED)
            help_text.setWordWrap(True)
            self.left.addWidget(help_text)

        self.left.addWidget(SectionTitle(f"Tu macro del juego (K → Macro) — preset {tuner.preset}", f))
        if not setup.ingame:
            empty = label("El juego guarda tu macro en tu PC y no lo envía, así que AionDex no puede leerlo: haz clic "
                          "en las teclas de la barra en el mismo orden que tus pasos de K → Macro y pon sus retrasos.",
                          f.small, theme.MUTED)
            empty.setWordWrap(True)
            self.left.addWidget(empty)
        else:
            note = label("Déjalo como tu macro del juego (mismas teclas y orden) y escribe el retraso de cada paso: "
                         "ese número no viaja por la red.", f.small, theme.MUTED)
            note.setWordWrap(True)
            self.left.addWidget(note)
        options = None
        for i, line in enumerate(setup.ingame):
            if line.slot is not None:
                self.left.addWidget(self._step_row(i, line, len(setup.ingame)))
            else:
                options = options or tuner.skill_options()
                self.left.addWidget(self._manual_row(i, line, options))
        add = QPushButton("+ Añadir paso")
        add.setProperty("flat", True)
        add.clicked.connect(lambda: self._add_step_menu(add))
        self.left.addWidget(add, 0, Qt.AlignLeft)

        self.left.addWidget(SectionTitle("Tu macro externo (Synapse, iCUE, G HUB…) — opcional", f))
        for i, step in enumerate(setup.external):
            self.left.addWidget(self._external_row(i, step))
        add_ext = QPushButton("+ Añadir pulsación")
        add_ext.setProperty("flat", True)
        add_ext.clicked.connect(lambda: self._edit(lambda s: s.external.append(ExternalStep("macro", 20, 20))))
        self.left.addWidget(add_ext, 0, Qt.AlignLeft)
        repeat = QCheckBox("Se repite mientras mantengo el botón")
        repeat.setChecked(setup.external_repeat)
        repeat.toggled.connect(lambda on: self._edit(lambda s: setattr(s, "external_repeat", on), rebuild=False))
        self.left.addWidget(repeat)
        queue = QCheckBox("Tengo activada la cola de habilidades (Skill Queue) en el juego")
        queue.setChecked(setup.skill_queue)
        queue.toggled.connect(lambda on: self._edit(lambda s: setattr(s, "skill_queue", on), rebuild=False))
        self.left.addWidget(queue)

        groups = tuner.loadout_groups()
        if groups:
            self.left.addWidget(SectionTitle("Tus habilidades", f))
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
        box.setStyleSheet("QFrame#Step { background: #0f1830; border-radius: 8px; }")
        row = QHBoxLayout(box)
        row.setContentsMargins(12, 8, 10, 8)
        row.setSpacing(12)
        row.addWidget(label(str(i + 1), f.name_big, theme.GOLD))
        key = tuner.slot_label(line.slot).replace("Tecla ", "")
        if line.skills:
            row.addWidget(tile_for(tuner, line.skills[0], STEP_TILE, key=key))
        else:
            row.addWidget(SkillTile(STEP_TILE, empty=True, key=key))
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(label(tuner.skill_name(line.skills[0]) if line.skills else "Tecla vacía", f.name, theme.TEXT))
        if len(line.skills) > 1:
            alt = label("Si está en recarga: " + " › ".join(tuner.skill_name(s) for s in line.skills[1:]),
                        f.small, theme.MUTED)
            alt.setWordWrap(True)
            info.addWidget(alt)
        delay_row = QHBoxLayout()
        delay_row.addWidget(label("Retraso", f.small, theme.MUTED))
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
        remove = QPushButton("Quitar")
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
        box.setStyleSheet("QFrame#Step { background: #0f1830; border-radius: 8px; }")
        grid = QGridLayout(box)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.addWidget(label(f"Paso {i + 1}", self.fonts.name, theme.TEXT), 0, 0)
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
        grid.addWidget(label("Retraso", self.fonts.small, theme.MUTED), 1, 0)
        grid.addWidget(delay, 1, 3)
        remove = QPushButton("Quitar")
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
        manual = menu.addAction("Escribirlo a mano")
        manual.triggered.connect(lambda: self._edit(lambda s: s.ingame.append(MacroLine([], 10))))
        menu.popup(button.mapToGlobal(QPoint(0, button.height())))

    def _set_line_skill(self, i, slot, sid):
        def change(s):
            skills = s.ingame[i].skills + [None] * 4
            skills[slot] = sid
            s.ingame[i].skills = [x for x in skills[:4] if x is not None]
        self._edit(change, rebuild=False)

    def _external_row(self, i, step):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(label(f"{i + 1}.", self.fonts.name, theme.MUTED))
        key = QComboBox()
        key.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        key.setMinimumContentsLength(14)
        for code, text in EXTERNAL_KEYS:
            key.addItem(text, code)
        key.setCurrentIndex(max(0, key.findData(step.key)))
        key.currentIndexChanged.connect(lambda _: self._edit(lambda s: setattr(s.external[i], "key", key.currentData()),
                                                            rebuild=False))
        lay.addWidget(key)
        for attr, text in (("hold_ms", "mantener"), ("wait_ms", "esperar")):
            spin = QSpinBox()
            spin.setRange(0, 2000)
            spin.setSuffix(" ms")
            spin.setValue(getattr(step, attr))
            spin.valueChanged.connect(lambda v, a=attr: self._edit(lambda s: setattr(s.external[i], a, v), rebuild=False))
            lay.addWidget(label(text, self.fonts.small, theme.MUTED))
            lay.addWidget(spin)
        remove = QPushButton("Quitar")
        remove.setProperty("flat", True)
        remove.clicked.connect(lambda: self._edit(lambda s: s.external.pop(i)))
        lay.addWidget(remove)
        lay.addStretch(1)
        return row

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
            self.test_button.setText("Iniciar prueba")
            self.rebuild()               # the test may have picked the preset you actually used
            self._show_report(report)
        else:
            self.tuner.start()
            self._timer.start()
            self.test_button.setText("Detener y analizar")
            _clear(self.results)

    def _tick(self):
        s = self.tuner.session
        if s:
            rejected = len(s.presses) - s.fired
            self.test_status.setText(f"Grabando… {int(s.duration)} s · {s.fired} lanzamientos"
                                     + (f" · {rejected} rechazados" if rejected else ""))

    def _show_report(self, report):
        _clear(self.results)
        if report is None:
            return
        f = self.fonts
        gap = f" · uno cada {report.median_gap_ms:.0f} ms" if report.median_gap_ms else ""
        rejected = f" · {report.rejected} rechazados" if report.rejected else ""
        self.test_status.setText(f"Prueba de {int(report.duration)} s ({int(report.fight_s)} s peleando) · "
                                 f"{report.total} lanzamientos{rejected}{gap}")
        self.results.addWidget(SectionTitle("Qué cambiar", f))
        for finding in report.findings:
            box = QFrame()
            box.setObjectName("Finding")
            color = LEVEL_COLOR.get(finding.level, theme.MUTED)
            box.setStyleSheet(f"QFrame#Finding {{ background: #0f1830; border-left: 3px solid {color.name()}; "
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
        self.results.addWidget(SectionTitle("Lanzado frente a posible", f))
        table = QWidget()
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        for col, head in enumerate(("", "Habilidad", "Dónde", "Lanzada", "Posible", "Rech.")):
            grid.addWidget(label(head, f.small, theme.MUTED), 0, col, Qt.AlignLeft if col < 3 else Qt.AlignRight)
        for r, row in enumerate(report.rows, 1):
            short = row.possible and row.fired / row.possible < 0.6
            color = theme.COMBAT if row.where and not row.fired else theme.WARN if short else theme.TEXT
            grid.addWidget(tile_for(self.tuner, row.skill, 24, border=False), r, 0)
            grid.addWidget(label(self.tuner.skill_name(row.skill), f.body, color, elide=True), r, 1)
            grid.addWidget(label(row.where or "fuera", f.small, theme.MUTED), r, 2)
            grid.addWidget(label(str(row.fired), f.name, color), r, 3, Qt.AlignRight)
            grid.addWidget(label(f"~{row.possible}" if row.possible else "—", f.small, theme.MUTED), r, 4,
                           Qt.AlignRight)
            grid.addWidget(label(str(row.rejected) if row.rejected else "", f.small, theme.WARN), r, 5, Qt.AlignRight)
        grid.setColumnStretch(1, 1)
        self.results.addWidget(table)
        note = label("«Posible»: veces que su recarga permitía lanzarla mientras peleabas. «—»: sin recarga.",
                     f.small, theme.MUTED)
        note.setWordWrap(True)
        self.results.addWidget(note)
        self.results.addStretch(1)


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
