"""Your own skills from the game traffic (layouts reverse-engineered from own captures).

0138  reply to each skill you press: u8 flags | u16 result (0 accepted, 0x18xx rejected) | u32 skill | [flags&1: u8]
0238  skill cast start, any entity: varint caster | u8 flags | u32 skill | u8 cast id | u8 mode | varint target |
      f32 heading | [mode&2: f32 x, y, z] | varint speed | u8 | varint cooldown ms | [flags&4: u8 charges] |
      [flags&8: varint recharge ms]
0051  skill list at login: u8 n | n x {u8 flags | u32 id | u8 level | u8 learned | 4 x u8 bonus | u8 |
      [flags&2: varint cooldown left] | 4 bytes | [flags&4: u8 charges]}
8ce2  quickslot presets at login: header lists | u8 active preset | u8 n | n x {u8 type (3 = skill) | u8 preset |
      u8 slot | [type 3: u32 skill] | u8 m | m x u32 priority}
90e2  preset switched in the Skill window: 00 00 02 | u8 preset | 00 00; the game then sends that preset's skill list
      (0051) again: skill levels and specializations are per preset (2026-10-15).
93e2  a preset's whole bar, with each change made in the Skill window: u8 1 | u8 n | n x the 8ce2 slot record (type 3
      = a skill, 2 = empty); 0856 comes with it (the change itself).

Skill ids: class prefix (14 = Ranger...) + skill number + 4 variant digits. The variant digits of the equipped id
are the chosen specializations (14020340 = Snipe with specializations 3 and 4). Skill number 00 is the basic attack.
"""
import struct
from collections import Counter, deque
from dataclasses import dataclass, field

from .protocol import read_varint

REQUEST_OPCODE = bytes.fromhex("0138")
CAST_OPCODE = bytes.fromhex("0238")
SKILL_LIST_OPCODE = bytes.fromhex("0051")
PRESETS_OPCODE = bytes.fromhex("8ce2")
PRESET_SWITCH_OPCODE = bytes.fromhex("90e2")
BAR_UPDATE_OPCODE = bytes.fromhex("93e2")
REJECT_TOO_SOON = 0x1813    # the most common rejection: pressed 0.1-0.5 s after another cast, skill off cooldown
# quickslot -> key in the game's bar (checked against the game's bar screen); each slot holds up to 4 skills by
# priority. The bar shows them as 1-4 | 5-8 | Q E mouse | T.
SLOT_KEYS = {2: "1", 3: "2", 4: "3", 5: "4", 20: "5", 21: "6", 22: "7", 23: "8", 6: "Q", 7: "E", 0: "Ratón", 1: "T"}
BAR_SLOTS = list(SLOT_KEYS)                 # in the game's order, left to right


def base_skill(skill_id):
    """Skill ids carry level/variant digits; the base id groups them (14020340 -> 14020000)."""
    return int(skill_id) // 10000 * 10000


def is_class_skill(skill_id):
    return 11_000_000 <= int(skill_id) < 19_000_000


def is_basic_attack(skill_id):
    return is_class_skill(skill_id) and int(skill_id) // 10000 % 100 == 0


