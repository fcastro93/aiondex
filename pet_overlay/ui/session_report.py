"""Session report: one image with the session (its runs, from "Comenzar sesión" to "Terminar sesión"), the totals,
every player of the party over all the runs (damage, DPS, healing, shields, deaths, revives, aggro) and the MVP of
the whole session; then one line per run with its dungeon, time, bosses, deaths, best DPS and its own MVP."""
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPen, QPixmap

from ..mvp import FORMULA, PARTS, session_scores
from ..paths import ASSETS_DIR
from ..sessions import player_totals, runs_of
from .dps_party import CLASS_COLOR, OTHER_COLOR, compact_number, thousands
from .dps_report import (BG, CARD, CARD_LINE, GOLD, GREEN, MUTED, PAD, TEAL, TEXT, ImageDialog, _font, _IconCache,
                         _slug, _text)

WIDTH = 1600
ROW = 46


def _span(seconds):
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h} h {m:02d} min" if h else f"{m}:{s:02d}"


class SessionData:
    def __init__(self, ctl, session):
        self.ctl, self.session = ctl, session
        self.runs = runs_of(ctl.session_fights(session), ctl.npc_db)
        self.fights = [f for run in self.runs for f in run.fights]          # what the runs hold
        boss_only = ctl.settings.get("dps_boss_only", True)
        self.ranked = session_scores(self.fights, boss_only)
        self.totals = player_totals(self.fights)
        self.combat = sum(enc.fight_time(group) for enc, group in self.fights)
        self.bosses = [b for run in self.runs for b in run.bosses()]
        self.cp = {}
        for enc, _ in self.fights:
            self.cp.update({n: cp for n, cp in enc.cp.items() if cp})

    def cp_of(self, name):
        return self.cp.get(name) or self.ctl.cp_of(name)


