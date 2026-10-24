"""The character picker in each page's head («Personaje: [El que juegas · Agame ▾]»): the same choice as the AionDex
window's rail (Controller.focus_character / set_character_choice), so every page that talks about one character -
Mejoras, Arcanas, Builds, Season Missions, Logros, Pase, Profesiones, Tracker - lets you pick it right there, and
picking it on one page changes it on all of them."""
import weakref

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QWidget

from . import theme
from .widgets import label
from ..i18n import tr

_ALL = weakref.WeakSet()


def refresh_all():
    """Every picker on screen shows the current choice and characters (after a change here or in the rail)."""
    for picker in list(_ALL):
        try:
            picker.fill()
        except RuntimeError:                     # its Qt object is gone
            continue


class CharacterPicker(QWidget):
    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(fonts.px(6))
        lay.addWidget(label(tr("Personaje"), fonts.small, theme.MUTED))
        self.box = QComboBox()
        self.box.setFont(fonts.body)
        self.box.setCursor(Qt.PointingHandCursor)
        self.box.setMinimumWidth(fonts.px(150))
        self.box.setSizeAdjustPolicy(QComboBox.AdjustToContents)      # the whole name, never cut
        self.box.setToolTip(tr("El personaje del que habla esta página (y todas las demás): el que juegas, o el que "
                               "elijas"))
        self.box.currentIndexChanged.connect(self._changed)
        lay.addWidget(self.box)
        self._key = None
        _ALL.add(self)
        self.fill()

    def fill(self):
        ctl = self.ctl
        playing = ctl.character_name()
        names = ctl.known_characters()
        chosen = ctl.settings.get("character_choice")
        key = (playing, tuple(names), chosen)
        if key == self._key:
            return
        self._key = key
        self.box.blockSignals(True)
        self.box.clear()
        self.box.addItem(tr("El que juegas") + (f" · {playing}" if playing else ""), None)
        for n in names:
            self.box.addItem(n + (tr(" (en juego)") if n == playing else ""), n)
        self.box.setCurrentIndex(max(0, self.box.findData(chosen)) if chosen in names else 0)
        self.box.blockSignals(False)

    def showEvent(self, event):
        self.fill()
        super().showEvent(event)

    def _changed(self, _index):
        self.ctl.set_character_choice(self.box.currentData())
