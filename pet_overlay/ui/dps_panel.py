"""Overlay panel of the DPS module: damage bars for the current (or last) encounter and your history."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .notifications_panel import ago
from .widgets import SectionTitle, label

ROWS = {True: 4, False: 10}


def short_number(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}K"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return f"{n:.0f}"


def mmss(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class DpsBar(QWidget):
    """A meter row: filled bar proportional to the top damage, name on the left, numbers on the right."""

    def __init__(self, name, right, ratio, color, fonts, highlight=False):
        super().__init__()
        self.name, self.right, self.ratio, self.color = name, right, ratio, color
        self.fonts, self.highlight = fonts, highlight
        self.setFixedHeight(fonts.px(22))
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
        p.setPen(theme.TEXT)
        p.setFont(self.fonts.name if self.highlight else self.fonts.body)
        text_rect = r.adjusted(pad, 0, -pad, 0)
        right_w = p.fontMetrics().horizontalAdvance(self.right) + pad
        p.drawText(text_rect.adjusted(0, 0, -right_w, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(self.name, Qt.ElideRight, int(text_rect.width() - right_w)))
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignRight, self.right)


class DpsPanel(QWidget):
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
            self._note("Midiendo… el medidor de daño todavía se está calibrando con el tráfico del juego.")
        elif encounter is None:
            self._note("Golpea a un enemigo para empezar a medir. Tu DPS y el de tu grupo aparecerán aquí.")
        else:
            state = "terminada" if encounter.ended else "en curso"
            target = encounter.main_target or "Pelea"
            self.content.addWidget(SectionTitle(f"{target} · {mmss(encounter.duration)} · {state}", f))
            group = self.ctl.combat.group
            # Names only for you and your party/force; everyone else is one anonymous bar.
            named = [a for a in encounter.ranking() if a.is_self or (a.name in group and not only_me)]
            outsiders = [a for a in encounter.actors.values() if not (a.is_self or a.name in group)]
            rows = [(a.name, a.damage, a.is_self) for a in named[:ROWS[compact]]]
            if outsiders and not only_me:
                rows.append((f"Otros jugadores ({len(outsiders)})", sum(a.damage for a in outsiders), False))
            top = max((d for _, d, _ in rows), default=1)
            total = max(1, encounter.total)
            for name, damage, is_self in rows:
                right = f"{short_number(damage)} · {short_number(damage / encounter.duration)}/s · {100 * damage / total:.0f}%"
                color = theme.GOLD if is_self else (theme.MUTED if name.startswith("Otros jugadores") else theme.ACCENT)
                self.content.addWidget(DpsBar(name, right, damage / top, color, f, highlight=is_self))
            self._contribution(encounter, group, outsiders)
            me = next((a for a in encounter.actors.values() if a.is_self), None)
            if me and me.hits:
                pct = lambda n: f"{100 * n / me.hits:.0f}%"  # noqa: E731
                self._note(f"Tus golpes: {me.hits} · críticos {pct(me.crits)} · por la espalda {pct(me.backs)}"
                           f" · perfect {pct(me.perfects)}" + (f" · DoT {short_number(me.dot)}" if me.dot else ""))
        if ready and encounter is not None:
            toggle = label("Ver: solo yo  (clic para ver a todos)" if only_me else "Ver: todos  (clic para ver solo tu barra)",
                           f.small, theme.ACCENT)
            toggle.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            toggle.setCursor(Qt.PointingHandCursor)
            toggle.on_click = lambda: self.ctl.toggle_setting("dps_only_me")
            toggle.setContentsMargins(f.px(4), f.px(2), 0, 0)
            self.content.addWidget(toggle)
        if not compact and history:
            self.content.addWidget(SectionTitle("Tus últimas peleas", f))
            for h in history[:5]:
                mark = " ✓" if h.killed else ""
                text = f"{h.target}{mark} · {mmss(h.duration)} · {short_number(h.dps)}/s ({100 * h.share:.0f}%) · {ago(h.ended_at)}"
                row = label(text, f.small, theme.TEXT, elide=True)
                row.setContentsMargins(f.px(4), 0, 0, 0)
                self.content.addWidget(row)
        self.adjustSize()

    def _contribution(self, encounter, group, outsiders):
        """For fights shared with other players: your group's share of the damage to the boss."""
        hp = encounter.boss_hp_ratio()
        if not outsiders and hp is None:
            return
        share = encounter.contribution(group)
        if not share:
            return
        inside, best_out, n_out, _ = share
        self.content.addWidget(SectionTitle("Contribución al jefe", self.fonts))
        who = "Tu grupo" if group else "Tú"
        lines = [f"{who}: {100 * inside:.0f}% del daño al jefe"]
        if n_out:
            lines.append(f"Fuera de tu grupo: {n_out} jugadores; el más fuerte lleva {100 * best_out:.0f}%")
        if hp is not None:
            lines.append(f"Vida del jefe: {100 * hp:.0f}%")
        if n_out:
            if inside > 0.5:
                lines.append("Más de la mitad del daño: tu grupo va primero seguro.")
            elif inside < best_out:
                lines.append("Un solo jugador de fuera lleva más que tu grupo: difícil ser primeros.")
        for line in lines:
            self._note(line)

    def _note(self, text):
        note = label(text, self.fonts.small, theme.MUTED)
        note.setWordWrap(True)
        note.setContentsMargins(self.fonts.px(4), self.fonts.px(2), 0, self.fonts.px(2))
        self.content.addWidget(note)
