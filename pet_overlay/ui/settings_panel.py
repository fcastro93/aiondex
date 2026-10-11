"""Overlay panel of the Ajustes module: version, updates, auto-update switch, Npcap status, links."""
import webbrowser

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import REPO, __version__
from . import theme
from .notifications_panel import ago
from .widgets import SectionTitle, label


class PillButton(QLabel):
    """Clickable pill; the overlay shell calls on_click on a plain click."""

    def __init__(self, text, fonts, on_click, enabled=True):
        super().__init__(text)
        self.setFont(fonts.name)
        self.setAlignment(Qt.AlignCenter)
        self.enabled = enabled
        self.on_click = on_click if enabled else (lambda: None)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        self.setContentsMargins(fonts.px(14), fonts.px(5), fonts.px(14), fonts.px(5))
        self._style(False)

    def _style(self, hover):
        if not self.enabled:
            bg, fg = "rgba(255,255,255,0.06)", theme.MUTED.name()
        else:
            bg, fg = ("#3fd3ff" if hover else theme.ACCENT.name()), "#06101f"
        self.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 12px;")

    def enterEvent(self, _):
        self._style(True)

    def leaveEvent(self, _):
        self._style(False)


class Switch(QWidget):
    """Small on/off switch drawn in the theme colors."""

    def __init__(self, on, fonts):
        super().__init__()
        self.on = on
        self.setFixedSize(fonts.px(30), fonts.px(16))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.ACCENT if self.on else theme.BAR_BG)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.right() - d - 2 if self.on else r.left() + 2
        p.setBrush(theme.TEXT)
        p.drawEllipse(QRectF(x, r.top() + 2, d, d))


class SettingsPanel(QWidget):
    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(3))

    def render(self):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                _clear(item.layout())
        f = self.fonts
        up = self.ctl.updates

        self.content.addWidget(SectionTitle("Actualizaciones", f))
        version = QHBoxLayout()
        version.addWidget(label(f"{self.ctl.app_name} v{__version__}", f.name, theme.TEXT), 1)
        busy = up.state in ("checking", "downloading")
        if up.state == "ready" and up.can_install:
            version.addWidget(PillButton(f"Instalar v{up.release.version}", f, up.install_now))
        else:
            version.addWidget(PillButton("Buscando…" if busy else "Buscar updates", f,
                                         lambda: up.check(manual=True), enabled=not busy))
        self.content.addLayout(version)
        color = {"error": theme.WARN, "ready": theme.GAIN, "available": theme.GAIN}.get(up.state, theme.MUTED)
        status = label(up.detail or "Aún no se buscaron actualizaciones", f.small, color)
        status.setWordWrap(True)
        self.content.addWidget(status)
        if up.last_check:
            self.content.addWidget(label(f"Última búsqueda: {ago(up.last_check)}", f.small, theme.MUTED))
        self.content.addWidget(self._toggle("Actualizar automáticamente", "auto_update"))

        self.content.addWidget(SectionTitle("Sistema", f))
        npcap = self.ctl.npcap_installed()
        self.content.addWidget(label("Npcap instalado" if npcap else "Falta Npcap: la app no puede leer el juego",
                                     f.small, theme.GAIN if npcap else theme.WARN))
        if not npcap:
            self.content.addWidget(_link("Descargar Npcap", "https://npcap.com/#download", f))
        self.content.addWidget(_link("Ver el proyecto en GitHub", f"https://github.com/{REPO}", f))
        credit = label("Datos y mapas: metabot.gg · No afiliado a NCSoft", f.small, theme.MUTED)
        credit.setWordWrap(True)
        self.content.addWidget(credit)
        self.adjustSize()

    def _toggle(self, text, key):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, self.fonts.px(4), 0, 0)
        lay.addWidget(label(text, self.fonts.body, theme.TEXT), 1)
        lay.addWidget(Switch(self.ctl.settings.get(key, True), self.fonts))
        row.setCursor(Qt.PointingHandCursor)
        row.on_click = lambda: self.ctl.toggle_setting(key)
        return row


def _link(text, url, fonts):
    lbl = label(text, fonts.small, theme.ACCENT)
    lbl.setAttribute(Qt.WA_TransparentForMouseEvents, False)
    lbl.setCursor(Qt.PointingHandCursor)
    lbl.on_click = lambda: webbrowser.open(url)
    return lbl


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