def specializations(skill_id):
    """Specialization numbers encoded in an equipped variant id (14020340 -> [3, 4])."""
    skill_id = int(skill_id)
    if skill_id % 10:
        return []                    # effect ids (…047) are not variants
    return sorted(int(d) for d in str(skill_id % 10000 // 10) if d != "0")


@dataclass
class Cast:
    caster: int
    skill: int
    cast_id: int
    cooldown_ms: int


@dataclass
class OwnSkill:
    base: int
    level: int
    learned: int
    bonus: int
    specs: list = field(default_factory=list)


@dataclass
class QuickSlot:
    slot: int
    skill: int
    priority: list                   # base skill ids, highest priority first


def caster_of(body):
    caster, _ = read_varint(body, 0)
    return caster


def parse_cast(body):
    try:
        caster, p = read_varint(body, 0)
        if caster is None:
            return None
        skill = struct.unpack_from("<I", body, p + 1)[0]
        cast_id, mode = body[p + 5], body[p + 6]
        _, q = read_varint(body, p + 7)
        q += 4 + (12 if mode & 2 else 0)
        _, q = read_varint(body, q)
        cooldown_ms, _ = read_varint(body, q + 1)
    except (IndexError, struct.error, TypeError):
        return None
    if cooldown_ms is None:
        return None
    return Cast(caster, skill, cast_id, cooldown_ms)


def parse_request(body):
    """(skill, result) of your own skill press; result 0 = accepted."""
    if len(body) < 7:
        return None
    result, skill = struct.unpack_from("<HI", body, 1)
    return skill, result


class OwnEntityFinder:
    """Your entity id before the game sends your character again (AionDex opened mid-session, or just after a
    teleport): 0138 only answers your own presses, and the 0238 cast of that same skill within half a second is
    yours. Checked on the captures: 2,532 of 2,534 matches were the right entity, so a few votes settle it."""
    WINDOW = 0.5
    VOTES = 3

    def __init__(self):
        self.reset()

    def reset(self):
        self.casts = deque(maxlen=64)       # (time, caster, skill)
        self.presses = deque(maxlen=16)     # (time, skill) accepted
        self.votes = Counter()

    def on_cast(self, now, caster, skill):
        self.casts.append((now, caster, skill))
        if any(s == skill and abs(now - t) <= self.WINDOW for t, s in self.presses):
            return self._vote(caster)
        return None

    def on_press(self, now, skill, result):
        if result != 0:
            return None
        self.presses.append((now, skill))
        caster = next((c for t, c, s in reversed(self.casts) if s == skill and abs(now - t) <= self.WINDOW), None)
        return self._vote(caster) if caster is not None else None

    def _vote(self, caster):
        self.votes[caster] += 1
        n = self.votes[caster]
        return caster if n >= self.VOTES and n >= 0.8 * sum(self.votes.values()) else None


def parse_skill_list(body):
    """Own skills by base id (highest level seen per base, specializations from the equipped variant)."""
    try:
        n, p = body[0], 1
        out = {}
        for _ in range(n):
            flags = body[p]
            sid = struct.unpack_from("<I", body, p + 1)[0]
            level, learned, *bonus = body[p + 5:p + 11]
            p += 12
            if flags & 2:
                _, p = read_varint(body, p)
                if p is None:
                    return None
            p += 4 + (1 if flags & 4 else 0)
            if not is_class_skill(sid) or is_basic_attack(sid):
                continue
            base = base_skill(sid)
            entry = out.get(base)
            if entry is None:
                entry = out[base] = OwnSkill(base, level, learned, sum(bonus))
            entry.level = max(entry.level, level)
            if sid != base and specializations(sid):
                entry.specs = specializations(sid)
        if p != len(body):
            return None
    except (IndexError, struct.error, TypeError):
        return None
    return out


def _skip_kv_list(body, p, value_size):
    return p + 1 + body[p] * (value_size + 1)


def parse_preset_switch(body):
    """90e2 -> the preset now on (1-3) or None."""
    if len(body) == 6 and body[2] == 2 and 1 <= body[3] <= 3:
        return body[3]
    return None


def _slot_records(body, p, n):
    """n slot records from p -> ({preset: [QuickSlot]}, end) (8ce2 / 93e2)."""
    presets = {}
    for _ in range(n):
        typ, preset, slot = body[p], body[p + 1], body[p + 2]
        p += 3
        skill = None
        if typ == 3:
            skill = struct.unpack_from("<I", body, p)[0]
            p += 4
        m = body[p]
        prio = list(struct.unpack_from(f"<{m}I", body, p + 1))
        p += 1 + 4 * m
        presets.setdefault(preset, [])
        if typ == 3:
            presets[preset].append(QuickSlot(slot, skill, [base_skill(s) for s in prio if s] or [base_skill(skill)]))
    return presets, p


def parse_bar_update(body):
    """93e2 -> (preset, [QuickSlot]) or None."""
    try:
        presets, end = _slot_records(body, 2, body[1])
    except (IndexError, struct.error):
        return None
    if end != len(body) or len(presets) != 1:
        return None
    return next(iter(presets.items()))


def parse_presets(body):
    """(active preset, {preset number: [QuickSlot]}) for the 3 presets (an unused preset has no slots)."""
    try:
        p = _skip_kv_list(body, 3, 8) + 3
        p = _skip_kv_list(body, p, 8)
        active, n = body[p], body[p + 1]
        presets, _ = _slot_records(body, p + 2, n)
    except (IndexError, struct.error):
        return None
    return active, presets


def chain_parents(skills, known):
    """Chain skills (2nd/3rd hit of a combo) are not in the skills database; the game lists them right after the
    skill that opens them, with its level. {chain base: (opener base, step)}."""
    out = {}
    for base in sorted(skills):
        own = skills[base]
        if base in known or own.bonus or own.learned != own.level:
            continue
        prev = skills.get(base - 10000)
        if prev and prev.level == own.level:
            opener, step = out.get(prev.base, (prev.base, 1))
            out[base] = (opener, step + 1)
    return out
