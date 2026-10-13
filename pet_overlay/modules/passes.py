"""Pase module: each Daeva Pass of the character you play (level and EXP into it, this week's EXP against the weekly
limit, premium or basic, the rewards waiting to be collected and when it ends), and the other characters' in short."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from . import Module, ModuleView
from ..passes import level_of, weekly_limit
from ..status import countdown, next_weekly
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.widgets import ProgressBar, SectionTitle, label

GLYPH_PASS = ""            # ribbon / badge
OTHERS = {True: 0, False: 4}     # other characters shown (compact / expanded)


class PassesPanel(QWidget):
    def __init__(self, ctl, fonts):
        super().__init__()
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(2))

    def render(self, compact):
        while self.content.count():
            widget = self.content.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        f, ps, now = self.fonts, self.ctl.passes, time.time()
        playing = self.ctl.character_name()
        names = ps.names(now, playing)
        name = playing if playing in names else (names[0] if names else None)
        if name is None:
            self._line("Entra con tu personaje con AionDex abierto: aquí verás su Daeva Pass (nivel, EXP de la semana "
                       "contra el tope y las recompensas por recoger).", theme.MUTED)
        else:
            self.content.addWidget(SectionTitle(f"Daeva Pass · {name}", f))
            for pid, info, s in ps.of(name, now):
                self._pass(pid, info, s, ps, now, compact)
            others = [n for n in names if n != name][:OTHERS[compact]]
            if others:
                self.content.addWidget(SectionTitle("Tus otros personajes", f))
                for other in others:
                    bits = []
                    for pid, info, s in ps.of(other, now):
                        lv = level_of(pid, s.exp)
                        cap = weekly_limit(pid)
                        full = cap and s.weekly >= cap
                        bits.append(f"{_short(info.get('title'))} Lv {lv[0] if lv else '?'}"
                                    + (" ✓" if full else f" ({s.weekly * 100 // cap}%)" if cap else ""))
                    self._line(f"{other}: " + " · ".join(bits), theme.TEXT)
            self._line(f"La EXP semanal vuelve a cero el miércoles (en {countdown(next_weekly(now) - now)})",
                       theme.MUTED)
        self.adjustSize()

    def _pass(self, pid, info, s, ps, now, compact):
        f = self.fonts
        lv = level_of(pid, s.exp)
        cap = weekly_limit(pid)
        head = QHBoxLayout()
        head.setSpacing(f.px(6))
        title = info.get("title") or f"Pase {pid}"
        head.addWidget(label(title, f.name, theme.TEXT, elide=True), 1)
        head.addWidget(label(f"Lv {lv[0]}" if lv else f"{s.exp:,} EXP", f.name, theme.GOLD))
        holder = QWidget()
        holder.setLayout(head)
        head.setContentsMargins(f.px(4), 0, f.px(2), 0)
        self.content.addWidget(holder)
        if lv:
            self._bar(lv[1] / lv[2] if lv[2] else 0, theme.ACCENT, f"{lv[1] * 100 // lv[2]}% · {lv[1]:,}/{lv[2]:,}")
        if cap:
            full = s.weekly >= cap
            self._bar(min(1.0, s.weekly / cap), theme.GOLD if full else theme.GAIN,
                      f"Semana {s.weekly:,}/{cap:,}" + (" · tope alcanzado" if full else
                                                         f" · faltan {cap - s.weekly:,}"))
        waiting = ps.waiting(s)
        end = info.get("end")
        bits = ["Premium" if s.premium else "Básico",
                f"termina en {countdown(end - now)}" if end and end > now else ""]
        if waiting:
            bits.append(f"{waiting} {'recompensa' if waiting == 1 else 'recompensas'} por recoger")
        self._line(" · ".join(b for b in bits if b), theme.GOLD if waiting else theme.MUTED, indent=6)

    def _bar(self, ratio, color, text):
        f = self.fonts
        row = QWidget()
        lay = QVBoxLayout(row)
        lay.setContentsMargins(f.px(6), 0, f.px(2), 0)
        lay.setSpacing(0)
        lay.addWidget(ProgressBar(ratio, color, f.px(4)))
        lay.addWidget(label(text, f.small, theme.MUTED))
        self.content.addWidget(row)

    def _line(self, text, color, indent=4):
        lbl = label(text, self.fonts.small, color)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(self.fonts.px(indent), 0, 0, 0)
        self.content.addWidget(lbl)


def _short(title):
    return {"Ishalgen Mercenary": "Ishalgen", "Planet's Call": "Planet's", "Dawn Legion": "Dawn"}.get(title, title or "?")


class PassesModule(Module):
    key = "passes"
    title = "Pase"

    def icon(self):
        return glyph_icon(GLYPH_PASS, theme.ACCENT, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = PassesPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.panel.render(compact)
        ps = self.ctl.passes
        name = self.ctl.character_name()
        waiting = sum(ps.waiting(s) for _, _, s in ps.of(name)) if name else 0
        return ModuleView(summary=f"{waiting} por recoger" if waiting else "")
