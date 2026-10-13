"""Diagnóstico window: live status, one button per common problem, the diagnosis and the report to send."""
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import APP_NAME, __version__
from .. import diagnostics
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label

PROBLEM_STYLE = f"""
QPushButton[problem="true"] {{ background: #0f1830; color: {theme.TEXT.name()}; border: 1px solid #24345a;
    border-radius: 8px; padding: 9px 12px; text-align: left; font-weight: 500; }}
QPushButton[problem="true"]:hover {{ border-color: {theme.ACCENT.name()}; }}
QPushButton[problem="true"]:checked {{ border-color: {theme.ACCENT.name()}; background: #13264a; }}
QPushButton[secondary="true"] {{ background: #1a2747; color: {theme.TEXT.name()}; }}
QPushButton[secondary="true"]:hover {{ background: #22345e; }}
"""
LEVEL_COLOR = {"ok": theme.GAIN, "info": theme.ACCENT, "warn": theme.WARN, "bad": theme.COMBAT}


class DiagnosticsWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.problem = None              # (key, label, function) shown on the right
        self.diagnosis = None
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — Diagnóstico")
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + PROBLEM_STYLE)
        self.resize(1040, 720)
        f = self.fonts

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(16)

        # left: status + problems -------------------------------------------------------------------
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left = QWidget()
        col = QVBoxLayout(left)
        col.setSpacing(8)
        col.addWidget(SectionTitle("Estado de AionDex", f))
        status_holder = QWidget()
        self.status_rows = QVBoxLayout(status_holder)
        self.status_rows.setContentsMargins(0, 0, 0, 0)
        self.status_rows.setSpacing(5)
        col.addWidget(status_holder)
        col.addSpacing(8)
        col.addWidget(SectionTitle("¿Qué problema tienes?", f))
        hint = label("Elige uno: AionDex revisa lo que está viendo, arregla lo que puede él solo y te dice qué hacer.",
                     f.small, theme.MUTED)
        hint.setWordWrap(True)
        col.addWidget(hint)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.problem_buttons = {}
        for i, problem in enumerate(diagnostics.PROBLEMS):
            button = QPushButton(problem[1])
            button.setProperty("problem", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, p=problem: self.run(p))
            grid.addWidget(button, i // 2, i % 2)
            self.problem_buttons[problem[0]] = button
        col.addLayout(grid)
        col.addStretch(1)
        left_scroll.setWidget(left)
        root.addWidget(left_scroll, 1)

        # right: diagnosis + report -------------------------------------------------------------------
        side = QFrame()
        side.setObjectName("Side")
        side.setFixedWidth(470)
        right = QVBoxLayout(side)
        right.setContentsMargins(14, 12, 10, 12)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("Diagnóstico", f.name_big, theme.TEXT))
        titles.addWidget(label(f"AionDex {__version__}", f.small, theme.MUTED))
        head.addLayout(titles, 1)
        right.addLayout(head)
        result_scroll = QScrollArea()
        result_scroll.setWidgetResizable(True)
        result_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.result = QVBoxLayout(holder)
        self.result.setSpacing(6)
        result_scroll.setWidget(holder)
        right.addWidget(result_scroll, 1)

        right.addWidget(SectionTitle("¿Sigue igual?", f))
        send = label("Copia el informe y mándaselo al creador de AionDex (no lleva tu nombre de personaje ni tu "
                     "usuario de Windows).", f.small, theme.MUTED)
        send.setWordWrap(True)
        right.addWidget(send)
        buttons = QHBoxLayout()
        copy = QPushButton("Copiar informe")
        copy.clicked.connect(self.copy_report)
        github = QPushButton("Reportar en GitHub")
        github.setProperty("secondary", True)
        github.clicked.connect(self.open_issue)
        buttons.addWidget(copy)
        buttons.addWidget(github)
        right.addLayout(buttons)
        self.copied = label("", f.small, theme.GAIN)
        right.addWidget(self.copied)
        root.addWidget(side)

        self._timer = QTimer(self, interval=2000, timeout=self.refresh_status)
        self.refresh_status()
        self._intro()

    def showEvent(self, event):
        self._timer.start()
        self.refresh_status()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # --- status ---------------------------------------------------------------------------------------
    def rerun(self, key):
        if self.isVisible() and self.problem and self.problem[0] == key:
            self.run(self.problem)

    def refresh_status(self):
        if self.problem and self.problem[0] == "blink" and self.ctl.blink_watch.active:
            self._show(self.problem[2](self.ctl))           # the recording's countdown
        _clear(self.status_rows)
        for check in diagnostics.status(self.ctl):
            self.status_rows.addWidget(self._check_row(check))

    def _check_row(self, check):
        f = self.fonts
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        dot = label("●", f.body, LEVEL_COLOR.get(check.level, theme.MUTED))
        lay.addWidget(dot, 0, Qt.AlignTop)
        text = label(f"<b>{check.title}</b> — {check.detail}" if check.detail else f"<b>{check.title}</b>",
                     f.body, theme.TEXT)
        text.setTextFormat(Qt.RichText)
        text.setWordWrap(True)
        lay.addWidget(text, 1)
        return row

    # --- diagnosis ------------------------------------------------------------------------------------
    def _intro(self):
        _clear(self.result)
        text = label("Elige a la izquierda el problema que tienes. AionDex mirará lo que está recibiendo del juego, "
                     "arreglará lo que pueda él solo y te dirá qué hacer con lo demás.", self.fonts.body, theme.MUTED)
        text.setWordWrap(True)
        self.result.addWidget(text)
        self.result.addStretch(1)

    def run(self, problem):
        self.problem = problem
        for key, button in self.problem_buttons.items():
            button.setChecked(key == problem[0])
        try:
            self.diagnosis = problem[2](self.ctl)
        except Exception as exc:                 # a diagnosis must never take the app down
            import logging
            logging.getLogger(__name__).exception("diagnosis %s failed", problem[0])
            self.diagnosis = diagnostics.Diagnosis(problem[1], [diagnostics.Check("Diagnóstico", "bad",
                                                                                  f"Falló: {exc}")])
        self._show(self.diagnosis)
        self.refresh_status()

    def _show(self, d):
        f = self.fonts
        _clear(self.result)
        self.result.addWidget(label(d.title, f.name_big, theme.TEXT))
        verdict = {"ok": ("Todo lo que puedo revisar está bien.", theme.GAIN),
                   "warn": ("Encontré algo que revisar.", theme.WARN),
                   "bad": ("Encontré el problema.", theme.COMBAT)}[d.level]
        if d.fixed and d.level == "ok":
            verdict = ("Lo arreglé.", theme.GAIN)
        self.result.addWidget(label(verdict[0], f.name, verdict[1]))

        self.result.addWidget(SectionTitle("Lo que revisé", f))
        for check in d.checks:
            self.result.addWidget(self._check_row(check))
        if d.fixed:
            self.result.addWidget(SectionTitle("Lo que arreglé", f))
            for text in d.fixed:
                self.result.addWidget(self._bullet(text, theme.GAIN))
        if d.todo:
            self.result.addWidget(SectionTitle("Qué hacer", f))
            for text in d.todo:
                self.result.addWidget(self._bullet(text, theme.TEXT))
        actions = QHBoxLayout()
        for action in d.actions:
            button = QPushButton(action.label)
            button.clicked.connect(lambda _=False, a=action: self._do(a))
            actions.addWidget(button)
        again = QPushButton("Volver a revisar")
        again.setProperty("secondary", True)
        again.clicked.connect(lambda: self.run(self.problem))
        actions.addWidget(again)
        actions.addStretch(1)
        self.result.addLayout(actions)
        self.result.addStretch(1)

    def _bullet(self, text, color):
        row = label(f"• {text}", self.fonts.body, color)
        row.setWordWrap(True)
        return row

    def _do(self, action):
        action.run()
        QTimer.singleShot(1500, lambda: self.run(self.problem) if self.problem else None)

    # --- report ---------------------------------------------------------------------------------------
    def copy_report(self):
        QGuiApplication.clipboard().setText(diagnostics.report(self.ctl, self.diagnosis))
        self.copied.setText("Informe copiado: pégalo (Ctrl+V) en Discord, WhatsApp o donde hables con el creador.")

    def open_issue(self):
        self.copy_report()
        title = f"[{__version__}] {self.diagnosis.title if self.diagnosis else 'Problema'}"
        webbrowser.open(diagnostics.github_issue_url(title))
        self.copied.setText("Informe copiado: en la página de GitHub, pégalo (Ctrl+V) donde se indica.")


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
