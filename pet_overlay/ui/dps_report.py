"""Combat report: one image with the fight, the group's totals, every player of your group and the fight's MVP
(mvp.py), to save as a PNG or paste anywhere. Players outside your group are only a total, never named."""
import re
import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout)

from .. import APP_NAME, __version__
from ..combat import aggro_counts
from ..mvp import FORMULA, PARTS, scores
from ..paths import ASSETS_DIR
from .dps_panel import CLASS_ICON, mmss
from .dps_party import CLASS_COLOR, OTHER_COLOR, compact_number, thousands
from ..i18n import tr

WIDTH = 1600
PAD = 40
BG = QColor("#0a1120")
CARD = QColor("#111b31")
CARD_LINE = QColor(255, 255, 255, 26)
TEXT = QColor("#eef2f8")
MUTED = QColor("#8b95ab")
TEAL = QColor("#5ed6c6")
GOLD = QColor("#f0c24e")
GREEN = QColor("#7bd88f")
ROW_H = 48


def _font(px, weight=QFont.Normal):
    f = QFont("Segoe UI")
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def _pct(value):
    return f"{value:.2f}%" if 0 < value < 1 else f"{value:.1f}%"


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "pelea"


class Report:
    """Everything the image shows, worked out once."""

    def __init__(self, ctl, enc):
        self.ctl, self.enc = ctl, enc
        self.group = enc.group_names(ctl.combat.group)
        boss_only = ctl.settings.get("dps_boss_only", True)
        self.act = enc.activity(self.group)
        self.boss = enc.main_target_eid if boss_only and self.act.boss else None
        self.ranked = scores(enc, self.group, boss_only)
        self.value = (lambda a: a.by_target[self.boss]) if self.boss is not None else (lambda a: a.damage)
        dealers = [a for a in enc.actors.values() if self.value(a)]
        self.outsiders = [a for a in dealers if not (a.is_self or a.name in self.group)]
        visible = sum(self.value(a) for a in dealers) or 1
        c = enc.contribution(self.group) if self.boss is not None else None
        self.basis = c.total if c else visible
        hp = enc.hp.get(enc.main_target_eid)
        self.max_hp = (hp[1] if hp else None) or enc.target_max_hp.get(enc.main_target_eid)
        self.seconds = enc.target_time(self.boss) if self.boss is not None else enc.duration
        self.aggro = aggro_counts(enc)

    def cp(self, name):
        return self.enc.cp.get(name) or self.ctl.cp_of(name)


