"""DPS meter model: encounters built from combat events, per-player stats, encounter history.

Players (you and the people fighting with you) are the actors: the damage they deal, the healing and shields they
give, the damage they take and avoid, and the stagger they cause (events from contribution.CombatFeed). Summons
count for their owner. Encounters start with the first hit dealt or taken and end after IDLE_END seconds without
one or when the main target dies. The history keeps only your own numbers; the last few full encounters stay in
memory (never on disk) for the details window.

At world bosses the client only receives the hits of nearby players, so the share of the boss's damage is
measured against the HP the boss lost (stat updates reach everyone), not against the hits we happen to see.
"""
import time
from collections import Counter, deque
from dataclasses import dataclass, field

from .protocol import read_varint

PLAYER_SPAWN_OPCODE = bytes.fromhex("4536")
IDLE_END = 8.0          # seconds without damage that end an encounter
AFTER_KILL_END = 3.0    # shorter wait once the main target has died (adds may still be fought)
HISTORY_MAX = 20
RECENT_MAX = 20         # full encounters kept in memory for the history and the details window
PARTY_SIZE = 5          # a group roster larger than this is a force (up to 4 parties)
ABYSS_MAPS = {20}       # game map ids (2136) of the Abyss
BOSS_HP = 150_000       # a target with this much HP is a boss (seal bosses have ~200K, field mobs ~40K)
WORLD_BOSS_HP = 50_000_000
CROWD = 8               # players outside your group hitting the same fight = a mass fight
def _hp_text(hp):
    """276000000 -> "276M", 119000 -> "119K" (a monster no database names is called by its HP)."""
    return f"{hp / 1e6:.0f}M" if hp >= 10_000_000 else f"{hp / 1e6:.1f}M" if hp >= 1_000_000 else f"{hp // 1000}K"


def in_instance(map_id):
    """Dungeons and the other instances (Fire Temple 600022, Draupnir 600032...): not the open world or the Abyss."""
    return map_id is not None and not 1000 <= map_id < 1200 and map_id not in ABYSS_MAPS


# Revives (2026-10-12 captures): a dead player who gets up by themselves casts 8047 about 3 s after dying (dungeons);
# a healer's revive is a cleric Salvation (1727xxxx, set beforehand: the player gets up at once) or Summon
# Resurrection (1739xxxx), then the dead player acts again. The cast does not say whom it targets, so the revive
# goes to the healer whose cast came last before the player got up.
REVIVE_SKILLS = {1727: "Salvation", 1739: "Summon Resurrection"}      # skill // 10000
SELF_REVIVE = 8047
REVIVE_WINDOW = 30.0         # a revive cast this long before the player got up still counts
SALVATION_WINDOW = 180.0     # Salvation is cast before the death: back within 2 s of dying, cast this long before


def aggro_counts(enc):
    """{player eid: enemies that hit this player the most}: who held each monster's attention. The areas a boss
    leaves (2920000-2929999) do not count."""
    holders = Counter()
    for enemy in enc.enemy_damage:
        npc = enc.target_npc.get(enemy)
        if npc is not None and 2920000 <= npc < 2930000:
            continue
        best = max(enc.actors.values(), key=lambda a: a.taken_from.get(enemy, 0), default=None)
        if best is not None and best.taken_from.get(enemy):
            holders[best.eid] += 1
    return holders


CLASS_NAMES = {"11": "Gladiador", "12": "Templario", "13": "Asesino", "14": "Cazador", "15": "Hechicero",
               "16": "Invocador", "17": "Clérigo", "18": "Cantor"}


@dataclass
class Hit:
    ts: float
    attacker: int
    target: int
    amount: int
    skill: int = 0
    crit: bool = False
    back: bool = False
    perfect: bool = False
    double: bool = False
    dot: bool = False
    kind: str = "damage"        # damage | taken | heal | absorb | avoid | stagger
    overheal: int = 0           # heal: the part beyond the HP the target was missing
    defense: str = ""           # taken / avoid: parry | block | endure | evade
    reduced: int = 0            # taken: damage the target's endure removed
    by: int | None = None       # absorb: whose hit the shield took (attacker = who cast the shield)


