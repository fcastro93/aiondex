"""Mejoras: what to do next with your gear, in order, from what AionDex knows about you.

What it reads:
- your equipped gear (settings "gear", gear.py) and every piece in your bag and storages, with its soul binding
  lines, manastones and Theostone (inventory: gear.parse_owned_gear);
- what you have to spend (inventory: Kina, Enhance Stones, Transfer Stones, Gear Change Vouchers, Soul Codex,
  Philosopher's Stones, Amplify Stones, manastones...);
- the market's gear prices (your market samples: Market > Equipo);
- the dungeons you run (the final bosses you killed in them, fights/index.json; or the ones you pick);
- data/upgrades_db.json (tools/build_upgrades_db.py, aion2data's tables of the global client): what each enhancement
  level costs, what a transfer takes, where each piece drops, every dungeon cube's guaranteed reward, the Substance
  Morph recipes (3 Gear Change Vouchers: Ferocious Horn Den + 2,000,000 Kina = Nuakum Guard...) and each piece's soul
  binding stat pool.

Soul binding (kanonxo's "Aion 2 Bible", the Global PvE guide, and the game's own window): a piece's stat lines come
from its pool; each line shows value/highest and the Soul Binding Rate is the average of how far each line is from its
lowest to its highest value (6 lines at 100% and one at 32% = 90.2%; a skill effect line counts as 100%). Bind (30,000
Kina + Soul Codex, more each time) raises the values; Sync rerolls one line (a Unique piece of the same kind or a Sync
Stone); Reset rerolls them all (Soul Codex: Reset + 300,000 Kina); at 100% 2 Philosopher's Stones + 1,000,000 Kina add
one more line. A transfer keeps the +N, the Amp levels and the lines, never that extra line, the manastones, the
Theostone or Potential. Each manastone (weapons, armor) or soulstone (accessories) you use rerolls every socket of the
piece. The lines worth having per piece are the guide's (IDEAL), skill effects included.

Steps you do not want («No quiero hacer este paso», kept in settings "upgrades" "skipped") are left out and the plan
is made again without them: a piece or a voucher you will not get no longer counts as "about to be replaced", so its
enhancing and soul binding come back; a Sync you skip frees the piece it was going to eat.

The plan, in order: wear what you already have that is better; spend your vouchers; enhance up to the level that
never fails (+10 for Unique and Epic gear, +5 below); soul binding and manastones on the pieces you keep; Amp your
weapon; buy (the piece that gives the most for its price, and the one of the Dragon Lord line, which goes on to item
level 102 with transfer crafting); farm (the pieces your dungeons drop and their guaranteed reward). Gains are in
item level, the game's own measure of gear (what dungeons and the Abyss ask for).
"""
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .gear import CLASS_OF_JOB, POSITIONS, SLOTS

GRADE_TABLE = {"Common": "Common", "Rare": "Rare", "Epic": "Legend", "Unique": "Unique"}   # the tables' names
PLANNED = (1, 2, 3, 4, 5, 6, 7, 8, 19, 10, 11, 12, 13, 14, 15, 16, 17, 22, 23, 24)    # Arcana: their own system
LADDER = re.compile(r"^(Splendent )?(Star|Dark|Ebony|True|White|Wise) Dragon Lord ")
LADDER_TOP = 102
DUNGEON_DAYS = 14              # a dungeon whose final boss you killed in these days is one you run
DUNGEON_KILLS = 2
TRANSFER_WORTH = 5             # below this +N, enhancing the new piece costs little: no transfer suggested
BIND_KINA, RESET_KINA, FUSE_KINA, FUSE_STONES, THEOSTONE_KINA = 30_000, 300_000, 1_000_000, 2, 10_000
AMPLIFY_RECIPE = (4, 2500)     # 4 Amplify Stone Fragments (Unique) + 2,500 Enhance Stones = 1 Amplify Stone (Unique)
WORN = 11
PLACES = {1: "bolsa", 2: "almacén de cuenta", 15: "almacén"}
MANA_SLOTS = {"weapon", "guard", "helm", "shoulders", "chest", "legs", "gloves", "boots", "cloak"}
SOUL_SLOTS = {"necklace", "earring", "ring"}     # their sockets take soulstones
# the stat lines to aim for, per piece (kanonxo's Aion 2 Bible, chapter 3, Global): rarest first; skill effects count
IDEAL = {
    "weapon": ("Damage Boost", "Combat Speed", "Weapon Damage Boost", "Might", "Multi-hit Chance", "Precision",
               "Attack"),
    "helm": ("Double Chance", "Attack increase", "Attack"),
    "cloak": ("Double Chance", "Attack increase", "Perfect Chance", "Attack"),
    "shoulders": ("Double Chance", "Critical Damage Boost", "Attack"),
    "chest": ("Perfect Chance", "Damage Boost", "Attack"),
    "legs": ("Attack increase", "Perfect Chance", "Damage Tolerance", "Attack"),
    "gloves": ("Perfect Chance", "Combat Speed", "Attack"),
    "boots": ("Perfect Chance", "Move Speed", "Movement Speed", "Attack"),
    "necklace": ("Combat Speed", "Might", "Attack", "Accuracy"),
    "earring": ("Move Speed", "Movement Speed", "Might", "Precision", "Attack", "Accuracy"),
    "ring": ("Might", "Precision", "Attack"),
}
IDEAL["guard"] = IDEAL["weapon"]
GUIDE = {"text": "Líneas buscadas y reglas de Soul Binding: Aion 2 Bible de kanonxo",
         "url": "https://docs.google.com/document/d/11u4wLCG1WfL-xSka2Aze0rI9vYRa7mq3N3Gp1bt0AWY/edit"}


@dataclass
class Soul:
    rate: float                 # 0..1, the game's Soul Binding Rate (the average of the lines)
    lines: list                 # [(stat name or None, value, lowest, highest, 0..1)]
    skills: int                 # skill effect lines
    props: int                  # how many properties a binding gives the piece
    locked: bool                # at 100%: no more Bind, the Philosopher's Stone line opens
    bad: list                   # stat names not worth keeping on this piece (not in IDEAL)
    sockets: list               # [(stone id, stat, grade)] (stone id 0: empty)
    theostone: int              # the engraved Theostone (weapons, guards), 0 if none

    @property
    def empty(self):
        return sum(1 for s in self.sockets if not s[0])


