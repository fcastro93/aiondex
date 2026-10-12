"""Your crafting profession levels, from the game's own messages (2026-10-12, one relog and two Curry Meat Stirfry).

- 3336 (your character, at login) holds the list: u8 n | n x {u8 profession id | u8 level | u8 ? | u64 experience in
  the level | u16 ?}, ids 1..n. Cooking is id 5: level 1 with 0 experience, then 7 after a failed craft.
- 7c8d after each craft: u8 profession id | u64 experience in the level | u64 gained (7 for a fail, 26 for a
  success; 7 + 26 -> level 2 with 7/28).
- 6f8d when a craft is done: u8 0 | u8 profession id | varint ? | u32 recipe id | u8 1 success / 0 fail | ... The
  recipe tells which profession an id is (only Cooking = 5 is known beforehand).
"""
import struct

from .protocol import read_varint

EXP_OPCODE = bytes.fromhex("7c8d")
CRAFT_OPCODE = bytes.fromhex("6f8d")
KNOWN_IDS = {5: "Cooking"}          # the others are learnt from the first craft of each profession
RECORD = 13


def parse_levels(body):
    """3336 -> {profession id: (level, experience)} or {} (the block is found by its shape: ids 1..n in order)."""
    for p in range(0, min(len(body) - RECORD, 1200)):
        n = body[p]
        if not 3 <= n <= 10 or p + 1 + n * RECORD > len(body):
            continue
        out = {}
        for k in range(n):
            r = p + 1 + k * RECORD
            pid, level = body[r], body[r + 1]
            exp = struct.unpack_from("<Q", body, r + 3)[0]
            if pid != k + 1 or not 1 <= level <= 200 or exp > 10 ** 9:
                break
            out[pid] = (level, exp)
        if len(out) == n:
            return out
    return {}


def parse_exp(body):
    """7c8d -> (profession id, experience in the level, gained) or None."""
    if len(body) < 17:
        return None
    exp, gained = struct.unpack_from("<QQ", body, 1)
    if not 1 <= body[0] <= 10 or exp > 10 ** 9 or gained > 10 ** 9:
        return None
    return body[0], exp, gained


def parse_craft(body):
    """6f8d -> (profession id, recipe id, success) or None."""
    if len(body) < 8 or not 1 <= body[1] <= 10:
        return None
    _, p = read_varint(body, 2)
    if p is None or p + 5 > len(body):
        return None
    recipe = struct.unpack_from("<I", body, p)[0]
    return body[1], recipe, body[p + 4] == 1