@dataclass
class ActorStats:
    eid: int
    name: str
    is_self: bool = False
    damage: int = 0
    hits: int = 0
    crits: int = 0
    backs: int = 0
    perfects: int = 0
    doubles: int = 0
    dot: int = 0
    max_hit: int = 0
    skills: Counter = field(default_factory=Counter)
    skill_hits: Counter = field(default_factory=Counter)
    by_target: Counter = field(default_factory=Counter)
    # healing given (overheal = the part beyond the missing HP, already left out of healing)
    healing: int = 0
    overheal: int = 0
    heal_hits: int = 0
    heal_crits: int = 0
    heal_skills: Counter = field(default_factory=Counter)
    heal_skill_hits: Counter = field(default_factory=Counter)
    heal_to: Counter = field(default_factory=Counter)
    # damage the shields this player cast absorbed (on anyone)
    shielding: int = 0
    shield_skills: Counter = field(default_factory=Counter)
    shield_to: Counter = field(default_factory=Counter)
    # damage taken (absorbed = what shields took off it before it reached the HP)
    taken: int = 0
    taken_hits: int = 0
    taken_max: int = 0
    absorbed: int = 0
    taken_from: Counter = field(default_factory=Counter)
    taken_skills: Counter = field(default_factory=Counter)
    # enemy hits avoided or softened (avoided = damage removed by endure; parries and blocks do not say how much)
    avoided_hits: int = 0
    parries: int = 0
    blocks: int = 0
    evades: int = 0
    endures: int = 0
    avoided: int = 0
    avoided_skills: Counter = field(default_factory=Counter)
    # stagger gauge points taken off bosses (split per hit: the game does not say who)
    stagger: float = 0.0
    deaths: int = 0             # times this player died during the fight (048d)
    revives: int = 0            # times this player (a healer) revived someone (the same person 3 times counts 3)
    revived: int = 0            # times this player was revived by a healer
    stagger_skills: Counter = field(default_factory=Counter)
    # upkeep, filled when the fight ends (upkeep.py): what this player had on and used
    upkeep_time: float = 0.0    # seconds of fight these numbers cover (0: none)
    distance: float = -1.0      # metres moved (-1: unknown; needs the fight's simulation)
    dead_time: float = -1.0     # seconds dead (-1: unknown)
    potions: int = 0            # potions drunk
    food_uptime: float = 0.0    # share of the fight with food / drink / scroll on
    buff_list: list = field(default_factory=list)   # [[key, name, caster, uptime 0-1, times, "class" | "item"]]
    item_uses: list = field(default_factory=list)   # [[skill, name, times, category]] consumables used

    @property
    def class_name(self):
        """The class, from the prefix of the skills used (11 Gladiator ... 18 Chanter)."""
        prefixes = Counter()
        for counter in (self.skills, self.heal_skills, self.shield_skills):
            for skill, amount in counter.items():
                prefix = str(skill)[:2]
                if prefix in CLASS_NAMES and len(str(skill)) == 8:
                    prefixes[prefix] += amount
        return CLASS_NAMES[prefixes.most_common(1)[0][0]] if prefixes else ""


@dataclass
class Contribution:
    """Damage to the main target: yours, your group's, what we see of everyone, and what the boss lost."""
    me: int
    group: int
    group_kind: str             # "party" | "force" | "" (no group)
    visible: int                # damage to the boss in the hits we receive (nearby players only)
    hp_lost: int | None         # HP the boss lost during the encounter (all players), from its HP updates
    max_hp: int | None
    outsiders: int              # players outside your group seen hitting the boss
    best_outsider: int          # biggest damage to the boss by one of them

    @property
    def total(self):
        return max(self.hp_lost or 0, self.visible) or 1

    def share(self, amount):
        return amount / self.total

    @property
    def seen(self):
        """Fraction of the boss's damage we actually see hit by hit."""
        return min(1.0, self.visible / self.total)


@dataclass
class Activity:
    """What kind of fight this is, so the meter can show what matters for it."""
    kind: str                   # "farm" | "solo_boss" | "group" | "group_boss" | "crowd"
    label: str                  # shown in the header: "Farmeo", "Jefe del Abismo"...
    place: str                  # "mundo" | "abismo" | "instancia"
    boss: bool


