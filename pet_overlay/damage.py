"""Combat messages besides the hits: deaths, HP / MP updates, summon owners.

The hits themselves (0438 direct, 0538 periodic: damage, healing, shields...) are decoded by contribution.py.
048d death: varint entity | varint killer entity | ...
"""
import struct

from .protocol import read_varint

DIRECT_OPCODE = bytes.fromhex("0438")
TICK_OPCODE = bytes.fromhex("0538")
DEATH_OPCODE = bytes.fromhex("048d")
DAMAGE_OPCODES = (DIRECT_OPCODE, TICK_OPCODE)
READY = True
STATS_OPCODE = bytes.fromhex("008d")
MP_STAT, MAX_MP_STAT = 1, 8          # u32 stats: current and max MP (checked: casts lower it by their cost)


def parse_mp(body):
    """008d -> (entity, mp, max mp or None) when the update carries the MP, else None."""
    try:
        eid, p = read_varint(body, 0)
        mask = body[p]
        p += 1
        if eid is None or not mask & 1:
            return None
        stats = {}
        for _ in range(body[p]):
            stats[body[p + 1]] = struct.unpack_from("<I", body, p + 2)[0]
            p += 5
    except (IndexError, struct.error, TypeError):
        return None
    return (eid, stats[MP_STAT], stats.get(MAX_MP_STAT)) if MP_STAT in stats else None


def parse_hp(body):
    """008d stat update: varint entity | u8 mask | [mask&1: u8 n + n*(u8 id, u32)] | [mask&2: u8 n + n*(u8 id, u64)].
    Stat 0 of the u64 group is the current HP. -> (entity, hp) or None."""
    try:
        eid, p = read_varint(body, 0)
        mask = body[p]
        p += 1
        if mask & 1:
            p += 1 + 5 * body[p]
        if mask & 2:
            n = body[p]
            p += 1
            for _ in range(n):
                if body[p] == 0:
                    return eid, struct.unpack_from("<Q", body, p + 1)[0]
                p += 9
    except (IndexError, struct.error):
        pass
    return None


def summon_owner(body):
    """Owner entity of a summon from its 4136 spawn: the u32 right after the bytes 07 02 06."""
    i = body.find(b"\x07\x02\x06")
    if i > 0 and i + 7 <= len(body):
        return struct.unpack_from("<I", body, i + 3)[0]
    return None
