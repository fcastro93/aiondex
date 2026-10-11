"""Skill icons drawn like the game's: icon, "Lv. N" bottom-right, key badge top-left, colored border, empty slots."""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QWidget

from ..skills import BAR_SLOTS, SLOT_KEYS
from . import theme
from .widgets import label

ACTIVE_BORDER = QColor("#e8963a")       # the game's orange frame for active skills and stigmas
PASSIVE_BORDER = QColor("#4fbf63")      # green frame for passives
EMPTY_BG = QColor("#2a3346")
EMPTY_STAR = QColor("#56627c")
GROUP_GAPS = {5, 23, 0}                 # extra space after keys 4, 8 and the mouse (slots 5, 23, 0), like the game
MOUSE_GLYPH = ""                  # Segoe MDL2 Assets / Fluent Icons mouse, for the mouse key's badge
MIN_LEVEL_SIZE = 32                     # smaller tiles skip the "Lv." text
_pixmaps = {}


def _pixmap(path):
    key = str(path)
    if key not in _pixmaps:
        _pixmaps[key] = QPixmap(key)
    return _pixmaps[key]


def clear_tile_cache():
    _pixmaps.clear()


def _font(px, bold=True, family="Segoe UI"):
    font = QFont(family)
    font.setPixelSize(max(7, int(px)))
    font.setBold(bold)
    return font


