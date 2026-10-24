"""Crafting's "Informe": one picture to share with friends — what to make at your level and the recipes on screen with
their numbers — with AionDex's logo in a corner as a watermark. Drawn with QPainter, saved as JPG."""
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

from ..paths import ASSETS_DIR
from ..professions import level_text
from . import theme
from ..i18n import tr

WIDTH = 1280
MARGIN = 40
ROW_H = 34
MAX_ROWS = 18
COLS = ((tr("Receta"), 0.0, Qt.AlignLeft), (tr("Nivel"), 0.40, Qt.AlignRight), (tr("Precio"), 0.49, Qt.AlignRight),
        (tr("Fallo"), 0.58, Qt.AlignRight), (tr("Recolectando"), 0.71, Qt.AlignRight), (tr("Comprando"), 0.83, Qt.AlignRight),
        (tr("¿Se vende?"), 1.0, Qt.AlignRight))


def _font(px, bold=False):
    f = QFont("Segoe UI")
    f.setPixelSize(px)
    f.setWeight(QFont.DemiBold if bold else QFont.Normal)
    return f


def _kinah(value, sign=False):
    if value is None:
        return "—"
    return f"{value:+,.0f}" if sign else f"{value:,.0f}"


def render(rows, picks, title, subtitle, grade_color, level_pick=None):
    """-> QImage of the report. rows: RecipeRow on screen (the first MAX_ROWS); picks: the recommended ones;
    level_pick: the recipe to level up with (marked EXP)."""
    rows = rows[:MAX_ROWS]
    lines = len(picks) + (1 if level_pick is not None else 0)
    height = MARGIN + 92 + (50 + 30 * lines if lines else 0) + 44 + ROW_H * max(1, len(rows)) + 120
    image = QImage(WIDTH, height, QImage.Format_RGB32)
    image.fill(theme.WINDOW_BG)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    inner = WIDTH - 2 * MARGIN
    y = MARGIN

    # header
    p.setPen(theme.TEXT)
    p.setFont(_font(30, True))
    p.drawText(QRectF(MARGIN, y, inner, 40), Qt.AlignLeft | Qt.AlignVCenter, title)
    p.setPen(theme.MUTED)
    p.setFont(_font(15))
    p.drawText(QRectF(MARGIN, y + 42, inner, 24), Qt.AlignLeft | Qt.AlignVCenter, subtitle)
    p.drawText(QRectF(MARGIN, y, inner, 40), Qt.AlignRight | Qt.AlignVCenter,
               time.strftime(tr("Informe del %d/%m/%Y %H:%M")))
    y += 92

    # recommendation
    if lines:
        box = QRectF(MARGIN, y, inner, 40 + 30 * lines)
        p.setPen(theme.LINE)
        p.setBrush(theme.SURFACE)
        p.drawRoundedRect(box, 8, 8)
        p.setPen(theme.GOLD)
        p.setFont(_font(16, True))
        p.drawText(QRectF(MARGIN + 16, y + 6, inner - 32, 24), Qt.AlignLeft | Qt.AlignVCenter, tr("Lo que más deja"))
        p.setFont(_font(15))
        for i, r in enumerate(picks, start=1):
            sold, kind = r.sales.verdict()
            parts = [tr("{0} por intento", _kinah(r.profit, sign=True)),
                     tr("{0:.0%} de fallo", 1 - r.success) if r.success is not None else "",
                     sold if kind == "good" else tr("aún sin ventas vistas")]
            p.setPen(theme.TEXT)
            p.drawText(QRectF(MARGIN + 16, y + 6 + 30 * i, inner - 32, 26), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{i}.  {r.product}   —   " + " · ".join(x for x in parts if x))
        if level_pick is not None:
            cost = level_pick.profit if level_pick.profit is not None else -level_pick.cost
            p.setPen(theme.GAIN)
            p.drawText(QRectF(MARGIN + 16, y + 6 + 30 * lines, inner - 32, 26), Qt.AlignLeft | Qt.AlignVCenter,
                       tr("Para subir de nivel (EXP):  {0} ({1})   —   {2} por intento", level_pick.product, level_text(level_pick.needs), _kinah(cost, sign=True)))
        y += box.height() + 10

    # table
    def col_rect(index, top):
        name, frac, _ = COLS[index]
        if index == 0:
            return QRectF(MARGIN + 12, top, inner * 0.38, ROW_H)
        right = MARGIN + inner * frac - 12
        width = inner * (frac - COLS[index - 1][1]) - 8
        return QRectF(right - width, top, width, ROW_H)

    p.setFont(_font(13, True))
    p.setPen(theme.MUTED)
    for i, (name, _, align) in enumerate(COLS):
        p.drawText(col_rect(i, y), align | Qt.AlignVCenter, name)
    y += ROW_H
    p.setFont(_font(15))
    if not rows:
        p.setPen(theme.MUTED)
        p.drawText(QRectF(MARGIN + 12, y, inner, ROW_H), Qt.AlignLeft | Qt.AlignVCenter, tr("Sin recetas con precio."))
        y += ROW_H
    for n, r in enumerate(rows):
        if n % 2 == 0:
            p.fillRect(QRectF(MARGIN, y, inner, ROW_H), QColor(255, 255, 255, 8))
        locked = r.can_craft is False
        sold, kind = r.sales.verdict()
        cells = [
            (r.product + (f" ×{r.count}" if r.count > 1 else ""),
             theme.MUTED if locked else QColor(grade_color.get(r.recipe.get("grade")) or theme.grade_color(None))),
            (level_text(r.needs) if r.needs else "—", theme.WARN if locked else theme.MUTED),
            (_kinah(r.sell), theme.TEXT),
            (f"{1 - r.success:.0%}" if r.success is not None else "—", theme.TEXT),
            (_kinah(r.profit, sign=True) if r.profit is not None else "?",
             theme.MUTED if r.profit is None else theme.GAIN if r.profit > 0 else theme.COMBAT),
            (_kinah(r.buy_profit, sign=True) if r.buy_profit is not None else "?",
             theme.MUTED if r.buy_profit is None else theme.GAIN if r.buy_profit > 0 else theme.COMBAT),
            (sold, {"good": theme.GAIN, "bad": theme.COMBAT}.get(kind, theme.MUTED)),
        ]
        for i, (text, color) in enumerate(cells):
            rect = col_rect(i, y)
            p.setPen(color)
            shown = p.fontMetrics().elidedText(text, Qt.ElideRight, int(rect.width()) - (48 if i == 0 else 0))
            p.drawText(rect, COLS[i][2] | Qt.AlignVCenter, shown)
            if i == 0 and r is level_pick:                    # the little EXP mark after the name
                x = rect.left() + p.fontMetrics().horizontalAdvance(shown) + 8
                badge = QRectF(x, y + ROW_H / 2 - 9, 38, 18)
                p.setPen(Qt.NoPen)
                p.setBrush(theme.GAIN)
                p.drawRoundedRect(badge, 6, 6)
                p.setPen(QColor("#06101f"))
                p.setFont(_font(11, True))
                p.drawText(badge, Qt.AlignCenter, "EXP")
                p.setFont(_font(15))
        y += ROW_H

    # footer + watermark
    y += 16
    p.setPen(theme.MUTED)
    p.setFont(_font(13))
    p.drawText(QRectF(MARGIN, y, inner - 180, 40), Qt.AlignLeft | Qt.TextWordWrap,
               tr("Ganancia media por intento: lo que paga el mercado (−10%) × la probabilidad de éxito − los "
                  "materiales (recolectando: lo recolectado gratis; comprando: todo al precio del mercado). "
                  "Recetas: aion2data.com."))
    logo = QPixmap(str(ASSETS_DIR / "badge.png"))
    if not logo.isNull():
        size = 92
        logo = logo.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.setOpacity(0.55)
        p.drawPixmap(WIDTH - MARGIN - logo.width(), height - MARGIN - logo.height() + 10, logo)
        p.setOpacity(1.0)
    p.setPen(QColor(theme.TEXT.red(), theme.TEXT.green(), theme.TEXT.blue(), 150))
    p.setFont(_font(14, True))
    p.drawText(QRectF(WIDTH - MARGIN - 260, height - MARGIN - 4, 160, 24), Qt.AlignRight | Qt.AlignVCenter, "AionDex")
    p.end()
    return image