@dataclass
class Piece:
    pos: int
    slot: str                   # "Collar"...
    item: object                # gear.Item or None (empty slot)
    enchant: int = 0
    level: int = 0              # item level now (base + enchant + stones)
    safe: int = 0               # the +N every attempt reaches
    top: int = 0
    to_safe: tuple = None       # (Kina, Enhance Stones, other items) from its +N to safe
    soul: Soul = None

    @property
    def base(self):
        return self.item.level if self.item else 0

    @property
    def key(self):
        return SLOTS[self.pos][0]


@dataclass
class Option:
    kind: str                   # own | voucher | drop | market
    pos: int
    item: object                # gear.Item
    gain: int                   # item level over the piece it replaces (base against base)
    kinah: int = None           # what it costs (market price, voucher fee)
    text: str = ""
    enchant: int = 0            # an owned piece's +N
    ladder: bool = False        # Dragon Lord line: goes on to item level 102
    chance: float = None        # drop: per clear
    source: str = ""            # voucher / drop: the dungeon
    need: int = 0               # voucher: how many it takes


@dataclass
class Step:
    kind: str       # own | voucher | enhance | soul | manastone | amp | morph | buy | save | farm | spare | theostone
    title: str
    detail: str
    gain: int = 0
    kinah: int = None
    stones: int = None
    ready: bool = True          # you can do it now with what you have
    pos: int = None
    key: str = ""               # what «No quiero hacer este paso» skips
    parts: list = field(default_factory=list)       # a card of several: [(key, text)] each one skippable
    summary: str = ""           # the step in one short line (the window shows it; the detail opens on a click)


@dataclass
class Spare:
    """A piece in your bag or storage you do not need: food for a Sync of a piece you keep (the same kind and grade),
    or 1 Sync Stone or 1 Transfer Stone in Substance Morph (any Unique piece)."""
    item: object
    enchant: int
    level: int
    container: int
    rate: float = 0.0           # its own soul binding (lost when eaten)
    use: str = ""               # what the plan eats it for ("Sync de tus Nuakum Boots"), "" = a stone

    @property
    def place(self):
        return PLACES.get(self.container, "almacén")

    @property
    def text(self):
        return f"{self.item.name} +{self.enchant} ({self.place})"


@dataclass
class Plan:
    character: str
    resources: dict = field(default_factory=dict)     # kind -> count (kina, enhance_stone, transfer_stone...)
    vouchers: dict = field(default_factory=dict)      # dungeon -> count
    stones: dict = field(default_factory=dict)        # manastone / soulstone name -> count
    pieces: list = field(default_factory=list)
    options: dict = field(default_factory=dict)       # pos -> [Option], best first
    steps: list = field(default_factory=list)
    dungeons: list = field(default_factory=list)
    spare: list = field(default_factory=list)         # owned pieces you do not need: Spare (food for upgrades)
    scrolls: dict = field(default_factory=dict)       # Enhance Scroll name -> count (belts, amulets, runes)
    replace: set = field(default_factory=set)         # positions about to get another piece
    skipped: set = field(default_factory=set)         # the keys of the steps you do not want
    item_level: int = 0
    read_at: float = None                             # when your items were read

    def have(self, kind):
        return self.resources.get(kind, 0)


