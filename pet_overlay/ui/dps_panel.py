"""Overlay panel of the DPS module, kept clean for the fight (the look of dps_party.py everywhere): a card with the
main enemy (its HP, rage countdown and, in dungeons, your Odyle Energy and the dungeon's rewards left), then one row
per player with their class colour, combat power, DPS and share; a selector (you / your party / force / everyone)
only when there is a choice to make; and the history of the last 48 hours with a pin (★) for the fights to keep:
each dungeon visit is one entry with its boss fights inside (the trash in between left out), and each boss fight
there has its Simulación (ui/simwindow.py)."""
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..combat import in_instance
from ..gear import CLASS_OF_JOB
from ..sessions import history_entries
from . import theme
from .dps_party import BossCard, PartyRow, compact_number, thousands
from .notifications_panel import ago
from .widgets import label
from ..i18n import tr

CLASS_ICON = {"Gladiador": "gladiator", "Templario": "templar", "Cazador": "ranger", "Asesino": "assassin",
              "Hechicero": "sorcerer", "Invocador": "spiritmaster", "Clérigo": "cleric", "Cantor": "chanter"}
VIEWS = (("me", tr("Yo")), ("party", "Party"), ("force", "Force"), ("all", tr("Todos")))
ROWS = {True: 5, False: 8}
HISTORY_ROWS = {True: 5, False: 10}


def short_number(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}K"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return f"{n:.0f}"


def pct(ratio):
    """Percentage with decimals for the small shares typical of world bosses (0.04% on Gartua, not "0.0%")."""
    value = 100 * ratio
    return f"{value:.2f}%" if 0 < value < 1 else f"{value:.1f}%" if value < 10 else f"{value:.0f}%"


def share_text(ratio):
    value = 100 * ratio
    return f"{value:.2f}%" if 0 < value < 1 else f"{value:.1f}%"


