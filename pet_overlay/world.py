"""Where the player is: map entry (2136) and nearby NPC spawns/despawns (4136/4236).

2136 map entry:  u32 seq | u32 map_id | u32 ? | u32 0 | f32 x, y, z | f32 heading ...   (seq 1 = login)
4136 NPC spawn:  varint entity | u16 flags | u8 has_name [u8 len, name] | u32 npc_id | 2 bytes | f32 x, y, z ...
4236 despawn:    varint entity | 00 | u8 reason (1 out of range, 3 dead, 7 summon expired)
Validated against LOXIN fight logs (entity -> npc ids, deaths) on the user's captures.
"""
import struct
from collections import Counter

from .protocol import read_varint

MAP_ENTER_OPCODE = bytes.fromhex("2136")
SPAWN_OPCODE = bytes.fromhex("4136")
DESPAWN_OPCODE = bytes.fromhex("4236")
SUMMON_IDS = range(2_920_000, 2_930_000)
MIN_VOTES = 2


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


def parse_spawn_full(body):
    """4136 -> (entity id, npc id, owner name or None, max hp or None); summons carry their owner's name.
    After npc_id: 2 bytes | f32 x,y,z | 4 bytes | 3 bytes | varint hp | varint max hp."""
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
    q = p + 4 + 2 + 12 + 4 + 3
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
        self.nearby = {}                    # entity id -> npc id (only npcs we can place in a zone)
        self.zone = None

    def on_message(self, op, body):
        if op == MAP_ENTER_OPCODE:
            parsed = parse_map_enter(body)
            if parsed:
                seq, map_id, pos = parsed
                self.map_id = map_id
                self.nearby.clear()
                self.zone = None
                return ("map", (seq, map_id, pos))
        elif op == SPAWN_OPCODE:
            parsed = parse_spawn(body)
            if parsed and parsed[1] not in SUMMON_IDS and parsed[1] in self.npc_zones:
                self.nearby[parsed[0]] = parsed[1]
                return self._zone_event()
        elif op == DESPAWN_OPCODE:
            eid, _ = read_varint(body, 0)
            if self.nearby.pop(eid, None) is not None:
                return self._zone_event()
        return None

    def _zone_event(self):
        side = game_map_faction(self.map_id) if self.map_id is not None else None
        votes = Counter()
        for npc in self.nearby.values():
            zones = [z for z in self.npc_zones[npc] if not side or self.zone_faction.get(z) in (side, "abyss", None)]
            for z in zones:
                votes[z] += 1 / len(zones)
        best = votes.most_common(1)
        zone = best[0][0] if best and best[0][1] >= MIN_VOTES else self.zone
        if zone != self.zone:
            self.zone = zone
            return ("zone", zone)
        return None
