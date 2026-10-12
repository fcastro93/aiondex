"""Combat contribution from the game's messages: damage dealt and taken, healing, shields, avoided hits and stagger.

Decoded from the captures and checked against the HP the server reports after each event and against LOXIN's fight
logs (hit counts, damage taken, parries and death recaps match; LOXIN's healing does not: it adds the whole pool of
a heal-over-time and MP/buff values, so healing is checked against the HP changes instead).

0438 direct effect (hit, heal, status):
  varint target | u8 f1 | u8 f2 | varint source | u32 skill | u8 seq | u8 kind (1 evaded/resisted, 2 normal,
  3 critical, 6 status) | [f1&0x02: u8 quality | varint reduced | u8 direction (1 = back)]
  | u32 effect (skill*100 + idx) | u32 hit no | varint power scalar | [f1&0x04: varint amount]
  | [f1&0x10 and not f1&0x02: u8] | [f1&0x20: u8 n + n varint sub-hits] | [f1&0x40: u8 n + n u32 absorbing effects]
  | [f2&0x01: u8 n + n u32 statuses] | u8 target index | u8 0 | [f2&0x04: varint] | [f2&0x10: optional u8]
  quality: 0x01 guard, 0x02 parry, 0x04 perfect, 0x08 double, 0x10 block, 0x20 endure (reduced = damage removed),
  0x40 nullified. Heals carry no quality block and go between allied players.
0538 periodic / pool effect:
  varint target | u8 kind | varint source | varint instance | u32 effect | [kind&1: varint pool]
  | [kind&2: varint applied] | [kind&4: u8 n + n u32] | [kind&8: u32 skill]   (kind&0x30: another layout)
  0x09 pool created (heal over time or shield; source = caster) · 0x0b pool used (applied = healed now, or absorbed
  when the source is not the caster) · 0x0a applied only (DoT, HoT tick, or the hit that breaks a shield) · 0x0e DoT
  partly absorbed by an enemy shield.
1b92 player HP: varint entity | varint hp | varint max hp ...   (max HP of the people around you)
05e0 stagger gauge of a boss: varint npc | 03 01 | u32 max | u32 value | 02, or varint npc | 00 03 02 when it breaks.
  No attacker field: each drop is split over the player hits on that boss in the same burst.
4536 / 3336 player / own character: ... u32 job | u8 faction (1 / 2). Heals never cross factions; hits do (PvP).
"""
import struct
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .character import parse_character
from .combat import Hit, parse_player
from .protocol import read_varint
from .roster import parse_roster
from .world import parse_spawn_full

OP = {name: bytes.fromhex(code) for name, code in (
    ("effect", "0438"), ("periodic", "0538"), ("party_hp", "1b92"), ("stagger", "05e0"), ("stats", "008d"),
    ("player", "4536"), ("self", "3336"), ("spawn", "4136"), ("despawn", "4236"), ("roster", "0297"))}
OPCODES = frozenset(OP.values())

Q_GUARD, Q_PARRY, Q_PERFECT, Q_DOUBLE, Q_BLOCK, Q_ENDURE, Q_NULLIFY = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40
KIND_EVADED, KIND_CRIT = 1, 3
T_POOL, T_APPLIED, T_ABSORBERS, T_SKILL, T_OTHER = 0x01, 0x02, 0x04, 0x08, 0x30
SYSTEM_SKILL_MAX = 2_000_000        # below this: system effects (rest, full restore on login), not someone's heal
BURST = 0.003                       # messages this close together came in the same server tick

# Effects of the heal shape that change something else (MP, rage, flight time, a buff's value), learned from HP
# changes: key = (skill // 10000, effect % 100) for class skills, (skill, idx) for items and spirit attacks.
NON_HP_EFFECTS = {
    (1101, 11), (1111, 11), (1111, 12), (1126, 14), (1136, 11), (1211, 11), (1211, 12), (1308, 11), (1308, 12),
    (1336, 11), (1524, 14), (1576, 11), (1817, 13), (1821, 11), (4000301, 11), (16990002, 11), (16990004, 12),
    (2010301, 11), (2010302, 11), (1243, 11), (1814, 11), (1814, 12), (1606, 11), (1735, 12), (1732, 22),
    (1820, 14), (1326, 14), (1426, 13),
}