def render(ctl, enc):
    r = Report(ctl, enc)
    rows = len(r.ranked) + (1 if r.outsiders else 0)
    footer_h = 120
    height = 130 + 220 + 112 + 50 + rows * ROW_H + footer_h + 20
    img = QImage(WIDTH, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(BG)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    icons = _IconCache(ctl)
    y = _header(p, r)
    y = _mvp(p, r, icons, y + 16)
    y = _tiles(p, r, y + 18)
    y = _table(p, r, icons, y + 22)
    _footer(p, y + 18, height)
    p.end()
    return img


class _IconCache:
    def __init__(self, ctl):
        self.dir = Path(ctl.data_dir) / "class_icons"
        self.cache = {}

    def get(self, class_name):
        slug = CLASS_ICON.get(class_name)
        if not slug:
            return None
        if slug not in self.cache:
            pix = QPixmap(str(self.dir / f"{slug}.png"))
            self.cache[slug] = None if pix.isNull() else pix
        return self.cache[slug]


def _text(p, rect, text, font, color, align=Qt.AlignLeft | Qt.AlignVCenter):
    p.setFont(font)
    p.setPen(color)
    p.drawText(rect, int(align), text)


def _header(p, r):
    enc = r.enc
    grad = QLinearGradient(0, 0, WIDTH, 0)
    grad.setColorAt(0, QColor("#13213f"))
    grad.setColorAt(1, QColor("#0a1120"))
    p.fillRect(QRectF(0, 0, WIDTH, 130), grad)
    p.fillRect(QRectF(0, 128, WIDTH, 2), QColor(46, 192, 242, 90))
    badge = QPixmap(str(ASSETS_DIR / "badge.png"))
    if not badge.isNull():
        p.drawPixmap(QRectF(PAD, 30, 70, 70), badge, QRectF(badge.rect()))
    x = PAD + 90
    _text(p, QRectF(x, 22, 700, 22), tr("INFORME DE COMBATE"), _font(15, QFont.Bold), TEAL)
    title = enc.main_target or tr("Pelea")
    _text(p, QRectF(x, 44, 760, 44), QFontMetrics(_font(34, QFont.Bold)).elidedText(title, Qt.ElideRight, 760),
          _font(34, QFont.Bold), TEXT)
    state = "✓ Derrotado" if enc.killed else (tr("En curso") if not enc.ended else tr("Sin terminar"))
    when = time.strftime("%d/%m/%Y %H:%M", time.localtime(enc.last))
    _text(p, QRectF(x, 88, 800, 26), f"{state}  ·  {mmss(r.seconds)}  ·  {r.act.label}  ·  {when}",
          _font(17), GREEN if enc.killed else MUTED)
    right = QRectF(WIDTH - PAD - 360, 30, 360, 30)
    group_dmg = sum(s.parts["damage"] for s in r.ranked)
    if r.max_hp:
        _text(p, right, f"{thousands(r.max_hp)} HP", _font(24, QFont.DemiBold), TEXT, Qt.AlignRight | Qt.AlignVCenter)
    _text(p, QRectF(right.left(), 64, 360, 26), tr("DPS del grupo: {0}/s", thousands(group_dmg / r.seconds)),
          _font(17), MUTED, Qt.AlignRight | Qt.AlignVCenter)
    return 130


def _name(r, actor):
    return actor.name + (tr(" (tú)") if actor.is_self else "")


def _mvp(p, r, icons, y):
    rect = QRectF(PAD, y, WIDTH - 2 * PAD, 220)
    p.setPen(QPen(GOLD, 2))
    p.setBrush(CARD)
    p.drawRoundedRect(rect, 16, 16)
    if not r.ranked:
        _text(p, rect.adjusted(30, 0, -30, 0), tr("Nadie de tu grupo participó en esta pelea."), _font(20), MUTED)
        return rect.bottom()
    best = r.ranked[0]
    a = best.actor
    color = QColor(CLASS_COLOR.get(a.class_name, OTHER_COLOR))
    disc = QRectF(rect.left() + 34, rect.top() + 40, 140, 140)
    p.setPen(QPen(color.lighter(130), 5))
    p.setBrush(QColor(0, 0, 0, 120))
    p.drawEllipse(disc)
    icon = icons.get(a.class_name)
    if icon is not None:
        p.drawPixmap(disc.adjusted(26, 26, -26, -26), icon, QRectF(icon.rect()))
    # the MVP tag over the disc
    tag = QRectF(disc.center().x() - 42, disc.top() - 22, 84, 34)
    p.setPen(Qt.NoPen)
    p.setBrush(GOLD)
    p.drawRoundedRect(tag, 17, 17)
    _text(p, tag, "MVP", _font(19, QFont.Black), QColor("#1a1206"), Qt.AlignCenter)
    x = disc.right() + 34
    _text(p, QRectF(x, rect.top() + 34, 560, 46), QFontMetrics(_font(36, QFont.Bold)).elidedText(
        _name(r, a), Qt.ElideRight, 560), _font(36, QFont.Bold), GOLD)
    cp = r.cp(a.name)
    sub = " · ".join(x for x in (a.class_name, f"CP {cp / 1000:.1f}K" if cp else "") if x)
    _text(p, QRectF(x, rect.top() + 82, 470, 28), sub, _font(19), MUTED)
    _text(p, QRectF(x, rect.top() + 118, 470, 40), f"{best.points:.1f} pts", _font(32, QFont.Bold), TEXT)
    dmg = best.parts["damage"]
    line = tr("{0} del daño · {1}/s", _pct(100 * dmg / r.basis), thousands(dmg / r.seconds)) if dmg else ""
    _text(p, QRectF(x, rect.top() + 160, 600, 28), tr("{0:.0f}% del aporte del grupo", best.points)
          + (f" · {line}" if line else ""), _font(17), MUTED)
    # what made it: one bar per kind of contribution
    bx = rect.left() + 820
    bw = rect.right() - 30 - bx
    parts = [(key, title, best.parts.get(key, 0)) for key, title in PARTS if best.parts.get(key, 0) > 0]
    top = max((v for _, _, v in parts), default=1) or 1
    step = min(44.0, (rect.height() - 44) / max(1, len(parts)))
    by = rect.top() + 22 + (rect.height() - 44 - step * len(parts)) / 2
    for key, title, value in parts:
        _text(p, QRectF(bx, by, 140, 24), title, _font(16), MUTED)
        _text(p, QRectF(bx, by, bw, 24), compact_number(value), _font(16, QFont.DemiBold), TEXT,
              Qt.AlignRight | Qt.AlignVCenter)
        track = QRectF(bx, by + 25, bw, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(track, 3, 3)
        p.setBrush(GOLD if key == "damage" else TEAL)
        p.drawRoundedRect(QRectF(track.left(), track.top(), max(6, track.width() * value / top), 6), 3, 3)
        by += step
    return rect.bottom()


def _tiles(p, r, y):
    enc = r.enc
    members = [s.actor for s in r.ranked]
    totals = [(tr("Daño del grupo"), sum(s.parts["damage"] for s in r.ranked), True),
              (tr("Curación"), sum(a.healing for a in members), False),
              (tr("Escudos"), sum(a.shielding for a in members), False),
              (tr("Daño recibido"), sum(a.taken for a in members), False),
              (tr("Golpes evitados"), sum(a.avoided_hits for a in members), False),
              (tr("Aturdimiento"), sum(a.stagger for a in members), False)]
    totals = [t for t in totals if t[1]]
    if not totals:
        return y
    gap = 14
    w = (WIDTH - 2 * PAD - gap * (len(totals) - 1)) / len(totals)
    for i, (title, value, rate) in enumerate(totals):
        rect = QRectF(PAD + i * (w + gap), y, w, 94)
        p.setPen(QPen(CARD_LINE, 1))
        p.setBrush(CARD)
        p.drawRoundedRect(rect, 12, 12)
        _text(p, QRectF(rect.left() + 18, rect.top() + 12, w - 30, 22), title.upper(), _font(13, QFont.Bold), MUTED)
        shown = str(int(value)) if title == tr("Golpes evitados") else compact_number(value)
        _text(p, QRectF(rect.left() + 18, rect.top() + 36, w - 30, 34), shown, _font(26, QFont.Bold), TEXT)
        if rate:
            _text(p, QRectF(rect.left() + 18, rect.top() + 66, w - 30, 22),
                  f"{compact_number(value / r.seconds)}/s", _font(15), MUTED)
    return y + 94


COLUMNS = [("#", 40, "l"), (tr("Jugador"), 0, "l"), ("CP", 84, "r"), (tr("Daño"), 96, "r"), ("DPS", 92, "r"),
           ("%", 76, "r"), (tr("Crít."), 64, "r"), (tr("Curación"), 98, "r"), (tr("Escudos"), 90, "r"),
           (tr("Recibido"), 96, "r"), (tr("Evitados"), 86, "r"), (tr("Muertes"), 78, "r"), (tr("Reviv."), 70, "r"),
           ("Aggro", 70, "r"), (tr("Puntos MVP"), 150, "r")]


def _table(p, r, icons, y):
    enc = r.enc
    fixed = sum(w for _, w, _ in COLUMNS)
    widths = [w or (WIDTH - 2 * PAD - fixed - 24) for _, w, _ in COLUMNS]
    xs, x = [], PAD + 12
    for w in widths:
        xs.append(x)
        x += w
    head = _font(14, QFont.Bold)
    for (title, _, align), x, w in zip(COLUMNS, xs, widths):
        _text(p, QRectF(x, y, w - 8, 30), title.upper(), head, MUTED,
              (Qt.AlignLeft if align == "l" else Qt.AlignRight) | Qt.AlignVCenter)
    y += 36
    p.fillRect(QRectF(PAD, y - 4, WIDTH - 2 * PAD, 1), CARD_LINE)
    top_points = r.ranked[0].points if r.ranked else 1
    for i, s in enumerate(r.ranked, 1):
        a = s.actor
        rect = QRectF(PAD, y, WIDTH - 2 * PAD, ROW_H - 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 10 if i % 2 else 5))
        p.drawRoundedRect(rect, 10, 10)
        color = QColor(CLASS_COLOR.get(a.class_name, OTHER_COLOR))
        p.setBrush(color)
        p.drawRoundedRect(QRectF(rect.left(), rect.top() + 6, 5, rect.height() - 12), 2, 2)
        dmg = s.parts["damage"]
        crit = f"{100 * a.crits / a.hits:.0f}%" if a.hits else "—"
        values = [str(i), None, (f"{r.cp(a.name) / 1000:.1f}K" if r.cp(a.name) else "—"),
                  compact_number(dmg) if dmg else "—", f"{thousands(dmg / r.seconds)}" if dmg else "—",
                  _pct(100 * dmg / r.basis) if dmg else "—", crit,
                  compact_number(a.healing) if a.healing else "—", compact_number(a.shielding) if a.shielding else "—",
                  compact_number(a.taken) if a.taken else "—", str(a.avoided_hits) if a.avoided_hits else "—",
                  str(a.deaths) if a.deaths else "—", str(a.revives) if a.revives else "—",
                  str(r.aggro.get(a.eid, 0) or "—"), None]
        body = _font(17, QFont.DemiBold if i == 1 else QFont.Normal)
        for (title, _, align), x, w, value in zip(COLUMNS, xs, widths, values):
            cell = QRectF(x, rect.top(), w - 8, rect.height())
            if title == tr("Jugador"):
                icon = icons.get(a.class_name)
                side = rect.height() - 12
                if icon is not None:
                    p.drawPixmap(QRectF(x, rect.top() + 6, side, side), icon, QRectF(icon.rect()))
                name = QFontMetrics(_font(18, QFont.Bold)).elidedText(_name(r, a), Qt.ElideRight, int(w - side - 20))
                _text(p, QRectF(x + side + 10, rect.top(), w - side - 10, rect.height()), name, _font(18, QFont.Bold),
                      GOLD if i == 1 else TEXT)
            elif title == tr("Puntos MVP"):
                bar = QRectF(x, rect.center().y() + 9, w - 70, 5)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 22))
                p.drawRoundedRect(bar, 2, 2)
                p.setBrush(GOLD if i == 1 else TEAL)
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(4, bar.width() * s.points / top_points), 5), 2, 2)
                _text(p, cell, f"{s.points:.1f}", _font(18, QFont.Bold), GOLD if i == 1 else TEXT,
                      Qt.AlignRight | Qt.AlignVCenter)
            else:
                _text(p, cell, value, body, TEXT if value != "—" else MUTED,
                      (Qt.AlignLeft if align == "l" else Qt.AlignRight) | Qt.AlignVCenter)
        y += ROW_H
    if r.outsiders:
        rect = QRectF(PAD, y, WIDTH - 2 * PAD, ROW_H - 4)
        dmg = sum(r.value(a) for a in r.outsiders)
        _text(p, QRectF(xs[1], rect.top(), widths[1] + 300, rect.height()),
              tr("Fuera de tu grupo: {0} jugadores (sin nombre)", len(r.outsiders)), _font(17), MUTED)
        for col, value in ((3, compact_number(dmg)), (4, thousands(dmg / r.seconds)),
                           (5, _pct(100 * dmg / r.basis))):
            _text(p, QRectF(xs[col], rect.top(), widths[col] - 8, rect.height()), value, _font(17), MUTED,
                  Qt.AlignRight | Qt.AlignVCenter)
        y += ROW_H
    return y