class UpgradesDB:
    def __init__(self, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.credit = data.get("credit") or {"text": "aion2data.com", "url": "https://aion2data.com"}
        self.tracked = {int(k): tuple(v) for k, v in data.get("tracked", {}).items()}
        self.enhance = data.get("enhance", {})
        self.decompose = data.get("decompose", {})
        self.transfer = data.get("transfer", {})
        self.drops = {int(k): v for k, v in data.get("drops", {}).items()}
        self.cubes = data.get("cubes", [])
        self.conversions = data.get("conversions", [])
        self.chests = data.get("chests", {})
        self.pools = data.get("pools", [])
        self.pool_of = {int(k): v for k, v in data.get("pool_of", {}).items()}
        self.props = {int(k): v for k, v in data.get("props", {}).items()}
        self._tables = {}
        for group in self.enhance:
            m = re.match(r"Enchant_(\w+?)_(\d+)$", group)
            if m:
                self._tables.setdefault(m.group(1), []).append(int(m.group(2)))
        for levels in self._tables.values():
            levels.sort()

    def tracked_names(self):
        return {i: name for i, (_, name) in self.tracked.items()}

    def pool(self, item_id):
        """[(stat, lowest, highest)] a piece's soul binding lines come from (percents x100), or []."""
        i = self.pool_of.get(item_id)
        return [tuple(s) for s in self.pools[i]] if i is not None else []

    def table(self, item):
        """The enhancement table of a piece: [[Kina, Enhance Stones, chance %, other items] per +N], or None."""
        grade = GRADE_TABLE.get(item.grade)
        if item.category == "Belt":
            return self.enhance.get(f"Belt_{grade}")
        if item.category == "Amulet":
            return self.enhance.get(f"Amulet_{grade}")
        if item.category == "Rune":
            return self.enhance.get("Rune_PvE_Unique")
        levels = [lv for lv in self._tables.get(grade, []) if lv <= item.level]
        return self.enhance.get(f"Enchant_{grade}_{levels[-1]}") if levels else None

    def safe_level(self, item):
        rows = self.table(item) or []
        n = 0
        while n < len(rows) and rows[n][2] >= 100:
            n += 1
        return n

    def top_level(self, item):
        return len(self.table(item) or [])

    def enhance_cost(self, item, start, end):
        """(Kina, Enhance Stones, other items) from +start to +end, or None."""
        rows = self.table(item)
        if not rows or end <= start:
            return None
        part = rows[start:end]
        return sum(r[0] for r in part), sum(r[1] for r in part), sum(r[3] for r in part)

    def transfer_stones(self, item):
        """Transfer Stones a transfer into this piece takes (its tier: 1, or 2 from item level 86), or None."""
        tiers = [t for t in self.transfer.get(item.category, []) if t[0] <= item.level <= t[1]]
        return max(t[2] for t in tiers) if tiers else None

    def cube_for(self, dungeon, name):
        """[(mode, cubes to the guaranteed reward, energy per cube)] of the dungeon's cubes whose guaranteed reward
        lets you pick this piece."""
        return [(c["mode"], c["ceiling"], c["energy"]) for c in self.cubes
                if c["dungeon"] == dungeon and c["open"] and name in c["choices"]]

    def voucher_recipes(self, dungeon):
        """[(piece names it gives a choice of, vouchers, Kina)] of the Substance Morph recipes paid with this
        dungeon's Gear Change Vouchers."""
        token = f"Gear Change Voucher: {dungeon} (Bound)"
        out = []
        for c in self.conversions:
            mats = dict((n, a) for n, a in c["materials"])
            if token not in mats:
                continue
            choices = self.chests.get(c["makes"]) or [c["makes"]]
            out.append((choices, mats[token], mats.get("Kina (All)", 0)))
        return out

    def dungeons(self):
        """Every dungeon with a guaranteed cube reward, for the «Mazmorras que haces» choice."""
        return sorted({c["dungeon"] for c in self.cubes if c["open"]})


def dungeons_you_run(fights_index, now=None, seen=None):
    """Dungeons whose final boss you killed at least DUNGEON_KILLS times in the last DUNGEON_DAYS days, most first.
    seen: {dungeon: last time} kept by the app (the fight log forgets fights after 48 hours), updated here."""
    now = now or time.time()
    counts = {}
    for f in fights_index or []:
        if f.get("instance") and f.get("final") and f.get("killed") and now - (f.get("start") or 0) <= \
                DUNGEON_DAYS * 86400 and f.get("place"):
            counts[f["place"]] = counts.get(f["place"], 0) + 1
    found = [d for d, n in sorted(counts.items(), key=lambda x: -x[1]) if n >= DUNGEON_KILLS]
    if seen is not None:
        for d in found:
            seen[d] = now
        found += [d for d, at in sorted(seen.items(), key=lambda x: -x[1])
                  if d not in found and now - at <= DUNGEON_DAYS * 86400]
    return found


def kinah_text(value):
    if value is None:
        return "?"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if value >= 10_000:
        return f"{value / 1000:.0f}K"
    return f"{value:,.0f}"


def stat_value(name, value):
    """A line's value as the game writes it: percents x100 in the messages ('Damage Boost 4.89%')."""
    return f"{value / 100:.2f}%" if value >= 100 and name and any(
        w in name for w in ("Boost", "Speed", "Chance", "increase", "Resist", "Regeneration", "Penetration",
                            "Tolerance", "Endurance")) else f"{value:,}"


def learn_stats(pieces, db):
    """Stat id -> the stat names it can be: each line of each piece narrows it to the stats of that piece's pool
    whose range holds its value (the client sends ids, aion2data names; one id is one stat everywhere)."""
    names = {}
    for item_id, soul in pieces:
        pool = db.pool(item_id)
        for sid, value in (soul or {}).get("lines", []):
            cands = {n for n, lo, hi in pool if lo <= value <= hi}
            if not cands:
                continue
            names[sid] = names[sid] & cands if names.get(sid) and names[sid] & cands else (names.get(sid) or cands)
    return names


def read_soul(item, soul, db, names):
    """Soul of a piece from its record (gear.parse_soul) or None."""
    if not soul:
        return None
    pool = db.pool(item.ids[0]) or next((db.pool(i) for i in item.ids if db.pool(i)), [])
    lines = []
    for sid, value in soul.get("lines", []):
        cands = [(n, lo, hi) for n, lo, hi in pool if lo <= value <= hi]
        known = [c for c in cands if c[0] in names.get(sid, ())]
        pick = (known or cands or [None])[0]
        if pick:
            n, lo, hi = pick
            lines.append((n, value, lo, hi, (value - lo) / (hi - lo) if hi > lo else 1.0))
        else:
            lines.append((None, value, None, None, None))
    parts = [x[4] for x in lines if x[4] is not None] + [1.0] * soul.get("skills", 0)
    locked = soul.get("state") == 10
    rate = 1.0 if locked else (sum(parts) / len(parts) if parts else 0.0)
    ideal = IDEAL.get(SLOTS[POSITIONS[item.slot][0]][0] if item.slot in POSITIONS else "", ())
    bad = [x[0] for x in lines if x[0] and ideal and x[0] not in ideal]
    return Soul(rate, lines, soul.get("skills", 0), db.props.get(item.ids[0], len(lines) + soul.get("skills", 0)),
                locked, bad, [tuple(s) for s in soul.get("sockets", [])], soul.get("theostone", 0))


def _wearable(item, gear):
    cls = CLASS_OF_JOB.get(gear.get("job"))
    if item.weapon_class and cls and item.weapon_class != cls:
        return False
    if item.required > (gear.get("level") or 99):
        return False
    return item.faction in ("B", gear.get("faction") or item.faction)


def amp_max(item):
    """The Amp levels a Unique weapon reaches past +15 (the guide: Kromede and Ludra 5, dungeon weapons 3, the Dragon
    Lord line 1 / 3 / 5 by tier)."""
    if any(w in item.name for w in ("Kromede", "Ludra")):
        return 5
    m = LADDER.match(item.name)
    if m:
        return 1 if not m.group(1) and m.group(2) in ("Star", "True") else 3 if m.group(2) in ("Star", "True") else 5
    return 3


def option_key(o):
    """The skip key of an option: you do not want that piece that way ("market:Argo Earrings")."""
    return f"{o.kind}:{o.item.name}"


def build_plan(name, gear, gear_db, db, inventory, prices=None, dungeons=None, levels=None, recipes=None,
               skipped=None):
    """The Plan for a character. gear: settings["gear"][name]; inventory: the app's Inventory (bag and storage);
    prices: {item name: (lowest, when)} of the market; dungeons: the ones you run; levels: {profession: level};
    recipes: crafting_db's (the Dragon Lord pieces' Splendent step); skipped: the keys of the steps you do not want."""
    gear = gear or {}
    plan = Plan(name)
    plan.skipped = set(skipped or ())
    plan.dungeons = list(dungeons or [])
    plan.read_at = inventory.read_at(name) if inventory else None
    counted = set()
    for item_id, (kind, item_name) in db.tracked.items():
        if item_name in counted:                    # one name, several ids (owned() counts them all)
            continue
        counted.add(item_name)
        have = inventory.owned(name, item_name) if inventory else None
        if not have or not have[0]:
            continue
        if kind == "enhance_scroll":
            plan.scrolls[item_name] = have[0]
        elif kind == "voucher":
            dungeon = re.sub(r"^Gear Change Voucher: | \(Bound\)$", "", item_name)
            plan.vouchers[dungeon] = plan.vouchers.get(dungeon, 0) + have[0]
        else:
            if kind in ("manastone", "soulstone"):
                plan.stones[item_name] = plan.stones.get(item_name, 0) + have[0]
            key = kind
            if kind == "transfer_stone" and item_name != "Transfer Stone (Unique) (Bound)":
                key = "transfer_stone_other"
            if kind == "amplify_fragment" and "(Unique)" not in item_name:
                key = "amplify_fragment_other"
            plan.resources[key] = plan.resources.get(key, 0) + have[0]
    kina = plan.have("kina")
    stones = plan.have("enhance_stone")

    stored = inventory.pieces(name) if inventory else []
    names = learn_stats([(p[0], p[4]) for p in stored], db)
    worn = [p for p in stored if p[3] == WORN]
    slots = gear.get("slots", {})
    by_pos = {}
    for pos in PLANNED:
        raw = slots.get(str(pos))
        item = gear_db.by_id.get(raw[0]) if raw else None
        enchant = raw[1] if raw else 0
        level = (raw[2] if raw and raw[2] else (item.level + enchant if item else 0))
        piece = Piece(pos, SLOTS[pos][1], item, enchant, level)
        if item:
            piece.safe, piece.top = db.safe_level(item), db.top_level(item)
            piece.to_safe = db.enhance_cost(item, enchant, piece.safe)
            record = next((p for p in worn if p[0] == raw[0] and p[1] == enchant), None) or \
                next((p for p in worn if p[0] == raw[0]), None)
            if record is not None:
                worn.remove(record)
                piece.soul = read_soul(item, record[4], db, names)
        by_pos[pos] = piece
        plan.pieces.append(piece)
    plan.item_level = sum(p.level for p in plan.pieces)

    def target(slot_key):
        """The piece of a slot kind an upgrade would replace: the weakest of its positions."""
        cands = [by_pos[p] for p in POSITIONS.get(slot_key, []) if p in by_pos]
        return min(cands, key=lambda p: (p.base, p.level)) if cands else None

    def gain_over(item):
        piece = target(item.slot)
        if piece is None:
            return None, 0
        return piece, item.level - piece.base

    # what you already have
    owned = inventory.owned_gear(name) if inventory else None
    for item_id, enchant, level, _, _ in owned or []:
        item = gear_db.by_id.get(item_id)
        if item is None or not _wearable(item, gear):
            continue
        piece, gain = gain_over(item)
        if piece and level > piece.level:
            plan.options.setdefault(piece.pos, []).append(
                Option("own", piece.pos, item, level - piece.level, 0, f"La tienes guardada (+{enchant}, {level}).",
                       enchant=enchant))

    # vouchers
    for dungeon, count in plan.vouchers.items():
        for choices, need, fee in db.voucher_recipes(dungeon):
            for choice in choices:
                item = gear_db.named(choice, gear.get("faction"))
                if item is None or not _wearable(item, gear):
                    continue
                piece, gain = gain_over(item)
                if piece and gain > 0:
                    plan.options.setdefault(piece.pos, []).append(
                        Option("voucher", piece.pos, item, gain, fee,
                               f"{need} Gear Change Voucher de {dungeon} + {kinah_text(fee)} Kinah (Substance Morph); "
                               f"tienes {count}.", source=dungeon, need=need))

    # the market
    for item_name, (lowest, _) in (prices or {}).items():
        item = gear_db.named(item_name, gear.get("faction"))
        if item is None or not _wearable(item, gear) or item.slot not in POSITIONS:
            continue
        piece, gain = gain_over(item)
        if piece and gain > 0:
            ladder = bool(LADDER.match(item.name))
            text = f"En el market a {kinah_text(lowest)}"
            step = splendent_step(item.name, recipes) if ladder else None
            if step:
                nxt = gear_db.named(step[0], gear.get("faction"))
                text += (f" · con transfer crafting pasa a {step[0]} ({nxt.level if nxt else '?'}): "
                         + ", ".join(f"{n}× {m}" for m, n in step[1]))
            elif ladder:
                text += " · sube hasta 102 con transfer crafting"
            plan.options.setdefault(piece.pos, []).append(Option("market", piece.pos, item, gain, lowest, text,
                                                                 ladder=ladder))

    # the dungeons you run
    for item_id, (kind, source, detail, chance, per) in db.drops.items():
        if kind != "dungeon clear" or source not in plan.dungeons:
            continue
        item = gear_db.by_id.get(item_id)
        if item is None or not _wearable(item, gear) or item.slot not in POSITIONS:
            continue
        piece, gain = gain_over(item)
        if piece and gain > 0:
            pity = db.cube_for(source, item.name)
            text = f"{source} ({detail}): {chance * 100:.1f}% por {'clear' if per == 'clear' else per}"
            if pity:
                text += " · garantizado: " + ", ".join(f"{m} cada {n} cubos" for m, n, _ in
                                                       sorted(pity, key=lambda x: x[1]))
            plan.options.setdefault(piece.pos, []).append(Option("drop", piece.pos, item, gain, None, text,
                                                                 chance=chance, source=source))
    order = {"own": 0, "voucher": 1, "drop": 2, "market": 3}
    skip = plan.skipped
    for pos, opts in plan.options.items():
        best = {}
        for o in opts:                                          # one option per piece and kind (the cheapest)
            if option_key(o) in skip or o.kind == "voucher" and f"voucher-dungeon:{o.source}" in skip or \
                    o.kind == "drop" and f"farm-dungeon:{o.source}" in skip:
                continue                                        # you said no to it
            key = (o.kind, o.item.name)
            if key not in best or (o.kinah or 0) < (best[key].kinah or 0):
                best[key] = o
        plan.options[pos] = sorted(best.values(), key=lambda o: (-o.gain, order[o.kind], o.kinah or 0))
    # a piece you are about to change (one you have, or get with a voucher, or a market one far better): no more
    # Kina into it (its +N and lines would need a transfer)
    plan.replace = {pos for pos, opts in plan.options.items()
                    if any(o.kind in ("own", "voucher") and o.gain >= 10 or o.kind == "market" and o.gain >= 15
                           for o in opts)}

    # the pieces you do not need (not one to wear): food for the upgrades, the cheapest first (+0, low level)
    for item_id, enchant, level, cont, soul in owned or []:
        item = gear_db.by_id.get(item_id)
        if item is None or item.grade not in ("Unique", "Epic"):
            continue
        if any(o.item is item and o.enchant == enchant for opts in plan.options.values() for o in opts
               if o.kind == "own"):
            continue
        s = read_soul(item, soul, db, names)
        plan.spare.append(Spare(item, enchant, level, cont, s.rate if s and s.lines else 0.0))
    plan.spare.sort(key=lambda x: (x.enchant, x.level, x.rate))

    plan.steps = _steps(plan, by_pos, kina, stones, db, levels or {}, prices or {}, recipes, gear_db,
                        gear.get("faction"))
    return plan


def splendent_step(name, recipes):
    """(the Splendent piece, [(material, count)]) of the transfer crafting that makes a Dragon Lord piece Splendent
    (Star Dragon Lord Necklace + 7 Enhanced Thick Balaur Scale + 2 Wrathful Mind), or None."""
    if not recipes or name.startswith("Splendent "):
        return None
    for r in recipes:
        if r["product"] == f"Splendent {name}" and not any(m["name"].startswith("Artisan's") for m in r["materials"]):
            return r["product"], [(m["name"], m["count"]) for m in r["materials"]]
    return None


def _lost_on_transfer(piece):
    """What a transfer out of this piece keeps and loses, for the buy / voucher texts."""
    if not piece or not piece.soul:
        return ""
    s = piece.soul
    keep = f"tu +{piece.enchant}" + (f" y su Soul Binding ({s.rate:.0%})" if s.lines else "")
    lost = [w for w, has in (("las manastones", any(x[0] for x in s.sockets)), ("la Theostone", s.theostone))
            if has]
    return f" Con transfer pasan {keep}" + (f"; se pierden {' y '.join(lost)}" if lost else "") + "."


def _steps(plan, by_pos, kina, stones, db, levels, prices, recipes, gear_db, faction):
    steps = []
    transfer = plan.have("transfer_stone")
    # 1. wear what you have
    for pos, opts in plan.options.items():
        for o in opts:
            if o.kind == "own":
                piece = by_pos[pos]
                steps.append(Step("own", f"Ponte {o.item.name} +{o.enchant}",
                                  f"La tienes en la bolsa o el almacén: {o.item.level} de base contra tu "
                                  f"{piece.item.name if piece.item else 'hueco'} ({piece.level} ahora).",
                                  o.gain, 0, pos=pos, key=option_key(o),
                                  summary=f"Guardada: {o.item.level} de base contra {piece.base}"))
                break
    # 2. vouchers: the pieces they give and what you lack for the best one
    for dungeon, count in plan.vouchers.items():
        picks = sorted((o for opts in plan.options.values() for o in opts
                        if o.kind == "voucher" and o.source == dungeon), key=lambda o: -o.gain)
        if not picks:
            continue
        cheap = [o for o in picks if o.need == 1]
        big = [o for o in picks if o.need > 1]
        parts = []
        if cheap:
            n = min(count, len(cheap))
            parts.append(f"con 1 voucher + {kinah_text(cheap[0].kinah)} cada pieza: " +
                         ", ".join(f"{o.item.name} (+{o.gain})" for o in cheap[:max(n, 1)]))
        for o in big:
            missing = o.need - count
            parts.append(f"con {o.need} vouchers + {kinah_text(o.kinah)}: {o.item.name} (+{o.gain})" +
                         (f" · te falta{'n' if missing > 1 else ''} {missing}: el garantizado de Conquest de "
                          f"{dungeon} ofrece el voucher" if missing > 0 else ""))
        best = max(picks, key=lambda o: o.gain if o.need <= count else -1)
        ready = best.need <= count and kina >= (best.kinah or 0)
        steps.append(Step("voucher", f"Tus {count} Gear Change Voucher de {dungeon}",
                          "; ".join(parts) + "." + _lost_on_transfer(by_pos[best.pos]), best.gain, best.kinah,
                          ready=ready, pos=best.pos, key=f"voucher-dungeon:{dungeon}",
                          summary=" · ".join(
                              [f"{cheap[0].item.name} por {kinah_text(cheap[0].kinah)}"] if cheap else [])
                          + "".join(f" · {o.need} = {o.item.name}" + (f" (te falta{'n' if o.need - count > 1 else ''} "
                                                                    f"{o.need - count})" if o.need > count else "")
                                    for o in big[:1]),
                          parts=[(option_key(o), f"{o.item.name} (+{o.gain}) con {o.need} voucher"
                                                 f"{'s' if o.need > 1 else ''}") for o in picks]))
    # 3. enhance up to the level that never fails, cheapest per level first, on the pieces you keep
    enh = []
    for piece in plan.pieces:
        if piece.item and piece.to_safe and piece.enchant < piece.safe and piece.pos not in plan.replace \
                and "enhance-group" not in plan.skipped \
                and f"enhance:{piece.item.name}:{piece.pos}" not in plan.skipped:
            cost_k, cost_s, extra = piece.to_safe
            n = piece.safe - piece.enchant
            scroll = f"{piece.item.name} Enhance Scroll (Bound)"
            have = plan.scrolls.get(scroll, 0)
            text = (f"de +{piece.enchant} a +{piece.safe} (no falla): {kinah_text(cost_k)} Kinah y "
                    f"{cost_s:,} Enhance Stones" + (f" y {extra} {scroll.replace(' (Bound)', '')} (tienes {have})"
                                                     if extra else ""))
            enh.append(Step("enhance", f"Encanta {piece.item.name}", text, n, cost_k, cost_s,
                            ready=kina >= cost_k and stones >= cost_s and have >= extra, pos=piece.pos,
                            key=f"enhance:{piece.item.name}:{piece.pos}",
                            summary=f"+{piece.enchant} → +{piece.safe} · {kinah_text(cost_k)} Kinah · "
                                    f"{cost_s:,} piedras"))
    enh.sort(key=lambda s: s.kinah / max(1, s.gain))
    if len(enh) > 2:                     # one card: every piece, cheapest per level first, and the total
        total_k, total_s = sum(s.kinah for s in enh), sum(s.stones for s in enh)
        steps.append(Step("enhance", f"Encanta {len(enh)} piezas hasta donde no falla",
                          "Sin riesgo (cada intento sale): " + "; ".join(
                              f"{by_pos[s.pos].item.name} +{by_pos[s.pos].enchant}→+{by_pos[s.pos].safe} "
                              f"({kinah_text(s.kinah)} Kinah, {s.stones:,} piedras"
                              + (f", {by_pos[s.pos].to_safe[2]} pergaminos: tienes "
                                 f"{plan.scrolls.get(by_pos[s.pos].item.name + ' Enhance Scroll (Bound)', 0)}"
                                 if by_pos[s.pos].to_safe[2] else "") + ")" for s in enh)
                          + f". En total {kinah_text(total_k)} Kinah y {total_s:,} Enhance Stones (tienes "
                            f"{kinah_text(kina)} y {stones:,}); de arriba abajo si no te alcanza.",
                          sum(s.gain for s in enh), total_k, total_s, ready=enh[0].ready, pos=enh[0].pos,
                          key="enhance-group",
                          summary=f"{len(enh)} piezas a +10 · {kinah_text(total_k)} Kinah · {total_s:,} piedras",
                          parts=[(s.key, f"{by_pos[s.pos].item.name} +{by_pos[s.pos].enchant}→+{by_pos[s.pos].safe}")
                                 for s in enh]))
    else:
        steps.extend(enh)
    # belts and amulets at +10: Substance Morph into the next grade (the guide)
    for piece in plan.pieces:
        if piece.item and piece.item.category in ("Belt", "Amulet") and piece.enchant >= piece.top > 0:
            steps.append(Step("morph", f"Sube de grado {piece.item.name}",
                              f"Ya está a +{piece.enchant}: con Substance Morph pasa al grado siguiente (Heroic es el "
                              f"máximo) y vuelve a encantarse.", 0, ready=True, pos=piece.pos,
                              key=f"morph:{piece.item.name}:{piece.pos}",
                              summary="Al máximo: pásalo al grado siguiente"))
    # 4. soul binding, on the pieces you keep
    codex, resets, sync = plan.have("soul_codex"), plan.have("soul_reset"), plan.have("sync_stone")

    def food(piece, n):
        """Up to n spare pieces of the same kind and grade to eat in Syncs of this piece, the cheapest first."""
        out = [x for x in plan.spare if not x.use and x.item.category == piece.item.category
               and x.item.grade == piece.item.grade][:n]
        for x in out:
            x.use = f"Sync de tu {piece.item.name}"
        return out

    soul = []
    for piece in plan.pieces:
        s = piece.soul
        if not piece.item or not s or not s.lines or piece.pos in plan.replace or \
                f"soul:{piece.item.name}:{piece.pos}" in plan.skipped:
            continue                     # (a skipped Sync does not eat its piece: it stays for the next one)
        lines = ", ".join(f"{n or '?'} {x[4] * 100:.0f}%" if x[4] is not None else f"{n or '?'}"
                          for x in s.lines for n in [x[0]])
        if s.locked:
            fuse = plan.have("philosopher")
            soul.append((0, Step("soul", f"Línea extra en {piece.item.name}",
                                 f"Soul Binding al 100%: {FUSE_STONES} Philosopher's Stones + {kinah_text(FUSE_KINA)} "
                                 f"le dan una línea más (tienes {fuse}); esa línea no pasa con un transfer.",
                                 1, FUSE_KINA, ready=fuse >= FUSE_STONES and kina >= FUSE_KINA, pos=piece.pos,
                                 summary=f"Al 100% · {FUSE_STONES} Philosopher's Stones + {kinah_text(FUSE_KINA)} "
                                         f"(tienes {fuse})")))
            continue
        good = len(s.lines) - len(s.bad) + s.skills
        if len(s.bad) >= 2 and good < len(s.bad):       # more to throw away than to keep: reroll them all
            soul.append((1, Step("soul", f"Reset de líneas: {piece.item.name}",
                                 f"Soul Binding {s.rate:.0%} ({lines}): {len(s.bad)} líneas que no son las buscadas "
                                 f"({', '.join(s.bad)}). Reset las vuelve a tirar todas: 1 Soul Codex: Reset + "
                                 f"{kinah_text(RESET_KINA)} Kinah (tienes {resets}). Después, Bind.",
                                 len(s.bad), RESET_KINA, ready=resets >= 1 and kina >= RESET_KINA, pos=piece.pos,
                                 summary=f"{s.rate:.0%} · {len(s.bad)} líneas a cambiar · 1 Reset + "
                                         f"{kinah_text(RESET_KINA)}")))
        elif s.bad:                                       # keep the good lines: reroll the bad ones one by one
            eat = food(piece, len(s.bad))
            if eat:
                how = "Usa como comida: " + ", ".join(x.text for x in eat) + "."
            else:                                         # no piece of its kind: a Sync Stone from any spare Unique
                stone = [x for x in plan.spare if not x.use and x.item.grade == "Unique"][:max(0, len(s.bad) - sync)]
                for x in stone:
                    x.use = f"Sync Stone para tu {piece.item.name}"
                how = (f"No tienes otra pieza {piece.item.grade} del mismo tipo: usa una Sync Stone (tienes {sync})"
                       + (". Conviértela en Substance Morph (1 pieza Unique = 1 Sync Stone): "
                          + ", ".join(x.text for x in stone) + "." if stone else "."))
            soul.append((2, Step("soul", f"Sync de {' y '.join(s.bad)}: {piece.item.name}",
                                 f"Soul Binding {s.rate:.0%} ({lines}). Sync vuelve a tirar una línea (las demás "
                                 f"se quedan) comiéndose una pieza del mismo tipo y grado, o una Sync Stone, por "
                                 f"intento. {how} Después, Bind.", len(s.bad),
                                 ready=bool(eat or sync or any(x.item.grade == "Unique" for x in plan.spare)),
                                 pos=piece.pos,
                                 summary=f"{s.rate:.0%} · cambiar {', '.join(s.bad)} · comida: "
                                         + (", ".join(f"{x.item.name} +{x.enchant}" for x in eat) if eat
                                            else "una Sync Stone"))))
        else:
            soul.append((3, Step("soul", f"Bind: {piece.item.name}",
                                 f"Soul Binding {s.rate:.0%} ({lines}): sus líneas ya son de las buscadas. Bind las "
                                 f"sube: {kinah_text(BIND_KINA)} Kinah + Soul Codex por intento, más cada vez "
                                 f"(tienes {codex}).", round((1 - s.rate) * 10), BIND_KINA,
                                 ready=codex > 0 and kina >= BIND_KINA, pos=piece.pos,
                                 summary=f"{s.rate:.0%} · líneas buenas: súbelas con Bind (tienes {codex} Soul Codex)")))
    soul.sort(key=lambda x: (x[0], x[1].pos not in (1, 2), -x[1].gain))
    for _, s in soul:
        s.key = f"soul:{by_pos[s.pos].item.name}:{s.pos}"
        steps.append(s)
    # 5. manastones / soulstones on the pieces you keep (each stone rerolls every socket of the piece)
    for kind, keys, word in (("manastone", MANA_SLOTS, "Manastone"), ("soulstone", SOUL_SLOTS, "Soulstone")):
        empty = [(p, p.soul.empty) for p in plan.pieces if p.soul and p.soul.empty and p.key in keys
                 and p.pos not in plan.replace]
        if not empty:
            continue
        have = {n: c for n, c in plan.stones.items() if word in n}
        sup = sum(c for n, c in have.items() if n.startswith("Superior"))
        total = sum(n for _, n in empty)
        if kind in plan.skipped:
            continue
        steps.append(Step(kind, f"{total} huecos de {word.lower()} vacíos", key=kind, detail=
                          ", ".join(f"{p.item.name} ({n})" for p, n in empty)
                          + f". Tienes {sup} {word} Superior y {sum(have.values()) - sup} de otros grados. Cada "
                            f"piedra que pones vuelve a tirar todos los huecos de la pieza: ponlas en las que vas a "
                            f"conservar (se pierden con un transfer).", gain=total, ready=sup > 0 or bool(have),
                          summary=f"{total} huecos en {len(empty)} piezas · tienes {sup} {word} Superior"))
    # 6. Amp the weapon (+16..+20 of a Unique piece)
    weapon = by_pos.get(1)
    if weapon and weapon.item and weapon.item.grade == "Unique" and weapon.enchant >= 15 and 1 not in plan.replace:
        amp, top = weapon.enchant - 15, amp_max(weapon.item)
        if amp < top:
            frags = plan.have("amplify_fragment")
            make = min(frags // AMPLIFY_RECIPE[0], stones // AMPLIFY_RECIPE[1])
            steps.append(Step("amp", f"Amp de {weapon.item.name}: {amp}/{top}",
                              f"Cada nivel de Amp (pasado +15) da Attack y Attack increase. Tienes "
                              f"{plan.have('amplify_stone')} Amplify Stone (Unique) y con tus {frags} fragmentos haces "
                              f"{make} más ({AMPLIFY_RECIPE[0]} fragmentos + {AMPLIFY_RECIPE[1]:,} Enhance Stones cada "
                              f"una, Substance Morph). El Amp pasa con un transfer.", top - amp,
                              ready=plan.have("amplify_stone") > 0 or make > 0, pos=1, key="amp",
                              summary=f"Amp {amp}/{top} · {plan.have('amplify_stone')} Amplify Stone (+{make} con tus "
                                      f"fragmentos)"))
    # 7. buy: the most item level for the Kina, and the piece of the Dragon Lord line worth saving for
    market = [o for opts in plan.options.values() for o in opts if o.kind == "market" and o.kinah]
    if market:
        per_slot = {}                       # per slot: the best value among the pieces near its biggest gain
        for o in market:
            per_slot.setdefault(o.pos, []).append(o)
        value = []
        for opts in per_slot.values():
            top = max(o.gain for o in opts)
            value.append(min((o for o in opts if o.gain >= 0.6 * top), key=lambda o: o.kinah / o.gain))
        value.sort(key=lambda o: o.kinah / o.gain)
        for o in value[:3]:
            piece = by_pos[o.pos]
            stones_needed = db.transfer_stones(o.item)
            detail = (f"{o.item.level} de base contra {piece.item.name if piece.item else 'nada'} "
                      f"({piece.base}) por {kinah_text(o.kinah)}: {kinah_text(o.kinah / o.gain)} por nivel.")
            if piece.enchant >= TRANSFER_WORTH and stones_needed:
                detail += (f" Transfer: {stones_needed} Transfer Stone{'s' if stones_needed > 1 else ''} (tienes "
                           f"{transfer}).") + _lost_on_transfer(piece)
            if o.ladder:
                detail += " Es de la línea Dragon Lord: con transfer crafting sigue subiendo hasta 102."
            steps.append(Step("buy", f"Compra {o.item.name}", detail, o.gain, o.kinah, ready=kina >= o.kinah,
                              pos=o.pos, key=option_key(o),
                              summary=f"{o.item.level} contra {piece.base} · {kinah_text(o.kinah / o.gain)} por nivel"
                                      + (" · línea Dragon Lord" if o.ladder else "")))
        routes = []                         # the Dragon Lord line: (level it reaches now, Kina, option, how)
        for o in market:
            if not o.ladder:
                continue
            routes.append((o.item.level, o.kinah, o, ""))
            step = splendent_step(o.item.name, recipes)
            nxt = gear_db.named(step[0], faction) if step else None
            if nxt:
                known = sum((prices.get(m) or (0,))[0] * n for m, n in step[1])
                unknown = [m for m, _ in step[1] if m not in prices]
                own = [m for m in unknown if m.startswith("Enhanced ") and levels.get("Alchemy", 0) >= 51]
                how = (f"Compra {o.item.name} ({kinah_text(o.kinah)}) y pásalo a {nxt.name} ({nxt.level}) en la "
                       f"mesa de Artesanía con transfer crafting (no pide nivel y no falla): "
                       + ", ".join(f"{n}× {m}" for m, n in step[1])
                       + (f" ({', '.join(own)}: los haces tú con Alquimia)" if own else "") + ".")
                routes.append((nxt.level, o.kinah + known, o, how))
        if routes:
            top_level = max(r[0] for r in routes)
            reach, cost, o, how = min((r for r in routes if r[0] == top_level), key=lambda r: r[1])
            if o not in value[:3] or how:
                piece = by_pos[o.pos]
                steps.append(Step(
                    "save" if kina < cost else "buy",
                    f"{'Ahorra para' if kina < cost else 'Compra'} {o.item.name}",
                    (how or f"Compra {o.item.name} ({kinah_text(o.kinah)}).")
                    + f" La que más te sirve a futuro: {reach} contra tu {piece.item.name if piece.item else 'hueco'} "
                      f"({piece.base}) y, con transfer crafting, sigue hasta {LADDER_TOP} sin perder el encantamiento."
                    + (f" Te faltan {kinah_text(cost - kina)}." if kina < cost else ""),
                    reach - piece.base, cost, ready=kina >= cost, pos=o.pos, key=option_key(o),
                    summary=f"{reach} hoy y hasta {LADDER_TOP} con transfer crafting"
                            + (f" · te faltan {kinah_text(cost - kina)}" if kina < cost else "")))
    # 8. farm: what your dungeons give for your weakest pieces, one card per dungeon
    drops = sorted((o for opts in plan.options.values() for o in opts if o.kind == "drop"), key=lambda o: -o.gain)
    by_dungeon, seen = {}, set()
    for o in drops:
        if o.pos not in seen:
            seen.add(o.pos)
            by_dungeon.setdefault(o.source, []).append(o)
    for dungeon, opts in by_dungeon.items():
        pity = sorted({(m, n) for o in opts for m, n, _ in db.cube_for(dungeon, o.item.name)}, key=lambda x: x[1])
        steps.append(Step("farm", f"Farmea en {dungeon}",
                          ", ".join(f"{o.item.name} (+{o.gain}, {o.chance * 100:.1f}% por clear)" for o in opts)
                          + (". Garantizado (eliges una): " + ", ".join(f"{m} cada {n} cubos" for m, n in pity)
                             if pity else "") + ".", opts[0].gain, ready=True, pos=opts[0].pos,
                          key=f"farm-dungeon:{dungeon}",
                          summary=f"{len(opts)} piezas que te sirven"
                                  + (f" · garantizado cada {pity[0][1]} cubos ({pity[0][0]})" if pity else ""),
                          parts=[(option_key(o), f"{o.item.name} (+{o.gain})") for o in opts]))
    # 9. what the rest of your spare pieces are worth: a Sync Stone or a Transfer Stone each
    rest = [x for x in plan.spare if not x.use and x.item.grade == "Unique"]
    if rest:
        need = sum(db.transfer_stones(o.item) or 1 for o in (
            o for opts in plan.options.values() for o in opts[:1] if o.kind in ("market", "voucher")))
        for x in rest:
            x.use = "Transfer Stone o Sync Stone"
        steps.append(Step("spare", f"{len(rest)} piezas Unique que no usas → Transfer o Sync Stones",
                          "Cada una se convierte en 1 Transfer Stone (Unique) o 1 Sync Stone (Unique) en Substance "
                          f"Morph (la que elijas): {', '.join(x.text for x in rest[:5])}"
                          f"{'…' if len(rest) > 5 else ''}. Tienes {transfer} Transfer Stones y {plan.have('sync_stone')} "
                          f"Sync Stones; un transfer a nivel 86+ pide 2. Primero las +0: las encantadas devuelven "
                          f"Enhance Stones si las rompes." + (f" Si haces con transfer todos los cambios que te "
                                                              f"recomiendo, te harían falta unas {need}." if need
                                                              else ""), 0, ready=True, key="spare",
                          summary=f"{len(rest)} piezas → 1 Transfer Stone o 1 Sync Stone cada una"))
    # 10. Theostones: weapons and guards, under 1% of the damage (the guide): last
    for pos in (1, 2):
        piece = by_pos.get(pos)
        if piece and piece.item and piece.soul and not piece.soul.theostone and pos not in plan.replace:
            steps.append(Step("theostone", f"Theostone para {piece.item.name}",
                              f"No tiene ninguna: grabar una cuesta {kinah_text(THEOSTONE_KINA)} Kinah (tienes "
                              f"{plan.have('theostone')}). Aporta menos del 1% del daño y se pierde con un transfer.",
                              0, THEOSTONE_KINA, ready=plan.have("theostone") > 0, pos=pos, key=f"theostone:{pos}",
                              summary=f"Sin Theostone · {kinah_text(THEOSTONE_KINA)} Kinah · menos del 1% del daño"))
    return [s for s in steps if s.key not in plan.skipped]


def next_steps(plan, n=3):
    """The first n steps you can do now (then the rest), for the module's panel."""
    ready = [s for s in plan.steps if s.ready and s.kind not in ("farm", "theostone", "spare")]
    rest = [s for s in plan.steps if s not in ready]
    return (ready + rest)[:n]
