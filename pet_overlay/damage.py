"""Combat messages for the DPS meter (validated hit-by-hit against LOXIN fight logs: 431/431 hits).

0438 direct hit or heal:
  varint target | u8 f1 | u8 f2 | varint attacker | u32 skill | u8 seq | u8 kind (2 normal, 3 critical)
  | [f1&0x02: u8 quality (0x04 perfect, 0x08 double) | varint absorbed | u8 direction (1 = back)]
  | u32 effect | u32 hit no | u16 ? | [f1&0x04: varint amount (total, sub-hits included)]
  | [f1&0x10 and not f1&0x02: 1 byte] | [f1&0x20: u8 n + n varint sub-hits] | [f1&0x40, f2&0x01: u8 n + n u32]
  | u8 | u8 0 | [f2&0x04: varint]
  Heals use the same message; they are the ones aimed at the caster or a friendly player.
0538 periodic tick: varint target | u8 kind | varint source | varint instance | (kind&0x30: other layout)
  | u32 effect (skill = effect // 100) | [kind&3: varint amount] | ... ; kind 0x0A = damage over time.
048d death: varint entity | varint killer entity | ...
"""
import struct
import time

from .combat import Hit
from .protocol import read_varint

DIRECT_OPCODE = bytes.fromhex("0438")
TICK_OPCODE = bytes.fromhex("0538")
DEATH_OPCODE = bytes.fromhex("048d")
DAMAGE_OPCODES = (DIRECT_OPCODE, TICK_OPCODE)
READY = True


def parse_direct(b):
    target, p = read_varint(b, 0)
    f1, f2 = b[p], b[p + 1]
    attacker, p = read_varint(b, p + 2)
    skill = struct.unpack_from("<I", b, p)[0]
    p += 4
    kind = b[p + 1]
    p += 2
    quality, direction = 0, None
    if f1 & 0x02:
        quality = b[p]
        _, p = read_varint(b, p + 1)
        direction = b[p]
        p += 1
    p += 10                                      # u32 effect, u32 hit no, u16 ?
    if not f1 & 0x04:
        return None                              # no amount (miss / no-damage variant)
    amount, p = read_varint(b, p)
    return Hit(ts=time.time(), attacker=attacker, target=target, amount=amount, skill=skill,
               crit=kind == 3, back=direction == 1, perfect=bool(quality & 0x04), double=bool(quality & 0x08))


def parse_tick(b):
    target, p = read_varint(b, 0)
    kind = b[p]
    source, p = read_varint(b, p + 1)
    _, p = read_varint(b, p)
    if kind != 0x0A:                             # only damage-over-time ticks count as damage
        return None
    effect = struct.unpack_from("<I", b, p)[0]
    amount, _ = read_varint(b, p + 4)
    return Hit(ts=time.time(), attacker=source, target=target, amount=amount, skill=effect // 100, dot=True)


def parse_damage(op, body):
    """-> [Hit] (empty if the message is not damage or does not parse)."""
    try:
        hit = parse_direct(body) if op == DIRECT_OPCODE else parse_tick(body)
    except (IndexError, struct.error):
        return []
    return [hit] if hit and hit.amount and hit.amount > 0 else []       # amount None: truncated varint


STATS_OPCODE = bytes.fromhex("008d")


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
