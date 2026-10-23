"""Overlay panel of the Pets module.

Shows the pets farmable where the player is: the ones you are killing on top (the last one first), then the rest
closest to max level. When there are none (zone unknown, or everything there is maxed) it shows collection
statistics: share of pets per level.
"""
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGridLayout, QMenu, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .widgets import PetRow, SectionTitle, label
from ..i18n import tr

NEARBY_ROWS = {True: 4, False: 10}      # compact / expanded


@dataclass
class ViewState:
    pinned: object = None                # Pet pinned by the user, shown first
    recent: list = field(default_factory=list)
    zone_name: str = ""                  # zone the player is in
    nearby: list = field(default_factory=list)   # [(pet, [monster names])], last killed first
    zone_maxed: int = 0                  # pets of this zone already at max level
    stats: list = field(default_factory=list)    # [(label, count, color)]
    total: int = 0
    summary: str = ""
    status: str = ""
    status_color: QColor = field(default_factory=lambda: QColor(theme.MUTED))
    session_line: str = ""
    flash_ids: set = field(default_factory=set)


class StatsBar(QWidget):
    """Stacked bar: one segment per level, proportional to the number of pets."""

    def __init__(self, stats, total, height):
        super().__init__()
        self.stats, self.total = stats, max(1, total)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(theme.BAR_BG)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setClipRect(r)
        x = r.left()
        for _, count, color in self.stats:
            w = r.width() * count / self.total
            if w > 0:
                c = QColor(color)
                if color == theme.MUTED:
                    c.setAlpha(70)
                p.setBrush(c)
                p.drawRect(QRectF(x, r.top(), w, r.height()))
                x += w


