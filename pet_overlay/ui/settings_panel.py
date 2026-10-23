"""Overlay panel of the Ajustes module: version, updates, auto-update switch, language, size, minimap, alert sounds,
Npcap status, links."""
import webbrowser

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import REPO, __version__
from ..sounds import SOUNDS, VOLUME_STEPS
from . import theme
from .notifications_panel import ago
from .widgets import SectionTitle, label
from .. import i18n
from ..i18n import LANGUAGES, tr


class PillButton(QLabel):
    """Clickable pill; the overlay shell calls on_click on a plain click."""

    def __init__(self, text, fonts, on_click, enabled=True, small=False, outline=False):
        super().__init__(text)
        self.outline = outline
        self.setFont(fonts.small if small else fonts.name)
        self.setAlignment(Qt.AlignCenter)
        self.enabled = enabled
        self.on_click = on_click if enabled else (lambda: None)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        pad = (10, 3) if small else (14, 5)
        self.radius = fonts.px(9 if small else 12)
        self.setContentsMargins(fonts.px(pad[0]), fonts.px(pad[1]), fonts.px(pad[0]), fonts.px(pad[1]))
        self._style(False)

    def _style(self, hover):
        if not self.enabled:
            bg, fg = "rgba(255,255,255,0.06)", theme.MUTED.name()
        elif self.outline:                       # a choice that is not the chosen one
            border = "#3fd3ff" if hover else theme.ACCENT.name()
            self.setStyleSheet(f"background: transparent; color: {theme.TEXT.name()}; border: 1px solid {border}; "
                               f"border-radius: {self.radius}px;")
            return
        else:
            bg, fg = ("#3fd3ff" if hover else theme.ACCENT.name()), "#06101f"
        self.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: {self.radius}px;")

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


class VolumeStep(QWidget):
    """One segment of a volume bar: a click sets the volume up to it."""

    def __init__(self, filled, muted, fonts, on_click):
        super().__init__()
        self.filled, self.muted = filled, muted
        self.on_click = on_click
        self.setFixedSize(fonts.px(11), fonts.px(14))
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 3, -1, -3)
        p.setPen(Qt.NoPen)
        p.setBrush((theme.MUTED if self.muted else theme.ACCENT) if self.filled else theme.BAR_BG)
        p.drawRoundedRect(r, 2, 2)


UI_SCALES = (1.0, 1.1, 1.2, 1.3, 1.4, 1.5)


