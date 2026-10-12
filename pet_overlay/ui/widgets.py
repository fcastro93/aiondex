"""Overlay building blocks, all custom-painted so they stay crisp on a translucent panel."""
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QRadialGradient
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme

_icon_cache = {}
LED_COLORS = {"red": QColor("#ff4d4d"), "green": QColor("#46d97a"), "grey": QColor("#5a6478")}


def clear_icon_cache():
    """Forget cached (and cached-missing) portraits after new icons were downloaded."""
    _icon_cache.clear()


def pet_pixmap(icons_dir, pet_id, size, dpr):
    key = (pet_id, size, dpr)
    if key not in _icon_cache:
        src = QPixmap(str(Path(icons_dir) / f"{pet_id}.png"))
        if src.isNull():
            _icon_cache[key] = None
        else:
            px = src.scaled(QSize(size, size) * dpr, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            px.setDevicePixelRatio(dpr)
            _icon_cache[key] = px
    return _icon_cache[key]


class ElidedLabel(QLabel):
    """Single-line label that shrinks with '…' instead of clipping."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        r = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, r.width())
        p.drawText(r, int(self.alignment()) | Qt.AlignVCenter, text)


def label(text, font, color, parent=None, elide=False):
    lbl = ElidedLabel(text, parent) if elide else QLabel(text, parent)
    lbl.setFont(font)
    lbl.setStyleSheet(f"color: {color.name(QColor.HexArgb)}; background: transparent;")
    lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
    return lbl


class Led(QWidget):
    """A small lamp: "red" (something needs you), "green" (fine) or "grey" (nothing to watch / no reading)."""

    def __init__(self, state, size, tip="", parent=None):
        super().__init__(parent)
        self.state = state
        self.setFixedSize(size, size)
        if tip:
            self.setToolTip(tip)
        else:
            self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        color = LED_COLORS.get(self.state, LED_COLORS["grey"])
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.state != "grey":                   # the glow around a lit lamp
            glow = QColor(color)
            glow.setAlpha(60)
            p.setBrush(glow)
            p.drawEllipse(r)
        inset = r.width() * 0.2
        bulb = r.adjusted(inset, inset, -inset, -inset)
        shine = QRadialGradient(bulb.center() - QPointF(bulb.width() * 0.18, bulb.height() * 0.18), bulb.width() * 0.75)
        shine.setColorAt(0, color.lighter(165))
        shine.setColorAt(1, color.darker(135))
        p.setBrush(shine)
        p.drawEllipse(bulb)


class ProgressBar(QWidget):
    def __init__(self, ratio, color, height, parent=None):
        super().__init__(parent)
        self.ratio, self.color = ratio, color
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        radius = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(theme.BAR_BG)
        p.drawRoundedRect(r, radius, radius)
        if self.ratio > 0:
            fill = QRectF(r.x(), r.y(), max(r.height(), r.width() * self.ratio), r.height())
            p.setBrush(self.color)
            p.drawRoundedRect(fill, radius, radius)


class PetIcon(QWidget):
    """Rounded pet portrait with the level badge in the corner, like the in-game collection."""

    def __init__(self, icons_dir, pet, size, fonts, parent=None):
        super().__init__(parent)
        self.pet, self.fonts = pet, fonts
        self.size_px = size
        self.pixmap = pet_pixmap(icons_dir, pet.pet_id, size, self.devicePixelRatioF())
        badge = round(size * 0.42)
        self.setFixedSize(size + badge // 3, size + badge // 3)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.size_px
        frame = QRectF(self.width() - s, 0, s, s)
        path = QPainterPath()
        path.addRoundedRect(frame, s * 0.22, s * 0.22)
        p.fillPath(path, QColor(255, 255, 255, 14))
        if self.pixmap:
            p.setClipPath(path)
            p.drawPixmap(frame.topLeft(), self.pixmap)
            p.setClipping(False)
        ring = theme.GOLD if self.pet.is_max else theme.BORDER
        p.setPen(ring)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        d = round(s * 0.42)
        badge = QRectF(0, self.height() - d, d, d)
        color = theme.GOLD if self.pet.is_max else (theme.ACCENT if self.pet.level else theme.MUTED)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(12, 14, 20, 235))
        p.drawEllipse(badge)
        p.setPen(color)
        p.setFont(self.fonts.badge)
        p.drawText(badge, Qt.AlignCenter, str(self.pet.level))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(badge.adjusted(0.5, 0.5, -0.5, -0.5))


class _Flashable(QWidget):
    """Widget that can briefly glow to signal fresh progress."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._glow = 0.0
        self._glow_color = theme.ACCENT

    def flash(self, color=None):
        self._glow_color = color or theme.ACCENT
        anim = QVariantAnimation(self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(1600)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._set_glow)
        anim.start(QVariantAnimation.DeleteWhenStopped)

    def _set_glow(self, value):
        self._glow = value
        self.update()

    def paintEvent(self, _):
        if self._glow <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self._glow_color)
        c.setAlphaF(0.22 * self._glow)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0, 0, -1, -1), 8, 8)


def _progress_text(pet):
    if pet.is_max:
        return "MAX"
    if pet.level == 0:
        return f"{pet.progress}/{pet.threshold} almas"
    return f"{pet.progress}/{pet.threshold}" if pet.threshold else str(pet.progress)


def side_chip(side, fonts):
    """Small 'Elyos' / 'Asmo' tag for pets that can only be farmed on one side."""
    if not side:
        return None
    color = theme.faction_color(side)
    lbl = QLabel("Elyos" if side == "elyos" else "Asmo")
    lbl.setFont(fonts.badge)
    lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
    lbl.setStyleSheet(f"color: {color.name()}; border: 1px solid {color.name()}; border-radius: 6px;"
                      f" padding: 0 4px; background: transparent;")
    return lbl


class PetRow(_Flashable):
    """One line in a list: portrait, name + mob, mini bar, count and session gain."""

    def __init__(self, pet, info, name, fonts, icons_dir, gained, side=None, parent=None):
        super().__init__(parent)
        self.pet_id = pet.pet_id
        f = fonts
        row = QHBoxLayout(self)
        row.setContentsMargins(f.px(4), f.px(3), f.px(6), f.px(3))
        row.setSpacing(f.px(8))
        row.addWidget(PetIcon(icons_dir, pet, f.px(28), f), 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(0)
        name_row = QHBoxLayout()
        name_row.setSpacing(f.px(5))
        name_row.addWidget(label(name, f.name, theme.TEXT, elide=True), 1)
        chip = side_chip(side, f)
        if chip:
            name_row.addWidget(chip)
        col.addLayout(name_row)
        if info.get("mob"):
            col.addWidget(label(info["mob"], f.small, theme.MUTED, elide=True))
        row.addLayout(col, 1)

        right = QVBoxLayout()
        right.setSpacing(f.px(3))
        count = QHBoxLayout()
        count.addStretch(1)
        if gained:
            count.addWidget(label(f"+{gained}", f.name, theme.GAIN))
        count.addWidget(label(_progress_text(pet), f.name, theme.GOLD if pet.is_max else theme.TEXT))
        right.addLayout(count)
        bar = ProgressBar(pet.ratio, theme.GOLD if pet.is_max else theme.ACCENT, f.px(4))
        bar.setFixedWidth(f.px(84))
        right.addWidget(bar, 0, Qt.AlignRight)
        row.addLayout(right)


class SectionTitle(QWidget):
    def __init__(self, text, fonts, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(fonts.px(4), fonts.px(8), 0, fonts.px(2))
        lay.addWidget(label(text.upper(), fonts.section, theme.MUTED))
