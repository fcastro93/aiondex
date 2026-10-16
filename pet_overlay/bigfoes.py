"""The Abyss's world bosses and siege cores under the minimap (with the pets' Modo Focus, ui/focuspanel.py): their HP
always in view while you fight around them, so a PvP fight does not make you lose track of the boss.

Which: in the Abyss (Reshanta), the monsters the game shows you that are bosses (npc_db: Hero / Legend, or the
Tracker's field bosses), always; and the ones with at least BIG_HP of HP (a siege's core or gate, whatever its name)
once they take damage (a town's guards have 20M HP and are never hit). Never the objects a boss leaves (292xxxx: they
copy its HP). From the game:
- 4136 it comes into sight: entity | ... | npc | 2 bytes | f32 x, y, z | 4 + 3 bytes | varint HP | varint max HP;
- 008d its HP (damage.parse_hp), the 1a37 family where it moves, 048d it died, 4236 out of sight (1) or gone (3).
Out of sight it stays listed for KEEP seconds with its last HP ("fuera de vista hace N s"); dead, for DEAD_FOR.
One whose coming into sight was missed (AionDex started mid-fight) is taken from its HP updates once they are this
big (Executor Argo, a rift boss of Chaotic Lower Reshanta, 2026-10-15: 206M -> dead in 26 s); an absurd HP (the
first update of that fight read 3.1e21) is left out.
Runs on the capture thread; the overlay reads rows() under the lock."""
import math
import threading

from .damage import parse_hp
from .mobs import parse_spawn_at
from .protocol import read_varint
from .simulation import MOVE_AT, parse_move
from .world import spawn_hp_at

SPAWN = bytes.fromhex("4136")
GONE = bytes.fromhex("4236")
DEATH = bytes.fromhex("048d")
STATS = bytes.fromhex("008d")
ABYSS = {20, 22, 23}             # Reshanta's maps (2136 map ids)
BIG_HP = 1_000_000               # a monster with this much HP is a boss or a siege object (field mobs: ~120K)
KEEP = 300                       # seconds one out of sight stays listed
DEAD_FOR = 20
ROWS = 3
OBJECTS = range(2_920_000, 2_930_000)
SANE_HP = 100_000_000_000        # no monster has more


def parse_spawn_hp(body):
    """4136 -> (HP, max HP) or None (the two varints after the position)."""
    eid, p = read_varint(body, 0)
    if eid is None or p + 3 > len(body):
        return None
    p += 2
    p += 2 + body[p + 1] if body[p] else 1
    q = spawn_hp_at(body, p)
    if q >= len(body):
        return None
    hp, q = read_varint(body, q)
    if hp is None or q >= len(body):
        return None
    top, _ = read_varint(body, q)
    return (hp, top) if top else None


class Foe:
    __slots__ = ("eid", "npc", "name", "boss", "hp", "max_hp", "x", "y", "in_sight", "seen_at", "died_at")

    def __init__(self, eid, npc, name, boss, hp, max_hp, x, y, now):
        self.eid, self.npc, self.name, self.boss = eid, npc, name, boss
        self.hp, self.max_hp, self.x, self.y = hp, max_hp, x, y
        self.in_sight, self.seen_at, self.died_at = True, now, None


class BigFoes:
    def __init__(self):
        self.lock = threading.Lock()
        self.foes = {}                       # entity -> Foe
        self.game_map = None
        self.which = lambda npc, max_hp: None    # -> (name, boss) or None when it is not one (the Controller)
        self.name_of = lambda eid: None          # a name the rest of AionDex knows for an entity (the DPS)
        self.current_map = lambda: None          # the map AionDex knows you are on (whereami: kept across a restart
                                                 # or read from the minimap's title) until a map entry tells

    def on_map(self, game_map):
        with self.lock:
            self.game_map = game_map
            self.foes.clear()

    def in_abyss(self):
        return (self.game_map if self.game_map is not None else self.current_map()) in ABYSS

    def on_message(self, op, body, now):
        if op == SPAWN:
            if not self.in_abyss():
                return
            spawn, hp = parse_spawn_at(body), parse_spawn_hp(body)
            if not spawn or not hp:
                return
            eid, npc, x, y = spawn
            if npc in OBJECTS:
                return
            which = self.which(npc, hp[1])
            if which:
                with self.lock:
                    foe = self.foes.get(eid)
                    if foe is None:
                        self.foes[eid] = Foe(eid, npc, which[0], which[1], hp[0], hp[1], x, y, now)
                    else:
                        foe.hp, foe.max_hp, foe.x, foe.y, foe.in_sight, foe.seen_at = hp[0], hp[1], x, y, True, now
        elif op == STATS:
            if not self.in_abyss():
                return
            update = parse_hp(body)
            if not update or update[1] > SANE_HP:
                return
            eid, hp = update
            foe = self.foes.get(eid)
            if foe is None:
                if hp >= BIG_HP:                     # its coming into sight was missed: from its HP
                    with self.lock:
                        self.foes[eid] = Foe(eid, None, self.name_of(eid) or "Jefe", False, hp, hp, 0.0, 0.0, now)
                        self.foes[eid].x = self.foes[eid].y = None
                return
            if hp > foe.max_hp * 1.05 and foe.npc is not None:
                return
            foe.hp, foe.seen_at, foe.in_sight = hp, now, True
            foe.max_hp = max(foe.max_hp, hp)
        elif not self.foes:
            return
        elif op in MOVE_AT:
            move = parse_move(op, body)
            foe = self.foes.get(move[0]) if move else None
            if foe:
                foe.x, foe.y = move[1], move[2]
        elif op == DEATH:
            eid, _ = read_varint(body, 0)
            foe = self.foes.get(eid)
            if foe:
                foe.hp, foe.died_at = 0, now
        elif op == GONE:
            eid, p = read_varint(body, 0)
            foe = self.foes.get(eid)
            if foe and p + 1 < len(body):
                foe.in_sight = False
                foe.seen_at = now

    def rows(self, now, me=None):
        """[(name, hp, max hp, in sight, seconds since seen, dead, metres from you or None)] to show, the ones in
        sight first (closest first), then the latest out of sight."""
        out = []
        with self.lock:
            for eid, foe in list(self.foes.items()):
                if foe.died_at is not None and now - foe.died_at > DEAD_FOR or \
                        not foe.in_sight and now - foe.seen_at > KEEP:
                    del self.foes[eid]
                    continue
                if not foe.boss and foe.hp >= foe.max_hp and foe.died_at is None:
                    continue                         # a big one never hit: a guard, not a siege's core
                far = math.dist(me, (foe.x, foe.y)) if me and foe.x is not None else None
                out.append((foe.name, foe.hp, foe.max_hp, foe.in_sight, now - foe.seen_at, foe.died_at is not None,
                            far))
        out.sort(key=lambda r: (not r[3], r[6] if r[6] is not None else 0, r[4]))
        return out[:ROWS]