def render_session(ctl, session):
    d = SessionData(ctl, session)
    players = [s.name for s in d.ranked]
    height = 130 + 16 + 200 + 18 + 94 + 22 + 50 + max(1, len(players)) * ROW + 30 + 50 + max(1, len(d.runs)) * ROW + 140
    img = QImage(WIDTH, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(BG)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    icons = _IconCache(ctl)
    y = _header(p, d)
    y = _mvp(p, d, icons, y + 16)
    y = _tiles(p, d, y + 18)
    y = _players(p, d, icons, y + 22)
    y = _runs(p, d, y + 30)
    p.fillRect(QRectF(PAD, y + 18, WIDTH - 2 * PAD, 1), CARD_LINE)
    _text(p, QRectF(PAD, y + 30, WIDTH - 2 * PAD, 70), FORMULA + " MVP de la sesión: el aporte de cada uno sumado en "
          "todas las peleas.", _font(14), MUTED, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap)
    from .. import APP_NAME, __version__
    _text(p, QRectF(PAD, height - 34, WIDTH - 2 * PAD, 22), f"{APP_NAME} {__version__} · github.com/fcastro93/aiondex",
          _font(13), QColor(139, 149, 171, 170))
    p.end()
    return img


def _header(p, d):
    s = d.session
    grad = QLinearGradient(0, 0, WIDTH, 0)
    grad.setColorAt(0, QColor("#13213f"))
    grad.setColorAt(1, QColor("#0a1120"))
    p.fillRect(QRectF(0, 0, WIDTH, 130), grad)
    p.fillRect(QRectF(0, 128, WIDTH, 2), QColor(46, 192, 242, 90))
    badge = QPixmap(str(ASSETS_DIR / "badge.png"))
    if not badge.isNull():
        p.drawPixmap(QRectF(PAD, 30, 70, 70), badge, QRectF(badge.rect()))
    x = PAD + 90
    _text(p, QRectF(x, 22, 700, 22), "INFORME DE SESIÓN", _font(15, QFont.Bold), TEAL)
    _text(p, QRectF(x, 44, 900, 44), QFontMetrics(_font(34, QFont.Bold)).elidedText(s["name"], Qt.ElideRight, 900),
          _font(34, QFont.Bold), TEXT)
    end = s.get("end") or time.time()
    first, last = (d.runs[0].start, d.runs[-1].end) if d.runs else (s["start"], end)     # when the runs were
    when = (time.strftime("%d/%m/%Y %H:%M", time.localtime(first)) + " – " +
            time.strftime("%H:%M", time.localtime(last)))
    party = ", ".join(sorted(d.totals)) or "—"
    _text(p, QRectF(x, 88, WIDTH - x - 380, 26),
          QFontMetrics(_font(17)).elidedText(f"{when}  ·  {_span(last - first)}  ·  {len(d.runs)} runs  ·  "
                                             f"{party}", Qt.ElideRight, WIDTH - x - 400), _font(17), MUTED)
    group_dmg = sum(t.damage for t in d.totals.values())
    right = QRectF(WIDTH - PAD - 360, 30, 360, 30)
    _text(p, right, f"{thousands(group_dmg)} de daño", _font(24, QFont.DemiBold), TEXT, Qt.AlignRight | Qt.AlignVCenter)
    _text(p, QRectF(right.left(), 64, 360, 26), f"En combate: {_span(d.combat)}", _font(17), MUTED,
          Qt.AlignRight | Qt.AlignVCenter)
    return 130


def _mvp(p, d, icons, y):
    rect = QRectF(PAD, y, WIDTH - 2 * PAD, 200)
    p.setPen(QPen(GOLD, 2))
    p.setBrush(CARD)
    p.drawRoundedRect(rect, 16, 16)
    if not d.ranked:
        _text(p, rect.adjusted(30, 0, -30, 0), "Aún no hay peleas en esta sesión.", _font(20), MUTED)
        return rect.bottom()
    best = d.ranked[0]
    totals = d.totals.get(best.name)
    color = QColor(CLASS_COLOR.get(best.class_name, OTHER_COLOR))
    disc = QRectF(rect.left() + 34, rect.top() + 36, 130, 130)
    p.setPen(QPen(color.lighter(130), 5))
    p.setBrush(QColor(0, 0, 0, 120))
    p.drawEllipse(disc)
    icon = icons.get(best.class_name)
    if icon is not None:
        p.drawPixmap(disc.adjusted(24, 24, -24, -24), icon, QRectF(icon.rect()))
    tag = QRectF(disc.center().x() - 74, disc.top() - 22, 148, 34)
    p.setPen(Qt.NoPen)
    p.setBrush(GOLD)
    p.drawRoundedRect(tag, 17, 17)
    _text(p, tag, "MVP SESIÓN", _font(17, QFont.Black), QColor("#1a1206"), Qt.AlignCenter)
    x = disc.right() + 34
    _text(p, QRectF(x, rect.top() + 30, 600, 46), best.name, _font(36, QFont.Bold), GOLD)
    cp = d.cp_of(best.name)
    _text(p, QRectF(x, rect.top() + 78, 600, 28), " · ".join(v for v in (best.class_name, f"CP {cp / 1000:.1f}K" if cp
                                                                          else "") if v), _font(19), MUTED)
    _text(p, QRectF(x, rect.top() + 112, 600, 40), f"{best.points:.1f} pts", _font(32, QFont.Bold), TEXT)
    if totals:
        _text(p, QRectF(x, rect.top() + 154, 700, 28),
              f"{best.points:.0f}% del aporte de la party en {totals.fights} peleas · "
              f"{thousands(totals.damage / max(1, totals.seconds))}/s de media", _font(17), MUTED)
    bx = rect.left() + 900
    bw = rect.right() - 30 - bx
    parts = [(key, title, best.parts.get(key, 0)) for key, title in PARTS if best.parts.get(key, 0) > 0]
    top = max((v for _, _, v in parts), default=1) or 1
    step = min(40.0, (rect.height() - 40) / max(1, len(parts)))
    by = rect.top() + 20 + (rect.height() - 40 - step * len(parts)) / 2
    for key, title, value in parts:
        _text(p, QRectF(bx, by, 160, 22), title, _font(15), MUTED)
        _text(p, QRectF(bx, by, bw, 22), compact_number(value), _font(15, QFont.DemiBold), TEXT,
              Qt.AlignRight | Qt.AlignVCenter)
        track = QRectF(bx, by + 23, bw, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(track, 3, 3)
        p.setBrush(GOLD if key == "damage" else TEAL)
        p.drawRoundedRect(QRectF(track.left(), track.top(), max(6, track.width() * value / top), 6), 3, 3)
        by += step
    return rect.bottom()


def _tiles(p, d, y):
    killed = sum(1 for _, k in d.bosses if k)
    totals = d.totals.values()
    tiles = [("Runs", str(len(d.runs)), ""), ("Jefes derrotados", f"{killed} de {len(d.bosses)}", ""),
             ("En combate", _span(d.combat), f"{len(d.fights)} peleas"),
             ("Daño de la party", compact_number(sum(t.damage for t in totals)),
              f"{compact_number(sum(t.damage for t in totals) / max(1, d.combat))}/s"),
             ("Muertes", str(sum(t.deaths for t in totals)), ""),
             ("Revividos", str(sum(t.revives for t in totals)), "por healers")]
    gap = 14
    w = (WIDTH - 2 * PAD - gap * (len(tiles) - 1)) / len(tiles)
    for i, (title, value, sub) in enumerate(tiles):
        rect = QRectF(PAD + i * (w + gap), y, w, 94)
        p.setPen(QPen(CARD_LINE, 1))
        p.setBrush(CARD)
        p.drawRoundedRect(rect, 12, 12)
        _text(p, QRectF(rect.left() + 18, rect.top() + 12, w - 30, 22), title.upper(), _font(13, QFont.Bold), MUTED)
        _text(p, QRectF(rect.left() + 18, rect.top() + 36, w - 30, 34), value, _font(26, QFont.Bold), TEXT)
        if sub:
            _text(p, QRectF(rect.left() + 18, rect.top() + 66, w - 30, 22), sub, _font(15), MUTED)
    return y + 94


PLAYER_COLUMNS = [("#", 40, "l"), ("Jugador", 0, "l"), ("CP", 84, "r"), ("Daño", 100, "r"), ("DPS", 92, "r"),
                  ("A jefes", 100, "r"), ("Curación", 100, "r"), ("Escudos", 90, "r"), ("Recibido", 96, "r"),
                  ("Muertes", 80, "r"), ("Reviv.", 74, "r"), ("Aggro", 74, "r"), ("Puntos MVP", 150, "r")]


def _columns(spec):
    fixed = sum(w for _, w, _ in spec)
    widths = [w or (WIDTH - 2 * PAD - fixed - 24) for _, w, _ in spec]
    xs, x = [], PAD + 12
    for w in widths:
        xs.append(x)
        x += w
    return xs, widths


def _heading(p, spec, xs, widths, y, title):
    _text(p, QRectF(PAD, y, 400, 26), title, _font(15, QFont.Bold), TEAL)
    y += 30
    for (name, _, align), x, w in zip(spec, xs, widths):
        _text(p, QRectF(x, y, w - 8, 28), name.upper(), _font(13, QFont.Bold), MUTED,
              (Qt.AlignLeft if align == "l" else Qt.AlignRight) | Qt.AlignVCenter)
    y += 32
    p.fillRect(QRectF(PAD, y - 4, WIDTH - 2 * PAD, 1), CARD_LINE)
    return y


def _players(p, d, icons, y):
    xs, widths = _columns(PLAYER_COLUMNS)
    y = _heading(p, PLAYER_COLUMNS, xs, widths, y, "JUGADORES (TODA LA SESIÓN)")
    top = d.ranked[0].points if d.ranked else 1
    for i, s in enumerate(d.ranked, 1):
        t = d.totals.get(s.name)
        rect = QRectF(PAD, y, WIDTH - 2 * PAD, ROW - 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 10 if i % 2 else 5))
        p.drawRoundedRect(rect, 10, 10)
        p.setBrush(QColor(CLASS_COLOR.get(s.class_name, OTHER_COLOR)))
        p.drawRoundedRect(QRectF(rect.left(), rect.top() + 6, 5, rect.height() - 12), 2, 2)
        cp = d.cp_of(s.name)
        values = [str(i), None, f"{cp / 1000:.1f}K" if cp else "—",
                  compact_number(t.damage) if t and t.damage else "—",
                  thousands(t.damage / max(1, t.seconds)) if t and t.damage else "—",
                  compact_number(t.boss_damage) if t and t.boss_damage else "—",
                  compact_number(t.healing) if t and t.healing else "—",
                  compact_number(t.shielding) if t and t.shielding else "—",
                  compact_number(t.taken) if t and t.taken else "—",
                  str(t.deaths) if t and t.deaths else "—", str(t.revives) if t and t.revives else "—",
                  str(t.aggro) if t and t.aggro else "—", None]
        for (title, _, align), x, w, value in zip(PLAYER_COLUMNS, xs, widths, values):
            cell = QRectF(x, rect.top(), w - 8, rect.height())
            if title == "Jugador":
                icon = icons.get(s.class_name)
                side = rect.height() - 12
                if icon is not None:
                    p.drawPixmap(QRectF(x, rect.top() + 6, side, side), icon, QRectF(icon.rect()))
                _text(p, QRectF(x + side + 10, rect.top(), w - side - 10, rect.height()),
                      QFontMetrics(_font(18, QFont.Bold)).elidedText(s.name, Qt.ElideRight, int(w - side - 20)),
                      _font(18, QFont.Bold), GOLD if i == 1 else TEXT)
            elif title == "Puntos MVP":
                bar = QRectF(x, rect.center().y() + 9, w - 70, 5)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 22))
                p.drawRoundedRect(bar, 2, 2)
                p.setBrush(GOLD if i == 1 else TEAL)
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(4, bar.width() * s.points / (top or 1)), 5), 2, 2)
                _text(p, cell, f"{s.points:.1f}", _font(18, QFont.Bold), GOLD if i == 1 else TEXT,
                      Qt.AlignRight | Qt.AlignVCenter)
            else:
                _text(p, cell, value, _font(17), TEXT if value != "—" else MUTED,
                      (Qt.AlignLeft if align == "l" else Qt.AlignRight) | Qt.AlignVCenter)
        y += ROW
    return y


