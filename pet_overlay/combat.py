"""DPS meter model: encounters built from damage events, per-player stats, encounter history.

Only players (you and the people fighting with you) are ranked; monsters hitting you are ignored, and
summons count for their owner. Encounters start with the first hit and end after IDLE_END seconds without
damage or when the main target dies. The history keeps only your own numbers; the last few full encounters
stay in memory (never on disk) for the details window.

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
RECENT_MAX = 5          # full encounters kept in memory for the details window
PARTY_SIZE = 5          # a group roster larger than this is a force (up to 4 parties)
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

    @property
    def class_name(self):
        """The class, from the prefix of the skills that did the damage (11 Gladiator ... 18 Chanter)."""
        prefixes = Counter()
        for skill, amount in self.skills.items():
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
class Encounter:
    start: float
    last: float
    actors: dict = field(default_factory=dict)       # eid -> ActorStats
    targets: Counter = field(default_factory=Counter)  # target eid -> damage taken
    target_names: dict = field(default_factory=dict)
    dead: set = field(default_factory=set)            # targets that died during the encounter
    hp: dict = field(default_factory=dict)            # target eid -> [current hp, max hp]
    hp_start: dict = field(default_factory=dict)      # target eid -> first hp seen during the encounter
    killed: bool = False
    ended: bool = False

    def contribution(self, group_names):
        """Contribution to the main target, or None when nobody hit it."""
        boss = self.main_target_eid
        dealt = [(a, a.by_target[boss]) for a in self.actors.values() if a.by_target[boss]]
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

    def boss_hp_ratio(self):
        cur_max = self.hp.get(self.main_target_eid)
        if not cur_max or not cur_max[1]:
            return None
        return max(0.0, min(1.0, cur_max[0] / cur_max[1]))

    @property
    def main_target_eid(self):
        return self.targets.most_common(1)[0][0] if self.targets else None

    @property
    def duration(self):
        return max(1.0, self.last - self.start)

    @property
    def total(self):
        return sum(a.damage for a in self.actors.values())

    @property
    def main_target(self):
        if not self.targets:
            return None
        return self.target_names.get(self.targets.most_common(1)[0][0])

    def dps(self, actor):
        return actor.damage / self.duration

    def ranking(self):
        return sorted(self.actors.values(), key=lambda a: -a.damage)


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
        self.group = set()       # names of your party / force members (from the group roster)
        self.self_eid = None
        self.current = None
        self.history = []
        self.recent = deque(maxlen=RECENT_MAX)   # finished encounters (memory only) for the details window

    # --- world knowledge ---------------------------------------------------------------------
    def set_self(self, eid, name):
        self.self_eid = eid
        self.players[eid] = name

    def add_player(self, eid, name):
        self.players[eid] = name

    def add_summon(self, eid, owner):
        self.owners[eid] = owner

    def add_npc(self, eid, name, max_hp=None):
        if not name:
            name = f"Jefe ({max_hp // 1000}K HP)" if max_hp and max_hp >= 300_000 else "Enemigo"
        self.npc_names[eid] = name
        if max_hp:
            self.max_hp[eid] = max_hp

    def set_group(self, names):
        self.group = set(names)

    def on_hp(self, eid, hp):
        enc = self.current
        if enc and not enc.ended and eid in enc.targets:
            enc.hp[eid] = [hp, self.max_hp.get(eid) or max(hp, enc.hp.get(eid, [0, 0])[1])]
            enc.hp_start.setdefault(eid, hp)

    # --- combat ----------------------------------------------------------------------------------
    def on_hit(self, hit):
        """Returns a finished Encounter if this hit started a new one after the previous ended."""
        attacker = self.owners.get(hit.attacker, hit.attacker)
        if attacker not in self.players or hit.amount <= 0:
            return None
        if hit.target in self.players or self.owners.get(hit.target) in self.players:
            return None          # heals and buffs on players are not damage
        finished = None
        enc = self.current
        if enc is None or enc.ended or hit.ts - enc.last > IDLE_END:
            finished = self._finish() if enc and not enc.ended else None
            enc = self.current = Encounter(start=hit.ts, last=hit.ts)
        enc.last = hit.ts
        actor = enc.actors.get(attacker)
        if actor is None:
            actor = enc.actors[attacker] = ActorStats(attacker, self.players[attacker], attacker == self.self_eid)
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
        if hit.target in self.npc_names:
            enc.target_names[hit.target] = self.npc_names[hit.target]
        return finished

    def on_death(self, eid):
        """Deaths do not cut the encounter (a dying add may not be the real fight); tick() closes it."""
        enc = self.current
        if enc and not enc.ended and eid in enc.targets:
            enc.dead.add(eid)
        return None

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
        self.recent.appendleft(enc)
        me = enc.actors.get(self.self_eid)
        if me:
            self.history.insert(0, EncounterSummary(enc.last, enc.main_target or "Pelea", enc.duration, me.damage,
                                                    enc.dps(me), me.damage / max(1, enc.total), enc.killed))
            del self.history[HISTORY_MAX:]
        return enc