class SkillTile(QWidget):
    """One square skill icon. Empty tiles show the game's faint star. Clickable when on_click is given."""
    clicked = Signal()

    def __init__(self, size, icon=None, level=None, key=None, corner=None, border=None, empty=False, step=None,
                 dim=False, initials="", tooltip="", clickable=False):
        super().__init__()
        self.setFixedSize(size, size)
        self.icon, self.level, self.key, self.corner = icon, level, key, corner
        self.border, self.empty, self.step, self.dim, self.initials = border, empty, step, dim, initials
        self.clickable, self.hover = clickable, False
        if tooltip:
            self.setToolTip(tooltip)
        if clickable:
            self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, _):
        self.hover = self.clickable
        self.update()

    def leaveEvent(self, _):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if self.clickable and event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _):
        s = self.width()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(1, 1, s - 2, s - 2)
        path = QPainterPath()
        path.addRoundedRect(r, 4, 4)
        p.setClipPath(path)
        if self.empty:
            p.fillRect(r, EMPTY_BG)
            p.setPen(EMPTY_STAR)
            p.setFont(_font(s * 0.3, bold=False))
            p.drawText(r, Qt.AlignCenter, "✦")
        elif self.icon:
            p.drawPixmap(r.toRect(), _pixmap(self.icon))
        else:
            p.fillRect(r, QColor("#34405c"))
            p.setPen(theme.TEXT)
            p.setFont(_font(s * 0.3))
            p.drawText(r, Qt.AlignCenter, self.initials)
        if self.dim:
            p.fillRect(r, QColor(0, 0, 0, 120))
        if self.level is not None and not self.empty and s >= MIN_LEVEL_SIZE:
            shade = QLinearGradient(0, s * 0.55, 0, s)
            shade.setColorAt(0, QColor(0, 0, 0, 0))
            shade.setColorAt(1, QColor(0, 0, 0, 190))
            p.fillRect(QRectF(0, s * 0.55, s, s * 0.45), shade)
            p.setPen(QColor("#ffffff"))
            p.setFont(_font(s * 0.21))
            p.drawText(r.adjusted(0, 0, -s * 0.06, -s * 0.03), Qt.AlignRight | Qt.AlignBottom, f"Lv. {self.level}")
        p.setClipping(False)
        if self.key:
            mouse = self.key == "Ratón"
            width = s * 0.26 if mouse else max(s * 0.26, s * 0.13 * len(self.key))
            self._badge(p, QRectF(2, 2, width, s * 0.26), MOUSE_GLYPH if mouse else self.key,
                        QColor(12, 16, 26, 230), QColor("#ffffff"), "Segoe MDL2 Assets" if mouse else "Segoe UI")
        if self.corner:
            w = s * 0.13 * len(self.corner) + s * 0.08
            self._badge(p, QRectF(s - w - 2, 2, w, s * 0.24), self.corner, QColor(12, 16, 26, 230), theme.GOLD)
        if self.step is not None:
            d = s * 0.32
            rect = QRectF(2, s - d - 2, d, d)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.ACCENT)
            p.drawEllipse(rect)
            p.setPen(QColor("#06101f"))
            p.setFont(_font(d * 0.62))
            p.drawText(rect, Qt.AlignCenter, str(self.step))
        border = theme.ACCENT if self.hover else self.border
        if border is not None:
            p.setPen(QPen(border, 2 if self.hover or s >= 40 else 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        p.end()

    @staticmethod
    def _badge(p, rect, text, bg, fg, family="Segoe UI"):
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, 2, 2)
        p.setPen(fg)
        p.setFont(_font(rect.height() * 0.72, family=family))
        p.drawText(rect, Qt.AlignCenter, text)


def tile_for(tuner, sid, size, key=None, step=None, clickable=False, dim=False, border=True, extra_tip=""):
    """A SkillTile for one of your skills, with its icon, level and the game's frame color."""
    kind = tuner.skill_kind(sid)
    own = tuner.own.get(tuner.fold(sid))
    name = tuner.skill_name(sid)
    tip = name + (f" — nivel {own.level}" + (f" ({own.learned} + {own.bonus})" if own.bonus else "") if own else "")
    if own and own.specs:
        tip += f"\nEspecializaciones: {', '.join(map(str, own.specs))}"
    if extra_tip:
        tip += "\n" + extra_tip
    color = (PASSIVE_BORDER if kind == "passive" else ACTIVE_BORDER) if border and kind else None
    initials = "".join(w[0] for w in name.split()[:2]).upper()
    return SkillTile(size, icon=tuner.icon_path(sid), level=own.level if own else None, key=key,
                     corner="·".join(map(str, own.specs)) if own and own.specs and size >= 40 else None,
                     border=color, step=step, dim=dim, initials=initials, tooltip=tip, clickable=clickable)


class BarGrid(QWidget):
    """The game's quickslot bar: 12 keys (1-8, Q, E, mouse, T) x 4 priority rows (0 at the bottom).
    Clicking a key column calls on_toggle(slot)."""

    def __init__(self, tuner, slots, steps, on_toggle, fonts, size=48):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        by_slot = {q.slot: q for q in slots}
        col = 0
        for slot in BAR_SLOTS:
            q = by_slot.get(slot)
            for row, prio in enumerate((3, 2, 1, 0)):
                sid = q.priority[prio] if q and prio < len(q.priority) else None
                key = SLOT_KEYS[slot] if prio == 0 else None
                if sid is None:
                    tile = SkillTile(size, empty=True, key=key, step=steps.get(slot) if prio == 0 else None,
                                     clickable=bool(q), tooltip=_slot_tip(tuner, slot, q, steps))
                else:
                    tile = tile_for(tuner, sid, size, key=key, step=steps.get(slot) if prio == 0 else None,
                                    clickable=True, dim=prio > 0 and slot not in steps,
                                    extra_tip=_slot_tip(tuner, slot, q, steps))
                if tile.clickable:
                    tile.clicked.connect(lambda s=slot: on_toggle(s))
                grid.addWidget(tile, row, col)
            col += 1
            if slot in GROUP_GAPS:
                grid.setColumnMinimumWidth(col, 6)
                col += 1
        for row, prio in enumerate((3, 2, 1, 0)):
            lbl = QWidget()
            lay = QHBoxLayout(lbl)
            lay.setContentsMargins(6, 0, 0, 0)
            lay.addWidget(label(str(prio), fonts.name_big, theme.MUTED))
            grid.addWidget(lbl, row, col)


def _slot_tip(tuner, slot, q, steps):
    where = tuner.slot_label(slot)
    if not q:
        return f"{where}: vacía"
    order = " › ".join(tuner.skill_name(s) for s in q.priority)
    state = f"Paso {steps[slot]} de tu macro (clic para quitarla)" if slot in steps else "Clic para añadirla al macro"
    return f"{where} · prioridad: {order}\n{state}"