class ScaleStep(QWidget):
    """One step of the "Tamaño de todo" slider: a click picks that size (the ones up to it are filled)."""

    def __init__(self, filled, fonts, on_click):
        super().__init__()
        self.filled = filled
        self.on_click = on_click
        self.setFixedSize(fonts.px(30), fonts.px(18))
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 5, -1, -5)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.ACCENT if self.filled else theme.BAR_BG)
        p.drawRoundedRect(r, 3, 3)


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
                item.widget().hide()            # gone at once, not when the deferred delete runs: no blink
                item.widget().deleteLater()
            elif item.layout():
                _clear(item.layout())
        f = self.fonts
        up = self.ctl.updates

        self.content.addWidget(SectionTitle(tr("Actualizaciones"), f))
        version = QHBoxLayout()
        version.addWidget(label(f"{self.ctl.app_name} v{__version__}", f.name, theme.TEXT), 1)
        busy = up.state in ("checking", "downloading")
        if up.state == "ready" and up.can_install:
            version.addWidget(PillButton(tr("Instalar v{0}", up.release.version), f, up.install_now))
        else:
            version.addWidget(PillButton(tr("Buscando…") if busy else tr("Buscar updates"), f,
                                         lambda: up.check(manual=True), enabled=not busy))
        self.content.addLayout(version)
        color = {"error": theme.WARN, "ready": theme.GAIN, "available": theme.GAIN}.get(up.state, theme.MUTED)
        status = label(up.detail or tr("Aún no se buscaron actualizaciones"), f.small, color)
        status.setWordWrap(True)
        self.content.addWidget(status)
        if up.last_check:
            self.content.addWidget(label(tr("Última búsqueda: {0}", ago(up.last_check)), f.small, theme.MUTED))
        self.content.addWidget(self._toggle(tr("Actualizar automáticamente"), "auto_update"))

        self._language()
        self._ui_scale()
        self._minimap()

        self.content.addWidget(SectionTitle(tr("Sonidos"), f))
        for key, (text, _) in SOUNDS.items():
            self._sound_row(key, text)

        self.content.addWidget(SectionTitle(tr("Sistema"), f))
        npcap = self.ctl.npcap_installed()
        self.content.addWidget(label(tr("Npcap instalado") if npcap else tr("Falta Npcap: la app no puede leer el juego"),
                                     f.small, theme.GAIN if npcap else theme.WARN))
        if not npcap:
            self.content.addWidget(_link(tr("Descargar Npcap"), "https://npcap.com/#download", f))
        self.content.addWidget(_link(tr("Ver el proyecto en GitHub"), f"https://github.com/{REPO}", f))
        credit = label(tr("Datos y mapas: metabot.gg · No afiliado a NCSoft"), f.small, theme.MUTED)
        credit.setWordWrap(True)
        self.content.addWidget(credit)
        self.adjustSize()

    def _minimap(self):
        """Minimapa: its 4 corners, where the route over it is drawn (and what is read of it)."""
        f = self.fonts
        self.content.addWidget(SectionTitle(tr("Minimapa"), f))
        sizes = sorted(((self.ctl.settings.get("minimap") or {}).get("corners") or {}))
        text = (tr("Esquinas calibradas ({0}): la ruta se dibuja solo dentro de ellas.", ', '.join(sizes)) if sizes else
                tr("AionDex lo encuentra solo por el nombre de la zona. Si la ruta se sale del minimapa, calibra "
                   "sus 4 esquinas."))
        note = label(text, f.small, theme.GAIN if sizes else theme.MUTED)
        note.setWordWrap(True)
        self.content.addWidget(note)
        self.content.addWidget(self._toggle(tr("Vida de jefes bajo el minimapa"), "abyss_bosses"))
        hint = label(tr("Los jefes que tienes cerca (mazmorras, world bosses, el Abismo y sus cores de asedio), con "
                        "su vida siempre a la vista bajo tus misiones (y fuera de vista, la última que se vio)."), f.small,
                     theme.MUTED)
        hint.setWordWrap(True)
        self.content.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(PillButton(tr("Calibrar esquinas"), f, self.ctl.open_minimap_calibration, small=True))
        if sizes:
            row.addWidget(PillButton(tr("Automático"), f, self.ctl.reset_minimap_calibration, small=True))
        row.addStretch(1)
        self.content.addLayout(row)

    def _language(self):
        """Idioma / Language: the app's texts in English (the default) or Spanish; the game's names stay as they are."""
        f = self.fonts
        self.content.addWidget(SectionTitle(tr("Idioma"), f))
        chosen = self.ctl.language
        row = QHBoxLayout()
        row.setSpacing(f.px(6))
        for lang, name in LANGUAGES.items():
            if lang == chosen:
                row.addWidget(PillButton(f"✓ {name}", f, lambda: None, small=True))
            else:
                row.addWidget(PillButton(name, f, lambda v=lang: self.ctl.set_language(v), small=True, outline=True))
        row.addStretch(1)
        self.content.addLayout(row)
        if chosen != i18n.LANG:
            line = QHBoxLayout()
            line.addWidget(label(tr("Se aplica al reiniciar AionDex"), f.small, theme.WARN), 1)
            line.addWidget(PillButton(tr("Reiniciar ahora"), f, self.ctl.restart_app, small=True))
            self.content.addLayout(line)
        else:
            note = label(tr("Los textos de AionDex. Los nombres del juego (pets, monstruos, objetos, mapas, clases, "
                            "habilidades) salen como los manda el juego."), f.small, theme.MUTED)
            note.setWordWrap(True)
            self.content.addWidget(note)

    def _ui_scale(self):
        """Accesibilidad: the size of everything (overlay, windows, texts), 100-250%."""
        f = self.fonts
        self.content.addWidget(SectionTitle(tr("Accesibilidad"), f))
        chosen = min(float(self.ctl.settings.get("ui_scale", 1.0) or 1.0), UI_SCALES[-1])
        head = QHBoxLayout()
        head.addWidget(label(tr("Tamaño de todo"), f.body, theme.TEXT), 1)
        head.addWidget(label(f"{round(chosen * 100)}%", f.name, theme.TEXT))
        self.content.addLayout(head)
        row = QHBoxLayout()                    # the slider: one step per size, filled up to the chosen one
        row.setSpacing(f.px(4))
        for value in UI_SCALES:
            step = ScaleStep(value <= chosen + 0.01, f, lambda v=value: self.ctl.set_ui_scale(v))
            step.setToolTip(f"{round(value * 100)}%")
            row.addWidget(step)
        row.addSpacing(f.px(6))
        if chosen >= UI_SCALES[-1] - 0.01:     # the slider at its end
            row.addWidget(label(tr("Modo Marvin"), f.name, theme.GOLD))
        row.addStretch(1)
        self.content.addLayout(row)
        if abs(chosen - self.ctl.ui_scale_active) > 0.01:
            line = QHBoxLayout()
            note = label(tr("Se aplica al reiniciar AionDex"), f.small, theme.WARN)
            line.addWidget(note, 1)
            line.addWidget(PillButton(tr("Reiniciar ahora"), f, self.ctl.restart_app, small=True))
            self.content.addLayout(line)
        else:
            self.content.addWidget(label(tr("Agranda el overlay, las ventanas, los textos y los iconos."), f.small,
                                         theme.MUTED))

    def _sound_row(self, key, text):
        """Name and a test button, then the volume bar (click a segment), the % and the on/off switch."""
        f, sounds = self.fonts, self.ctl.sounds
        conf = sounds.config(key)
        head = QHBoxLayout()
        head.setContentsMargins(0, f.px(4), 0, 0)
        head.addWidget(label(text, f.body, theme.TEXT), 1)
        test = label(tr("▶ Probar"), f.small, theme.ACCENT)
        test.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        test.setCursor(Qt.PointingHandCursor)
        test.on_click = lambda: sounds.play(key, test=True)
        head.addWidget(test)
        self.content.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(f.px(2))
        level = round(conf["volume"] / (100 / VOLUME_STEPS))
        for i in range(1, VOLUME_STEPS + 1):
            row.addWidget(VolumeStep(i <= level, conf["muted"], f,
                                     lambda v=i * 100 // VOLUME_STEPS: self._set_volume(key, v)))
        row.addSpacing(f.px(6))
        row.addWidget(label(tr("silenciado") if conf["muted"] else f"{conf['volume']}%", f.small,
                            theme.MUTED if conf["muted"] else theme.TEXT))
        row.addStretch(1)
        switch = QWidget()
        lay = QHBoxLayout(switch)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(Switch(not conf["muted"], f))
        switch.setCursor(Qt.PointingHandCursor)
        switch.setToolTip(tr("Activar / silenciar este sonido"))
        switch.on_click = lambda: self._toggle_mute(key)
        row.addWidget(switch)
        self.content.addLayout(row)

    def _set_volume(self, key, volume):
        self.ctl.sounds.set_volume(key, volume)
        self.ctl.sounds.play(key)
        self.ctl.refresh()

    def _toggle_mute(self, key):
        self.ctl.sounds.toggle_mute(key)
        self.ctl.sounds.play(key)          # silent when it was just muted; a sample when it was turned back on
        self.ctl.refresh()

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
            item.widget().hide()                # gone at once, not when the deferred delete runs: no blink
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