def _footer(p, y, height):
    p.fillRect(QRectF(PAD, y, WIDTH - 2 * PAD, 1), CARD_LINE)
    _text(p, QRectF(PAD, y + 12, WIDTH - 2 * PAD, 70), FORMULA, _font(14), MUTED,
          Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap)
    _text(p, QRectF(PAD, height - 34, WIDTH - 2 * PAD, 22),
          tr("{0} {1} · github.com/fcastro93/aiondex · el % es sobre la vida que perdió el jefe (o sobre el daño de "
             "la pelea)", APP_NAME, __version__), _font(13), QColor(139, 149, 171, 170))


class ImageDialog(QDialog):
    """A report image's preview with Copiar and Guardar (saved in Pictures/AionDex by default)."""

    def __init__(self, image, file_name, title, parent=None):
        super().__init__(parent)
        self.image, self.file_name = image, file_name
        self.setWindowTitle(f"{APP_NAME} — {title}")
        from .mapwindow import STYLE
        from .tunerwindow import EXTRA_STYLE
        self.setObjectName("MapWindow")                    # the app's dark window style
        self.setStyleSheet(STYLE + EXTRA_STYLE)
        self.resize(1080, 840)
        col = QVBoxLayout(self)
        col.setContentsMargins(16, 14, 16, 14)
        preview = QLabel()
        preview.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        preview.setPixmap(QPixmap.fromImage(self.image).scaledToWidth(1020, Qt.SmoothTransformation))
        scroll = QScrollArea()
        scroll.setWidget(preview)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        col.addWidget(scroll, 1)
        row = QHBoxLayout()
        self.status = QLabel("")
        row.addWidget(self.status, 1)
        copy = QPushButton(tr("Copiar imagen"))
        copy.clicked.connect(self._copy)
        save = QPushButton(tr("Guardar PNG…"))
        save.setProperty("primary", True)
        save.clicked.connect(self._save)
        row.addWidget(copy)
        row.addWidget(save)
        col.addLayout(row)

    def _copy(self):
        QApplication.clipboard().setImage(self.image)
        self.status.setText(tr("Copiada: pégala en Discord o donde quieras (Ctrl+V)."))

    def _save(self):
        folder = Path.home() / "Pictures" / APP_NAME
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = Path.home()
        path, _ = QFileDialog.getSaveFileName(self, tr("Guardar informe"), str(folder / self.file_name),
                                              tr("Imagen PNG (*.png)"))
        if path:
            self.image.save(path, "PNG")
            self.status.setText(tr("Guardado en {0}", path))


class ReportDialog(ImageDialog):
    """The fight report's preview."""

    def __init__(self, ctl, enc, parent=None):
        name = tr("informe-{0}-{1}.png", _slug(enc.main_target or 'pelea'), time.strftime('%Y%m%d-%H%M', time.localtime(enc.last)))
        super().__init__(render(ctl, enc), name, tr("Informe de combate"), parent)