class PetsPanel(QWidget):
    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(1))
        self._rows = {}

    def render(self, state, compact):
        _clear(self.content)
        self._rows = {}
        nearby_ids = {p.pet_id for p, _ in state.nearby}
        if state.pinned and state.pinned.pet_id not in nearby_ids:
            self._section(tr("Fijado"))
            self._row(state.pinned)
        if state.nearby:
            self._section(tr("Cerca de ti · {0}", state.zone_name))
            limit = NEARBY_ROWS[compact]
            for pet, mobs in state.nearby[:limit]:
                self._row(pet, mobs)
            if len(state.nearby) > limit:
                self._note(tr("y {0} más en esta zona (expande o abre el mapa)", len(state.nearby) - limit))
        else:
            self._stats(state)
        if not compact and state.recent:
            self._section(tr("Esta sesión"))
            for pet in state.recent:
                self._row(pet)
        for pid in state.flash_ids:
            if pid in self._rows:
                self._rows[pid].flash()
        self._mob_route(state, compact)
        focus = bool(self.ctl.settings.get("pets_focus"))
        toggle = label(tr("◉ Modo Focus activo · quitar") if focus else tr("○ Modo Focus: tus pets junto a las misiones"),
                       self.fonts.name, theme.GAIN if focus else theme.ACCENT)
        toggle.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        toggle.setCursor(Qt.PointingHandCursor)
        toggle.setToolTip(tr("Pone la lista de los pets que estás farmeando en el juego, justo debajo de tus "
                             "misiones (bajo el minimapa) y con su mismo estilo: se mueve cuando la lista de "
                             "misiones crece o se acorta. No sale en tus capturas ni en tus streams."))
        toggle.on_click = lambda: self.ctl.set_pets_focus(not focus)
        toggle.setContentsMargins(self.fonts.px(4), self.fonts.px(6), 0, 0)
        self.content.addWidget(toggle)
        for text, tip, action in ((tr("Ver toda la colección"), tr("Todos los pets con tu progreso: subiendo, sin "
                                                               "empezar y al máximo, con buscador"), self.ctl.open_collection),
                                  (tr("Pets exclusivos de cada lado"), tr("Los pets que solo se farmean en el lado "
                                                                      "Asmodian o en el Elyos, con tu progreso"),
                                   self.ctl.open_exclusive_pets)):
            link = label(text, self.fonts.name, theme.ACCENT)
            link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            link.setCursor(Qt.PointingHandCursor)
            link.setToolTip(tip)
            link.on_click = action                        # the shell calls it on a plain click
            link.setContentsMargins(self.fonts.px(4), self.fonts.px(4), 0, 0)
            self.content.addWidget(link)
        self.adjustSize()

    @staticmethod
    def footer(state, compact):
        if compact and state.session_line:
            return state.session_line
        return "\n".join(t for t in (state.session_line, state.status) if t)

    # --- pieces -------------------------------------------------------------------------------
    def _section(self, title):
        self.content.addWidget(SectionTitle(title, self.fonts))

    def _note(self, text, color=None):
        note = label(text, self.fonts.small, color or theme.MUTED)
        note.setWordWrap(True)
        note.setContentsMargins(self.fonts.px(4), self.fonts.px(2), 0, self.fonts.px(2))
        self.content.addWidget(note)

    def _row(self, pet, local_mobs=None):
        info, name = self.ctl.pet_info(pet.pet_id)
        if local_mobs:
            info = {**info, "mob": tr("Mata: {0}", ", ".join(local_mobs))}
        family = self.ctl.pet_family(pet.pet_id)
        if family:
            info = {**info, "mob": f"{family} · {info.get('mob') or ''}".rstrip(" ·")}
        eta = None if pet.is_max else self.ctl.session.eta_seconds(pet)
        if eta:                                   # at this session's pace: the next level in ~N min
            mins = eta / 60
            when = tr("~{0:.0f} min", mins) if mins < 90 else tr("~{0:.1f} h", mins / 60)
            info = {**info, "eta": tr("sube en {0}", when)}
        row = PetRow(pet, info, name, self.fonts, self.ctl.icons_dir,
                     self.ctl.session.per_pet.get(pet.pet_id, 0), side=self.ctl.pet_exclusive(pet.pet_id))
        row.on_click = lambda pid=pet.pet_id: self.ctl.open_map(pid)   # shell calls this on a plain click
        row.setContextMenuPolicy(Qt.CustomContextMenu)
        row.customContextMenuRequested.connect(lambda pos, w=row, pid=pet.pet_id: self._pet_menu(w, pid, pos))
        self._rows.setdefault(pet.pet_id, row)
        self.content.addWidget(row)

    def _mob_route(self, state, compact):
        """Ruta de mobs en el minimapa: on/off, and the zone's monsters to tick (the ones you kill join it)."""
        f, ctl = self.fonts, self.ctl
        route = ctl.settings.get("mob_route") or {}
        on = bool(route.get("on"))
        toggle = self._link(tr("◉ Ruta de mobs en el minimapa · quitar") if on else
                            tr("○ Ruta de mobs en el minimapa (se arma con lo que matas)"),
                            theme.GAIN if on else theme.ACCENT, lambda: ctl.set_mob_route(not on),
                            tr("Dibuja sobre el minimapa del juego los spawns de los mobs que farmeas, una ruta por "
                               "los más cercanos y cuántos segundos le faltan a cada uno muerto para reaparecer (lo "
                               "aprende viéndolos; aro rosa: lo mataste tú, gris: otro jugador). Cada mob que matas "
                               "entra solo; los de pets que ya tienes al máximo, no."))
        toggle.setContentsMargins(f.px(4), f.px(6), 0, 0)
        if not on:
            return
        chosen = set(ctl.mob_route_names())
        mobs = list(dict.fromkeys(m for _, ms in state.nearby for m in ms if ctl.monster_needed(m)))
        mobs += [m for m in chosen if m not in mobs]
        for name in mobs[:4 if compact else 8]:
            respawn = ctl.bridge.mobs.respawn(name)
            spread = ctl.bridge.mobs.respawn_range(name)
            when = ""
            if respawn:
                when = tr(" · reaparece en {0:.0f} s", respawn)
                if spread and spread[1] > 1.6 * max(1.0, spread[0]):
                    when = tr(" · reaparece en {0:.0f}–{1:.0f} s", spread[0], spread[1])
            line = self._link(f"{'☑' if name in chosen else '☐'} {name}{when}",
                              theme.TEXT if name in chosen else theme.MUTED, lambda n=name: ctl.toggle_mob(n),
                              tr("Clic: meterlo en la ruta o sacarlo"))
            line.setContentsMargins(f.px(14), 0, 0, 0)

    def _link(self, text, color, action, tip=""):
        link = label(text, self.fonts.small, color)
        link.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        link.setCursor(Qt.PointingHandCursor)
        link.setToolTip(tip)
        link.on_click = action
        self.content.addWidget(link)
        return link

    def _stats(self, state):
        f = self.fonts
        owned = sum(count for lbl, count, _ in state.stats[:3])
        pct = round(100 * owned / state.total) if state.total else 0
        self._section(tr("Tu colección"))
        headline = label(tr("{0} de {1} pets desbloqueados ({2}%)", owned, state.total, pct), f.name, theme.TEXT)
        headline.setContentsMargins(f.px(4), 0, 0, f.px(3))
        self.content.addWidget(headline)
        bar = StatsBar(state.stats, state.total, f.px(8))
        bar.setContentsMargins(f.px(4), 0, f.px(4), 0)
        self.content.addWidget(bar)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(f.px(4), f.px(5), f.px(4), f.px(2))
        grid.setHorizontalSpacing(f.px(8))
        grid.setVerticalSpacing(f.px(2))
        for i, (text, count, color) in enumerate(state.stats):
            share = 100 * count / state.total if state.total else 0
            grid.addWidget(label("●", f.small, color), i, 0)
            grid.addWidget(label(text, f.body, theme.TEXT), i, 1)
            grid.addWidget(label(str(count), f.name, theme.TEXT), i, 2, Qt.AlignRight)
            grid.addWidget(label(f"{share:.0f}%", f.body, theme.MUTED), i, 3, Qt.AlignRight)
        grid.setColumnStretch(1, 1)
        self.content.addWidget(grid_host)
        stats = self.ctl.pet_family_stats()
        if stats:
            self._family_achievement()
            line = label(tr("Al máximo: ") + " · ".join(f"{fam} {maxed}/{total}" for fam, (_, maxed, total)
                                                    in stats.items()), f.small, theme.ACCENT)
            line.setContentsMargins(f.px(4), f.px(2), 0, 0)
            line.setToolTip(tr("Cada pet al nivel máximo suma su stat para siempre: ")
                            + ", ".join(tr("cada {0} +1 {1} (llevas +{2})", fam, stat, maxed) for fam, (stat, maxed, _)
                                        in stats.items())
                            + tr(". Súbelos primero (guía de kanonxo)."))
            line.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.content.addWidget(line)
        if state.zone_name and state.zone_maxed:
            self._note(tr("En {0} ya tienes al máximo todos los pets ({1}).", state.zone_name, state.zone_maxed),
                       theme.GAIN)
        elif not state.zone_name:
            self._note(tr("Sin zona aún: al detectarla, los pets que puedes farmear."))

    def _family_achievement(self):
        """The pet-family achievement closest to its next reward («Reach Level 2 with 3 Cogni Pets»: count/target),
        from the game's own counters; a click opens Logros."""
        import re
        ctl = self.ctl
        ms = ctl.achievements
        name = ctl.missions_character(ms)
        if not ms.available or not name:
            return
        best = None
        for m in ms.db.values():
            if "pets" not in (m.where or []) or not re.search(r"Reach Level \d+ with \d+", m.desc or ""):
                continue
            p = ms.progress(name, m.id)
            if p.done:
                continue
            ratio = p.count / p.target if p.target else 0
            if best is None or ratio > best[0]:
                best = (ratio, m, p)
        if best is None:
            return
        _, m, p = best
        f = self.fonts
        text = label(tr("Logro: {0} · {1}/{2}", m.desc, p.count, p.target) + (tr(" · ¡por cobrar!") if p.ready else ""),
                     f.small, theme.GAIN if p.ready else theme.ACCENT, elide=True)
        text.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        text.setCursor(Qt.PointingHandCursor)
        text.setToolTip(m.name + (tr(" · premio: {0}", m.rewards) if m.rewards else ""))
        text.on_click = ctl.open_achievements
        self.content.addWidget(text)

    def _pet_menu(self, widget, pet_id, pos):
        menu = QMenu(self)
        menu.addAction(tr("Ver en el mapa"), lambda: self.ctl.open_map(pet_id))
        if self.ctl.settings["pinned"] == pet_id:
            menu.addAction(tr("Quitar fijado"), lambda: self.ctl.pin(None))
        else:
            menu.addAction(tr("Fijar arriba"), lambda: self.ctl.pin(pet_id))
        menu.exec(widget.mapToGlobal(pos))


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()            # gone at once, not when the deferred delete runs
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
