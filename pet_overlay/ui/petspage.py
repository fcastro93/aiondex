"""Pets, the page of the AionDex window (the HUD's Pets module is its short version). Your collection in numbers
(unlocked, at max level, each family with its permanent bonus), the pets you can farm where you are as rich rows
(portrait, family, side, which monster, progress, this session's gain, when the next level comes at your pace, a
button to the map), filters to see every pet you still miss or the maxed ones, this session (kills, pace), the
family achievement closest to its reward, and the two switches of what AionDex draws over the game (Modo Focus,
the mob route on the minimap: off until you turn them on). The collection is the account's, not a character's."""
import re
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from . import theme
from .design import Card, Chip, ChipGroup, Divider, Meter, PageHeader, ProgressRing, SearchBox, Toggle
from .widgets import PetIcon, label, side_chip
from ..pets import Pet
from ..i18n import tr

FAMILIES = ("Cogni", "Fera", "Natura", "Varian", "Special")
ORIGIN_TEXT = {"world": tr("mundo abierto"), "boss": tr("jefe de campo"), "abyss": tr("Abismo"),
               "dungeon": tr("mazmorra"), "chest": tr("cofre")}
SCOPES = (("here", tr("Zona actual"), tr("Los pets que puedes farmear donde estás")),
          ("left", tr("Todos los que faltan"), tr("Cada pet que aún no está al máximo y puedes farmear en tu lado")),
          ("session", tr("Esta sesión"), tr("Los que subiste desde que abriste AionDex")),
          ("max", tr("Al máximo"), tr("Los que ya tienes al nivel máximo")))


def _progress_text(pet):
    if pet.is_max:
        return "MAX"
    if pet.level == 0:
        return tr("{0}/{1} almas", pet.progress, pet.threshold)
    return f"{pet.progress}/{pet.threshold}" if pet.threshold else str(pet.progress)


def _eta_text(seconds):
    mins = seconds / 60
    return tr("sube en ~{0:.0f} min", mins) if mins < 90 else tr("sube en ~{0:.1f} h", mins / 60)


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())


