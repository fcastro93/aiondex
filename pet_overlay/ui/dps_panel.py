"""Overlay panel of the DPS module, kept clean for the fight: the enemies (HP and the damage they dealt) on top, the
players' damage below with a selector (you / your party or force / everyone), and the history of recent fights."""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .notifications_panel import ago
from .widgets import label

ROWS = {True: 4, False: 10}
ENEMY_ROWS = {True: 2, False: 4}
CLASS_ICON = {"Gladiador": "gladiator", "Templario": "templar", "Cazador": "ranger", "Asesino": "assassin",
              "Hechicero": "sorcerer", "Invocador": "spiritmaster", "Clérigo": "cleric", "Cantor": "chanter"}
PLAYER_COLUMNS = ("8.88M", "88.8K", "100%")      # widest text of each column: damage, DPS, %
ENEMY_COLUMNS = ("muerto", "8.88M")              # HP, damage it dealt
VIEWS = (("me", "Yo"), ("group", None), ("all", "Todos"))   # None: "Party" or "Force", by the size of your group


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


def _column_widths(fm, samples, texts, gap):
    return [max(fm.horizontalAdvance(s), fm.horizontalAdvance(t)) + gap for s, t in zip(samples, texts)]


class DpsBar(QWidget):
    """A meter row: filled bar proportional to `ratio`, rank, class icon and name on the left, the numbers in aligned
    columns on the right."""

    def __init__(self, name, columns, ratio, color, fonts, highlight=False, rank=None, icon=None,
                 samples=PLAYER_COLUMNS, height=22, dim=False):
        super().__init__()
        self.name, self.columns, self.ratio, self.color = name, columns, ratio, color
        self.fonts, self.highlight, self.rank, self.icon = fonts, highlight, rank, icon
        self.samples, self.dim = samples, dim
        self.setFixedHeight(fonts.px(height))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 12))
        p.drawRoundedRect(r, 4, 4)
        if self.ratio > 0:
            fill = QColor(self.color)
            fill.setAlpha(150 if self.highlight else 105)
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(r.left(), r.top(), max(6.0, r.width() * self.ratio), r.height()), 4, 4)
        pad = self.fonts.px(7)
        p.setFont(self.fonts.name if self.highlight else self.fonts.body)
        fm = p.fontMetrics()
        text = theme.MUTED if self.dim else theme.TEXT
        right = r.right() - pad
        widths = _column_widths(QFontMetrics(self.fonts.body), self.samples[-len(self.columns):], self.columns,
                                self.fonts.px(10))
        p.setPen(text)
        for value, width in zip(reversed(self.columns), reversed(widths)):
            p.drawText(QRectF(right - width, r.top(), width, r.height()), Qt.AlignVCenter | Qt.AlignRight, value)
            right -= width
        left = r.left() + pad
        if self.rank is not None:
            p.setPen(QColor(255, 255, 255, 150))
            p.drawText(QRectF(left, r.top(), self.fonts.px(14), r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       str(self.rank))
            left += self.fonts.px(15)
        if self.icon is not None:
            side = r.height() - self.fonts.px(4)
            p.drawPixmap(QRectF(left, r.top() + (r.height() - side) / 2, side, side), self.icon,
                         QRectF(self.icon.rect()))
            left += side + self.fonts.px(5)
        p.setPen(text)
        p.drawText(QRectF(left, r.top(), right - left - pad, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(self.name, Qt.ElideRight, int(right - left - pad)))


class ColumnLabels(QWidget):
    """The headings of the bars' number columns, painted at the same positions as the numbers below them."""

    def __init__(self, titles, samples, fonts):
        super().__init__()
        self.titles, self.fonts = titles, fonts
        self.setFixedHeight(fonts.px(16))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.widths = _column_widths(QFontMetrics(fonts.body), samples, titles, fonts.px(10))
        self.setFixedWidth(sum(self.widths) + fonts.px(7))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setFont(self.fonts.section)
        p.setPen(theme.MUTED)
        right = self.width() - self.fonts.px(7)
        for title, width in zip(reversed(self.titles), reversed(self.widths)):
            p.drawText(QRectF(right - width, 0, width, self.height()), Qt.AlignVCenter | Qt.AlignRight, title.upper())
            right -= width


class Pill(QLabel):
    """Small clickable pill; the `selected` one is filled (the overlay shell calls on_click on a plain click)."""

    def __init__(self, text, fonts, on_click, selected=False, enabled=True, tip=""):
        super().__init__(text)
        self.setFont(fonts.small)
        self.setAlignment(Qt.AlignCenter)
        self.on_click = on_click if enabled else (lambda: None)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        self.setContentsMargins(fonts.px(8), fonts.px(1), fonts.px(8), fonts.px(1))
        if tip:
            self.setToolTip(tip)
        if selected:
            bg, fg, border = theme.ACCENT.name(), "#06101f", theme.ACCENT.name()
        else:
            bg, border = "rgba(255,255,255,0.06)", "rgba(255,255,255,0.18)"
            fg = theme.TEXT.name() if enabled else theme.MUTED.name()
        self.setStyleSheet(f"background: {bg}; color: {fg}; border: 1px solid {border}; "
                           f"border-radius: {fonts.px(8)}px;")


class DpsPanel(QWidget):
    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        self.mode = "live"               # "live" | "history"
        self._icons = {}
        self._live_encounter = None      # the fight on screen when the history was opened
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(2))

    def render(self, encounter, compact, view, ready):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self.mode == "history" and encounter is not None and not encounter.ended and \
                encounter is not self._live_encounter:
            self.mode = "live"           # a new fight started: back to the meter
        if self.mode == "history":
            self._history()
        elif not ready:
            self._note("Preparando el medidor…")
        elif encounter is None:
            self._note("Golpea a un enemigo para empezar.")
            self._footer()
        else:
            self._live(encounter, compact, view)
        self.adjustSize()

    # --- the fight ---------------------------------------------------------------------------------------------
    def _live(self, encounter, compact, view):
        f = self.fonts
        group = self.ctl.combat.group
        act = encounter.activity(group)
        boss_view = act.boss and self.ctl.settings.get("dps_boss_only", True)
        boss = encounter.main_target_eid
        value = (lambda a: a.by_target[boss]) if boss_view else (lambda a: a.damage)  # noqa: E731

        enemies = [e for e in encounter.enemies() if e[0] == boss or not e[3]][:ENEMY_ROWS[compact]]  # dead adds go
        if enemies:
            self._header(label("ENEMIGOS", f.section, theme.MUTED), ("Vida", "Daño"), ENEMY_COLUMNS)
            left = encounter.time_to_kill()
            for eid, name, hp, dead, dealt, _ in enemies:
                if eid == boss and left is not None and left < 5400 and not dead:
                    name = f"{name} · ~{mmss(left)}"
                life = "muerto" if dead else (f"{100 * hp:.0f}%" if hp is not None else "—")
                self.content.addWidget(DpsBar(name, (life, short_number(dealt) if dealt else "—"), hp or 0.0,
                                              theme.COMBAT, f, samples=ENEMY_COLUMNS, height=20, dim=dead))

        dealers = [a for a in encounter.dealers() if value(a)]
        mine = [a for a in dealers if a.is_self or a.name in group]
        outsiders = [a for a in dealers if not (a.is_self or a.name in group)]
        shown = [a for a in dealers if a.is_self] if view == "me" else sorted(mine, key=lambda a: -value(a))
        rows = [(a, a.name, value(a), a.is_self) for a in shown[:ROWS[compact]]]
        if view == "all" and outsiders:
            rows.append((None, f"Otros jugadores ({len(outsiders)})", sum(value(a) for a in outsiders), False))
        self._header(self._selector(view), ("Daño", "DPS", "%"), PLAYER_COLUMNS)
        top = max((row[2] for row in rows), default=1) or 1
        total = max(1, sum(value(a) for a in dealers))
        for rank, (actor, name, damage, is_self) in enumerate(rows, 1):
            columns = (short_number(damage), short_number(damage / encounter.duration), f"{100 * damage / total:.0f}%")
            color = theme.GOLD if is_self else (theme.MUTED if actor is None else theme.ACCENT)
            self.content.addWidget(DpsBar(name, columns, damage / top, color, f, highlight=is_self,
                                          rank=None if actor is None else rank,
                                          icon=self._class_icon(actor.class_name) if actor else None))
        if not rows:
            self._note("Aún sin daño en esta vista.")
        if act.kind == "crowd":
            c = encounter.contribution(group)
            if c:
                self._note(f"Tu aporte al jefe: {pct(c.share(c.me))}"
                           + (f" · tu {c.group_kind}: {pct(c.share(c.group))}" if c.group_kind else ""))
        self._footer()

    def _selector(self, view):
        """Yo / Party (or Force) / Todos."""
        f = self.fonts
        group = self.ctl.combat.group
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(f.px(4))
        for key, text in VIEWS:
            enabled, tip = True, ""
            if key == "group":
                text = "Force" if len(group) > 5 else "Party"
                enabled = bool(group)
                tip = "Tú y tu grupo" if group else "No estás en una party ni en una force"
            lay.addWidget(Pill(text, f, lambda k=key: self.ctl.set_setting("dps_view", k),
                               selected=key == view, enabled=enabled, tip=tip))
        return holder

    def _header(self, left, titles, samples):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(self.fonts.px(2), self.fonts.px(4), 0, self.fonts.px(1))
        lay.addWidget(left)
        lay.addStretch(1)
        lay.addWidget(ColumnLabels(titles, samples, self.fonts))
        self.content.addWidget(row)

    def _footer(self):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, self.fonts.px(4), 0, 0)
        lay.addStretch(1)
        lay.addWidget(Pill("Historial", self.fonts, self._open_history))
        self.content.addWidget(row)

    def _open_history(self):
        self.mode = "history"
        self._live_encounter = self.ctl.combat.current
        self.ctl.refresh()

    def _close_history(self):
        self.mode = "live"
        self.ctl.refresh()

    # --- the history -------------------------------------------------------------------------------------------
    def _history(self):
        """Your recent fights (this session, kept in memory) with a button to open each one's details."""
        f = self.fonts
        head = QWidget()
        lay = QHBoxLayout(head)
        lay.setContentsMargins(f.px(2), f.px(2), 0, f.px(2))
        lay.addWidget(label("HISTORIAL", f.section, theme.MUTED))
        lay.addStretch(1)
        lay.addWidget(Pill("Volver", f, self._close_history))
        self.content.addWidget(head)
        combat = self.ctl.combat
        fights = list(combat.recent)
        if combat.current is not None and combat.current not in fights and combat.current.total:
            fights.insert(0, combat.current)
        if not fights:
            self._note("Aún no hay peleas en esta sesión.")
        group = combat.group
        for enc in fights[:12]:
            me = next((a for a in enc.dealers() if a.is_self), None)
            mark = " ✓" if enc.killed else ""
            when = "en curso" if not enc.ended else ago(enc.last)
            yours = f" · {short_number(enc.dps(me))}/s ({100 * me.damage / max(1, enc.total):.0f}%)" if me else ""
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(f.px(4), 0, 0, 0)
            rl.setSpacing(f.px(6))
            rl.addWidget(label(f"{enc.main_target or enc.activity(group).label}{mark}", f.body, theme.TEXT,
                               elide=True), 1)
            rl.addWidget(label(f"{mmss(enc.duration)}{yours} · {when}", f.small, theme.MUTED))
            rl.addWidget(Pill("Ver", f, lambda e=enc: self.ctl.open_dps_details(e)))
            self.content.addWidget(row)

    # --- helpers -------------------------------------------------------------------------------------------------
    def _class_icon(self, class_name):
        slug = CLASS_ICON.get(class_name)
        if not slug:
            return None
        if slug not in self._icons:
            path = Path(self.ctl.data_dir) / "class_icons" / f"{slug}.png"
            pix = QPixmap(str(path)) if path.exists() else QPixmap()
            if pix.isNull():
                self.ctl.fetch_class_icons()
                return None
            self._icons[slug] = pix
        return self._icons[slug]

    def _note(self, text):
        note = label(text, self.fonts.small, theme.MUTED)
        note.setWordWrap(True)
        note.setContentsMargins(self.fonts.px(4), self.fonts.px(2), 0, self.fonts.px(2))
        self.content.addWidget(note)
