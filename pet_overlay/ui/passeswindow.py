"""Progreso › Pase: the Daeva Pass of a character laid out like the game's Subscribe window (its Daeva Pass tab): a
banner with the pass's title, premium or basic and when it ends; at its right this week's EXP against the weekly
limit and the ring with your level and how far into it you are; under it the track of levels (a diamond per level on
a line, cyan up to yours, the basic reward above and the premium one below: collected, waiting, not reached yet).
AionDex adds what the game does not say: the levels you still get this week, the EXP to the limit and when it starts
again, your pace, the level you would end the pass at using the whole limit every week, and the rewards waiting.

The level is the game's while the EXP table holds (the early levels, and from the anchor read off the game's window:
passes.ANCHORS); in between, the cost per level grows and the level comes from the rewards you collected. No reward
per level is known (aion2data lists only the premium's instant one), so each level's tiles show their state, not
the item."""
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QToolTip, QVBoxLayout, QWidget

from .. import APP_NAME
from ..passes import level_estimate, level_of, weekly_limit
from ..status import WEEK, countdown, next_weekly
from .charpicker import CharacterPicker
from . import theme
from .design import Card, Chip, Meter, PageHeader, ProgressRing, Stat, serif_font
from .theme import Fonts
from .widgets import label
from ..i18n import thousands, tr

COL_W = 64                   # one level of the track
TILE = 42
AHEAD = 12                   # levels drawn past the current one
LEGEND_W = 104
PREMIUM = theme.PURPLE


def _n(value):
    return thousands(f"{int(value):,}")


def _alpha(color, a):
    c = QColor(color)
    c.setAlpha(a)
    return c


class _Banner(QFrame):
    """The pass's banner: a dark gradient tinted purple (premium) or cyan (basic), like the game's art strip."""

    def __init__(self, premium, parent=None):
        super().__init__(parent)
        self.tint = PREMIUM if premium else theme.ACCENT

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        grad = QLinearGradient(r.topLeft(), r.topRight())
        grad.setColorAt(0, theme.SURFACE_2)
        grad.setColorAt(0.55, _alpha(self.tint.darker(260), 255))
        grad.setColorAt(1, _alpha(self.tint.darker(190), 255))
        p.setPen(QPen(_alpha(self.tint, 90), 1))
        p.setBrush(grad)
        p.drawRoundedRect(r, 6, 6)


class _Legend(QWidget):
    """The track's left block, like the game's: «Básica» over the line, «Premium» under it."""

    def __init__(self, fonts, height, premium, parent=None):
        super().__init__(parent)
        self.fonts, self.premium = fonts, premium
        self.setFixedSize(LEGEND_W, height)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        top, bottom = QRectF(0, 0, w, h / 2 - 1), QRectF(0, h / 2 + 1, w, h / 2 - 1)
        g = QLinearGradient(0, 0, 0, h / 2)
        g.setColorAt(0, theme.SURFACE)
        g.setColorAt(1, _alpha(theme.ACCENT.darker(300), 255))
        p.fillRect(top, g)
        g = QLinearGradient(0, h / 2, 0, h)
        g.setColorAt(0, _alpha(PREMIUM.darker(220 if self.premium else 320), 255))
        g.setColorAt(1, theme.SURFACE)
        p.fillRect(bottom, g)
        p.setFont(serif_font(self.fonts, 11))
        p.setPen(theme.TEXT)
        p.drawText(top, Qt.AlignCenter, tr("Básica"))
        p.setPen(PREMIUM.lighter(130) if self.premium else _alpha(PREMIUM, 150))
        p.drawText(bottom.adjusted(0, 0, 0, -14), Qt.AlignCenter, "Premium")
        p.setFont(self.fonts.small)
        p.setPen(theme.MUTED)
        p.drawText(bottom.adjusted(0, 22, 0, 0), Qt.AlignCenter, tr("activa") if self.premium else tr("sin suscripción"))


