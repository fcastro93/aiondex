"""A skill named the way the game names it: its small rounded icon (framed orange for an active, green for a passive,
like the game's skill window), the name and a tooltip. While the icon is not on disk it shows the skill's initials
and the page asks for it (Controller.fetch_skill_icons: the page passes a set that collects the missing ids and draws
again when they come)."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from . import theme
from .skilltiles import ACTIVE_BORDER, PASSIVE_BORDER
from .widgets import label
from ..i18n import tr

_cache = {}


def initials(name):
    return "".join(w[0] for w in str(name).split()[:2] if w).upper()


def skill_pixmap(path, size, dpr, name="", kind=None, family="Segoe UI"):
    """The icon at path (or the initials of name when None), size px with rounded corners and its frame."""
    border = ACTIVE_BORDER if kind == "active" else PASSIVE_BORDER if kind == "passive" else None
    key = (str(path) if path else None, "" if path else initials(name), size, round(dpr, 2), kind)
    if key in _cache:
        return _cache[key]
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    r = QRectF(0.5, 0.5, size - 1, size - 1)
    radius = max(2.0, size * 0.2)
    shape = QPainterPath()
    shape.addRoundedRect(r, radius, radius)
    src = QPixmap(str(path)) if path else QPixmap()
    if not src.isNull():
        p.setClipPath(shape)
        p.drawPixmap(r, src, QRectF(src.rect()))
        p.setClipping(False)
    else:
        p.fillPath(shape, theme.SURFACE_2)
        font = QFont(family)
        font.setPixelSize(max(6, int(size * 0.42)))
        font.setBold(True)
        p.setFont(font)
        p.setPen(theme.MUTED)
        p.drawText(r, Qt.AlignCenter, initials(name))
    if border is not None:
        p.setPen(QPen(border, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, radius, radius)
    p.end()
    _cache[key] = pm
    return pm


class SkillName(QWidget):
    """[icon] text: a skill with its icon; text defaults to its name. missing: a set the skill id goes into when its
    icon is not downloaded yet (the page asks for those after drawing); elide / wrap: how a long text fits."""

    def __init__(self, ctl, fonts, sid, text=None, font=None, color=None, size=18, elide=False, tip="",
                 missing=None, wrap=False, parent=None):
        super().__init__(parent)
        tuner = ctl.tuner
        name = tuner.skill_name(sid)
        kind = tuner.skill_kind(sid)
        path = tuner.icon_path(sid)
        if path is None and missing is not None:
            missing.add(tuner.fold(sid))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(fonts.px(5))
        icon = QLabel()
        icon.setFixedSize(size, size)
        icon.setPixmap(skill_pixmap(path, size, self.devicePixelRatioF(), name, kind, fonts.family))
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        icon.setStyleSheet("background: transparent;")
        lay.addWidget(icon, 0, Qt.AlignVCenter)
        self.text = label(name if text is None else text, font or fonts.small, color or theme.TEXT, elide=elide)
        if wrap:
            self.text.setWordWrap(True)
        lay.addWidget(self.text, 1 if elide or wrap else 0, Qt.AlignVCenter)
        if not (elide or wrap):
            lay.addStretch(1)
        head = name + ({"active": " · " + tr("Activa"), "passive": " · " + tr("Pasiva")}.get(kind, ""))
        self.setToolTip(head + ("\n" + tip if tip else ""))
