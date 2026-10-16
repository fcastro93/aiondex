"""Where the player is: map entry (2136) and nearby NPC spawns/despawns (4136/4236).

2136 map entry:  u32 seq | u32 map_id | u32 ? | u32 0 | f32 x, y, z | f32 heading ...   (seq 1 = login)
4136 NPC spawn:  varint entity | u16 flags | u8 has_name [u8 len, name] | u32 npc_id | u8 move | u8 | f32 x, y, z |
                 f32 heading | [move & 8: 12 bytes, where it is going] | 3 bytes | varint HP | varint max HP ...
4236 despawn:    varint entity | 00 | u8 reason (1 out of range, 3 dead, 7 summon expired)
Validated against LOXIN fight logs (entity -> npc ids, deaths) on the user's captures.
"""
import struct
import time
from collections import Counter

from .protocol import read_varint

MAP_ENTER_OPCODE = bytes.fromhex("2136")
SPAWN_OPCODE = bytes.fromhex("4136")
DESPAWN_OPCODE = bytes.fromhex("4236")
SUMMON_IDS = range(2_920_000, 2_930_000)
MIN_VOTES = 2
MAX_UNKNOWN = 500       # unknown NPCs remembered for the Diagnóstico module


def parse_map_enter(body):
    if len(body) < 32:
        return None
    seq, map_id = struct.unpack_from("<II", body, 0)
    x, y, z = struct.unpack_from("<3f", body, 16)
    return seq, map_id, (x, y, z)


def parse_spawn(body):
    eid, p = read_varint(body, 0)
    if eid is None:
        return None
    p += 2
    if p + 1 >= len(body):
        return None
    p += 2 + body[p + 1] if body[p] else 1
    if p + 4 > len(body):
        return None
    return eid, struct.unpack_from("<I", body, p)[0]


def spawn_hp_at(body, p):
    """Where the HP varint of a 4136 is, from the offset of its npc id: a monster on the move carries 12 more bytes
    (2026-10-15: without them a walking Modified Lupyllini read 122,963,139 max HP for 119,700)."""
    return p + 4 + 2 + 12 + 4 + (12 if p + 4 < len(body) and body[p + 4] & 8 else 0) + 3


def parse_spawn_full(body):
    """4136 -> (entity id, npc id, owner name or None, max hp or None); summons carry their owner's name.
    After npc_id: u8 move | u8 | f32 x,y,z | f32 heading | [move & 8: 12 bytes] | 3 bytes | varint hp | varint max."""
    eid, p = read_varint(body, 0)
    if eid is None or p + 3 > len(body):
        return None
    p += 2
    owner = None
    if body[p]:
        n = body[p + 1]
        try:
            owner = body[p + 2:p + 2 + n].decode("utf-8")
        except UnicodeDecodeError:
            return None
        p += 2 + n
    else:
        p += 1
    if p + 4 > len(body):
        return None
    npc_id = struct.unpack_from("<I", body, p)[0]
    max_hp = None
    q = spawn_hp_at(body, p)
    if q < len(body):
        _, q = read_varint(body, q)
        max_hp, _ = read_varint(body, q) if q < len(body) else (None, q)
    return eid, npc_id, owner, max_hp


def game_map_faction(map_id):
    """Faction of an open-world map id (1010 Verteron, 1110 Altgard; Seal_Verteron 3100NN, Seal_Altgard 3200NN)."""
    if 1000 <= map_id < 1100 or 310000 <= map_id < 311000:
        return "elyos"
    if 1100 <= map_id < 1200 or 320000 <= map_id < 321000:
        return "asmodian"
    return None


class WorldTracker:
    """Runs on the capture thread; returns events for the UI when the map or the zone changes."""

    def __init__(self, npc_zones, zone_faction):
        self.npc_zones = npc_zones          # npc id -> [zone slugs]
        self.zone_faction = zone_faction    # zone slug -> faction
        self.map_id = None
        self.fallback_side = None           # the character's side, used until a map entry tells where we are
        self.nearby = {}                    # entity id -> npc id (only npcs we can place in a zone)
        self.unknown = {}                   # entity id -> npc id of nearby npcs the database cannot place
        self.spawned = {}                   # entity id -> when it appeared (both kinds)
        self.votes = Counter()              # zone slug -> votes at the last change
        self.zone = None

    def on_message(self, op, body):
        if op == MAP_ENTER_OPCODE:
            parsed = parse_map_enter(body)
            if parsed:
                seq, map_id, pos = parsed
                self.map_id = map_id
                self.nearby.clear()
                self.unknown.clear()
                self.spawned.clear()
                self.votes = Counter()
                self.zone = None
                return ("map", (seq, map_id, pos))
        elif op == SPAWN_OPCODE:
            parsed = parse_spawn(body)
            if parsed and parsed[1] not in SUMMON_IDS:
                self.spawned[parsed[0]] = time.time()
                if parsed[1] in self.npc_zones:
                    self.nearby[parsed[0]] = parsed[1]
                    return self._zone_event()
                self.unknown[parsed[0]] = parsed[1]
                if len(self.unknown) > MAX_UNKNOWN:
                    self.spawned.pop(next(iter(self.unknown)), None)
                    del self.unknown[next(iter(self.unknown))]
        elif op == DESPAWN_OPCODE:
            eid, _ = read_varint(body, 0)
            self.unknown.pop(eid, None)
            self.spawned.pop(eid, None)
            if self.nearby.pop(eid, None) is not None:
                return self._zone_event()
        return None

    def _zone_event(self):
        side = game_map_faction(self.map_id) if self.map_id is not None else self.fallback_side
        votes = Counter()
        for npc in self.nearby.values():
            zones = [z for z in self.npc_zones[npc] if not side or self.zone_faction.get(z) in (side, "abyss", None)]
            for z in zones:
                votes[z] += 1 / len(zones)
        self.votes = votes
        best = votes.most_common(1)
        zone = best[0][0] if best and best[0][1] >= MIN_VOTES else self.zone
        if zone != self.zone:
            self.zone = zone
            return ("zone", zone)
        return None

    def snapshot(self):
        """Copy of what the tracker sees, safe to call from another thread:
        {"map_id", "nearby" {entity: npc}, "unknown" {entity: npc}, "votes", "spawned" {entity: time}}."""
        for _ in range(5):
            try:
                return {"map_id": self.map_id, "nearby": dict(self.nearby), "unknown": dict(self.unknown),
                        "votes": Counter(self.votes), "spawned": dict(self.spawned)}
            except RuntimeError:        # changed size while copying: try again
                continue
        return {"map_id": self.map_id, "nearby": {}, "unknown": {}, "votes": Counter(), "spawned": {}}

    def reset(self):
        """Forget the nearby NPCs and the zone (they may be from before a teleport AionDex did not see)."""
        self.nearby.clear()
        self.unknown.clear()
        self.spawned.clear()
        self.votes = Counter()
        self.zone = None