class _PetRow(QFrame):
    """One pet: portrait, name with its family and side, where (which monster here, or its zones), progress."""

    def __init__(self, page, pet, name, where, mobs, parent=None):
        super().__init__(parent)
        self.setObjectName("Row")                          # design.HUB_STYLE: the list row with a hover border
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("Clic: ver en el Mapa de pets dónde aparecen sus monstruos"))
        self.page = page
        self.pet_id = pet.pet_id
        ctl, f = page.ctl, page.fonts
        lay = QHBoxLayout(self)
        lay.setContentsMargins(f.px(10), f.px(7), f.px(10), f.px(7))
        lay.setSpacing(f.px(12))
        lay.addWidget(PetIcon(ctl.icons_dir, pet, f.px(40), f), 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(f.px(2))
        head = QHBoxLayout()
        head.setSpacing(f.px(6))
        head.addWidget(label(name, f.name, theme.GOLD if pet.is_max else theme.TEXT))
        family = ctl.pet_family(pet.pet_id)
        if family:
            head.addWidget(Chip(family, theme.MUTED, f, tip=tr("Familia del pet")))
        side = side_chip(ctl.pet_exclusive(pet.pet_id), f)
        if side:
            head.addWidget(side)
        head.addStretch(1)
        col.addLayout(head)
        kinds = [ORIGIN_TEXT[k] for k in ORIGIN_TEXT if k in (ctl.pet_sources(pet.pet_id).get("kinds") or [])]
        text = tr("Mata: {0}", ", ".join(mobs)) if mobs else where
        if kinds:
            text = kinds[0].capitalize() + (" · " + text if text else "")
        if text:
            line = label(text, f.small, theme.MUTED, elide=True)
            if kinds:
                line.setToolTip(tr("De dónde sale su alma: {0}", ", ".join(kinds)))
            col.addWidget(line)
        lay.addLayout(col, 1)
        right = QVBoxLayout()
        right.setSpacing(f.px(3))
        top = QHBoxLayout()
        top.setSpacing(f.px(8))
        top.addStretch(1)
        gained = ctl.session.per_pet.get(pet.pet_id, 0)
        if gained:
            top.addWidget(label(tr("+{0} esta sesión", gained), f.small, theme.GAIN))
        top.addWidget(label(_progress_text(pet), f.name, theme.GOLD if pet.is_max else theme.TEXT))
        right.addLayout(top)
        bar = Meter(pet.ratio, theme.GOLD if pet.is_max else theme.ACCENT, height=f.px(5), glow=not pet.is_max)
        bar.setFixedWidth(f.px(120))
        right.addWidget(bar, 0, Qt.AlignRight)
        eta = None if pet.is_max else ctl.session.eta_seconds(pet)
        if eta:
            right.addWidget(label(_eta_text(eta), f.small, theme.MUTED), 0, Qt.AlignRight)
        lay.addLayout(right)
        go = QPushButton(tr("Mapa"))
        go.setProperty("ghost", True)
        go.setToolTip(tr("Dónde aparecen sus monstruos, y qué otros pets se farmean allí"))
        go.clicked.connect(lambda: ctl.open_map(pet.pet_id))
        lay.addWidget(go, 0, Qt.AlignVCenter)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.page.ctl.open_map(self.pet_id)


class PetsPage(QWidget):
    live = True                 # the hub refreshes it every 10 s (kills land in the list as they happen)

    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        f = fonts
        root = QVBoxLayout(self)
        root.setContentsMargins(f.px(22), f.px(16), f.px(22), f.px(16))
        root.setSpacing(f.px(10))
        self.header = PageHeader(f, tr("Pets"), tr("Lo que puedes farmear donde estás, tu colección y tu sesión. Lo "
                                                   "que se dibuja sobre el juego se enciende aquí."))
        for text, tip, action in ((tr("Colección"), tr("Todos los pets con tu progreso, con buscador y filtros"),
                                   ctl.open_collection),
                                  (tr("Exclusivos"), tr("Los pets que solo se farmean en un lado"),
                                   ctl.open_exclusive_pets),
                                  (tr("Mapa de pets"), tr("Dónde aparecen los monstruos de cada pet"),
                                   lambda: ctl.open_map())):
            btn = QPushButton(text)
            btn.setProperty("ghost", True)
            btn.setToolTip(tip)
            btn.clicked.connect(action)
            self.header.add_right(btn)
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
        # the top: your collection in numbers, then the families as a strip
        self.top = QHBoxLayout()
        self.top.setSpacing(f.px(12))
        self.body.addLayout(self.top)
        self.families_row = QVBoxLayout()
        self.body.addLayout(self.families_row)
        # the filters: what to list and a search, then the family
        bar = QHBoxLayout()
        bar.setSpacing(f.px(10))
        self.scope = ChipGroup(f, SCOPES, "here")
        self.scope.changed.connect(self._filters_changed)
        bar.addWidget(self.scope, 1)
        self.search = SearchBox(f, tr("Buscar un pet o un monstruo…"))
        self.search.changed.connect(lambda _t: self._filters_changed())
        bar.addWidget(self.search)
        self.body.addLayout(bar)
        self.family = ChipGroup(f, [("all", tr("Todas las familias"))] + [(x, x) for x in FAMILIES], "all",
                                gold=True)
        self.family.changed.connect(self._filters_changed)
        self.body.addWidget(self.family)
        # the list at the left, the session / achievement / overlay cards in a column of their own at the right
        columns = QHBoxLayout()
        columns.setSpacing(f.px(12))
        self.list = QVBoxLayout()
        self.list.setSpacing(f.px(6))
        columns.addLayout(self.list, 1)
        aside = QWidget()
        aside.setFixedWidth(f.px(300))
        self.aside = QVBoxLayout(aside)
        self.aside.setContentsMargins(0, 0, 0, 0)
        self.aside.setSpacing(f.px(12))
        columns.addWidget(aside, 0, Qt.AlignTop)
        self.body.addLayout(columns)
        self.body.addStretch(1)
        self._stamp = None

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _filters_changed(self):
        self._stamp = None
        self.refresh()

    # --- drawing ---
    def refresh(self):
        ctl = self.ctl
        pets = ctl.collection.pets
        scope, fam, query = self.scope.selected(), self.family.selected(), self.search.text().strip().lower()
        rows = self._rows(scope, fam, query)
        stamp = (scope, fam, query, ctl.current_zone, ctl.session.gains, ctl.session.level_ups, len(pets),
                 tuple((p.pet_id, p.level, p.progress) for p, _, _ in rows),
                 bool(ctl.settings.get("pets_focus")), tuple(ctl.mob_route_names()),
                 bool((ctl.settings.get("mob_route") or {}).get("on")), int(time.time() // 60))
        if stamp == self._stamp:
            return
        self._stamp = stamp
        self._draw_top()
        self._draw_list(scope, rows)
        self._draw_aside()

    def _draw_top(self):
        _clear(self.top)
        ctl, f = self.ctl, self.fonts
        stats, total = ctl.collection_stats()
        owned = sum(count for _, count, _ in stats[:3])
        maxed = stats[0][1] if stats else 0
        total = max(1, total)
        for ratio, big, small, color, title in ((owned / total, f"{100 * owned // total}%", f"{owned}/{total}",
                                                  None, tr("Desbloqueados")),
                                                 (maxed / total, f"{100 * maxed // total}%", f"{maxed}/{total}",
                                                  theme.GOLD, tr("Al máximo"))):
            col = QVBoxLayout()
            col.setSpacing(f.px(2))
            col.addWidget(ProgressRing(f, ratio, big, small, size=f.px(96), color=color), 0, Qt.AlignHCenter)
            col.addWidget(label(title, f.small, theme.MUTED), 0, Qt.AlignHCenter)
            self.top.addLayout(col)
        levels = Card(f, tr("Tu colección"))
        for text, count, color in stats:
            line = QHBoxLayout()
            line.setSpacing(f.px(8))
            line.addWidget(label("●", f.small, color))
            line.addWidget(label(text, f.body, theme.TEXT), 1)
            line.addWidget(label(str(count), f.name, theme.TEXT))
            line.addWidget(label(f"{100 * count // total}%", f.small, theme.MUTED))
            levels.body.addLayout(line)
        levels.body.addStretch(1)
        self.top.addWidget(levels, 1)
        self._draw_families()

    def _draw_families(self):
        """A strip with the five families: how many you have and at max, and the permanent bonus of the ones that
        give one (Cogni: Might, Natura: Precision)."""
        _clear(self.families_row)
        ctl, f = self.ctl, self.fonts
        ctl.pet_family(0)                                   # loads the families
        by_family = getattr(ctl, "_pet_families", ({}, {}))[0]
        bonus = ctl.pet_family_stats()
        card = Card(f, tr("Familias"))
        card.setToolTip(tr("Cada pet al nivel máximo suma su stat para siempre: cada Cogni +1 Might, cada "
                           "Natura +1 Precision. Súbelos primero (guía de kanonxo)."))
        strip = QHBoxLayout()
        strip.setSpacing(f.px(14))
        for fam in FAMILIES:
            ids = [int(pid) for pid, x in by_family.items() if x == fam]
            if not ids:
                continue
            owned_f = sum(1 for pid in ids if (p := ctl.collection.pets.get(pid)) is not None and p.level >= 1)
            maxed_f = sum(1 for pid in ids if (p := ctl.collection.pets.get(pid)) is not None and p.is_max)
            col = QVBoxLayout()
            col.setSpacing(f.px(3))
            head = QHBoxLayout()
            head.setSpacing(f.px(6))
            head.addWidget(label(fam, f.name, theme.TEXT), 1)
            if fam in bonus:
                stat, got, _ = bonus[fam]
                head.addWidget(Chip(f"+{got} {stat}", theme.GAIN, f,
                                    tip=tr("Cada {0} al máximo da +1 {1} para siempre", fam, stat)))
            col.addLayout(head)
            col.addWidget(Meter(maxed_f / len(ids), theme.GOLD, height=f.px(5)))
            col.addWidget(label(tr("{0} al máximo · {1} de {2}", maxed_f, owned_f, len(ids)), f.small, theme.MUTED,
                                elide=True))
            strip.addLayout(col, 1)
        card.body.addLayout(strip)
        self.families_row.addWidget(card)

    def _rows(self, scope, fam, query):
        """[(pet, where text, [monster names here])] for the scope, in its order."""
        ctl = self.ctl
        pets = ctl.collection.pets
        faction = ctl.map_faction()
        out = []
        if scope == "here":
            _, nearby, _ = ctl.nearby_pets()
            out = [(p, "", mobs) for p, mobs in nearby]
        elif scope == "session":
            recent = sorted((p for p in pets.values() if p.session_gain), key=lambda p: -p.last_change)
            out = [(p, self._zones_text(p.pet_id, faction), []) for p in recent]
        else:
            ids = sorted({int(k) for k in ctl.db} | set(pets))
            for pid in ids:
                p = pets.get(pid) or Pet(pid, 0, 0)
                if (scope == "max") != p.is_max:
                    continue
                if not ctl.mapdata.can_farm(pid, faction) and scope == "left":
                    continue
                out.append((p, self._zones_text(pid, faction), []))
            if scope == "left":
                out.sort(key=lambda r: (r[0].level == 0 and r[0].progress == 0, r[0].remaining_to_max,
                                        ctl.pet_info(r[0].pet_id)[1]))
            else:
                out.sort(key=lambda r: ctl.pet_info(r[0].pet_id)[1])
        if fam != "all":
            out = [r for r in out if ctl.pet_family(r[0].pet_id) == fam]
        if query:
            def hit(r):
                info, name = ctl.pet_info(r[0].pet_id)
                hay = " ".join([name, info.get("mob") or "", " ".join(r[2]), r[1] or ""]).lower()
                return query in hay
            out = [r for r in out if hit(r)]
        return out

    def _zones_text(self, pid, faction):
        zones = self.ctl.mapdata.zones_of(pid, faction)
        info, _ = self.ctl.pet_info(pid)
        names = sorted({z.get("name") for z in zones.values() if z.get("name")})
        bits = []
        if info.get("mob"):
            bits.append(tr("Mata: {0}", info["mob"]))
        if names:
            bits.append(tr("Zonas: {0}", ", ".join(names[:3]) + (tr(" y {0} más", len(names) - 3) if len(names) > 3
                                                                     else "")))
        return " · ".join(bits)

    def _draw_list(self, scope, rows):
        _clear(self.list)
        ctl, f = self.ctl, self.fonts
        zone, _, zone_maxed = ctl.nearby_pets()
        if scope == "here":
            title = tr("Cerca de ti · {0}", zone["name"]) if zone else tr("Cerca de ti")
        elif scope == "left":
            title = tr("Los que te faltan ({0})", len(rows))
        elif scope == "session":
            title = tr("Los que subiste esta sesión")
        else:
            title = tr("Al máximo ({0})", len(rows))
        self.list.addWidget(label(title.upper(), f.section, theme.MUTED))
        if not rows:
            if scope == "here" and not zone:
                text = tr("Aún no sé en qué zona estás: en cuanto mates algo o pases junto a un NPC, aquí salen los "
                          "pets que puedes farmear allí, con su monstruo y cuánto te falta.")
            elif scope == "here" and zone_maxed:
                text = tr("En {0} ya tienes al máximo todos los pets ({1}).", zone["name"], zone_maxed)
            elif scope == "session":
                text = tr("Sin kills todavía: mata monstruos de pets y aquí verás tu ritmo.")
            else:
                text = tr("Nada que mostrar con esos filtros.")
            empty = Card(f)
            note = label(text, f.body, theme.MUTED)
            note.setWordWrap(True)
            empty.body.addWidget(note)
            if scope in ("here", "session"):
                go = QPushButton(tr("Ver todos los que faltan"))
                go.setProperty("ghost", True)
                go.clicked.connect(lambda: self.scope.set_selected("left"))
                empty.body.addWidget(go, 0, Qt.AlignLeft)
            self.list.addWidget(empty)
            self.list.addStretch(1)
            return
        for pet, where, mobs in rows[:120]:
            _, name = ctl.pet_info(pet.pet_id)
            self.list.addWidget(_PetRow(self, pet, name, where, mobs))
        if len(rows) > 120:
            self.list.addWidget(label(tr("y {0} más: afina el buscador", len(rows) - 120), f.small, theme.MUTED))
        self.list.addStretch(1)

    def _draw_aside(self):
        _clear(self.aside)
        ctl, f = self.ctl, self.fonts
        now = time.time()
        # this session
        session = Card(f, tr("Esta sesión"))
        s = ctl.session
        if s.gains:
            grid = QGridLayout()
            grid.setHorizontalSpacing(f.px(8))
            grid.setVerticalSpacing(f.px(2))
            active = s.active_time(now)
            rate = s.rate_per_hour(None, now)
            for r, (k, v) in enumerate(((tr("Progreso"), f"+{s.gains}"),
                                        (tr("Subidas de nivel"), str(s.level_ups)),
                                        (tr("Tiempo activo"), f"{int(active // 3600)}h {int(active % 3600 // 60):02d}m"
                                         if active >= 3600 else f"{int(active // 60)} min"),
                                        (tr("Ritmo"), tr("{0:.0f} por hora", rate) if rate else "—"))):
                grid.addWidget(label(k, f.body, theme.MUTED), r, 0)
                grid.addWidget(label(v, f.name, theme.TEXT), r, 1, Qt.AlignRight)
            grid.setColumnStretch(0, 1)
            session.body.addLayout(grid)
            top = sorted(s.per_pet.items(), key=lambda kv: -kv[1])[:5]
            if top:
                session.body.addWidget(Divider(plain=True))
                for pid, gained in top:
                    line = QHBoxLayout()
                    line.addWidget(label(ctl.pet_info(pid)[1], f.small, theme.TEXT, elide=True), 1)
                    line.addWidget(label(f"+{gained}", f.small, theme.GAIN))
                    session.body.addLayout(line)
            reset = QPushButton(tr("Reiniciar sesión"))
            reset.setProperty("flat", True)
            reset.clicked.connect(lambda: (ctl.reset_session(), self._filters_changed()))
            session.body.addWidget(reset, 0, Qt.AlignLeft)
        else:
            hint = label(tr("Sin kills todavía: mata monstruos de pets y aquí verás tu ritmo."), f.small, theme.MUTED)
            hint.setWordWrap(True)
            session.body.addWidget(hint)
        self.aside.addWidget(session)
        # the family achievement closest to its reward
        best = self._family_achievement()
        if best is not None:
            m, p = best
            ach = Card(f, tr("Logro de familia"))
            name = label(m.desc or m.name, f.name, theme.TEXT)
            name.setWordWrap(True)
            ach.body.addWidget(name)
            line = QHBoxLayout()
            line.addWidget(Meter(p.count / p.target if p.target else 0, theme.GAIN if p.ready else theme.ACCENT,
                                 height=f.px(6)), 1)
            line.addWidget(label(f"{p.count}/{p.target}", f.name, theme.GAIN if p.ready else theme.TEXT))
            ach.body.addLayout(line)
            if p.ready:
                ach.body.addWidget(Chip(tr("¡por cobrar!"), theme.GAIN, f), 0, Qt.AlignLeft)
            if m.rewards:
                prize = label(tr("Premio: {0}", m.rewards), f.small, theme.MUTED)
                prize.setWordWrap(True)
                ach.body.addWidget(prize)
            go = QPushButton(tr("Abrir Logros"))
            go.setProperty("flat", True)
            go.clicked.connect(ctl.open_achievements)
            ach.body.addWidget(go, 0, Qt.AlignLeft)
            self.aside.addWidget(ach)
        # what is drawn over the game: off until you say so
        over = Card(f, tr("Sobre el juego"))
        focus_on = bool(ctl.settings.get("pets_focus"))
        self._switch(over, tr("Modo Focus"), focus_on,
                     tr("Tus pets junto a las misiones del juego (bajo el minimapa), con su mismo estilo; se "
                        "mueve con tu lista de misiones. No sale en capturas ni en streams."),
                     ctl.set_pets_focus)
        over.body.addWidget(Divider(plain=True))
        route = ctl.settings.get("mob_route") or {}
        route_on = bool(route.get("on"))
        self._switch(over, tr("Ruta de mobs en el minimapa"), route_on,
                     tr("Los spawns de los mobs que farmeas sobre el minimapa, la ruta por los más cercanos y "
                        "cuánto le falta a cada uno muerto para reaparecer (lo aprende viéndolos). Se arma con lo "
                        "que matas; los de pets al máximo salen solos."),
                     ctl.set_mob_route)
        if route_on:
            chosen = set(ctl.mob_route_names())
            _, nearby, _ = ctl.nearby_pets()
            mobs = list(dict.fromkeys(m for _, ms in nearby for m in ms if ctl.monster_needed(m)))
            mobs += [m for m in chosen if m not in mobs]
            over.body.addWidget(label(tr("Mobs de la ruta").upper(), f.section, theme.MUTED))
            if not mobs:
                note = label(tr("Mata un mob de un pet que te falte y entra solo en la ruta."), f.small, theme.MUTED)
                note.setWordWrap(True)
                over.body.addWidget(note)
            for name in mobs[:10]:
                respawn = ctl.bridge.mobs.respawn(name)
                spread = ctl.bridge.mobs.respawn_range(name)
                when = ""
                if respawn:
                    when = tr("reaparece en {0:.0f} s", respawn)
                    if spread and spread[1] > 1.6 * max(1.0, spread[0]):
                        when = tr("reaparece en {0:.0f}–{1:.0f} s", spread[0], spread[1])
                short = name if len(name) <= 26 else name[:25] + "…"
                btn = QPushButton(short + (f"  ·  {when}" if when else ""))
                btn.setProperty("chip", True)
                btn.setCheckable(True)
                btn.setChecked(name in chosen)
                btn.setFont(f.small)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip(name + "\n" + tr("Clic: meterlo en la ruta o sacarlo"))
                btn.clicked.connect(lambda _=False, n=name: (ctl.toggle_mob(n), self._filters_changed()))
                over.body.addWidget(btn, 0, Qt.AlignLeft)
        self.aside.addWidget(over)
        self.aside.addStretch(1)

    def _switch(self, card, title, on, text, setter):
        f = self.fonts
        row = QHBoxLayout()
        row.setSpacing(f.px(10))
        toggle = Toggle(on)
        toggle.toggled.connect(lambda value: (setter(value), self._filters_changed()))
        row.addWidget(toggle, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(f.px(2))
        col.addWidget(label(title, f.name, theme.GAIN if on else theme.TEXT))
        note = label(text, f.small, theme.MUTED)
        note.setWordWrap(True)
        col.addWidget(note)
        row.addLayout(col, 1)
        card.body.addLayout(row)

    def _family_achievement(self):
        """(mission, progress) of the pet-family achievement closest to its next reward, or None."""
        ctl = self.ctl
        ms = ctl.achievements
        name = ctl.missions_character(ms)
        if not ms.available or not name:
            return None
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
        return None if best is None else (best[1], best[2])