def effect_key(skill, idx):
    return (skill // 10000, idx) if skill >= 10_000_000 and skill // 10000 != 1699 else (skill, idx)


def is_class_skill(skill):
    s = str(skill)
    return len(s) == 8 and "11" <= s[:2] <= "18"


@dataclass
class Effect:
    target: int
    source: int
    skill: int
    kind: int
    effect: int
    f1: int
    quality: int = 0
    reduced: int = 0
    direction: int | None = None
    amount: int | None = None
    absorbers: list = field(default_factory=list)


def parse_effect(b):
    """0438 -> Effect, or None."""
    try:
        target, p = read_varint(b, 0)
        f1, f2 = b[p], b[p + 1]
        source, p = read_varint(b, p + 2)
        skill = struct.unpack_from("<I", b, p)[0]
        kind = b[p + 5]
        p += 6
        e = Effect(target, source, skill, kind, 0, f1)
        if f1 & 0x02:
            e.quality = b[p]
            e.reduced, p = read_varint(b, p + 1)
            e.direction = b[p]
            p += 1
        e.effect = struct.unpack_from("<I", b, p)[0]
        _, p = read_varint(b, p + 8)            # power scalar: a varint (3 bytes from 16384 on)
        if f1 & 0x04:
            e.amount, p = read_varint(b, p)
        if f1 & 0x10 and not f1 & 0x02:
            p += 1
        if f1 & 0x20:
            n = b[p]
            p += 1
            for _ in range(n):
                _, p = read_varint(b, p)
        if f1 & 0x40:
            n = b[p]
            e.absorbers = list(struct.unpack_from(f"<{n}I", b, p + 1))
        if None in (target, source) or (f1 & 0x04 and e.amount is None):
            return None
        return e
    except (IndexError, struct.error, TypeError):
        return None


@dataclass
class Periodic:
    target: int
    kind: int
    source: int
    effect: int
    pool: int | None = None
    applied: int | None = None

    @property
    def skill(self):
        return self.effect // 100


def parse_periodic(b):
    """0538 -> Periodic, or None (also for the layout of kind&0x30, which carries no amounts we use)."""
    try:
        target, p = read_varint(b, 0)
        kind = b[p]
        source, p = read_varint(b, p + 1)
        _, p = read_varint(b, p)                # instance
        effect = struct.unpack_from("<I", b, p)[0]
        p += 4
        if kind & T_OTHER or None in (target, source):
            return None
        tick = Periodic(target, kind, source, effect)
        if kind & T_POOL:
            tick.pool, p = read_varint(b, p)
        if kind & T_APPLIED:
            tick.applied, p = read_varint(b, p)
        return tick
    except (IndexError, struct.error, TypeError):
        return None


def parse_party_hp(b):
    """1b92 -> (entity, hp, max hp) or None."""
    try:
        eid, p = read_varint(b, 0)
        hp, p = read_varint(b, p)
        mx, _ = read_varint(b, p)
    except (IndexError, TypeError):
        return None
    return (eid, hp, mx) if None not in (eid, hp, mx) and 0 < mx and hp <= mx else None


def parse_stagger(b):
    """05e0 -> (npc, value) with value None when the gauge broke, or None."""
    try:
        eid, p = read_varint(b, 0)
        rest = b[p:]
        if len(rest) == 11 and rest[0] == 3:
            return eid, struct.unpack_from("<I", rest, 6)[0]
        if rest[:2] == b"\x00\x03":
            return eid, None
    except (IndexError, struct.error, TypeError):
        pass
    return None


def parse_stats(b):
    """008d -> (entity, hp or None, max hp or None): u64 stat 0 = HP, u64 stat 7 = max HP (players)."""
    try:
        eid, p = read_varint(b, 0)
        mask = b[p]
        p += 1
        if mask & 1:
            p += 1 + 5 * b[p]
        hp = mx = None
        if mask & 2:
            n = b[p]
            p += 1
            for _ in range(n):
                value = struct.unpack_from("<Q", b, p + 1)[0]
                if b[p] == 0:
                    hp = value
                elif b[p] == 7:
                    mx = value
                p += 9
    except (IndexError, struct.error, TypeError):
        return None
    return (eid, hp, mx) if eid is not None and (hp is not None or mx is not None) else None


def _faction_after(body, name):
    """The faction byte of a 4536 / 3336 record: 4 bytes (job) after the name, 6 for 3336 (server + job)."""
    raw = name.encode("utf-8")
    i = body.find(bytes([len(raw)]) + raw)
    return i + 1 + len(raw) if i >= 0 else None


class CombatFeed:
    """Turns the combat messages into contribution events ([combat.Hit] with a kind), in message order.

    Runs on the capture thread: it keeps who is a player (and of which faction), summon owners, your group, every
    player's HP (for overheal), the shields and heals-over-time placed on each player, and the bosses' stagger
    gauges. Event kinds: damage (a player hits an enemy), taken (an enemy hits a player), heal, absorb (a shield
    took part of a hit; attacker = who cast the shield), avoid (evaded / resisted), stagger (gauge points)."""

    def __init__(self):
        self.players = {}           # eid -> name
        self.faction = {}           # eid -> 1 / 2
        self.owners = {}            # summon eid -> owner eid
        self.self_eid = None
        self.group = set()          # names of your party / force
        self.hp = {}                # eid -> [hp, max hp] of players
        self.pools = defaultdict(dict)   # target -> {effect: caster} of the shields / heals over time on it
        self.gauge = {}             # npc -> stagger gauge value
        self.points = defaultdict(Counter)   # skill -> Counter(gauge points it took when alone in its burst)
        self._burst_at = None
        self._burst_hits = defaultdict(list)     # npc -> [(player, skill)] hits of this burst
        self._last_hits = defaultdict(list)
        self._burst_drop = Counter()             # npc -> gauge points lost in this burst

    # --- who is who ---
    def owner(self, eid):
        return self.owners.get(eid, eid)

    def player_side(self, eid):
        """A player or a player's summon."""
        return self.owner(eid) in self.players

    def in_group(self, eid):
        o = self.owner(eid)
        return o == self.self_eid or self.players.get(o) in self.group

    def hostile(self, a, b):
        """Players of different factions, unless both are in your group (dungeon groups can mix factions)."""
        if self.in_group(a) and self.in_group(b):
            return False
        fa, fb = self.faction.get(self.owner(a)), self.faction.get(self.owner(b))
        return fa is not None and fb is not None and fa != fb

    def allied(self, a, b):
        return self.player_side(a) and self.player_side(b) and not self.hostile(a, b)

    # --- messages ---
    def feed(self, op, body):
        """One server message -> [Hit] (often empty)."""
        now = time.time()
        out = []
        if self._burst_at is None or now - self._burst_at > BURST:
            out += self._end_burst(now)
            self._burst_at = now
        handler = self._handlers.get(op)
        if handler:
            out += handler(self, body, now) or []
        return out

    def _on_player(self, body, now):
        player = parse_player(body)
        if player:
            eid, name, _ = player
            self.players[eid] = name
            at = _faction_after(body, name)
            if at is not None and at + 4 < len(body) and body[at + 4] in (1, 2):
                self.faction[eid] = body[at + 4]

    def _on_self(self, body, now):
        info = parse_character(body)
        if info:
            self.self_eid = info.entity_id
            self.players[info.entity_id] = info.name
            at = _faction_after(body, info.name)
            if at is not None and at + 6 < len(body) and body[at + 6] in (1, 2):
                self.faction[info.entity_id] = body[at + 6]

    def _on_spawn(self, body, now):
        from .damage import summon_owner
        spawn = parse_spawn_full(body)
        if not spawn:
            return
        eid, owner = spawn[0], summon_owner(body)
        if not spawn[2] and owner not in self.players:
            owner = None                               # summons without their owner's name: only a known player's
        self.pools.pop(eid, None)
        if owner is not None and owner != eid:
            self.owners[eid] = owner
        elif eid != self.self_eid:
            self.players.pop(eid, None)            # the id now belongs to a monster
            self.owners.pop(eid, None)

    def _on_despawn(self, body, now):
        eid, _ = read_varint(body, 0)
        if eid is not None and eid != self.self_eid:
            self.hp.pop(eid, None)
            self.pools.pop(eid, None)
            self.gauge.pop(eid, None)

    def _on_roster(self, body, now):
        members = parse_roster(body)
        if members:
            self.group = {m[0] for m in members}

    def _on_stats(self, body, now):
        stats = parse_stats(body)
        if stats:
            eid, hp, mx = stats
            state = self.hp.setdefault(eid, [None, None])
            if mx is not None:
                state[1] = mx
            if hp is not None:
                state[0] = hp

    def _on_party_hp(self, body, now):
        snap = parse_party_hp(body)
        if snap:
            self.hp[snap[0]] = [snap[1], snap[2]]

    def _change_hp(self, eid, delta):
        """Apply a heal (+) or damage (-) to the HP we track; -> the HP actually gained (None if unknown)."""
        state = self.hp.get(eid)
        if not state or state[0] is None:
            return None
        hp, mx = state
        new = max(0, min(hp + delta, mx) if mx else hp + delta)
        state[0] = new
        return new - hp

    def _heal(self, healer, target, skill, amount, crit, now, tick=False):
        state = self.hp.get(target)
        if state and state[1] and amount > state[1]:
            return []                              # more than the target's max HP: a buff value, not HP
        gained = self._change_hp(target, amount)
        overheal = amount - gained if gained is not None and state[1] else 0
        return [Hit(now, healer, target, amount, skill=skill, crit=crit, dot=tick, kind="heal", overheal=overheal)]

    def _on_effect(self, body, now):
        e = parse_effect(body)
        if e is None:
            return []
        src, tgt = self.owner(e.source), e.target
        target_player = tgt in self.players
        if e.kind == KIND_EVADED and e.amount is None:
            if target_player and not self.allied(e.source, tgt):
                return [Hit(now, src, tgt, 1, skill=e.skill, kind="avoid", defense="evade")]
            return []
        if not e.amount:
            return []
        if not e.f1 & 0x02 and self.allied(e.source, tgt):
            # a heal (or an MP / buff value with the same shape): only on players, never system effects
            if not target_player or e.skill < SYSTEM_SKILL_MAX or \
                    effect_key(e.skill, e.effect % 100) in NON_HP_EFFECTS:
                return []
            return self._heal(src, tgt, e.skill, e.amount, e.kind == KIND_CRIT, now)
        q = e.quality
        hit = dict(skill=e.skill, crit=e.kind == KIND_CRIT, back=e.direction == 1, perfect=bool(q & Q_PERFECT),
                   double=bool(q & Q_DOUBLE))
        out = []
        if target_player and (not self.player_side(e.source) or self.hostile(e.source, tgt)):
            reduced = e.reduced if q & Q_ENDURE else 0
            defense = ("parry" if q & Q_PARRY else "block" if q & (Q_BLOCK | Q_GUARD | Q_NULLIFY) else
                       "endure" if q & Q_ENDURE else "")
            out.append(Hit(now, src, tgt, e.amount, kind="taken", defense=defense, reduced=reduced, **hit))
            self._change_hp(tgt, -(e.amount - reduced))
        if self.player_side(e.source) and (not target_player or self.hostile(e.source, tgt)) and \
                not (self.self_eid in self.faction and self.hostile(e.source, self.self_eid)):
            out.append(Hit(now, src, tgt, e.amount, **hit))
            if not target_player:
                self._burst_hits[tgt].append((src, e.skill))
        return out

    def _on_periodic(self, body, now):
        t = parse_periodic(body)
        if t is None:
            return []
        src, tgt = self.owner(t.source), t.target
        if t.kind & T_POOL and not t.kind & T_APPLIED:
            self.pools[tgt][t.effect] = src            # a shield or heal over time placed on someone
            return []
        if not t.kind & T_APPLIED or not t.applied:
            return []
        caster = self.pools.get(tgt, {}).get(t.effect)
        target_player = tgt in self.players
        if caster is not None and src != caster:
            # someone else's hit used up part of a shield (already inside that hit's amount)
            if not (target_player and self.player_side(caster)):
                return []                              # a monster's shield, or an effect that redirects damage
            self._change_hp(tgt, t.applied)            # the hit counted it as HP lost
            return [Hit(now, caster, tgt, t.applied, skill=t.skill, kind="absorb", by=src)]
        if target_player and self.allied(t.source, tgt):
            if t.skill < SYSTEM_SKILL_MAX:
                self._change_hp(tgt, t.applied)
                return []
            fs, ft = self.faction.get(src), self.faction.get(tgt)
            sure = (caster is not None or src == tgt or (self.in_group(src) and self.in_group(tgt))
                    or (fs is not None and fs == ft) or not is_class_skill(t.skill))
            if not sure:
                return []                              # player to player, sides unknown: could be PvP
            return self._heal(src, tgt, t.skill, t.applied, False, now, tick=True)
        if target_player and not self.player_side(t.source) and is_class_skill(t.skill):
            # an enemy hit used up a player's shield we did not see being cast
            self._change_hp(tgt, t.applied)
            return [Hit(now, None, tgt, t.applied, skill=t.skill, kind="absorb", by=src)]
        if target_player and not self.allied(t.source, tgt):
            self._change_hp(tgt, -t.applied)
            return [Hit(now, src, tgt, t.applied, skill=t.skill, dot=True, kind="taken")]
        if self.player_side(t.source) and not target_player and t.kind in (0x0A, 0x0E):
            return [Hit(now, src, tgt, t.applied, skill=t.skill, dot=True)]
        return []

    def _on_stagger(self, body, now):
        g = parse_stagger(body)
        if g is None:
            return []
        npc, value = g
        last = self.gauge.get(npc)
        if value is None:                              # broken: whatever was left counts
            if last:
                self._burst_drop[npc] += last
        elif last is not None and value < last:
            self._burst_drop[npc] += last - value
        self.gauge[npc] = value
        return []

    def _end_burst(self, now):
        """Split each boss's stagger drop of the last burst over the player hits on it (by each skill's usual
        points), else over the hits of the burst before."""
        out = []
        for npc, drop in self._burst_drop.items():
            hits = self._burst_hits.get(npc) or self._last_hits.get(npc) or []
            if not hits:
                continue
            if len(hits) == 1:
                self.points[hits[0][1] // 10000][drop] += 1
            weights = []
            for _, skill in hits:
                seen = self.points.get(skill // 10000)
                weights.append(seen.most_common(1)[0][0] if seen else 1)
            total = sum(weights) or 1
            for (player, skill), w in zip(hits, weights):
                out.append(Hit(now, player, npc, drop * w / total, skill=skill, kind="stagger"))
        self._last_hits = self._burst_hits
        self._burst_hits = defaultdict(list)
        self._burst_drop = Counter()
        return out

    _handlers = {OP["effect"]: _on_effect, OP["periodic"]: _on_periodic, OP["stagger"]: _on_stagger,
                 OP["stats"]: _on_stats, OP["party_hp"]: _on_party_hp, OP["player"]: _on_player,
                 OP["self"]: _on_self, OP["spawn"]: _on_spawn, OP["despawn"]: _on_despawn, OP["roster"]: _on_roster}
