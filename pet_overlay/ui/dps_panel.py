"""Overlay panel of the DPS module: the damage bars of the current (or last) encounter, kept clean for the fight."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .widgets import label

ROWS = {True: 4, False: 10}


def short_number(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}K"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return f"{n:.0f}"


def pct(ratio):
    """Percentage with a decimal for the small shares typical of world bosses."""
    value = 100 * ratio
    return f"{value:.1f}%" if value < 10 else f"{value:.0f}%"


def mmss(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class DpsBar(QWidget):
    """A meter row: filled bar proportional to the top damage, rank and name on the left, the numbers in aligned
    columns on the right (damage, DPS, %), like a damage meter."""
    COLUMNS = ("8.88M", "88.8K", "100%")      # widest text of each column

    def __init__(self, name, columns, ratio, color, fonts, highlight=False, rank=None, height=22):
        super().__init__()
        self.name, self.columns, self.ratio, self.color = name, columns, ratio, color
        self.fonts, self.highlight, self.rank = fonts, highlight, rank
        self.setFixedHeight(fonts.px(height))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 12))
        p.drawRoundedRect(r, 4, 4)
        fill = QColor(self.color)
        fill.setAlpha(150 if self.highlight else 105)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(r.left(), r.top(), max(6.0, r.width() * self.ratio), r.height()), 4, 4)
        pad = self.fonts.px(7)
        p.setFont(self.fonts.name if self.highlight else self.fonts.body)
        fm = p.fontMetrics()
        right = r.right() - pad
        widths = [max(fm.horizontalAdvance(sample), fm.horizontalAdvance(text)) + self.fonts.px(10)
                  for sample, text in zip(self.COLUMNS[-len(self.columns):], self.columns)]
        p.setPen(theme.TEXT)
        for text, width in zip(reversed(self.columns), reversed(widths)):
            p.drawText(QRectF(right - width, r.top(), width, r.height()), Qt.AlignVCenter | Qt.AlignRight, text)
            right -= width
        left = r.left() + pad
        if self.rank is not None:
            p.setPen(QColor(255, 255, 255, 150))
            p.drawText(QRectF(left, r.top(), self.fonts.px(14), r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       str(self.rank))
            left += self.fonts.px(16)
        p.setPen(theme.TEXT)
        p.drawText(QRectF(left, r.top(), right - left - pad, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(self.name, Qt.ElideRight, int(right - left - pad)))


class DpsPanel(QWidget):
    """Only what matters mid-fight: the target and the time, the boss's HP, the damage bars and a "Detalles" button
    (everything else, from your hits to healing and history, is in the combat details window)."""

    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(2))

    def render(self, encounter, history, compact, only_me, ready):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        f = self.fonts
        if not ready:
            self._note("Preparando el medidor…")
        elif encounter is None:
            self._note("Golpea a un enemigo para empezar.")
        else:
            group = self.ctl.combat.group
            act = encounter.activity(group)
            boss_view = act.boss and self.ctl.settings.get("dps_boss_only", True)
            boss = encounter.main_target_eid
            value = (lambda a: a.by_target[boss]) if boss_view else (lambda a: a.damage)  # noqa: E731
            self._title(encounter)
            if act.boss:
                self._boss_hp(encounter)
            # Names only for you and your party/force; everyone else is anonymous.
            dealers = [a for a in encounter.dealers() if value(a)]
            named = sorted((a for a in dealers if a.is_self or (a.name in group and not only_me)), key=lambda a: -value(a))
            outsiders = [a for a in dealers if not (a.is_self or a.name in group)]
            rows = [(a.name, value(a), a.is_self) for a in named[:ROWS[compact]]]
            if outsiders and not only_me:
                rows.append((f"Otros jugadores ({len(outsiders)})", sum(value(a) for a in outsiders), False))
            top = max((d for _, d, _ in rows), default=1) or 1
            total = max(1, sum(value(a) for a in dealers))
            for rank, (name, damage, is_self) in enumerate(rows, 1):
                others = name.startswith("Otros jugadores")
                columns = (short_number(damage), short_number(damage / encounter.duration), f"{100 * damage / total:.0f}%")
                color = theme.GOLD if is_self else (theme.MUTED if others else theme.ACCENT)
                self.content.addWidget(DpsBar(name, columns, damage / top, color, f, highlight=is_self,
                                              rank=None if others else rank))
            if act.kind == "crowd":
                self._share(encounter, group)
        if ready and encounter is not None:
            self._details_button()
        self.adjustSize()

    def _title(self, encounter):
        """Target on the left, fight time on the right (with a check once the target is dead)."""
        f = self.fonts
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(f.px(2), 0, f.px(2), f.px(1))
        lay.addWidget(label(encounter.main_target or "Pelea", f.name, theme.TEXT, elide=True), 1)
        state = " ✓" if encounter.killed or encounter.main_target_eid in encounter.dead else ""
        lay.addWidget(label(mmss(encounter.duration) + state, f.name,
                            theme.GAIN if state else (theme.MUTED if encounter.ended else theme.TEXT)))
        self.content.addWidget(row)

    def _share(self, encounter, group):
        """World bosses: you only see the hits of the players around you, so your share comes from the boss's HP."""
        c = encounter.contribution(group)
        if c:
            text = f"Tu aporte al jefe: {pct(c.share(c.me))}"
            if c.group_kind:
                text += f" · tu {c.group_kind}: {pct(c.share(c.group))}"
            self._note(text)

    def _details_button(self):
        from .settings_panel import PillButton
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, self.fonts.px(4), 0, 0)
        lay.addStretch(1)
        lay.addWidget(PillButton("Detalles", self.fonts, self.ctl.open_dps_details, small=True))
        self.content.addWidget(row)

    def _boss_hp(self, encounter):
        """The boss's HP as a thin bar, with the time left at the current pace (everyone's damage)."""
        hp = encounter.boss_hp_ratio()
        if hp is None:
            return
        left = encounter.time_to_kill()
        right = f"{100 * hp:.0f}%" + (f" · ~{mmss(left)}" if left is not None and left < 5400 else "")
        if encounter.main_target_eid in encounter.dead:
            right = "derrotado"
        self.content.addWidget(DpsBar("Vida del jefe", (right,), hp, theme.COMBAT, self.fonts, height=18))

    def _note(self, text):
        note = label(text, self.fonts.small, theme.MUTED)
        note.setWordWrap(True)
        note.setContentsMargins(self.fonts.px(4), self.fonts.px(2), 0, self.fonts.px(2))
        self.content.addWidget(note)
