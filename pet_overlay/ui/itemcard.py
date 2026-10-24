"""The game's item tooltip as a card: a head in the grade's color (gold for Unique) with «+15 Enraged Kromede
Warhammer», «Unique Mace (Extend)», «Item Level 86(+43)», the piece's picture and its Extend diamonds, over a slate
body with the tooltip's rows: the stats, the Extend bonus in orange, «Soul Binding Rate» in cyan with its lines,
Soul Fuse in purple, the manastones in a 2 x 2 grid and the Theostone in green.

Built from plain data (ItemData), so it serves your own pieces read from the traffic (Mejoras › Tu equipo) and
another player's piece from the official profile (item_from_official) alike:

    card = ItemCard(fonts, ItemData(
        name="Enraged Kromede Warhammer", grade="Unique", kind="Mace", slot="Arma", base_level=86, bonus_level=43,
        enchant=15, enchant_top=15, enchant_safe=10, exceed=2, icon=pixmap,
        stats=[("Attack", "309–398(+146)")], bonus=[("Attack", "24")],
        soul_rate=1.0, soul_lines=[SoulLine("HP", "395", 1.0, "bad")],
        sockets=[Socket("MP", "Unique"), None], theostone=("Aulvicar's Sorcery", "Has a 4% chance..."),
        notes=[("→ Jefe: Guardian Lord Nahma Necklace (+41)", theme.GOLD)]))
    card = ItemCard(fonts, item_from_official(detail, icon=pixmap, slot="Arma", exceed=2))
"""
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .design import KeyValue, Meter, serif_font
from .widgets import label
from ..i18n import tr

BODY = QColor(theme.SURFACE_2).lighter(122)        # the tooltip's slate body
BONUS = theme.WARN                                  # the Extend bonus rows: the game's orange
BAD = theme.COMBAT                                  # a line to change
STAT_GRADE = {1: "Rare", 2: "Legend", 3: "Unique"}   # a manastone stat's grade byte in the traffic (its color)
SPARKLES = ((0.08, 0.22), (0.21, 0.71), (0.33, 0.18), (0.47, 0.62), (0.58, 0.3), (0.69, 0.78), (0.78, 0.15),
            (0.9, 0.55), (0.15, 0.9), (0.42, 0.88), (0.62, 0.1), (0.85, 0.85))


@dataclass
class SoulLine:
    """A soul binding line: «Attack 48», its rate in the game's terms (0..1) and the guide's verdict."""
    name: str
    value: str = ""
    pct: float = None                  # the line's own rate (value against its range), when known
    mark: str = ""                     # "good": the guide asks for it | "bad": to change | "": no verdict
    tag: str = ""                      # a small word after the name («skill»)


@dataclass
class Socket:
    """A filled manastone / soulstone socket (None in ItemData.sockets is an empty one)."""
    text: str                          # what it gives («MP+80», «Evasion Bonus») or the stone's name
    grade: str = ""                    # the stat's grade: its color (Rare green, Legend blue, Unique gold)
    tip: str = ""                      # the stone's full name


@dataclass
class ItemData:
    name: str
    grade: str = "Common"
    kind: str = ""                     # «Mace», «Amulet»... as the game writes it
    slot: str = ""                     # the slot's name (Arma, Casco...): the picture's stand-in
    base_level: int = 0                # item level of the piece
    bonus_level: int = 0               # what its +N adds: «86(+43)»
    enchant: int = 0                   # the +N (without the Extend levels)
    enchant_top: int = 0               # the +N it can reach (0: no bar)
    enchant_safe: int = 0              # the +N every attempt reaches (the bar's mark)
    exceed: int = 0                    # Extend levels past the top (the lit diamonds)
    exceed_max: int = 5
    icon: object = None                # QPixmap of the piece, or None
    stats: list = field(default_factory=list)        # [(name, value)]
    bonus: list = field(default_factory=list)        # [(name, value)] the Extend bonus (orange)
    soul_rate: float = None            # the Soul Binding Rate (0..1); None: the piece has no soul binding
    soul_locked: bool = False          # at 100%
    soul_lines: list = field(default_factory=list)   # [SoulLine]
    soul_none: str = ""                # a line to show instead when it has no soul binding yet («Sin Soul Binding»)
    fuse: str = ""                     # the Soul Fuse / Philosopher's Stone line (purple)
    sockets: list = field(default_factory=list)      # [Socket | None (empty)]
    theostone: tuple = None            # (name, description) or None
    theostone_missing: bool = False    # a weapon / guard with its Theostone socket empty
    notes: list = field(default_factory=list)        # [(text, QColor)] at the bottom (the piece that replaces it...)
    tooltip: str = ""