class _Track(QWidget):
    """The game's level track: a column per level with its diamond on a line (cyan up to your level), the basic
    reward's tile above and the premium one below, each collected (a check), waiting (gold), not reached (dim) or
    premium-only (a padlock); your level's column framed."""

    def __init__(self, fonts, levels, current, claimed_basic, claimed_premium, premium, parent=None):
        super().__init__(parent)
        self.fonts = fonts
        self.levels = levels                      # [first .. last]
        self.current = current
        self.claimed = (claimed_basic, claimed_premium)
        self.premium = premium
        self.setFixedSize(COL_W * len(levels), TILE * 2 + 84)
        self.setMouseTracking(True)

    def _x(self, i):
        return i * COL_W + COL_W / 2

    def column_x(self, level):
        """The track's x of a level (to scroll it into view)."""
        return int(self._x(self.levels.index(level))) if level in self.levels else 0

    def state(self, level, track):
        """"claimed" | "ready" | "locked" | "premium-locked" of one tile."""
        if track == 1 and not self.premium:
            return "premium-locked"
        if level <= self.claimed[track]:
            return "claimed"
        if level < self.current:                  # a level completed whose reward waits
            return "ready"
        return "locked"

    def event(self, ev):
        if ev.type() == QEvent.ToolTip:
            i = int(ev.pos().x() // COL_W)
            if 0 <= i < len(self.levels):
                lv = self.levels[i]
                words = {"claimed": tr("cobrada"), "ready": tr("por cobrar"),
                         "locked": tr("en curso: completa el nivel") if lv == self.current else tr("aún no llegas"),
                         "premium-locked": tr("solo con Premium")}
                QToolTip.showText(ev.globalPos(), tr("Nivel {0} · básica: {1} · premium: {2}", lv,
                                                     words[self.state(lv, 0)], words[self.state(lv, 1)]), self)
            return True
        return super().event(ev)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        y_line = h / 2
        y_top = y_line - 26 - TILE
        y_bottom = y_line + 26
        # the two halves: a cyan haze rising from the line above it, purple under it
        g = QLinearGradient(0, 0, 0, y_line)
        g.setColorAt(0, theme.SURFACE)
        g.setColorAt(1, _alpha(theme.ACCENT.darker(330), 255))
        p.fillRect(QRectF(0, 0, w, y_line), g)
        g = QLinearGradient(0, y_line, 0, h)
        g.setColorAt(0, _alpha(PREMIUM.darker(260 if self.premium else 360), 255))
        g.setColorAt(1, theme.SURFACE)
        p.fillRect(QRectF(0, y_line, w, h - y_line), g)
        p.setPen(QPen(theme.LINE_SOFT, 1))
        for i in range(1, len(self.levels)):
            x = i * COL_W
            p.drawLine(QPointF(x, 4), QPointF(x, h - 4))
        # your level's column
        if self.current in self.levels:
            i = self.levels.index(self.current)
            col = QRectF(i * COL_W + 1, 1, COL_W - 2, h - 2)
            p.setPen(QPen(_alpha(theme.ACCENT_GLOW, 170), 1.2))
            p.setBrush(_alpha(theme.TEXT, 14))
            p.drawRect(col)
        # the line: cyan up to the current level, dark past it
        done_x = self._x(self.levels.index(self.current)) if self.current in self.levels else \
            (w if self.current > self.levels[-1] else 0)
        p.setPen(QPen(theme.LINE, 4))
        p.drawLine(QPointF(0, y_line), QPointF(w, y_line))
        if done_x > 0:
            grad = QLinearGradient(0, 0, done_x, 0)
            grad.setColorAt(0, theme.ACCENT.darker(130))
            grad.setColorAt(1, theme.ACCENT)
            p.setPen(QPen(grad, 4))
            p.drawLine(QPointF(0, y_line), QPointF(done_x, y_line))
        small = self.fonts._make(self.fonts.family, 7.5, QFont.DemiBold)
        for i, lv in enumerate(self.levels):
            x = self._x(i)
            self._tile(p, QRectF(x - TILE / 2, y_top, TILE, TILE), self.state(lv, 0), False)
            self._tile(p, QRectF(x - TILE / 2, y_bottom, TILE, TILE), self.state(lv, 1), True)
            # the diamond with the level number
            d = 12
            reached = lv < self.current
            now = lv == self.current
            if now:
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha(theme.ACCENT, 70))
                p.drawEllipse(QPointF(x, y_line), d + 6, d + 6)
            p.setPen(QPen(theme.ACCENT_GLOW if now else theme.ACCENT if reached else theme.LINE.lighter(150), 1.5))
            p.setBrush(theme.ACCENT.darker(140) if now else theme.ACCENT.darker(220) if reached else theme.WINDOW_BG)
            p.drawPolygon([QPointF(x - d, y_line), QPointF(x, y_line - d), QPointF(x + d, y_line),
                           QPointF(x, y_line + d)])
            p.setPen(theme.TEXT if (reached or now) else theme.MUTED)
            p.setFont(small)
            p.drawText(QRectF(x - d - 4, y_line - d, 2 * d + 8, 2 * d), Qt.AlignCenter, str(lv))

    def _tile(self, p, r, state, premium_row):
        """A reward tile like RewardTile: dark slate (purple on the premium row), a check when collected, gold and
        lit when waiting, dim when not reached, a padlock when it needs Premium."""
        grad = QLinearGradient(r.topLeft(), r.bottomLeft())
        if premium_row:
            grad.setColorAt(0, PREMIUM.darker(240 if state != "premium-locked" else 330))
            grad.setColorAt(1, PREMIUM.darker(420))
        else:
            grad.setColorAt(0, theme.SURFACE_2.lighter(125))
            grad.setColorAt(1, theme.WINDOW_BG)
        p.setPen(QPen(theme.LINE, 1))
        p.setBrush(grad)
        p.drawRoundedRect(r, 3, 3)
        if state == "ready":
            p.setPen(Qt.NoPen)
            p.setBrush(_alpha(theme.GOLD, 55))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QPen(theme.GOLD, 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            p.setPen(QPen(theme.GOLD, 2.4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.26),
                       QPointF(r.center().x(), r.top() + r.height() * 0.58))
            p.drawPoint(QPointF(r.center().x(), r.top() + r.height() * 0.75))
        elif state == "claimed":
            p.setPen(Qt.NoPen)
            p.setBrush(_alpha(theme.WINDOW_BG, 120))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QPen(theme.TEXT, 2.2, Qt.SolidLine, Qt.RoundCap))
            s = r.width()
            p.drawPolyline([QPointF(r.left() + s * 0.3, r.top() + s * 0.52),
                            QPointF(r.left() + s * 0.45, r.top() + s * 0.68),
                            QPointF(r.left() + s * 0.72, r.top() + s * 0.34)])
        elif state == "premium-locked":
            color = _alpha(PREMIUM, 150)
            cx, cy = r.center().x(), r.center().y() + 3
            p.setPen(QPen(color, 1.8))
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(cx - 5, cy - 11, 10, 10), 0, 180 * 16)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(cx - 7, cy - 4, 14, 10), 2, 2)
        else:                                       # not reached: a faint diamond
            c = r.center()
            d = r.width() * 0.16
            p.setPen(QPen(theme.LINE.lighter(140), 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon([QPointF(c.x() - d, c.y()), QPointF(c.x(), c.y() - d), QPointF(c.x() + d, c.y()),
                           QPointF(c.x(), c.y() + d)])


class PassesWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        f = self.fonts
        self.setWindowTitle(tr("{0} — Pase", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.resize(1180, 860)
        root = QVBoxLayout(self)
        root.setContentsMargins(f.px(22), f.px(16), f.px(22), f.px(16))
        root.setSpacing(f.px(10))
        self.header = PageHeader(f, tr("Pase"), tr("El Daeva Pass de tu personaje, leído del juego."))
        self.header.add_picker(CharacterPicker(self.ctl, f))
        root.addWidget(self.header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(0, 0, f.px(6), 0)
        self.body.setSpacing(f.px(12))
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        self._tracks = []
        self._timer = QTimer(self, interval=60_000, timeout=self.refresh)

    def showEvent(self, event):
        self._timer.start()
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # --- who ---
    def _who(self, now):
        """The app's character if it has a pass, else the one you play, else the last one read."""
        ps, ctl = self.ctl.passes, self.ctl
        playing = ctl.character_name()
        names = ps.names(now, playing)
        chosen = ctl.focus_character()
        if chosen in names:
            return chosen, names
        return (playing if playing in names else (names[0] if names else None)), names

    def refresh(self):
        from .missionswindow import _clear
        _clear(self.body)
        self._tracks = []
        f, ps, now = self.fonts, self.ctl.passes, time.time()
        name, names = self._who(now)
        if not ps.available:
            self._hint(tr("Falta la lista de pases (data/passes_db.json)."), theme.WARN)
            return
        if name is None:
            self._hint(tr("Entra con tu personaje con AionDex abierto: el juego manda su Daeva Pass al entrar y "
                          "cada vez que gana EXP."))
            return
        rows = ps.of(name, now)
        self.header.set_subtitle(tr("{0} · leído del juego {1}", name, _ago(ps.characters.get(name, {}).get("at"), now)))
        if not rows:
            self._hint(tr("{0} no tiene ningún pase en marcha.", name))
        for pid, info, state in rows:
            self.body.addWidget(self._pass(pid, info, state, now))
        others = [n for n in names if n != name]
        if others:
            self.body.addWidget(self._others(others, now))
        credit = ps.credit
        if credit:
            link = label(credit.get("text", ""), f.small, theme.MUTED)
            link.setContentsMargins(f.px(4), 0, 0, 0)
            self.body.addWidget(link)
        self.body.addStretch(1)
        QTimer.singleShot(0, self._scroll_tracks)

    def _hint(self, text, color=None):
        lbl = label(text, self.fonts.body, color or theme.MUTED)
        lbl.setWordWrap(True)
        self.body.addWidget(lbl)
        self.body.addStretch(1)

    # --- one pass ---
    def _pass(self, pid, info, s, now):
        f, ps = self.fonts, self.ctl.passes
        card = Card(f)
        card.body.setSpacing(f.px(10))
        level, exact, into, need = level_estimate(pid, s)
        cap = weekly_limit(pid)
        start, end = info.get("start"), info.get("end")
        basic, premium = _claimed(s)
        waiting = ps.waiting(s)
        card.body.addWidget(self._banner(pid, info, s, level, exact, into, need, cap, start, end, now))
        # the track
        if level:
            first = max(1, min(basic, premium if s.premium else basic, level - 6) - 2)
            levels = list(range(first, level + AHEAD + 1))
            track = _Track(f, levels, level, basic, premium, s.premium)
            row = QHBoxLayout()
            row.setSpacing(0)
            row.addWidget(_Legend(f, track.height(), s.premium))
            box = QScrollArea()
            box.setWidgetResizable(False)
            box.setFrameShape(QFrame.NoFrame)
            box.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            box.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            box.setFixedHeight(track.height() + f.px(12))
            box.setWidget(track)
            row.addWidget(box, 1)
            card.body.addLayout(row)
            self._tracks.append((box, track, level))
            legend = QHBoxLayout()
            legend.setSpacing(f.px(14))
            if first > 1:
                legend.addWidget(label(tr("Niveles 1-{0}: todos cobrados", first - 1), f.small, theme.MUTED))
            legend.addStretch(1)
            legend.addWidget(label(tr("✓ cobrada"), f.small, theme.TEXT))
            legend.addWidget(label(tr("! por cobrar"), f.small, theme.GOLD))
            legend.addWidget(label(tr("◇ aún no llegas"), f.small, theme.MUTED))
            track.setToolTip(tr("El juego no publica qué da cada nivel: AionDex muestra el estado de cada recompensa."))
            card.body.addLayout(legend)
        else:
            card.body.addWidget(label(tr("Sin tabla de EXP para este pase: solo su EXP total ({0}).", _n(s.exp)),
                                      f.small, theme.MUTED))
        # what the game does not say
        card.body.addWidget(label(tr("LO QUE EL JUEGO NO TE DICE"), f.section, theme.GOLD))
        tiles = QGridLayout()
        tiles.setSpacing(f.px(8))
        hints = self._hints(pid, s, level, exact, into, need, cap, end, now, waiting)
        per_row = 3 if len(hints) > 4 else len(hints)
        for i, (value, name, color, tip) in enumerate(hints):
            tiles.addWidget(Stat(f, value, name, color, tip=tip), i // per_row, i % per_row)
        for c in range(per_row):
            tiles.setColumnStretch(c, 1)
        card.body.addLayout(tiles)
        if not exact and level:
            note = label(tr("Nivel estimado por tus recompensas cobradas: en estos niveles el coste por nivel sube "
                            "y AionDex no tiene la tabla entera."), f.small, theme.MUTED)
            note.setWordWrap(True)
            card.body.addWidget(note)
        return card

    def _banner(self, pid, info, s, level, exact, into, need, cap, start, end, now):
        f = self.fonts
        banner = _Banner(s.premium)
        lay = QHBoxLayout(banner)
        lay.setContentsMargins(f.px(18), f.px(14), f.px(18), f.px(14))
        lay.setSpacing(f.px(18))
        left = QVBoxLayout()
        left.setSpacing(f.px(5))
        left.addWidget(label("DAEVA PASS", f.section, theme.GOLD))
        left.addWidget(label(info.get("title") or tr("Pase {0}", pid), serif_font(f, 20), theme.TEXT))
        chips = QHBoxLayout()
        chips.setSpacing(f.px(6))
        chips.addWidget(Chip("Premium", PREMIUM, f, tr("Con la suscripción Premium: las dos filas")) if s.premium else
                        Chip(tr("Básico"), theme.MUTED, f, tr("Sin la suscripción Premium: solo la fila de arriba")))
        race = info.get("race") or ""
        if race:
            elyos = race.startswith("Ely")
            chips.addWidget(Chip("Elyos" if elyos else "Asmodian", theme.ELYOS if elyos else theme.ASMODIAN, f))
        if start and end:
            chips.addWidget(Chip(tr("{0} → {1}", time.strftime("%d/%m", time.localtime(start)),
                                    time.strftime("%d/%m", time.localtime(end))), theme.MUTED, f))
        chips.addStretch(1)
        left.addLayout(chips)
        left.addStretch(1)
        if end:
            soon = end - now < 3 * 86400
            left.addWidget(label(tr("Termina en {0}", countdown(end - now)), f.name, theme.WARN if soon else theme.TEXT))
            if start and end > start:
                left.addWidget(Meter((now - start) / (end - start), theme.WARN if soon else theme.MUTED, height=f.px(4)))
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        right.setSpacing(f.px(4))
        if cap:
            right.addWidget(label(tr("EXP semanal"), f.small, theme.MUTED), 0, Qt.AlignRight)
            week = QLabel()
            week.setTextFormat(Qt.RichText)
            week.setFont(f.name)
            full = s.weekly >= cap
            week.setText(f"<span style='color:{(theme.GAIN if full else theme.ACCENT).name()}'>{_n(s.weekly)}</span>"
                         f"<span style='color:{theme.TEXT.name()}'> / {_n(cap)}</span>")
            week.setStyleSheet("background: transparent;")
            right.addWidget(week, 0, Qt.AlignRight)
            meter = Meter(min(1.0, s.weekly / cap), theme.GAIN if full else theme.ACCENT, height=f.px(5), glow=not full)
            meter.setFixedWidth(f.px(170))
            right.addWidget(meter, 0, Qt.AlignRight)
        right.addStretch(1)
        lay.addLayout(right)
        level_text = f"Lv. {level}" if level else "—"
        if exact and need:
            ring = ProgressRing(f, into / need, f"{into * 100 // need}%", f"{_n(into)}/{_n(need)}", size=f.px(120),
                                title=level_text)
            ring.setToolTip(tr("Nivel {0}: {1} de {2} EXP", level, _n(into), _n(need)))
        elif cap:
            ratio = min(1.0, s.weekly / cap)
            ring = ProgressRing(f, ratio, f"{int(ratio * 100)}%", tr("semana"), size=f.px(120), title=level_text + " ≈")
            ring.setToolTip(tr("El juego no manda el nivel: AionDex lo saca de la EXP mientras su tabla coincide, y "
                               "si no, de las recompensas que ya cobraste. El anillo muestra la semana."))
        else:
            ring = ProgressRing(f, 0.0, _n(s.exp), "EXP", size=f.px(120), title=level_text)
        lay.addWidget(ring, 0, Qt.AlignVCenter)
        return banner

    def _hints(self, pid, s, level, exact, into, need, cap, end, now, waiting):
        """[(value, name, color, tip)] of the tiles under the track."""
        out = []
        reset = next_weekly(now)
        since = max(0.5, (now - (reset - WEEK)) / 86400)          # days of this week gone (at least half a day)
        if cap:
            left = max(0, cap - s.weekly)
            reset_tip = tr("La EXP semanal vuelve a cero el miércoles a las 07:00 UTC ({0}, hora de tu PC)",
                           time.strftime("%d/%m %H:%M", time.localtime(reset)))
            rate = s.weekly / since
            if not left:
                out.append((tr("tope ✓"), tr("reinicio en {0}", countdown(reset - now)), theme.GAIN,
                            tr("Tope semanal alcanzado: lo que ganes ahora no cuenta hasta el miércoles.") + " "
                            + reset_tip))
            else:
                gained = _levels_between(pid, s.exp, s.exp + left)
                if gained is not None:
                    out.append((("" if exact else "≈ ") + f"{gained:.1f}", tr("niveles más esta semana"), theme.ACCENT,
                                tr("Lo que te da la EXP que te queda hasta el tope semanal")))
                out.append((_n(left), tr("EXP al tope · reinicio en {0}", countdown(reset - now)), theme.TEXT,
                            reset_tip))
            if left and rate > 0:
                days = left / rate
                if now + days * 86400 <= reset:
                    out.append((countdown(days * 86400), tr("al tope a tu ritmo ({0}/día)", _n(rate)), theme.GAIN,
                                tr("A la EXP por día que llevas esta semana")))
                else:
                    missing = max(0, int(left - rate * (reset - now) / 86400))
                    out.append((tr("-{0}", _n(missing)), tr("no llegas al tope a tu ritmo"), theme.WARN,
                                tr("A tu ritmo de esta semana ({0} EXP al día) te faltarían {1} el miércoles",
                                   _n(rate), _n(missing))))
            elif left:
                out.append(("0", tr("EXP esta semana"), theme.MUTED, tr("Sin EXP todavía esta semana")))
            if end and end > now and level:
                weeks, r = 0, reset                  # whole weeks after this one before the pass ends
                while r < end - 3600:
                    weeks += 1
                    r += WEEK
                total_more = left + weeks * cap
                more = _levels_between(pid, s.exp, s.exp + total_more)
                if more is not None:
                    final = int(level + (into / need if exact and need else 0) + more)
                    out.append((f"≈ Lv. {final}", tr("al terminar ({0}) con todo el tope",
                                                       time.strftime("%d/%m", time.localtime(end))), theme.TEXT,
                                tr("{0} EXP más: lo que te queda esta semana y {1} semanas enteras. Si el coste por "
                                   "nivel no sube más; el juego no dice el nivel máximo.", _n(total_more), weeks)))
        out.append((str(waiting), tr("recompensas por cobrar"), theme.GOLD if waiting else theme.MUTED,
                    tr("Se cobran en el juego: Subscribe → Daeva Pass") if waiting else
                    tr("Ninguna: estás al día (o AionDex no puede saberlo en estos niveles)")))
        return out

    def _others(self, names, now):
        f, ps = self.fonts, self.ctl.passes
        card = Card(f, tr("Tus otros personajes"))
        row = QHBoxLayout()
        row.setSpacing(f.px(10))
        for n in names[:6]:
            tile = QFrame()
            tile.setObjectName("Stat")
            tile.setCursor(Qt.PointingHandCursor)
            tile.setToolTip(tr("Clic: ver el pase de {0}", n))
            col = QVBoxLayout(tile)
            col.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
            col.setSpacing(f.px(3))
            col.addWidget(label(n, f.name, theme.TEXT))
            for pid, info, s in ps.of(n, now):
                level, exact, _, _ = level_estimate(pid, s)
                cap = weekly_limit(pid)
                line = QHBoxLayout()
                line.setSpacing(f.px(6))
                line.addWidget(label(_short(info.get("title")), f.small, theme.MUTED))
                line.addWidget(label(f"Lv {level}{'' if exact else '≈'}" if level else "—", f.small, theme.GOLD))
                if cap:
                    meter = Meter(min(1.0, s.weekly / cap), theme.GAIN if s.weekly >= cap else theme.ACCENT,
                                  height=f.px(4))
                    meter.setFixedWidth(f.px(60))
                    meter.setToolTip(tr("Semana {0} / {1}", _n(s.weekly), _n(cap)))
                    line.addWidget(meter, 0, Qt.AlignVCenter)
                line.addStretch(1)
                col.addLayout(line)
            tile.mousePressEvent = lambda _e, who=n: self.ctl.set_character_choice(who)
            row.addWidget(tile)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _scroll_tracks(self):
        """Each track scrolled so the current level sits a third in from the left."""
        for box, track, level in self._tracks:
            x = track.column_x(level) - box.viewport().width() // 3
            box.horizontalScrollBar().setValue(max(0, x))


def _levels_between(pid, exp_from, exp_to):
    """Levels (with the fraction) that EXP from exp_from to exp_to gives by the pass's table, or None."""
    a, b = level_of(pid, exp_from), level_of(pid, exp_to)
    if a is None or b is None:
        return None
    return (b[0] + b[1] / b[2]) - (a[0] + a[1] / a[2])


def _claimed(state):
    """(last basic level collected, last premium level collected)."""
    tracks = state.tracks or []
    basic = state.claimed.get(str(tracks[0]), 0) if tracks else max(state.claimed.values(), default=0)
    premium = state.claimed.get(str(tracks[1]), 0) if len(tracks) > 1 else 0
    return basic, premium


def _short(title):
    return {"Ishalgen Mercenary": "Ishalgen", "Planet's Call": "Planet's", "Dawn Legion": "Dawn"}.get(title, title or "?")


def _ago(ts, now):
    if not ts:
        return tr("nunca")
    minutes = (now - ts) / 60
    if minutes < 2:
        return tr("hace un momento")
    if minutes < 90:
        return tr("hace {0:.0f} min", minutes)
    if minutes < 48 * 60:
        return tr("hace {0:.0f} h", minutes / 60)
    return tr("hace {0:.0f} días", minutes / 1440)
