"""Your crafting profession levels, from the game's own messages (2026-10-12, one relog and two Curry Meat Stirfry).

- 3336 (your character, at login) holds the list: u8 n | n x {u8 profession id | u8 level | u8 rank | u64 experience
  in the level | u16 ?}, ids 1..n. Cooking is id 5: level 1 with 0 experience, then 7 after a failed craft.
- Ranks: Novice 1-50, then (after a promotion quest) Professional 1-50. The client's recipes use one scale, 1-100
  (Professional 5 = 55), and so does AionDex underneath. The rank byte is read as 0 Novice / 1 Professional: every
  character seen so far was Novice (0); a level over 50 with rank 0 is taken as that one scale.
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
NOVICE_MAX = 50                     # levels per rank
RANKS = ("Novice", "Professional", "Expert")      # Expert: only in Korea so far (up to 70 there)


def level_text(value):
    """A proficiency as the game writes it: 'Novice 17', 'Professional 5' (from the one 1-100 scale)."""
    if value is None:
        return "—"
    value = max(1, int(value))
    rank = min((value - 1) // NOVICE_MAX, len(RANKS) - 1)
    return f"{RANKS[rank]} {value - rank * NOVICE_MAX}"


def parse_level(text, current=0):
    """'Novice 17' / 'pro 5' / 'p5' / '55' -> the one scale (0 = none), or None if it is not a level yet. A bare
    number up to 50 is a level of the rank you are at (`current`)."""
    import re
    t = text.strip().lower()
    if t in ("", "¿?", "?"):
        return 0
    m = re.fullmatch(r"(novice|novato|nov|n|professional|profesional|pro|p|expert|experto|e)?\s*\.?\s*(\d{1,3})", t)
    if not m:
        return None
    word, n = m.group(1), int(m.group(2))
    if word:
        rank = {"n": 0, "p": 1, "e": 2}[word[0]]
    elif n > NOVICE_MAX:
        return n if n <= NOVICE_MAX * len(RANKS) else None
    else:
        rank = (current - 1) // NOVICE_MAX if current else 0
    return rank * NOVICE_MAX + n if 1 <= n <= NOVICE_MAX else None


def parse_levels(body):
    """3336 -> {profession id: (level, experience)} or {} (the block is found by its shape: ids 1..n in order)."""
    for p in range(0, min(len(body) - RECORD, 1200)):
        n = body[p]
        if not 3 <= n <= 10 or p + 1 + n * RECORD > len(body):
            continue
        out = {}
        for k in range(n):
            r = p + 1 + k * RECORD
            pid, level, rank = body[r], body[r + 1], body[r + 2]
            exp = struct.unpack_from("<Q", body, r + 3)[0]
            if pid != k + 1 or not 1 <= level <= 200 or exp > 10 ** 9:
                break
            if level <= NOVICE_MAX and rank < len(RANKS):
                level += rank * NOVICE_MAX
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