def mmss(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class Pill(QLabel):
    """Small clickable pill; the `selected` one is filled (the overlay shell calls on_click on a plain click)."""

    def __init__(self, text, fonts, on_click, selected=False, enabled=True, tip="", color=None):
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
            fg = (color or theme.TEXT).name() if enabled else theme.MUTED.name()
        self.setStyleSheet(f"background: {bg}; color: {fg}; border: 1px solid {border}; "
                           f"border-radius: {fonts.px(8)}px;")


class DpsPanel(QWidget):
    def __init__(self, ctl, fonts, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.fonts = fonts
        self.mode = "live"               # "live" | "history" | "run" (a dungeon visit of the history)
        self._run_id = None
        self._icons = {}
        self._live_encounter = None      # the fight on screen when the history was opened
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(3))

    def render(self, encounter, compact, view, ready):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().hide()            # gone at once, not when the deferred delete runs: no blink
                item.widget().deleteLater()
        if self.mode in ("history", "run") and encounter is not None and not encounter.ended and \
                encounter is not self._live_encounter:
            self.mode = "live"           # a new fight started: back to the meter
        match = self.ctl.arena() if self.mode == "live" else None
        if match is not None:
            self._arena(match, compact)
        if self.mode == "history":
            self._history(compact)
        elif self.mode == "run":
            self._run_view(compact)
        elif not ready:
            self._note(tr("Preparando el medidor…"))
        elif encounter is None:
            self._idle(compact)
        else:
            self._live(encounter, compact, view)
        self.adjustSize()

    # --- before the first fight ----------------------------------------------------------------------------
    def _idle(self, compact):
        """The card waiting for the fight and your party (or you) with their combat power."""
        f = self.fonts
        ctl = self.ctl
        me = ctl.character_name()
        instance = in_instance(ctl.combat.map_id)
        energy, rewards = self._counters(me) if instance else (None, None)
        self.content.addWidget(BossCard(f, 0, energy, rewards, None, tr("Mazmorra") if instance else tr("Sin pelea")))
        for name, _, job, _, is_me in self._members(me)[:ROWS[compact]]:
            cls = CLASS_OF_JOB.get(job, "")
            self.content.addWidget(PartyRow(f, name, cls, self._class_icon(cls), ctl.cp_of(name), "", "", 0,
                                            is_me, compact))
        self._footer()

    # --- the fight -----------------------------------------------------------------------------------------
    def _live(self, encounter, compact, view):
        f = self.fonts
        ctl = self.ctl
        combat = ctl.combat
        group = encounter.group_names(combat.group)
        act = encounter.activity(group)
        boss = encounter.main_target_eid
        boss_view = act.boss and ctl.settings.get("dps_boss_only", True)
        value = (lambda a: a.by_target[boss]) if boss_view else (lambda a: a.damage)  # noqa: E731
        energy, rewards = self._counters(ctl.character_name()) if in_instance(encounter.map_id) else (None, None)
        hp = encounter.hp.get(boss)
        max_hp = (hp[1] if hp else None) or encounter.target_max_hp.get(boss)
        dead = boss in encounter.dead
        targets = [e for e, d in encounter.targets.items() if d]
        if not act.boss and len(targets) >= 2:       # several monsters: how many, how many down, damage to all
            card = {"count": len(targets), "dead": sum(1 for e in targets if e in encounter.dead),
                    "damage": sum(encounter.targets.values()), "label": act.label}
        else:
            card = {"name": encounter.target_names.get(boss) or tr("Enemigo"), "max_hp": max_hp, "dead": dead,
                    "hp": 0 if dead else (hp[0] if hp else max_hp or 0), "label": act.label,
                    "rage": None if encounter.ended else combat.rage_left(boss), "ttk": encounter.time_to_kill()}
        seconds = encounter.target_time(boss) if boss_view else encounter.duration    # boss DPS over the boss's time
        self.content.addWidget(BossCard(f, seconds, energy, rewards, card))

        dealers = sorted((a for a in encounter.dealers() if value(a)), key=lambda a: -value(a))
        contribution = encounter.contribution(group) if act.kind == "crowd" and act.boss else None
        if contribution and contribution.hp_lost and contribution.seen < 0.2:
            self._blind(encounter, contribution, dealers, value, seconds, compact)
            return
        outsiders = [a for a in dealers if not (a.is_self or a.name in group)]
        party = combat.party if encounter.group is None else group
        if outsiders or combat.in_force:             # a choice to make: who to show
            view = view if self._available(view) else "all"
            self.content.addWidget(self._selector(view))
        else:
            view = "all"
        if view == "me":
            shown = [a for a in dealers if a.is_self]
        elif view == "party":
            shown = [a for a in dealers if a.is_self or a.name in party]
        elif view == "force":
            shown = [a for a in dealers if a.is_self or a.name in group]
        else:
            shown = dealers
        alias = {a.eid: tr("Jugador {0}", i) for i, a in enumerate(outsiders, 1)}   # nobody outside your group is named
        total = max(1, sum(value(a) for a in dealers))
        if contribution:
            basis = contribution.total            # what the boss lost, not the few hits we see
        elif boss_view and max_hp:
            basis = max_hp                        # each one's part of the boss's HP (all add up to what it lost)
        else:
            basis = total
        top = max((value(a) for a in shown), default=1) or 1
        limit = ROWS[compact]
        for a in shown[:limit]:
            cls = a.class_name or CLASS_OF_JOB.get(self._job(a.name), "")
            self.content.addWidget(PartyRow(f, alias.get(a.eid, a.name), cls, self._class_icon(cls),
                                            None if a.eid in alias else (encounter.cp.get(a.name) or ctl.cp_of(a.name)),
                                            f"{thousands(value(a) / seconds)}/s",
                                            share_text(value(a) / basis), value(a) / top, a.is_self, compact))
        if len(shown) > limit:
            rest = shown[limit:]
            self._note(tr("y {0} más · {1}", len(rest), share_text(sum(value(a) for a in rest) / basis)))
        if not shown:
            if view == "me" and not any(a.is_self for a in encounter.actors.values()):
                self._note(tr("AionDex aún no sabe cuál eres tú (pasa justo tras abrirlo): lo sabrá con tus "
                              "próximas habilidades o al cambiar de mapa. Mientras, mira Todos."))
            else:
                self._note(tr("Aún sin daño en esta vista."))
        if not group and 1 <= len(outsiders) <= 4 and act.kind != "crowd":
            self._note(tr("¿Vas en party? El juego envía tu party al entrar a un mapa o teletransportarte: hasta "
                          "entonces tus compañeros salen como «Jugador N»."))
        if contribution:
            c = contribution
            self._note(tr("Tu aporte al jefe: {0}", pct(c.share(c.me)))
                       + (tr(" · tu {0}: {1}", c.group_kind, pct(c.share(c.group))) if c.group_kind else ""))
        self._footer()

    def _blind(self, encounter, c, dealers, value, seconds, compact):
        """A world boss with a crowd: the game sends only your hits (and a few players' damage over time: of your
        force of 20 on Silent Dartan, 2026-10-13, 17 sent nothing at all), so only what can be measured is shown:
        what the boss lost (everyone's damage) and your part of it. No selector and no other rows."""
        f, ctl = self.fonts, self.ctl
        lost = c.hp_lost
        self._note(tr("El jefe perdió {0} ({1}/s) mientras peleabas", compact_number(lost), compact_number(lost / max(1, seconds))), theme.TEXT)
        me = next((a for a in dealers if a.is_self), None)
        if me is not None:
            cls = me.class_name or CLASS_OF_JOB.get(self._job(me.name), "")
            self.content.addWidget(PartyRow(f, me.name, cls, self._class_icon(cls),
                                            encounter.cp.get(me.name) or ctl.cp_of(me.name),
                                            f"{thousands(value(me) / seconds)}/s", share_text(value(me) / c.total), 1.0,
                                            True, compact))
            self._note(tr("Tu aporte: {0} = {1} de lo que perdió. Con tanta gente el juego solo envía tu daño, no "
                          "el de tu force ni el del resto.", compact_number(value(me)), pct(c.share(c.me))))
        else:
            self._note(tr("Aún sin daño tuyo a este jefe."))
        self._footer()

    def _selector(self, view):
        """Yo / Party / Force / Todos."""
        f = self.fonts
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(f.px(2), f.px(1), 0, f.px(1))
        lay.setSpacing(f.px(4))
        for key, text in VIEWS:
            if key == "force" and not self.ctl.combat.in_force:
                continue
            lay.addWidget(Pill(text, f, lambda k=key: self.ctl.set_setting("dps_view", k), selected=key == view,
                               enabled=self._available(key), tip=self._view_tip(key)))
        lay.addStretch(1)
        return holder

    def _available(self, view):
        combat = self.ctl.combat
        if view == "party":
            return bool(combat.party)
        if view == "force":
            return combat.in_force
        return True

    def _view_tip(self, view):
        combat = self.ctl.combat
        if view == "me":
            return tr("Solo tú")
        if view == "party":
            if combat.in_force and not combat.party:
                return (tr("Estás en una force y AionDex aún no sabe cuál de las 4 parties es la tuya: mira Force o "
                           "Todos mientras tanto"))
            return tr("Tu party (5 personas)") if combat.party else tr("No estás en una party")
        if view == "force":
            return tr("Las 4 parties de tu force")
        return tr("Todos los jugadores que pelean cerca de ti (los de fuera de tu grupo, sin nombre)")

    def _members(self, me):
        """[(name, server, job, in your party, is you)]: your party / force, or only you."""
        members = self.ctl.group_members()
        if members:
            return members
        info = self.ctl.character
        return [(me, info.server, info.job, True, True)] if info else []

    def _job(self, name):
        if self.ctl.character and name == self.ctl.character.name:
            return self.ctl.character.job
        info = self.ctl._roster_info.get(name)
        return info[1] if info else 0

    def _counters(self, name):
        """(Odyle Energy, this dungeon's rewards left) as (count, bonus), or None when unknown."""
        if not name:
            return None, None
        status = self.ctl.status
        value = status.value(name, "energy")
        tid = status.instance_ticket(self.ctl.combat.map_id)
        return ((value.count, value.bonus) if value.known else None), (status.ticket(name, tid) if tid else None)

    # --- the Arena -----------------------------------------------------------------------------------------
    def _arena(self, match, compact):
        """Your rivals in the Arena match (and your team in 5v5): class, CP, item level and arena rating, from their
        official profile (loaded as the match starts, each from their own server)."""
        f, ctl = self.fonts, self.ctl
        me = ctl._me()
        mine = match.me(me)
        rivals, allies = match.rivals(me), match.allies(me)
        head = label(f"Arena {match.size} · " + (tr("rival") if len(rivals) == 1 else tr("{0} rivales", len(rivals)))
                     + (tr(" · tu rating {0:,}", mine.rating) if mine and mine.rating else "") + "  ›", f.small, theme.ACCENT)
        head.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        head.setCursor(Qt.PointingHandCursor)
        head.setToolTip(tr("Ver el perfil completo de cada uno (equipo, habilidades, Daevanion) y abrirlo en "
                           "shugo.gg.\nDEF: el Defiance de cada rival: ✓ listo · INMUNE los 5 s de Tenacity tras "
                           "usarlo · luego los segundos que le faltan (su enfriamiento, el que manda el juego en su "
                           "cast). Si lo usó en la ronda anterior sigue contando."))
        head.on_click = ctl.open_arena
        head.setContentsMargins(f.px(4), 0, 0, 0)
        self.content.addWidget(head)
        own_server = mine.server if mine else None
        shown = rivals if compact or not allies else rivals + allies
        for p in shown[:5 if compact else 10]:
            profile = ctl.armory.cached(p.name, p.server)
            cls = CLASS_OF_JOB.get(p.job, "")
            name = p.name + (f" [{p.server}]" if own_server and p.server != own_server else "")
            il = profile.item_level if profile else None
            middle = (f"IL {il:,}" if il else tr("cargando…") if ctl.armory.loading(p.name) else "")
            tag = None
            state = ctl.defiance_of(p.name) if p in rivals else None
            if state:
                kind, left = state
                tag = (("DEF ✓", QColor("#7bd88f")) if kind == "ready" else
                       (tr("INMUNE {0:.0f}s", left), QColor("#f0c24e")) if kind == "immune" else
                       (f"DEF {left:.0f}s", QColor("#ff7a7a")))
            self.content.addWidget(PartyRow(f, name, cls, self._class_icon(cls), profile.cp if profile else None,
                                            middle, f"{p.rating:,}" if p.rating else "", 0, p in allies, compact,
                                            tag=tag))

    def _footer(self):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, self.fonts.px(4), 0, 0)
        lay.addStretch(1)
        if self.ctl.arena() is not None:
            lay.addWidget(Pill("Arena", self.fonts, self.ctl.open_arena, color=theme.GOLD,
                               tip=tr("Los jugadores de tu partida de Arena con su perfil oficial y su shugo.gg")))
        combat = self.ctl.combat
        if combat.group:
            lay.addWidget(Pill(tr("Jugadores"), self.fonts, self.ctl.open_players,
                               tip=tr("Perfil de cada jugador de tu party / force: CP, item level, equipo, "
                                      "habilidades")))
        if combat.in_force:
            lay.addWidget(Pill("Parties", self.fonts, lambda: self.ctl.open_players("plan"),
                               tip=tr("Cómo repartir la force: sanadores y cantores por party, CP parejo, buffs que "
                                      "no se acumulan")))
        session = self.ctl.sessions.active()
        if session is None:
            lay.addWidget(Pill(tr("Sesión"), self.fonts, self.ctl.ask_session,
                               tip=tr("Comenzar una sesión: varios runs con la misma party en un solo informe, con "
                                      "el MVP de toda la sesión")))
        else:
            name = session["name"] if len(session["name"]) <= 16 else session["name"][:15] + "…"
            lay.addWidget(Pill(f"● {name} ({len(session['fights'])})", self.fonts, self.ctl.ask_session,
                               color=theme.GOLD, tip=tr("Sesión en marcha: clic para terminarla y ver su informe")))
        bosses = bool(self.ctl.settings.get("abyss_bosses"))
        lay.addWidget(Pill(tr("◉ Jefes") if bosses else tr("Jefes"), self.fonts,
                           lambda: self.ctl.toggle_setting("abyss_bosses"), color=theme.GOLD if bosses else None,
                           tip=tr("Jefes bajo misiones: la vida de los jefes que tienes cerca (mazmorras, world "
                                  "bosses, el Abismo y sus cores de asedio), siempre a la vista debajo de tus "
                                  "misiones (bajo el minimapa), como el Modo Focus de los pets. Clic para ") + (tr("quitarlo") if bosses else tr("ponerlo")) + "."))
        lay.addWidget(Pill(tr("Historial"), self.fonts, self._open_history,
                           tip=tr("Tus peleas de las últimas 48 horas (y las que marcaste con ★)")))
        self.content.addWidget(row)

    def _open_history(self):
        self.mode = "history"
        self._live_encounter = self.ctl.combat.current
        self.ctl.refresh()

    def _close_history(self):
        self.mode = "live"
        self.ctl.refresh()

    # --- the history -------------------------------------------------------------------------------------------
    def _history(self, compact):
        """The fights of the last 48 hours (the pinned ones first): each dungeon visit as one entry (its bosses
        inside), the rest one by one with their pin and a button to their details."""
        f = self.fonts
        self._head(tr("HISTORIAL · 48 H"), self._close_history)
        fights = self.ctl.fight_list()
        if not fights:
            self._note(tr("Aún no hay peleas guardadas."))
        entries = sorted(history_entries(fights),
                         key=lambda e: (not (e[1].pinned if e[0] == "run" else e[1]["pinned"]),
                                        -(e[1].last if e[0] == "run" else e[1]["last"])))[:HISTORY_ROWS[compact]]
        for kind, entry in entries:
            if kind == "run":
                self._run_row(entry)
            else:
                self._fight_row(entry)
        self._note(tr("Las peleas se guardan 48 horas en tu PC; las marcadas con ★ se quedan hasta que las quites. "
                      "Cada mazmorra muestra solo sus jefes."))

    def _head(self, title, back):
        f = self.fonts
        head = QWidget()
        lay = QHBoxLayout(head)
        lay.setContentsMargins(f.px(2), f.px(2), 0, f.px(2))
        lay.addWidget(label(title, f.section, theme.MUTED, elide=True), 1)
        lay.addWidget(Pill(tr("Volver"), f, back))
        self.content.addWidget(head)

    @staticmethod
    def _when(at):
        now = time.time()
        return ago(at) if now - at < 86400 else time.strftime("%d/%m %H:%M", time.localtime(at))

    def _run_row(self, run):
        """A dungeon visit: its name, its bosses (✓ the ones killed) and a button to open it."""
        f = self.fonts
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(f.px(6))
        bosses = run.bosses
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(run.place, f.body, theme.GOLD if run.pinned else theme.ACCENT, elide=True))
        killed = sum(1 for i in bosses if i["killed"])
        names = ", ".join(i["target"] + (" ✓" if i["killed"] else "") for i in bosses)
        col.addWidget(label((tr("1 jefe") if len(bosses) == 1 else tr("{0} jefes", len(bosses))) + f" ({killed} ✓) · "
                            f"{self._when(run.last)} · {names}", f.small, theme.MUTED, elide=True))
        rl.addLayout(col, 1)
        rl.addWidget(Pill(tr("Abrir"), f, lambda r=run.id: self._open_run(r),
                          tip=tr("Las peleas de cada jefe de esta mazmorra, con su Simulación")))
        self.content.addWidget(row)

    def _fight_row(self, item, simulation=False):
        f = self.fonts
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(f.px(6))
        pinned = item["pinned"]
        rl.addWidget(Pill("★" if pinned else "☆", f, lambda i=item["id"], p=pinned: self.ctl.pin_fight(i, not p),
                          color=theme.GOLD if pinned else theme.MUTED,
                          tip=tr("Quitar: se borrará cuando pasen 48 horas") if pinned else
                          tr("Conservar esta pelea (no se borra a las 48 horas)")))
        col = QVBoxLayout()
        col.setSpacing(0)
        mark = " ✓" if item["killed"] else ""
        col.addWidget(label(f"{item['target']}{mark}", f.body, theme.GOLD if pinned else theme.TEXT, elide=True))
        when = tr("en curso") if item.get("live") else self._when(item["last"])
        yours = f" · {short_number(item['dps'])}/s ({100 * item['share']:.0f}%)" if item["damage"] else ""
        deaths = tr(" · {0} muertes", item['deaths']) if simulation and item.get("deaths") else ""
        col.addWidget(label(f"{mmss(item['duration'])}{yours}{deaths} · {when}", f.small, theme.MUTED, elide=True))
        rl.addLayout(col, 1)
        if simulation:
            rl.addWidget(Pill(tr("Simulación"), f, lambda i=item["id"]: self.ctl.open_simulation(i),
                              enabled=bool(item.get("replay")),
                              tip=tr("Ver la pelea desde arriba: dónde estaba cada uno, las muertes y lo que lanzó "
                                     "el jefe") if item.get("replay") else
                                  tr("Sin simulación: AionDex no grabó esta pelea (solo las de jefes de mazmorra "
                                     "desde la versión 1.44)")))
        rl.addWidget(Pill(tr("Ver"), f, lambda i=item["id"]: self.ctl.open_dps_details(i)))
        self.content.addWidget(row)

    def _open_run(self, run_id):
        self.mode, self._run_id = "run", run_id
        self.ctl.refresh()

    def _back_to_history(self):
        self.mode = "history"
        self.ctl.refresh()

    def _run_view(self, compact):
        """A dungeon visit: its boss fights in order, each with its details and its Simulación."""
        run = next((e[1] for e in history_entries(self.ctl.fight_list()) if e[0] == "run" and e[1].id == self._run_id),
                   None)
        if run is None:
            self._back_to_history()
            return
        self._head(f"{run.place.upper()} · {time.strftime('%d/%m %H:%M', time.localtime(run.start))}",
                   self._back_to_history)
        for item in run.bosses:
            self._fight_row(item, simulation=True)
        deaths = sum(i.get("deaths", 0) for i in run.bosses)
        self._note(tr("Solo los jefes (los mobs de en medio no se muestran)")
                   + (tr(" · {0} muertes de tu grupo en ellos", deaths) if deaths else "") + ".")

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

    def _note(self, text, color=None):
        note = label(text, self.fonts.small, color or theme.MUTED)
        note.setWordWrap(True)
        note.setContentsMargins(self.fonts.px(4), self.fonts.px(2), 0, self.fonts.px(2))
        self.content.addWidget(note)