def _stat_text(s):
    """A mainStats row of the official API as the tooltip writes it: «309–398(+146)»."""
    value = str(s.get("value") or "")
    low = s.get("minValue")
    if low and str(low) != value:
        value = f"{low}–{value}"
    extra = s.get("extra")
    return value + (f"(+{extra})" if extra and str(extra) not in ("0", "") else "")


def item_from_official(detail, icon=None, slot="", exceed=0):
    """ItemData of a piece from the official profile's item detail (armory.Armory.item): another player's piece in
    Jugadores, or yours when the traffic has not read it."""
    d = detail or {}
    main = d.get("mainStats") or []
    subs = d.get("subStats") or []
    random_subs = bool(d.get("subStatRandom") or d.get("subStatCount"))
    data = ItemData(name=d.get("name") or "", grade=d.get("grade") or "Common", kind=d.get("categoryName") or "",
                    slot=slot, base_level=d.get("level") or 0, bonus_level=d.get("levelValue") or 0,
                    enchant=d.get("enchantLevel") or 0, enchant_top=d.get("maxEnchantLevel") or 0, exceed=exceed,
                    icon=icon)
    data.stats = [(s.get("name") or "", _stat_text(s)) for s in main if not s.get("exceed")]
    data.bonus = [(s.get("name") or "", _stat_text(s)) for s in main if s.get("exceed")]
    if subs and random_subs:
        try:
            data.soul_rate = float(d.get("soulBindRate") or 0) / 100
        except (TypeError, ValueError):
            data.soul_rate = 0.0
        data.soul_locked = data.soul_rate >= 1.0
        data.soul_lines = [SoulLine(s.get("name") or "", str(s.get("value") or "")) for s in subs]
    else:
        data.stats += [(s.get("name") or "", str(s.get("value") or "")) for s in subs]
    stones = d.get("magicStoneStat") or []
    if stones or d.get("magicStoneSlotCount"):
        by_pos = {s.get("slotPos", i): s for i, s in enumerate(stones)}
        for i in range(max(len(stones), d.get("magicStoneSlotCount") or 0)):
            s = by_pos.get(i)
            data.sockets.append(Socket(f"{s.get('name') or ''}{s.get('value') or ''}", s.get("grade") or "",
                                       s.get("name") or "") if s else None)
    gods = d.get("godStoneStat") or []
    if gods:
        name = (gods[0].get("name") or "").replace("Theostone: ", "")
        data.theostone = (name, gods[0].get("desc") or "")
    elif d.get("godStoneSlotCount"):
        data.theostone_missing = True
    return data


class _Diamonds(QWidget):
    """The Extend levels under the picture: the game's row of diamonds, the reached ones lit in cyan."""

    def __init__(self, lit, total, parent=None):
        super().__init__(parent)
        self.lit, self.total = lit, total
        self.setFixedSize(13 * max(total, 1) + 3, 14)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i in range(self.total):
            cx, cy, d = 7.5 + 13 * i, 7, 4.6
            poly = [QPointF(cx - d, cy), QPointF(cx, cy - d), QPointF(cx + d, cy), QPointF(cx, cy + d)]
            if i < self.lit:
                p.setPen(QPen(theme.ACCENT_GLOW, 1))
                p.setBrush(theme.ACCENT)
            else:
                p.setPen(QPen(theme.TEXT, 1))
                p.setBrush(QColor(0, 0, 0, 110))
            p.drawPolygon(poly)


