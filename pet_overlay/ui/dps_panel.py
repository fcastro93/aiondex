"""Overlay panel of the DPS module, kept clean for the fight (the look of dps_party.py everywhere): a card with the
main enemy (its HP, rage countdown and, in dungeons, your Odyle Energy and the dungeon's rewards left), then one row
per player with their class colour, combat power, DPS and share; a selector (you / your party / force / everyone)
only when there is a choice to make; and the history of the last 48 hours with a pin (★) for the fights to keep."""
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..combat import in_instance
from ..gear import CLASS_OF_JOB
from . import theme
from .dps_party import BossCard, PartyRow, thousands
from .notifications_panel import ago
from .widgets import label

CLASS_ICON = {"Gladiador": "gladiator", "Templario": "templar", "Cazador": "ranger", "Asesino": "assassin",
              "Hechicero": "sorcerer", "Invocador": "spiritmaster", "Clérigo": "cleric", "Cantor": "chanter"}
VIEWS = (("me", "Yo"), ("party", "Party"), ("force", "Force"), ("all", "Todos"))
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
    """Percentage with a decimal for the small shares typical of world bosses."""
    value = 100 * ratio
    return f"{value:.1f}%" if value < 10 else f"{value:.0f}%"


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
        self.mode = "live"               # "live" | "history"
        self._icons = {}
        self._live_encounter = None      # the fight on screen when the history was opened
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(fonts.px(3))

    def render(self, encounter, compact, view, ready):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self.mode == "history" and encounter is not None and not encounter.ended and \
                encounter is not self._live_encounter:
            self.mode = "live"           # a new fight started: back to the meter
        if self.mode == "history":
            self._history(compact)
        elif not ready:
            self._note("Preparando el medidor…")
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
        self.content.addWidget(BossCard(f, 0, energy, rewards, None, "Mazmorra" if instance else "Sin pelea"))
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
            card = {"name": encounter.target_names.get(boss) or "Enemigo", "max_hp": max_hp, "dead": dead,
                    "hp": 0 if dead else (hp[0] if hp else max_hp or 0), "label": act.label,
                    "rage": None if encounter.ended else combat.rage_left(boss), "ttk": encounter.time_to_kill()}
        self.content.addWidget(BossCard(f, encounter.duration, energy, rewards, card))

        dealers = sorted((a for a in encounter.dealers() if value(a)), key=lambda a: -value(a))
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
        alias = {a.eid: f"Jugador {i}" for i, a in enumerate(outsiders, 1)}   # nobody outside your group is named
        total = max(1, sum(value(a) for a in dealers))
        contribution = encounter.contribution(group) if act.kind == "crowd" and act.boss else None
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
                                            f"{thousands(value(a) / encounter.duration)}/s",
                                            share_text(value(a) / basis), value(a) / top, a.is_self, compact))
        if len(shown) > limit:
            rest = shown[limit:]
            self._note(f"y {len(rest)} más · {share_text(sum(value(a) for a in rest) / basis)}")
        if not shown:
            if view == "me" and not any(a.is_self for a in encounter.actors.values()):
                self._note("AionDex aún no sabe cuál eres tú (pasa justo tras abrirlo): lo sabrá con tus próximas "
                           "habilidades o al cambiar de mapa. Mientras, mira Todos.")
            else:
                self._note("Aún sin daño en esta vista.")
        if not group and 1 <= len(outsiders) <= 4 and act.kind != "crowd":
            self._note("¿Vas en party? El juego envía tu party al entrar a un mapa o teletransportarte: hasta entonces "
                       "tus compañeros salen como «Jugador N».")
        if act.kind == "crowd" and act.boss:
            c = encounter.contribution(group)
            if c:
                if c.group_kind and c.hp_lost and c.seen < 0.2:
                    # world bosses with a crowd: the game sends your own hits and only the damage-over-time ticks of
                    # the others (Gartua, 2026-10-12: 0.14% of its 312M HP visible), so your group's share cannot be
                    # measured; yours can (every hit of yours arrives)
                    self._note(f"Tu aporte al jefe: {pct(c.share(c.me))} · el juego no envía el daño del resto de tu "
                               f"{c.group_kind} en jefes con tanta gente")
                else:
                    self._note(f"Tu aporte al jefe: {pct(c.share(c.me))}"
                               + (f" · tu {c.group_kind}: {pct(c.share(c.group))}" if c.group_kind else ""))
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
            return "Solo tú"
        if view == "party":
            if combat.in_force and not combat.party:
                return ("Estás en una force y AionDex aún no sabe cuál de las 4 parties es la tuya: mira Force o Todos "
                        "mientras tanto")
            return "Tu party (5 personas)" if combat.party else "No estás en una party"
        if view == "force":
            return "Las 4 parties de tu force"
        return "Todos los jugadores que pelean cerca de ti (los de fuera de tu grupo, sin nombre)"

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

    def _footer(self):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, self.fonts.px(4), 0, 0)
        lay.addStretch(1)
        combat = self.ctl.combat
        if combat.group:
            lay.addWidget(Pill("Jugadores", self.fonts, self.ctl.open_players,
                               tip="Perfil de cada jugador de tu party / force: CP, item level, equipo, habilidades"))
        if combat.in_force:
            lay.addWidget(Pill("Parties", self.fonts, lambda: self.ctl.open_players("plan"),
                               tip="Cómo repartir la force: sanadores y cantores por party, CP parejo, buffs que "
                                   "no se acumulan"))
        lay.addWidget(Pill("Historial", self.fonts, self._open_history,
                           tip="Tus peleas de las últimas 48 horas (y las que marcaste con ★)"))
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
        """The fights of the last 48 hours (the pinned ones first), each with its pin and a button to its details."""
        f = self.fonts
        head = QWidget()
        lay = QHBoxLayout(head)
        lay.setContentsMargins(f.px(2), f.px(2), 0, f.px(2))
        lay.addWidget(label("HISTORIAL · 48 H", f.section, theme.MUTED))
        lay.addStretch(1)
        lay.addWidget(Pill("Volver", f, self._close_history))
        self.content.addWidget(head)
        fights = self.ctl.fight_list()
        if not fights:
            self._note("Aún no hay peleas guardadas.")
        fights = sorted(fights, key=lambda i: (not i["pinned"], -i["last"]))[:HISTORY_ROWS[compact]]
        now = time.time()
        for item in fights:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(f.px(6))
            pinned = item["pinned"]
            rl.addWidget(Pill("★" if pinned else "☆", f, lambda i=item["id"], p=pinned: self.ctl.pin_fight(i, not p),
                              color=theme.GOLD if pinned else theme.MUTED,
                              tip="Quitar: se borrará cuando pasen 48 horas" if pinned else
                              "Conservar esta pelea (no se borra a las 48 horas)"))
            col = QVBoxLayout()
            col.setSpacing(0)
            mark = " ✓" if item["killed"] else ""
            col.addWidget(label(f"{item['target']}{mark}", f.body, theme.GOLD if pinned else theme.TEXT, elide=True))
            when = "en curso" if item.get("live") else (ago(item["last"]) if now - item["last"] < 86400 else
                                                        time.strftime("%d/%m %H:%M", time.localtime(item["last"])))
            yours = f" · {short_number(item['dps'])}/s ({100 * item['share']:.0f}%)" if item["damage"] else ""
            col.addWidget(label(f"{mmss(item['duration'])}{yours} · {when}", f.small, theme.MUTED, elide=True))
            rl.addLayout(col, 1)
            rl.addWidget(Pill("Ver", f, lambda i=item["id"]: self.ctl.open_dps_details(i)))
            self.content.addWidget(row)
        self._note("Las peleas se guardan 48 horas en tu PC; las marcadas con ★ se quedan hasta que las quites.")

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

    def _note(self, text):
        note = label(text, self.fonts.small, theme.MUTED)
        note.setWordWrap(True)
        note.setContentsMargins(self.fonts.px(4), self.fonts.px(2), 0, self.fonts.px(2))
        self.content.addWidget(note)