RUN_COLUMNS = [("#", 40, "l"), ("Run", 0, "l"), ("Hora", 140, "l"), ("Duración", 100, "r"), ("Jefes", 420, "l"),
               ("Muertes", 86, "r"), ("Mejor DPS", 230, "r"), ("MVP", 170, "r")]


def _runs(p, d, y):
    xs, widths = _columns(RUN_COLUMNS)
    y = _heading(p, RUN_COLUMNS, xs, widths, y, "RUNS")
    if not d.runs:
        _text(p, QRectF(PAD, y, 600, ROW), "Sin peleas todavía.", _font(17), MUTED)
        return y + ROW
    for i, run in enumerate(d.runs, 1):
        rect = QRectF(PAD, y, WIDTH - 2 * PAD, ROW - 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 10 if i % 2 else 5))
        p.drawRoundedRect(rect, 10, 10)
        totals = player_totals(run.fights)
        best = max(totals.values(), key=lambda t: t.damage / max(1, t.seconds), default=None)
        mvp = session_scores(run.fights, d.ctl.settings.get("dps_boss_only", True))
        bosses = run.bosses()
        boss_text = ", ".join(f"{name}{' ✓' if killed else ''}" for name, killed in bosses) or "sin jefes"
        values = [str(i), run.place,
                  time.strftime("%H:%M", time.localtime(run.start)) + "–" + time.strftime("%H:%M",
                                                                                           time.localtime(run.end)),
                  _span(run.end - run.start), boss_text, str(sum(t.deaths for t in totals.values())),
                  f"{best.name} {thousands(best.damage / max(1, best.seconds))}/s" if best and best.damage else "—",
                  mvp[0].name if mvp else "—"]
        for (title, _, align), x, w, value in zip(RUN_COLUMNS, xs, widths, values):
            font = _font(17, QFont.Bold) if title in ("Run", "MVP") else _font(16)
            color = GOLD if title == "MVP" else GREEN if title == "Jefes" and bosses and all(k for _, k in bosses) \
                else TEXT
            text = QFontMetrics(font).elidedText(value, Qt.ElideRight, int(w - 10))
            _text(p, QRectF(x, rect.top(), w - 8, rect.height()), text, font, color,
                  (Qt.AlignLeft if align == "l" else Qt.AlignRight) | Qt.AlignVCenter)
        y += ROW
    return y


class SessionReportDialog(ImageDialog):
    def __init__(self, ctl, session, parent=None):
        image = render_session(ctl, session)
        name = f"sesion-{_slug(session['name'])}-{time.strftime('%Y%m%d-%H%M', time.localtime(session['start']))}.png"
        super().__init__(image, name, "Informe de sesión", parent)
