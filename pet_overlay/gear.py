"""Your equipped gear (read from the game's messages) and whether an item a boss drops would be an upgrade.

1156 full item list, once per login a few seconds before the first 3336: records with container 0x0b are the
     equipped items. Record: u32 id | u64 count | u8 container | u8 pos | 12 B | [bit byte] | u8 enchant | 13 B
     | u32 item level (base + enchant + socketed stones) | [bit byte] | u8 pos | u8 n sockets | n x 7 B ...
3336 own character: the visible equipment table (weapon, armor, cloak, necklace, earrings) with +N, re-sent about
     every minute. 1c56: a visible equipment change (old -> new records), seen for other players.
1b56 your items changing: an equipped piece you enchant or put on comes as its record in container 0x0b with its
     slot (2026-10-17: Gartua Ring +5 -> +10, item level 84 -> 89). A teleport sends no 1156, so the hidden slots
     (rings, bracelets, belt, amulet, runes, Arcana) only stay right with these (GearReader.feed_update).
Records carry one extra "bit byte" at varying places, so the parsers try both alignments and check the structure.
Checked on 19 logins of three characters: 1156 and 3336 agree item by item, the gear is the same login after login.
Item data (aion2data.com raw/items.json, the game's own tables) in data/gear_items.json.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from .i18n import tr, tr_data

INVENTORY_OPCODE = bytes.fromhex("1156")
EQUIPPED = 0x0B
SLOTS = {    # 1156 equipment position -> (key, name in Spanish)
    1: ("weapon", tr("Arma")), 2: ("guard", tr("Guardia")), 3: ("helm", tr("Casco")), 4: ("shoulders", tr("Hombreras")),
    5: ("chest", tr("Pecho")), 6: ("legs", tr("Grebas")), 7: ("gloves", tr("Guantes")), 8: ("boots", tr("Botas")),
    10: ("necklace", tr("Collar")), 11: ("earring", tr("Pendiente")), 12: ("earring", tr("Pendiente")), 13: ("ring", tr("Anillo")),
    14: ("ring", tr("Anillo")), 15: ("bracelet", tr("Brazalete")), 16: ("bracelet", tr("Brazalete")), 17: ("belt", tr("Cinturón")),
    19: ("cloak", tr("Capa")), 22: ("amulet", tr("Amuleto")), 23: ("rune", tr("Runa")), 24: ("rune", tr("Runa")),
    41: ("chalice", tr("Cáliz")), 42: ("parchment", tr("Pergamino")), 43: ("compass", tr("Brújula")), 44: ("bell", tr("Campana")),
    45: ("mirror", tr("Espejo")),
}
POSITIONS = {}
for _pos, (_key, _) in SLOTS.items():
    POSITIONS.setdefault(_key, []).append(_pos)
VISIBLE_TO_POS = {1: 1, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 19, 9: 10, 10: 11, 11: 12}   # 3336 slot -> 1156 pos
HIDDEN_POSITIONS = set(SLOTS) - set(VISIBLE_TO_POS.values())     # only in the login list (rings, belt...)
CLASS_OF_JOB = {job: cls for base, cls in ((5, "Gladiador"), (9, "Templario"), (13, "Cazador"), (17, "Asesino"),
                                           (21, "Invocador"), (25, "Hechicero"), (29, "Clérigo"), (33, "Cantor"))
                for job in range(base, base + 4)}
WEAPON_CLASS = {"Greatsword": "Gladiador", "Longsword": "Templario", "Bow": "Cazador", "Dagger": "Asesino",
                "Orb": "Invocador", "Spellbook": "Hechicero", "Mace": "Clérigo", "Staff": "Cantor"}
GRADE_RANK = {"Common": 1, "Rare": 2, "Epic": 3, "Unique": 4, "Special": 0}
FACTION = {1: "E", 2: "A"}
EQUIP_PREFIXES = ({1101, 1102, 1103, 1104, 1105, 1106, 1107, 1108, 1150} | set(range(1501, 1510))
                  | set(range(2101, 2108)) | {2152} | set(range(2501, 2508)) | {3101, 3102, 3103, 3104, 3109, 3110}
                  | set(range(8101, 8106)))


def _u32(b, p):
    return int.from_bytes(b[p:p + 4], "little")


def plausible_item(v):
    return v == 0 or (100_000_000 <= v < 900_000_000 and v // 100_000 in EQUIP_PREFIXES)


# --- 1156: the full list -------------------------------------------------------------------------------
def parse_equipped(body):
    """1156 -> {pos: (item id, enchant, item level)} of the equipped items."""
    out, p, end = {}, 0, len(body) - 48
    while p < end:
        if body[p + 12] != EQUIPPED or _u32(body, p + 4) != 1 or any(body[p + 8:p + 12]):
            p += 1
            continue
        item, pos = _u32(body, p), body[p + 13]
        if not item or not plausible_item(item) or not 1 <= pos <= 64 or pos in out:
            p += 1
            continue
        hit = None
        for s1 in (0, 1):
            for s2 in (0, 1):
                level = _u32(body, p + 40 + s1)
                if body[p + 44 + s1 + s2] == pos and 1 <= level <= 255:
                    hit = (s1, s2, body[p + 26 + s1], level)
                    break
            if hit:
                break
        if not hit:
            p += 1
            continue
        s1, s2, enchant, level = hit
        out[pos] = (item, enchant, level)
        p += 46 + s1 + s2
    return out


def parse_soul(body, q):
    """What a gear record holds after its item level, from its slot byte at q: u8 slot (0 when not worn) | u8 n
    sockets | n x 7 B (u32 manastone / soulstone id, 0 when empty | u16 its stat | u8 its grade) | u64 soul binding
    (0: none) | u8 state (10 at 100%: Enraged Kromede Warhammer, its three lines at their highest; 1 below) | u8 3 |
    u32 Theostone engraved (only weapons and guards have one: 19,950,016 on the Kromede Warhammer, 19,950,014 on the
    Spiritforged Guard; 0 elsewhere) | 9 B | u8 n | n x (u16 stat, u32 value: the soul binding lines, percents x100)
    | u8 n | n x (u16, u32 0) | u8 k | k x (u32 skill, u8 level: the skill effects) | u8 k | ...
    -> {"state", "lines": [[stat id, value]], "skills", "sockets": [[stone id, stat, grade]], "theostone"} or None
    when it does not fit. Stat ids are the client's own (193 HP, 317 Attack, 307 Defense, 6 Willpower...):
    upgrades.py names them from the piece's stat pool (the value falls in one stat's range)."""
    n = body[q + 1] if q + 1 < len(body) else 99
    if n > 8:
        return None
    r = q + 2 + 7 * n
    if r + 24 > len(body) or body[r + 9] != 3:
        return None
    sockets = [[_u32(body, q + 2 + 7 * i), int.from_bytes(body[q + 6 + 7 * i:q + 8 + 7 * i], "little"),
                body[q + 8 + 7 * i]] for i in range(n)]
    out = {"state": 0, "lines": [], "skills": 0, "sockets": sockets, "theostone": _u32(body, r + 10)}
    if not any(body[r:r + 8]):
        return out
    state, s = body[r + 8], r + 23
    lines = body[s]
    if lines > 8 or s + 1 + 6 * lines >= len(body):
        return None
    stats = [(int.from_bytes(body[s + 1 + 6 * i:s + 3 + 6 * i], "little"), _u32(body, s + 3 + 6 * i))
             for i in range(lines)]
    s += 1 + 6 * lines
    if body[s] != lines:
        return None
    s += 1 + 6 * lines
    skills = body[s] if s < len(body) else 0
    out.update(state=state, lines=[list(x) for x in stats], skills=skills if skills <= 8 else 0)
    return out


def parse_owned_gear(body, by_id, update=False):
    """1156 (or a 1b56 change) -> [(stack, container, item id, enchant, item level, count, soul, slot)] of every gear
    piece (slot: the equipment position of a worn one, else 0),
    equipped or not (bag 1, storage 15...): the same record as an equipped one, but with 0 instead of the slot after
    its item level (2026-10-17: the bag's second Nuakum Pauldrons +0 at 86, the storage's Kromede weapons of other
    classes). The bit byte before the enchant, when there is one, moves it and the level by one: the level is found
    where it is at least the piece's base level (by_id: GearDB.by_id). soul: parse_soul() (soul binding, manastones,
    Theostone) or None. In a change a piece that left has count 0."""
    out, p, end = [], 8, len(body) - 48
    while p < end:
        item = _u32(body, p)
        info = by_id.get(item) if 100_000_000 <= item < 900_000_000 else None
        count = _u32(body, p + 4)
        if info is None or count > 1 or (count == 0 and not update) or any(body[p + 8:p + 12]) or body[p + 12] >= 64:
            p += 1
            continue
        cont, pos = body[p + 12], body[p + 13]
        hit = None
        for s1 in (0, 1):
            level = _u32(body, p + 40 + s1)
            if not info.level <= level <= info.level + 150:
                continue
            after = pos if cont == EQUIPPED else 0
            slots = [s2 for s2 in (0, 1) if body[p + 44 + s1 + s2] == after]
            if slots:
                soul = next((x for x in (parse_soul(body, p + 44 + s1 + s2) for s2 in slots) if x is not None), None)
                hit = (s1, body[p + 26 + s1], level, soul)
                break
        if hit is None:
            p += 1
            continue
        s1, enchant, level, soul = hit
        stack = f"{_u32(body, p - 8)}:{int.from_bytes(body[p - 3:p - 1], 'little')}"
        out.append((stack, cont, item, enchant if enchant <= 30 else 0, level, count, soul,
                    pos if cont == EQUIPPED else 0))
        p += 46 + s1
    return out


# --- 3336: the visible table -----------------------------------------------------------------------------
SUB_ENTRY = 28
SCAN_COST = 10.0


def _header_ok(b, p, slot):
    if p + 6 > len(b) or b[p + 5] != slot:
        return False
    v, enchant = _u32(b, p), b[p + 4]
    return plausible_item(v) and enchant <= 30 and not (v == 0 and enchant)


def _record_ends(b, p):
    """Possible end offsets of the visible record at p -> {end: cost}. Record: u32 id | u8 enchant | u8 slot | u64
    | [bit byte] | u32 appearance | u8 c | c x 28 B | u8 d | d x u32 | [bit byte] | u8 flags (0x0e almost always)."""
    out, n = {}, len(b)
    for s in (0, 1):
        if p + 19 + s >= n:
            continue
        c = b[p + 18 + s]
        if c > 8:
            continue
        for k_in in range(0, 2 + c // 4):
            dpos = p + 19 + s + SUB_ENTRY * c + k_in
            if dpos >= n or b[dpos] > 8:
                continue
            for k_out in (0, 1):
                bits = s + k_in + k_out
                if bits > 1 + (c + 7) // 8:
                    continue
                q = dpos + 1 + 4 * b[dpos] + k_out + 1
                if q <= n:
                    cost = (0.0 if b[q - 1] == 0x0E else 1.0) + 0.1 * bits
                    out[q] = min(out.get(q, 99.0), cost)
    return out


def _candidates(b, p, next_ok):
    ends = _record_ends(b, p)
    if next_ok is None:
        return sorted(ends.items(), key=lambda x: x[1])
    cands = [(q, c) for q, c in ends.items() if next_ok(q)]
    hits = 0
    for q in range(p + 21, min(p + 1200, len(b) - 6)):
        if q not in ends and next_ok(q):
            cands.append((q, SCAN_COST + (0.0 if b[q - 1] == 0x0E else 1.0)))
            hits += 1
            if hits >= 4:
                break
    return sorted(cands, key=lambda x: x[1])


def _walk(b, st):
    n, memo = b[st], {}

    def best(p, k):
        if (p, k) in memo:
            return memo[(p, k)]
        res = (float("inf"), None)
        if _header_ok(b, p, k):
            if k == n:
                tail = _candidates(b, p, None)
                res = ((tail[0][1] if tail else SCAN_COST), [p])
            else:
                for q, c in _candidates(b, p, lambda q, k=k: _header_ok(b, q, k + 1)):
                    sub = best(q, k + 1)
                    if sub[1] is not None and c + sub[0] < res[0]:
                        res = (c + sub[0], [p] + sub[1])
        memo[(p, k)] = res
        return res

    cost, offsets = best(st + 1, 1)
    return (None, cost) if offsets is None else ([(k + 1, _u32(b, o), b[o + 4]) for k, o in enumerate(offsets)], cost)


def parse_visible(body):
    """3336 -> {pos: (item id, enchant, None)} of the visible slots (empty slots left out)."""
    best = (float("inf"), None)
    for st in range(len(body) - 30):
        n = body[st]
        if 8 <= n <= 40 and body[st + 6] == 1 and _u32(body, st + 1) and plausible_item(_u32(body, st + 1)):
            records, cost = _walk(body, st)
            if records and cost <= best[0]:
                best = (cost, records)
    out = {}
    for slot, item, enchant in best[1] or []:
        if item and slot in VISIBLE_TO_POS:
            out[VISIBLE_TO_POS[slot]] = (item, enchant, None)
    return out


def own_faction(body, name):
    """3336: u8 faction 6 bytes after the name (u16 server, u32 job) -> "E" / "A" / None."""
    raw = name.encode("utf-8")
    i = body.find(bytes([len(raw)]) + raw)
    at = i + 1 + len(raw) + 6 if i >= 0 else None
    return FACTION.get(body[at]) if at is not None and at < len(body) else None


class GearReader:
    """Capture-thread state: binds the login's full list (1156) to the next character info (3336), then keeps the
    visible slots fresh from every 3336. feed_* return the character's gear when it changed, else None."""
    LOGIN_WINDOW = 60.0      # a 1156 belongs to a 3336 that follows within this

    def __init__(self):
        self.pending = None      # (time, {pos: ...}) of the last 1156
        self.name = None
        self.slots = {}
        self.full = False        # the slots include this session's login list (rings, belt...)
        self.by_id = {}          # gear item id -> Item (the app's GearDB.by_id): reads the worn pieces of a 1b56
        self.info = None         # (name, job, level, faction) of the last 3336, for a change between two of them

    def feed_inventory(self, now, body):
        equipped = parse_equipped(body)
        if equipped:
            self.pending = (now, equipped)

    def feed_character(self, now, body, info):
        before = dict(self.slots)
        if info.name != self.name:
            self.name, self.slots, self.full = info.name, {}, False
        if self.pending and now - self.pending[0] <= self.LOGIN_WINDOW:
            self.slots = dict(self.pending[1])
            self.full = True
        self.pending = None
        visible = parse_visible(body)
        if visible:
            for pos in VISIBLE_TO_POS.values():
                new = visible.get(pos)
                old = self.slots.get(pos)
                if new is None:
                    self.slots.pop(pos, None)
                elif not old or old[:2] != new[:2]:
                    self.slots[pos] = new           # changed since login: item level unknown until the next one
        return self.slots if self.slots != before else None

    def feed_update(self, body):
        """1b56: a worn piece enchanted or put on -> its slot now (item, +N, item level); the slots when they
        changed, else None."""
        if not self.name or not self.by_id:
            return None
        before = dict(self.slots)
        for _, cont, item, enchant, level, count, _, slot in parse_owned_gear(body, self.by_id, update=True):
            if cont == EQUIPPED and count == 1 and slot in SLOTS:
                self.slots[slot] = (item, enchant, level)
        return self.slots if self.slots != before else None