class _Picture(QWidget):
    """The piece's picture in a frame, or the slot's name when there is no picture."""

    def __init__(self, fonts, pixmap, text, color, size, parent=None):
        super().__init__(parent)
        self.fonts, self.pixmap, self.text, self.color = fonts, pixmap, text, QColor(color)
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        glow = QRadialGradient(r.center(), r.width() * 0.62)
        c = QColor(self.color)
        c.setAlpha(70)
        glow.setColorAt(0, c)
        glow.setColorAt(1, QColor(0, 0, 0, 120))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawRoundedRect(r, 4, 4)
        if self.pixmap is not None and not self.pixmap.isNull():
            inset = r.width() * 0.06
            p.drawPixmap(r.adjusted(inset, inset, -inset, -inset).toRect(), self.pixmap)
        elif self.text:
            p.setPen(QColor(self.color.red(), self.color.green(), self.color.blue(), 210))
            pt = 8.0
            room = int(r.width() - 6)
            font = serif_font(self.fonts, pt)
            while pt > 5.0 and QFontMetrics(font, self).horizontalAdvance(self.text) > room:   # this widget's DPI
                pt -= 0.5
                font = serif_font(self.fonts, pt)
            p.setFont(font)
            text = QFontMetrics(font, self).elidedText(self.text, Qt.ElideRight, room)          # never cut at both ends
            p.drawText(r.adjusted(3, 3, -3, -3), Qt.AlignCenter, text)
        edge = QColor(self.color)
        edge.setAlpha(120)
        p.setPen(QPen(edge, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 4, 4)


class _Head(QWidget):
    """The head: name, grade and kind, item level, and the picture with its diamonds at the right, on a gradient of
    the grade's color with a few sparkles (the game's gold for Unique)."""

    def __init__(self, fonts, data, parent=None):
        super().__init__(parent)
        f = fonts
        self.color = theme.grade_color(data.grade)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(f.px(12), f.px(9), f.px(10), f.px(9))
        lay.setSpacing(f.px(8))
        col = QVBoxLayout()
        col.setSpacing(f.px(1))
        name = (f"+{data.enchant} " if data.enchant else "") + data.name
        title = label(name, serif_font(f, 12), self.color)
        title.setWordWrap(True)
        col.addWidget(title)
        kind = data.kind + (" (Extend)" if data.exceed else "")
        line = QLabel(f"<span style='color:{self.color.name()}'>{data.grade}</span>"
                      + (f" <span style='color:{theme.TEXT.name()}'>{kind}</span>" if kind else ""))
        line.setFont(f.body)
        line.setStyleSheet("background: transparent;")
        line.setAttribute(Qt.WA_TransparentForMouseEvents)
        col.addWidget(line)
        if data.base_level:
            col.addSpacing(f.px(3))
            col.addWidget(label(tr("Nivel de objeto"), f.small, theme.TEXT))
            col.addWidget(label(f"{data.base_level}" + (f"({data.bonus_level:+d})" if data.bonus_level else ""),
                                serif_font(f, 13), theme.TEXT))
        col.addStretch(1)
        lay.addLayout(col, 1)
        side = QVBoxLayout()
        side.setSpacing(f.px(4))
        side.addWidget(_Picture(f, data.icon, data.slot, self.color, f.px(54)), 0, Qt.AlignRight)
        if data.exceed:
            side.addWidget(_Diamonds(data.exceed, max(data.exceed_max, data.exceed)), 0, Qt.AlignRight)
        side.addStretch(1)
        lay.addLayout(side)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        base = self.color
        grad = QLinearGradient(r.topLeft(), r.bottomLeft())
        grad.setColorAt(0.0, base.darker(290))
        grad.setColorAt(0.6, base.darker(470))
        grad.setColorAt(1.0, BODY.darker(115))
        p.fillRect(r, grad)
        shine = QRadialGradient(r.width() * 0.3, 0, r.width() * 0.75)
        c = QColor(base)
        c.setAlpha(55)
        shine.setColorAt(0, c)
        c.setAlpha(0)
        shine.setColorAt(1, c)
        p.fillRect(r, shine)
        p.setPen(Qt.NoPen)
        for i, (x, y) in enumerate(SPARKLES):
            dot = base.lighter(130)
            dot.setAlpha(40 + (i * 37) % 80)
            p.setBrush(dot)
            d = 0.9 + (i % 3) * 0.5
            p.drawEllipse(QPointF(r.width() * x, r.height() * y), d, d)
        edge = QColor(base)
        edge.setAlpha(110)
        p.setPen(QPen(edge, 1))
        p.drawLine(QPointF(0, r.bottom() - 0.5), QPointF(r.width(), r.bottom() - 0.5))


class _Gem(QWidget):
    """A manastone's socket: a small stone in its grade's color, or a dashed ring when empty."""

    def __init__(self, color, empty=False, parent=None):
        super().__init__(parent)
        self.color, self.empty = QColor(color), empty
        self.setFixedSize(13, 13)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self.empty:
            p.setPen(QPen(theme.MUTED, 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r)
            return
        grad = QRadialGradient(r.center() - QPointF(1.5, 1.5), r.width() * 0.8)
        grad.setColorAt(0, self.color.lighter(170))
        grad.setColorAt(1, self.color.darker(150))
        p.setPen(QPen(self.color.darker(170), 1))
        p.setBrush(grad)
        p.drawEllipse(r)


class ItemCard(QFrame):
    """One piece as the game's tooltip shows it (see the module's docstring). width: a fixed width (None: the
    layout's)."""

    def __init__(self, fonts, data, width=None, parent=None):
        super().__init__(parent)
        self.fonts = fonts
        self.data = data
        self.setObjectName("ItemCard")
        self.setStyleSheet(f"QFrame#ItemCard {{ background: {BODY.name()}; border: 1px solid {theme.LINE.name()}; "
                           f"border-radius: 4px; }}")
        if width:
            self.setFixedWidth(width)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)
        root.addWidget(_Head(fonts, data))
        self.body = QVBoxLayout()
        self.body.setContentsMargins(fonts.px(12), fonts.px(7), fonts.px(12), fonts.px(10))
        self.body.setSpacing(fonts.px(3))
        root.addLayout(self.body)
        self._rows = 0
        self._build()
        if data.tooltip:
            self.setToolTip(data.tooltip)

    # --- rows ---
    def _rule(self):
        if not self._rows:
            return
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.LINE.name()}; border: none;")
        self.body.addSpacing(self.fonts.px(3))
        self.body.addWidget(line)
        self.body.addSpacing(self.fonts.px(3))

    def _add(self, widget):
        self.body.addWidget(widget)
        self._rows += 1

    def _pair(self, name, value, color=None, value_color=None, font=None):
        self._add(KeyValue(self.fonts, name, value, color or theme.TEXT, value_color or color or theme.TEXT, font))

    def _text(self, text, color, font=None):
        lbl = label(text, font or self.fonts.small, color)
        lbl.setWordWrap(True)
        self._add(lbl)

    def _build(self):
        f, d = self.fonts, self.data
        if d.enchant_top:
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(f.px(6))
            lay.addWidget(label(tr("Encantamiento"), f.small, theme.MUTED))
            top = d.enchant_top
            lay.addWidget(Meter(min(1.0, d.enchant / top), theme.ACCENT, d.enchant_safe / top if d.enchant_safe else
                                None, height=5), 1)
            lay.addWidget(label(f"+{d.enchant}/{top}" + (f" ◆{d.exceed}" if d.exceed else ""), f.small, theme.TEXT))
            if d.enchant_safe and d.enchant < top:
                row.setToolTip(tr("La marca: hasta +{0} nunca falla", d.enchant_safe))
            self._add(row)
        if d.stats or d.bonus:
            self._rule()
            for name, value in d.stats:
                self._pair(name, value)
            for name, value in d.bonus:
                self._pair(name, value, BONUS)
        if d.soul_rate is not None:
            self._rule()
            self._pair("Soul Binding Rate", "100.0%" if d.soul_locked else f"{d.soul_rate * 100:.1f}%", theme.ACCENT,
                       theme.GOLD, f.name)
            meter = Meter(1.0 if d.soul_locked else d.soul_rate, theme.GOLD, height=4)
            self._add(meter)
            self.body.addSpacing(f.px(2))
            for line in d.soul_lines:
                self._soul_row(line)
        elif d.soul_none:
            self._rule()
            self._text(d.soul_none, theme.MUTED)
        if d.fuse:
            self._text("ⓘ " + d.fuse, theme.PURPLE)
        if d.sockets:
            self._rule()
            grid = QGridLayout()
            grid.setHorizontalSpacing(f.px(10))
            grid.setVerticalSpacing(f.px(3))
            for i, sock in enumerate(d.sockets):
                cell = QWidget()
                lay = QHBoxLayout(cell)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(f.px(6))
                if sock is None:
                    lay.addWidget(_Gem(theme.MUTED, empty=True))
                    lay.addWidget(label(tr("hueco vacío"), f.small, theme.MUTED, elide=True), 1)
                else:
                    color = theme.grade_color(sock.grade)
                    lay.addWidget(_Gem(color))
                    lay.addWidget(label(sock.text, f.small, color, elide=True), 1)
                    if sock.tip and sock.tip != sock.text:
                        cell.setToolTip(sock.tip)
                grid.addWidget(cell, i // 2, i % 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            self.body.addLayout(grid)
            self._rows += 1
        if d.theostone:
            self._rule()
            name, desc = d.theostone
            self._text(f"Theostone: {name}" if name else "Theostone", theme.GAIN, f.name)
            if desc:
                self._text(desc, theme.TEXT)
        elif d.theostone_missing:
            self._rule()
            self._text(tr("Sin Theostone"), theme.MUTED)
        if d.notes:
            self._rule()
            for text, color in d.notes:
                self._text(text, color or theme.MUTED)

    def _soul_row(self, line):
        f = self.fonts
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(f.px(6))
        mark, color = {"good": ("✓", theme.GAIN), "bad": ("✗", BAD)}.get(line.mark, ("", theme.MUTED))
        tick = label(mark, f.small, color)
        tick.setFixedWidth(f.px(12))
        lay.addWidget(tick)
        text_color = BAD if line.mark == "bad" else theme.TEXT
        lay.addWidget(label(line.name, f.body, text_color, elide=True), 1)
        if line.tag:
            lay.addWidget(label(line.tag, f.small, theme.MUTED))
        if line.pct is not None:
            lay.addWidget(label(f"{line.pct:.0%}", f.small, theme.MUTED))
        if line.value:
            lay.addWidget(label(line.value, f.body, text_color))
        if line.mark:
            row.setToolTip(tr("La guía la pide en esta pieza") if line.mark == "good" else
                           tr("La guía no la pide en esta pieza: a cambiar"))
        self._add(row)