@dataclass
class Encounter:
    start: float
    last: float
    actors: dict = field(default_factory=dict)       # eid -> ActorStats
    targets: Counter = field(default_factory=Counter)  # target eid -> damage taken
    side_targets: Counter = field(default_factory=Counter)  # target eid -> damage from you and your group
    enemy_damage: Counter = field(default_factory=Counter)  # enemy eid -> damage it dealt to players
    target_names: dict = field(default_factory=dict)
    dead: set = field(default_factory=set)            # targets that died during the encounter
    hp: dict = field(default_factory=dict)            # target eid -> [current hp, max hp]
    hp_start: dict = field(default_factory=dict)      # target eid -> first hp seen during the encounter
    hp_at: dict = field(default_factory=dict)         # target eid -> time of that first hp
    target_max_hp: dict = field(default_factory=dict)  # target eid -> max hp from its spawn
    target_npc: dict = field(default_factory=dict)    # enemy eid -> its npc id (its kind)
    target_boss: dict = field(default_factory=dict)   # enemy eid -> True boss / False normal or elite (when known)
    first_hit: dict = field(default_factory=dict)     # target eid -> time of the first hit on it
    last_hit: dict = field(default_factory=dict)      # target eid -> time of the last hit on it
    died_at: dict = field(default_factory=dict)       # target eid -> when it died
    map_id: int | None = None                         # game map where the fight happened
    killed: bool = False
    ended: bool = False
    group: set | None = None     # your party / force when it ended (None while it goes on: the current one counts)
    cp: dict = field(default_factory=dict)            # name -> combat power of you and your group (saved fights)
    pinned: bool = False         # saved fight kept past the 48 hours
    place: str = ""              # the dungeon it was in ("Fire Temple"), "Mundo abierto", "Abismo" (sessions.place_of)
    final: bool = False          # it killed the dungeon's last boss (the next fights are another run)
    replay: dict | None = None   # the Simulación of a dungeon boss fight (simulation.build)

    @property
    def id(self):
        return str(int(self.start * 1000))

    def group_names(self, current):
        """Your group for this fight: the one it had when it ended, else `current` (the group now)."""
        return self.group if self.group is not None else current

    def contribution(self, group_names):
        """Contribution to the main target, or None when nobody hit it."""
        boss = self.main_target_eid
        dealt = [(a, a.by_target[boss]) for a in self.dealers() if a.by_target[boss]]
        visible = sum(d for _, d in dealt)
        if not visible:
            return None
        me = sum(d for a, d in dealt if a.is_self)
        group = sum(d for a, d in dealt if a.is_self or a.name in group_names)
        outsiders = [d for a, d in dealt if not (a.is_self or a.name in group_names)]
        kind = "" if not group_names else "party" if len(group_names) <= PARTY_SIZE else "force"
        return Contribution(me, group, kind, visible, self.hp_lost(boss), self.hp.get(boss, [0, None])[1],
                            len(outsiders), max(outsiders, default=0))

    def hp_lost(self, eid):
        """HP the target lost while we fought it (None without HP updates)."""
        if eid not in self.hp_start or eid not in self.hp:
            return None
        now = 0 if eid in self.dead else self.hp[eid][0]
        return max(0, self.hp_start[eid] - now)

    def activity(self, group_names):
        place = ("abismo" if self.map_id in ABYSS_MAPS else
                 "mundo" if self.map_id is None or 1000 <= self.map_id < 1200 else "instancia")
        boss_eid = self.main_target_eid
        boss_hp = self.target_max_hp.get(boss_eid) or self.hp.get(boss_eid, [0, 0])[1] or 0
        # the monster's rank when the database knows it (Abyss elites have 168K HP and are no bosses), else its HP
        known = self.target_boss.get(boss_eid)
        boss = known if known is not None else boss_hp >= BOSS_HP
        others = [a for a in self.dealers() if not a.is_self]
        outsiders = [a for a in others if a.name not in group_names]
        if len(outsiders) >= CROWD or boss_hp >= WORLD_BOSS_HP:
            if boss:
                label = {"abismo": "Jefe del Abismo", "mundo": "Jefe de campo", "instancia": "Jefe de raid"}[place]
            else:
                label = "Pelea en el Abismo" if place == "abismo" else "Pelea multitudinaria"
            return Activity("crowd", label, place, boss)
        if others:
            if place == "instancia":
                label = "Jefe de mazmorra" if boss else "Mazmorra en grupo"
            else:
                label = "Jefe en grupo" if boss else ("Abismo en grupo" if place == "abismo" else "En grupo")
            return Activity("group_boss" if boss else "group", label, place, boss)
        if boss:
            return Activity("solo_boss", "Jefe en solitario", place, boss)
        return Activity("farm", "Abismo" if place == "abismo" else "Farmeo", place, boss)

    def enemies(self, limit=None):
        """[(eid, name, hp ratio or None, dead, damage it dealt, damage it took)]: the main target first, then the
        enemies you hit hardest, then those that only hit you."""
        main = self.main_target_eid
        eids = list(dict.fromkeys([e for e, _ in self.targets.most_common()] +
                                  [e for e, _ in self.enemy_damage.most_common()]))
        eids.sort(key=lambda e: (e != main, -self.targets[e], -self.enemy_damage[e]))
        out = []
        for eid in eids[:limit]:
            cur_max = self.hp.get(eid)
            dead = eid in self.dead
            ratio = 0.0 if dead else (max(0.0, min(1.0, cur_max[0] / cur_max[1])) if cur_max and cur_max[1] else None)
            out.append((eid, self.target_names.get(eid) or "Enemigo", ratio, dead, self.enemy_damage[eid],
                        self.targets[eid]))
        return out

    def time_to_kill(self):
        """Seconds left for the main target at the rate it is losing HP (everyone's damage), or None."""
        eid = self.main_target_eid
        lost = self.hp_lost(eid)
        if not lost or eid in self.dead or eid not in self.hp_at:
            return None
        elapsed = self.last - self.hp_at[eid]
        if elapsed < 3:
            return None
        return self.hp[eid][0] / (lost / elapsed)

    def boss_hp_ratio(self):
        cur_max = self.hp.get(self.main_target_eid)
        if not cur_max or not cur_max[1]:
            return None
        return max(0.0, min(1.0, cur_max[0] / cur_max[1]))

    @property
    def main_target_eid(self):
        """The enemy you and your group hit the most (in a crowd, the one everyone hits may not be yours), else the
        most hit one."""
        if self.side_targets:
            return self.side_targets.most_common(1)[0][0]
        return self.targets.most_common(1)[0][0] if self.targets else None

    @property
    def duration(self):
        return max(1.0, self.last - self.start)

    def target_time(self, eid):
        """Seconds from the first hit on this enemy to its death (or the last hit on it): a boss fight's time, the one
        boss DPS is measured over like LOXIN does, without the trash before the pull or the adds after the kill
        (Phantasmal Lakshmi, 2026-10-12: 87 s of boss in a 119 s encounter; same damage per player as LOXIN)."""
        if eid not in self.first_hit:
            return self.duration
        end = self.died_at.get(eid) or (self.last if not self.ended else self.last_hit.get(eid, self.last))
        return max(1.0, end - self.first_hit[eid])

    def fight_time(self, group_names, boss_only=True):
        """The time DPS is measured over: the boss's in boss fights (when the meter counts the damage to the boss),
        else the whole encounter."""
        if boss_only and self.activity(group_names).boss:
            return self.target_time(self.main_target_eid)
        return self.duration

    @property
    def total(self):
        return sum(a.damage for a in self.actors.values())

    @property
    def main_target(self):
        eid = self.main_target_eid
        return None if eid is None else self.target_names.get(eid)

    def dps(self, actor):
        return actor.damage / self.duration

    def dealers(self):
        """Actors that dealt damage (healers and tanks with only healing or damage taken are actors too)."""
        return [a for a in self.actors.values() if a.damage]

    def ranking(self):
        return sorted(self.dealers(), key=lambda a: -a.damage)