# --- items and verdicts ------------------------------------------------------------------------------------
@dataclass
class Item:
    ids: tuple
    name: str
    slot: str
    category: str
    grade: str
    level: int                  # base item level
    required: int               # character level needed
    faction: str                # "B" both | "E" | "A"

    @property
    def weapon_class(self):
        return WEAPON_CLASS.get(self.category)


class GearDB:
    def __init__(self, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.credit = tr_data("gear_items.json", data).get("credit", {})
        self.by_id, self.by_name = {}, {}
        for ids, name, slot, category, grade, level, required, faction in data.get("items", []):
            item = Item(tuple(ids), name, slot, category, grade, level, required, faction)
            for i in ids:
                self.by_id[i] = item
            self.by_name.setdefault(name, []).append(item)

    def named(self, name, faction=None):
        """The equipment item with this name (the faction's variant when there are several), or None."""
        items = self.by_name.get(name, [])
        return next((i for i in items if i.faction in ("B", faction)), items[0] if items else None)


@dataclass
class Verdict:
    kind: str                   # upgrade | base (better base, yours wins by +N) | no | class | level | faction
    slot: str                   # "Casco"...
    mine: Item | None = None
    mine_enchant: int = 0
    mine_level: int | None = None   # your item's level now (base + enchant + stones)

    @property
    def text(self):
        if self.kind == "class":
            return tr("No es un arma de tu clase.")
        if self.kind == "level":
            return tr("Necesitas más nivel para usarlo.")
        if self.kind == "faction":
            return tr("Es de la otra facción.")
        if self.mine is None:
            return tr("No llevas nada en {0}: te sirve.", self.slot.lower())
        yours = tr("{0} +{1} (nivel de objeto {2})", self.mine.name, self.mine_enchant, self.mine_level)
        return {"upgrade": tr("Mejora tu {0}: {1}.", self.slot.lower(), yours),
                "base": tr("Mejor base que tu {0} ({1}, {2}), pero el tuyo gana hoy con su +{3} ({4}): vale la pena "
                           "si lo mejoras.", self.slot.lower(), self.mine.name, self.mine.level, self.mine_enchant, self.mine_level),
                "no": tr("Tu {0} es mejor: {1}.", self.slot.lower(), yours)}[self.kind]


def verdict(item, gear, db):
    """Is `item` an upgrade for the character whose saved gear is `gear` ({"job", "level", "faction", "slots"})?"""
    cls = CLASS_OF_JOB.get(gear.get("job"))
    positions = POSITIONS.get(item.slot, [])
    name = SLOTS[positions[0]][1] if positions else item.slot
    if item.weapon_class and cls and item.weapon_class != cls:
        return Verdict("class", name)
    if item.required > (gear.get("level") or 99):
        return Verdict("level", name)
    if item.faction not in ("B", gear.get("faction") or item.faction):
        return Verdict("faction", name)
    worst = None                         # the weaker of the slots it can go in (earrings, rings...)
    for pos in positions:
        slot = gear.get("slots", {}).get(str(pos))
        if not slot:
            return Verdict("upgrade", name)
        mine = db.by_id.get(slot[0])
        now = slot[2] or ((mine.level if mine else 0) + slot[1])
        if worst is None or now < worst[2]:
            worst = (mine, slot[1], now)
    if worst is None:
        return Verdict("no", name)
    mine, enchant, now = worst
    base = mine.level if mine else now
    if item.level > now:
        kind = "upgrade"
    elif item.level > base or (item.level == base and mine and GRADE_RANK.get(item.grade, 0) >
                               GRADE_RANK.get(mine.grade, 0)):
        kind = "base"
    else:
        kind = "no"
    return Verdict(kind, name, mine, enchant, now)
