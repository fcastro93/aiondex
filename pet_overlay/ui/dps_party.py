"""The DPS meter's look: a card with the fight time, your Odyle Energy and the dungeon's rewards left (in dungeons, as
the game writes them, 75(+15)), the boss's rage countdown, its name and its HP (or "Enemigos (N)" and the damage
dealt when the fight is against several monsters); then one row per player with the class colour, their combat
power, DPS and share. Texts carry a thin dark shadow so they stay sharp over the game and the coloured bars."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

CLASS_COLOR = {"Gladiador": "#a8693c", "Templario": "#4b62b0", "Cazador": "#3d8a5c", "Asesino": "#84509f",
               "Hechicero": "#a84868", "Invocador": "#3b8299", "Clérigo": "#a8924a", "Cantor": "#8c733d"}
OTHER_COLOR = "#56637f"
TEAL = QColor("#5ed6c6")          # fight time, combat power, the game's blue "(+15)"
HP_COLOR = QColor("#52c98a")
RAGE_SOON = 30                    # seconds: the rage countdown turns red


SHADOW = QColor(0, 0, 0, 150)


def shadow_text(p, rect, flags, text, color):
    """Text with a 1 px dark shadow below it: sharp over any background."""
    p.setPen(SHADOW)
    p.drawText(QRectF(rect).translated(0, 1), flags, text)
    p.setPen(color)
    p.drawText(rect, flags, text)


def thousands(n):
    return f"{round(n):,}"


def compact_number(n):
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e4 else thousands(n)


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def short_cp(cp):
    return f"{cp / 1000:.1f}K" if cp >= 1000 else str(cp)


def counter_text(count, bonus):
    """Like the game: 75(+15)."""
    return (str(count), f"(+{bonus})" if bonus else "")


# --- small painted icons ---
def _boss_icon(p, rect, dead):
    """A red horned head (grey once the boss is dead)."""
    color = QColor("#7d8496") if dead else QColor("#e0333f")
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    horns = QPainterPath()
    for side in (-1, 1):
        cx = x + w / 2 + side * w * 0.18
        horns.moveTo(cx, y + h * 0.42)
        horns.quadTo(x + w / 2 + side * w * 0.62, y + h * 0.36, x + w / 2 + side * w * 0.46, y + h * 0.02)
        horns.quadTo(x + w / 2 + side * w * 0.40, y + h * 0.28, cx - side * w * 0.10, y + h * 0.34)
        horns.closeSubpath()
    p.drawPath(horns)
    head = QPainterPath()
    head.moveTo(x + w * 0.28, y + h * 0.36)
    head.lineTo(x + w * 0.72, y + h * 0.36)
    head.lineTo(x + w * 0.64, y + h * 0.80)
    head.lineTo(x + w * 0.50, y + h * 0.98)
    head.lineTo(x + w * 0.36, y + h * 0.80)
    head.closeSubpath()
    p.drawPath(head)
    p.setBrush(QColor(10, 14, 26))
    for side in (-1, 1):
        p.drawEllipse(QPointF(x + w / 2 + side * w * 0.09, y + h * 0.56), w * 0.05, h * 0.05)


def _bolt(p, x, y, s, color):
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([QPointF(x + s * 0.58, y), QPointF(x + s * 0.12, y + s * 0.58),
                             QPointF(x + s * 0.46, y + s * 0.58), QPointF(x + s * 0.34, y + s),
                             QPointF(x + s * 0.88, y + s * 0.36), QPointF(x + s * 0.54, y + s * 0.36)]))


def _chest(p, x, y, s, color):
    p.setPen(QPen(color, max(1.0, s / 9)))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(x + s * 0.08, y + s * 0.30, s * 0.84, s * 0.62), s * 0.10, s * 0.10)
    p.drawLine(QPointF(x + s * 0.08, y + s * 0.52), QPointF(x + s * 0.92, y + s * 0.52))
    p.drawRect(QRectF(x + s * 0.42, y + s * 0.44, s * 0.16, s * 0.18))


def _swords(p, x, y, s, color):
    p.setPen(QPen(color, max(1.2, s / 7), Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(x + s * 0.12, y + s * 0.12), QPointF(x + s * 0.88, y + s * 0.88))
    p.drawLine(QPointF(x + s * 0.88, y + s * 0.12), QPointF(x + s * 0.12, y + s * 0.88))
    p.drawLine(QPointF(x + s * 0.16, y + s * 0.62), QPointF(x + s * 0.38, y + s * 0.84))
    p.drawLine(QPointF(x + s * 0.84, y + s * 0.62), QPointF(x + s * 0.62, y + s * 0.84))


class BossCard(QWidget):
    """The fight's card. `boss` = None while waiting for the fight, else a dict with name, hp, max_hp, dead,
    rage (seconds left, negative once raged, None if it never rages) and ttk (seconds to kill at this pace)."""

    def __init__(self, fonts, elapsed, energy, rewards, boss, idle_title="Mazmorra"):
        super().__init__()
        self.fonts, self.elapsed, self.energy, self.rewards, self.boss = fonts, elapsed, energy, rewards, boss
        self.idle_title = idle_title
        self.setFixedHeight(fonts.px(50))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(self._tip())

    def _tip(self):
        lines = []
        if self.boss and self.boss.get("count"):
            lines.append("Pelea contra varios enemigos: cuántos hay, cuántos murieron y el daño total que les hicisteis.")
        if self.energy:
            lines.append("Rayo: tu Energía de Odyle (en azul, la extra). Paga el cubo de recompensa al final de "
                         "Exploración y Trascendencia.")
        if self.rewards:
            lines.append("Cofre: recompensas que te quedan de esta mazmorra.")
        elif self.energy:
            lines.append("Las recompensas que te quedan de esta mazmorra aparecen después de la primera vez que "
                         "cobres una aquí (el juego no dice cuál es hasta entonces).")
        if self.boss and self.boss.get("rage") is not None:
            lines.append("Furia: cuánto falta para que el jefe se enfurezca (cuenta desde el primer golpe).")
        return "\n".join(lines)

    def paintEvent(self, _):
        f = self.fonts
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(QColor(6, 11, 24, 215))
        p.drawRoundedRect(r, f.px(8), f.px(8))
        boss = self.boss
        pad = f.px(8)
        icon = f.px(28)
        _boss_icon(p, QRectF(r.left() + pad, r.top() + (r.height() - icon) / 2 - f.px(2), icon, icon),
                   bool(boss and boss["dead"] and not boss.get("count")))
        left = r.left() + pad + icon + f.px(8)
        right = r.right() - pad
        top = QRectF(left, r.top() + f.px(4), right - left, f.px(16))
        self._top_line(p, top, boss)
        line2 = QRectF(left, r.top() + f.px(19), right - left, f.px(18))
        if boss is None:
            p.setFont(f.body)
            p.setPen(theme.MUTED)
            p.drawText(line2, Qt.AlignVCenter | Qt.AlignLeft, "Golpea a un enemigo para empezar")
            return
        if boss.get("count"):
            self._several(p, line2, boss)
            return
        p.setFont(f.name)
        fm_name = p.fontMetrics()
        pct_text = "derrotado" if boss["dead"] else (f"{100 * boss['hp'] / boss['max_hp']:.1f}%"
                                                     if boss.get("max_hp") else "")
        pct_w = fm_name.horizontalAdvance(pct_text)
        shadow_text(p, line2, Qt.AlignVCenter | Qt.AlignRight, pct_text, theme.GAIN if boss["dead"] else theme.TEXT)
        hp_right = line2.right() - pct_w - f.px(8)
        hp_w = 0
        if boss.get("max_hp") and not boss["dead"]:
            p.setFont(f.small)
            hp_text = f"{thousands(boss['hp'])} / {thousands(boss['max_hp'])}"
            hp_w = p.fontMetrics().horizontalAdvance(hp_text)
            if fm_name.horizontalAdvance(boss["name"]) > hp_right - hp_w - f.px(8) - line2.left():
                hp_text = f"{compact_number(boss['hp'])} / {compact_number(boss['max_hp'])}"   # room for the name
                hp_w = p.fontMetrics().horizontalAdvance(hp_text)
            p.setPen(theme.MUTED)
            p.drawText(QRectF(line2.left(), line2.top(), hp_right - line2.left(), line2.height()),
                       Qt.AlignVCenter | Qt.AlignRight, hp_text)
        p.setFont(f.name)
        room = int(hp_right - hp_w - f.px(8) - line2.left())
        shadow_text(p, line2, Qt.AlignVCenter | Qt.AlignLeft, fm_name.elidedText(boss["name"], Qt.ElideRight, room),
                    theme.TEXT)
        bar = QRectF(left, r.bottom() - f.px(9), right - left, f.px(4))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 26))
        p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
        ratio = 0.0 if boss["dead"] or not boss.get("max_hp") else max(0.0, min(1.0, boss["hp"] / boss["max_hp"]))
        if ratio > 0:
            p.setBrush(HP_COLOR)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(bar.height(), bar.width() * ratio), bar.height()),
                              bar.height() / 2, bar.height() / 2)

    def _several(self, p, line, info):
        """Several monsters: "Enemigos (4)", how many died and the damage dealt to all of them."""
        f = self.fonts
        p.setFont(f.name)
        total = compact_number(info["damage"])
        fm = p.fontMetrics()
        shadow_text(p, line, Qt.AlignVCenter | Qt.AlignRight, total, theme.TEXT)
        right = line.right() - fm.horizontalAdvance(total) - f.px(8)
        p.setFont(f.small)
        dead = f"{info['dead']} derrotados · daño" if info["dead"] else "daño"
        p.setPen(theme.MUTED)
        p.drawText(QRectF(line.left(), line.top(), right - line.left(), line.height()),
                   Qt.AlignVCenter | Qt.AlignRight, dead)
        p.setFont(f.name)
        shadow_text(p, line, Qt.AlignVCenter | Qt.AlignLeft, f"Enemigos ({info['count']})", theme.TEXT)
        bar = QRectF(line.left(), self.rect().bottom() - f.px(9.5), line.width(), f.px(4))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 26))
        p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
        if info["dead"]:                 # how many of them are down
            p.setBrush(QColor("#e05a5a"))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * info["dead"] / info["count"], bar.height()),
                              bar.height() / 2, bar.height() / 2)

    def _top_line(self, p, rect, boss):
        f = self.fonts
        x = rect.left()
        mid = rect.center().y()
        p.setFont(f.badge)
        p.setPen(TEAL)
        text = f"Combate {clock(self.elapsed)}" if boss is not None else self.idle_title
        p.drawText(QRectF(x, rect.top(), rect.width(), rect.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
        x += p.fontMetrics().horizontalAdvance(text) + f.px(7)
        s = f.px(11)
        for value, painter in ((self.energy, _bolt), (self.rewards, _chest)):
            if not value:
                continue
            p.setPen(QColor(255, 255, 255, 40))
            p.drawLine(QPointF(x, mid - f.px(5)), QPointF(x, mid + f.px(5)))
            x += f.px(7)
            painter(p, x, mid - s / 2, s, QColor("#cfe7ff"))
            x += s + f.px(4)
            count, bonus = counter_text(*value)
            p.setFont(f.badge)
            p.setPen(theme.TEXT)
            p.drawText(QRectF(x, rect.top(), rect.width(), rect.height()), Qt.AlignVCenter | Qt.AlignLeft, count)
            x += p.fontMetrics().horizontalAdvance(count)
            if bonus:
                p.setPen(TEAL)
                p.drawText(QRectF(x, rect.top(), rect.width(), rect.height()), Qt.AlignVCenter | Qt.AlignLeft, bonus)
                x += p.fontMetrics().horizontalAdvance(bonus)
            x += f.px(7)
        rage = boss.get("rage") if boss and not boss["dead"] else None
        if rage is None:
            if boss and boss.get("label"):                  # what kind of fight: Farmeo, Jefe de campo...
                p.setFont(f.badge)
                p.setPen(theme.MUTED)
                p.drawText(QRectF(x, rect.top(), rect.right() - x, rect.height()), Qt.AlignVCenter | Qt.AlignRight,
                           boss["label"])
            return
        ttk = boss.get("ttk")
        if rage <= 0:
            text, color = "¡Furioso!", QColor("#ff4d4d")
        else:
            late = ttk is not None and ttk > rage           # at this pace it rages before it dies
            text = f"Furia {clock(rage)}"
            color = QColor("#ff4d4d") if rage <= RAGE_SOON or late else QColor("#ffb259")
        p.setFont(f.badge)
        p.setPen(color)
        p.drawText(rect, Qt.AlignVCenter | Qt.AlignRight, text)


class PartyRow(QWidget):
    """One player: class colour bar (as long as their damage against the top one), class icon, name, combat power,
    DPS and their share."""

    def __init__(self, fonts, name, class_name, icon, cp, dps, share, ratio, is_self=False, compact=False,
                 dim=False, tag=None):
        super().__init__()
        self.tag = tag                   # (text, QColor) after the name: an Arena rival's Defiance
        self.dim = dim                   # partial numbers (only their damage over time reached us): drawn faded
        self.fonts, self.name, self.icon, self.cp, self.dps, self.share, self.ratio = (fonts, name, icon, cp, dps,
                                                                                       share, ratio)
        self.color = QColor(CLASS_COLOR.get(class_name, OTHER_COLOR))
        self.is_self = is_self
        self.setFixedHeight(fonts.px(23 if compact else 27))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        f = self.fonts
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.dim:
            p.setOpacity(0.5)
        r = QRectF(self.rect()).adjusted(0.5, 1.5, -0.5, -1.5)
        radius = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(6, 11, 24, 190))
        p.drawRoundedRect(r, radius, radius)
        if self.ratio > 0:
            fill = QRectF(r.left(), r.top(), max(r.height(), r.width() * self.ratio), r.height())
            grad = QLinearGradient(fill.topLeft(), fill.topRight())
            start, end = QColor(self.color), QColor(self.color)
            start.setAlpha(225)
            end.setAlpha(165)
            grad.setColorAt(0, start)
            grad.setColorAt(1, end)
            p.setBrush(grad)
            p.drawRoundedRect(fill, radius, radius)
        if self.is_self:
            p.setPen(QPen(theme.GOLD, 1.3))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, radius, radius)
        # class icon in a dark disc
        side = r.height() - f.px(4)
        disc = QRectF(r.left() + f.px(2), r.top() + f.px(2), side, side)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 115))
        p.drawEllipse(disc)
        if self.icon is not None:
            inner = disc.adjusted(side * 0.14, side * 0.14, -side * 0.14, -side * 0.14)
            p.drawPixmap(inner, self.icon, QRectF(self.icon.rect()))
        # numbers on the right: share (gold) and DPS
        pad = f.px(10)
        p.setFont(f.name)
        fm = p.fontMetrics()
        share_w = fm.horizontalAdvance("100.0%")
        dps_w = fm.horizontalAdvance("888,888/s")
        right = r.right() - pad
        shadow_text(p, QRectF(right - share_w, r.top(), share_w, r.height()), Qt.AlignVCenter | Qt.AlignRight,
                    self.share, theme.GOLD)
        right -= share_w + f.px(8)
        shadow_text(p, QRectF(right - dps_w, r.top(), dps_w, r.height()), Qt.AlignVCenter | Qt.AlignRight, self.dps,
                    theme.TEXT)
        right = right - fm.horizontalAdvance(self.dps) - f.px(8)
        # name and combat power
        left = disc.right() + f.px(7)
        cp_text = short_cp(self.cp) if self.cp else ""
        small = QFontMetrics(f.badge)
        cp_w = (f.px(11) + f.px(3) + small.horizontalAdvance(cp_text) + f.px(6)) if cp_text else 0
        if self.tag:                     # the tag sits at the right end of the name's room
            tag_w = small.horizontalAdvance(self.tag[0]) + f.px(10)
            tag_rect = QRectF(right - tag_w, r.center().y() - small.height() / 2 - f.px(1), tag_w,
                              small.height() + f.px(2))
            fill = QColor(self.tag[1])
            fill.setAlpha(60)
            p.setPen(QPen(self.tag[1], 1))
            p.setBrush(fill)
            p.drawRoundedRect(tag_rect, tag_rect.height() / 2, tag_rect.height() / 2)
            p.setFont(f.badge)
            p.drawText(tag_rect, Qt.AlignCenter, self.tag[0])
            p.setFont(f.name)
            right = tag_rect.left() - f.px(6)
        name = fm.elidedText(self.name, Qt.ElideRight, int(max(10, right - left - cp_w)))
        shadow_text(p, QRectF(left, r.top(), right - left, r.height()), Qt.AlignVCenter | Qt.AlignLeft, name,
                    theme.TEXT)
        if cp_text:
            x = left + fm.horizontalAdvance(name) + f.px(6)
            s = f.px(10)
            _swords(p, x, r.center().y() - s / 2, s, TEAL)
            p.setFont(f.badge)
            p.setPen(TEAL)
            p.drawText(QRectF(x + s + f.px(3), r.top(), small.horizontalAdvance(cp_text) + 2, r.height()),
                       Qt.AlignVCenter | Qt.AlignLeft, cp_text)