@dataclass
class EncounterSummary:
    """What the history keeps: the encounter and your own numbers only."""
    ended_at: float
    target: str
    duration: float
    damage: int
    dps: float
    share: float
    killed: bool


def parse_player(body):
    """4536 player spawn: varint entity | 5 bytes | u8 len + name | u32 job ... -> (eid, name, job)."""
    eid, p = read_varint(body, 0)
    if eid is None or p + 6 > len(body):
        return None
    p += 5
    n = body[p]
    end = p + 1 + n
    if not 2 <= n <= 32 or end + 4 > len(body):
        return None
    try:
        name = body[p + 1:end].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return eid, name, int.from_bytes(body[end:end + 4], "little")


class CombatTracker:
    def __init__(self):
        self.players = {}        # eid -> name (players around, from spawns and your own character)
        self.owners = {}         # summon eid -> owner eid
        self.npc_names = {}      # eid -> monster name
        self.max_hp = {}         # eid -> max hp from the spawn
        self.npc_ids = {}        # eid -> npc id (the kind of monster)
        self.bosses = {}         # eid -> True boss / False not (from the monster database)
        self.rage = {}           # eid -> seconds from the start of the fight until that boss rages
        self.engaged = {}        # eid -> first hit between that monster and your side (kept across encounters)
        self.down = {}           # player eid -> when they died (until they act again)
        self.res_casts = deque(maxlen=50)    # (time, caster, skill base) of revive skills cast
        self.group = set()       # names of your party / force members (from the group roster)
        self.party = set()       # names of your own party (inside a force: only once the game says which it is)
        self.in_force = False
        self.self_eid = None
        self.map_id = None       # the game map you are in (set on every map entry)
        self.current = None
        self.history = []
        self.recent = deque(maxlen=RECENT_MAX)   # finished encounters (memory only) for the details window

    # --- world knowledge ---------------------------------------------------------------------
    def set_self(self, eid, name):
        self.self_eid = eid
        self.players[eid] = name
        enc = self.current
        if enc and eid in enc.actors:            # named "Tú" or after your last character until the info came
            enc.actors[eid].name = name
            enc.actors[eid].is_self = True

    def add_player(self, eid, name):
        self.players[eid] = name

    def rename_player(self, eid, name):
        """A player first known by entity only ("Compañero 2") got its name: the fights so far too."""
        self.players[eid] = name
        for enc in (self.current, *self.recent):
            if enc is not None and eid in enc.actors:
                enc.actors[eid].name = name

    def add_summon(self, eid, owner):
        self.owners[eid] = owner

    def add_npc(self, eid, name, max_hp=None, rage=None, npc_id=None, boss=None):
        """A monster came into view. Without a name (a variant no database lists) it is called by its HP, which
        tells the kinds apart; monsters that were around before AionDex listened have no spawn at all: "Enemigo"."""
        if not name and npc_id is not None and 2920000 <= npc_id < 2930000:
            name = "Efecto enemigo"      # objects a monster leaves (they copy its HP): areas, totems, clones
        elif not name:
            name = (f"Jefe ({_hp_text(max_hp)} HP)" if max_hp and max_hp >= 300_000 else
                    f"Enemigo ({_hp_text(max_hp)} HP)" if max_hp and max_hp >= 1000 else "Enemigo")
        if npc_id is not None:
            self.npc_ids[eid] = npc_id
        if boss is not None:
            self.bosses[eid] = boss
        self.npc_names[eid] = name
        if max_hp:
            self.max_hp[eid] = max_hp
        if rage:
            self.rage[eid] = rage

    def on_map(self, map_id):
        self.map_id = map_id
        self.engaged.clear()

    def rage_left(self, eid, now=None):
        """Seconds until this boss rages (negative once it has), counted from the first hit between it and your side,
        or None for monsters that never rage. A pause that splits the fight in two encounters does not reset it."""
        if eid not in self.rage or eid not in self.engaged:
            return None
        return self.rage[eid] - ((now or time.time()) - self.engaged[eid])

    def set_group(self, names, party=None):
        """Your group roster. A roster of up to 5 is your party; a bigger one is a force (4 parties of 5), and then
        `party` is your own party's names when the roster tells which one is yours (else unknown)."""
        self.group = set(names)
        self.in_force = len(self.group) > PARTY_SIZE
        self.party = set(party or ()) if self.in_force else set(self.group)

    def on_hp(self, eid, hp):
        enc = self.current
        if enc and not enc.ended and (eid in enc.targets or eid in enc.enemy_damage):
            enc.hp[eid] = [hp, self.max_hp.get(eid) or max(hp, enc.hp.get(eid, [0, 0])[1])]
            if eid not in enc.hp_start:
                enc.hp_start[eid], enc.hp_at[eid] = hp, enc.last

    # --- combat ----------------------------------------------------------------------------------
    def _actor(self, enc, eid):
        actor = enc.actors.get(eid)
        if actor is None:
            actor = enc.actors[eid] = ActorStats(eid, self.players[eid], eid == self.self_eid)
        return actor

    def _encounter(self, ts):
        """(the encounter a hit dealt or taken at ts belongs to, the one it closed or None)."""
        finished = None
        enc = self.current
        if enc is None or enc.ended or ts - enc.last > IDLE_END:
            finished = self._finish() if enc and not enc.ended else None
            enc = self.current = Encounter(start=ts, last=ts, map_id=self.map_id)
        enc.last = ts
        return enc, finished

    def on_hit(self, hit):
        """Returns a finished Encounter if this event started a new one after the previous ended."""
        if hit.amount is None or hit.amount <= 0:
            return None
        if hit.kind != "damage":
            return self._on_support(hit)
        attacker = self.owners.get(hit.attacker, hit.attacker)
        if attacker not in self.players:
            return None          # (hits on players only come from enemies of the other faction: PvP)
        if attacker in self.down:
            self._back(attacker, hit.ts)
        enc, finished = self._encounter(hit.ts)
        actor = self._actor(enc, attacker)
        actor.damage += hit.amount
        actor.hits += 1
        actor.crits += hit.crit
        actor.backs += hit.back
        actor.perfects += hit.perfect
        actor.doubles += hit.double
        actor.dot += hit.amount if hit.dot else 0
        actor.max_hit = max(actor.max_hit, hit.amount)
        actor.skills[hit.skill] += hit.amount
        actor.skill_hits[hit.skill] += 1
        actor.by_target[hit.target] += hit.amount
        enc.targets[hit.target] += hit.amount
        enc.first_hit.setdefault(hit.target, hit.ts)
        enc.last_hit[hit.target] = hit.ts
        if actor.is_self or actor.name in self.group:
            enc.side_targets[hit.target] += hit.amount
        self.engaged.setdefault(hit.target, hit.ts)
        if hit.target in self.npc_names:
            enc.target_names[hit.target] = self.npc_names[hit.target]
        elif hit.target in self.players:          # the other side's players (Abyss, PvP): never named
            enc.target_names.setdefault(hit.target, "Jugador enemigo")
        if hit.target in self.max_hp:
            enc.target_max_hp[hit.target] = self.max_hp[hit.target]
        if hit.target in self.npc_ids:
            enc.target_npc[hit.target] = self.npc_ids[hit.target]
        if hit.target in self.bosses:
            enc.target_boss[hit.target] = self.bosses[hit.target]
        return finished

    def _on_support(self, hit):
        """Damage taken (starts or extends a fight like a hit dealt), healing, shields, avoided hits and stagger
        (these only count inside a fight that is going on: healing out of combat is not a fight)."""
        enc = self.current
        live = enc is not None and not enc.ended and hit.ts - enc.last <= IDLE_END
        if hit.kind in ("taken", "avoid"):
            target = self.owners.get(hit.target, hit.target)
            if target not in self.players:
                return None
            enc, finished = self._encounter(hit.ts)
            actor = self._actor(enc, target)
            if hit.kind == "avoid":
                actor.avoided_hits += 1
                actor.evades += 1
                actor.avoided_skills[hit.skill] += 1
                return finished
            actor.taken += hit.amount
            actor.taken_hits += not hit.dot
            enemy = self.owners.get(hit.attacker, hit.attacker)
            enc.enemy_damage[enemy] += hit.amount
            self.engaged.setdefault(enemy, hit.ts)
            if enemy in self.npc_names:
                enc.target_names.setdefault(enemy, self.npc_names[enemy])
            if enemy in self.max_hp:
                enc.target_max_hp.setdefault(enemy, self.max_hp[enemy])
            if enemy in self.npc_ids:
                enc.target_npc.setdefault(enemy, self.npc_ids[enemy])
            if enemy in self.bosses:
                enc.target_boss.setdefault(enemy, self.bosses[enemy])
            actor.taken_max = max(actor.taken_max, hit.amount)
            actor.taken_from[self.owners.get(hit.attacker, hit.attacker)] += hit.amount
            actor.taken_skills[hit.skill] += hit.amount
            if hit.defense:
                actor.avoided_hits += 1
                actor.avoided_skills[hit.skill] += 1
                actor.parries += hit.defense == "parry"
                actor.blocks += hit.defense == "block"
                actor.endures += hit.defense == "endure"
                actor.avoided += hit.reduced
            return finished
        if not live:
            return None
        source = self.owners.get(hit.attacker, hit.attacker) if hit.attacker is not None else None
        if source in self.down and hit.kind in ("heal", "absorb"):
            self._back(source, hit.ts)
        if hit.kind == "heal":
            if source not in self.players:
                return None
            actor = self._actor(enc, source)
            effective = hit.amount - hit.overheal
            actor.healing += effective
            actor.overheal += hit.overheal
            actor.heal_hits += 1
            actor.heal_crits += hit.crit
            actor.heal_skills[hit.skill] += effective
            actor.heal_skill_hits[hit.skill] += 1
            actor.heal_to[hit.target] += effective
        elif hit.kind == "absorb":
            if hit.target in self.players:
                self._actor(enc, hit.target).absorbed += hit.amount
            if source in self.players:
                actor = self._actor(enc, source)
                actor.shielding += hit.amount
                actor.shield_skills[hit.skill] += hit.amount
                actor.shield_to[hit.target] += hit.amount
        elif hit.kind == "stagger":
            if source in self.players:
                actor = self._actor(enc, source)
                actor.stagger += hit.amount
                actor.stagger_skills[hit.skill] += hit.amount
        return None

    def on_death(self, eid):
        """Deaths do not cut the encounter (a dying add may not be the real fight); tick() closes it."""
        enc = self.current
        if enc and not enc.ended and (eid in enc.targets or eid in enc.enemy_damage):
            enc.dead.add(eid)
            enc.died_at.setdefault(eid, max(enc.last, enc.last_hit.get(eid, 0)))
        elif enc and not enc.ended and eid in self.players and time.time() - enc.last <= IDLE_END:
            self._actor(enc, eid).deaths += 1       # a player of the fight died
        if eid in self.players:
            self.down[eid] = time.time()
        self.engaged.pop(eid, None)
        return None

    def on_cast(self, caster, skill, ts):
        """A cast that matters for revives: 8047 (got up by themselves) or a healer's revive skill."""
        caster = self.owners.get(caster, caster)
        if skill == SELF_REVIVE:
            self.down.pop(caster, None)
        elif skill // 10000 in REVIVE_SKILLS:
            self.res_casts.append((ts, caster, skill // 10000))

    def _back(self, eid, ts):
        """A dead player acted again: revived. -> the healer credited, if a revive skill explains it."""
        died = self.down.pop(eid, None)
        if died is None:
            return
        reviver = None
        for at, caster, base in reversed(self.res_casts):
            if caster == eid:
                continue
            if died <= at <= ts and ts - at <= REVIVE_WINDOW:
                reviver = caster
                break
            if base == 1727 and ts - died <= 2 and died - SALVATION_WINDOW <= at < died:
                reviver = caster
                break
        enc = self.current
        if reviver is not None and reviver in self.players and enc is not None and not enc.ended:
            self._actor(enc, reviver).revives += 1
            self._actor(enc, eid).revived += 1

    def tick(self, now=None):
        """End the current encounter once it has been idle long enough; returns it when it ends."""
        enc = self.current
        if not enc or enc.ended:
            return None
        idle = (now or time.time()) - enc.last
        if idle > IDLE_END or (enc.main_target_eid in enc.dead and idle > AFTER_KILL_END):
            return self._finish()
        return None

    def _finish(self):
        enc = self.current
        enc.ended = True
        enc.killed = enc.main_target_eid in enc.dead
        enc.group = set(self.group)
        if not enc.total:
            return enc                   # nobody dealt damage (a monster hit you while passing by): not a fight
        self.recent.appendleft(enc)
        me = enc.actors.get(self.self_eid)
        if me and me.damage:
            self.history.insert(0, EncounterSummary(enc.last, enc.main_target or "Pelea", enc.duration, me.damage,
                                                    enc.dps(me), me.damage / max(1, enc.total), enc.killed))
            del self.history[HISTORY_MAX:]
        return enc
