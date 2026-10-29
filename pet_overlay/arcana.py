"""Arcana (Personaje › Arcanas, and a step of Mejoras): the cards of each character, what they raise and what to do
with them - which to wear, what to look for in each kind and which to feed to which.

Cards (data/arcana_db.json, tools/build_arcana_db.py, from aion2data): five kinds, one worn of each (Chalice 41,
Parchment 42, Compass 43, Bell 44, Mirror 45 in the equipped list), sets Vigor and Magic (2 and 4 of a set give its
bonus), Common to Unique. A card has a god stat (+1 per enhance level, Destiny 23 on a +3) and skill effects of the
wearer's class, each from its kind's pool: the Chalice any active or passive, the Parchment and the Compass six
actives each, the Bell and the Mirror five passives each. A new card has 1 (Common) to 4 (Unique) effects at +1; each
enhance level adds a new one at +1 until it has 4, then raises one of them (picked at random) by +1, up to +4 each: a
Unique +5 has 9 levels at most. Enhancing is feeding: a card fed to another gives its grade's base exp plus all the
exp put into it (a Unique +2: 2,000 + 9,000), and the Training Arcana give theirs (200 to 5,000).

The idea comes from the Korean community's skill simulator (a spreadsheet): set the level you want for each skill;
the skill's level is its points (10) + Daevanion + gear + arcana; the cards make up what is missing.

In the game's messages: every card is in the full item list (1156 at login, 1b56 when it changes) with the equipped
pieces' layout - container 11 (worn, position 41-45) or 14 (the Arcana bag), the +N where a piece has it and the
item level + N - then after the slot byte: ... | u8 1 | u16 god stat | u32 value (+1 per N) | u8 0 | u8 0 | u8 k |
k x (u32 skill, u8 1) | u8 k | k x (u32 skill, u8 levels added by enhancing) (2026-10-22: Agame's Parchment of Magic
+3 = Bolt 1+1, Debilitating Mark 1+0, Radiant Recovery 1+1, Condemnation 1+1, Destiny 23, item level 83).
parse_cards() finds that block by its skills (they must be of the card's kind's pool).
"""
import itertools
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import tr, tr_data

TYPES = ("Chalice", "Parchment", "Compass", "Bell", "Mirror")
SLOT_OF = {"Chalice": 41, "Parchment": 42, "Compass": 43, "Bell": 44, "Mirror": 45}
WORN, BAG = 11, 14
MAX_EFFECTS, MAX_EFFECT_LEVEL = 4, 4
GRADE_ORDER = {"Common": 0, "Rare": 1, "Epic": 2, "Unique": 3}
GRADE_COLOR = {"Common": "#c8ccd6", "Rare": "#5bd06a", "Epic": "#4aa3ff", "Unique": "#f0c24e"}
DEFAULT_ACTIVE_TARGET = 16          # the highest specialization of an active skill in Global (aion2data skill-levels)
VERSION_PICK = {   # class prefix -> per card, the version (Vigor / Magic: its Pantheon stat) most of the top wear. Clerics
    # (49 of NA East's top, 2026-10, and aion2maps' guide agrees): Chalice Vigor 44 of 49 (Time: Combat Speed), Parchment
    # Vigor 32 (Life: HP), Bell Magic 34 (Destruction: Attack), Mirror Vigor 32 (Illusion: cooldowns); Compass a tie
    17: {"Chalice": "Vigor", "Parchment": "Vigor", "Bell": "Magic", "Mirror": "Vigor"},
}
PANTHEON = {      # what a card's Pantheon stat gives at 20 (aion2maps' cleric guide, pasted by the user on 2026-10-23;
    # aion2data and the game's traffic only have its name and value). Game texts: not translated
    "Time": "+2% Combat Speed and Double Resist", "Space": "+2% Move Speed and Block",
    "Life": "+2% HP and Regeneration", "Destiny": "+2% MP and Endurance",
    "Freedom": "+2% Accuracy and Evasion", "Death": "+2% Critical Hit rating (it stops paying at the crit cap)",
    "Justice": "+2% Defense and Perfect", "Destruction": "+2% Attack increase and Perfect Resist",
    "Illusion": "-2% cooldowns and +2% Endurance Penetration", "Wisdom": "+2% Double Chance and -2% MP cost",
}
PRIORITY_DECAY = 0.75               # a skill target's weight is this to the power of its place in your list (1, 0.75,
#                                     0.56...): the first one counts most, but a lot of the next ones can still win
MAX_CONTENDERS = 3                  # alternatives kept out of the food per kind (2.5.5)
MIN_CHANCE = 0.1                    # an alternative that does not tie now: its least chance of beating the card to
#                                     wear once enhanced to its top
VERSION_COST = {                    # kind -> what a card of the version you do not want (Set y versión, or the top's)
    # has to give more in your skills, in levels of your first skill target (2.5.12): its Pantheon stat is the price.
    # Only where the stat matters - kanonxo's Aion 2 Bible (ch. 6): "the 3 most important lines here are Chalice (Time -
    # Combat Speed), Bell (Destruction - Attack Increase %), and Mirror (Wisdom - Double Chance)", Parchment and Compass
    # "can be from either set"; both Mirror stats are among its highest value ones (Illusion: cooldowns), so the Mirror
    # and the rest only break ties. Before, the version only broke ties everywhere: a Chalice of Magic trading a level
    # of Divine Aura for one of Condemnation beat the Vigor one and its +2% Combat Speed
    "Chalice": 1.0, "Bell": 1.0,
}


def _u32(b, p):
    return struct.unpack_from("<I", b, p)[0]


class ArcanaDB:
    def __init__(self, path):
        try:
            data = tr_data("arcana_db.json", json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            data = {}
        self.cards = {int(k): v for k, v in data.get("cards", {}).items()}
        self.pools = {kind: {c: [s for s, _ in v] for c, v in by.items()} for kind, by in data.get("pools", {}).items()}
        self.chances = {kind: {s: chance for v in by.values() for s, chance in v}
                        for kind, by in data.get("pools", {}).items()}
        self.enhance = data.get("enhance", {})
        self.sets = data.get("sets", {})
        self.transcendence = data.get("transcendence", {})       # dungeon -> {"url", "stages": {stage: {il, arcana}}}
        self.create = data.get("create") or {}
        self.training = {int(k): v for k, v in data.get("training", {}).items()}
        self.credit = data.get("credit") or {"text": "aion2data.com", "url": "https://aion2data.com/arcana"}
        self._pool_ids = {kind: {s for v in by.values() for s in v} for kind, by in self.pools.items()}

    def pool(self, kind, prefix):
        """The skills (base ids) a kind's effects can raise for a class (skill id prefix: 17 Cleric...)."""
        return self.pools.get(kind, {}).get(str(prefix), [])

    def max_level(self, grade):
        return len(self.enhance.get(grade, {}).get("exp", []))

    def to_next(self, grade, enchant):
        """Exp from +enchant to the next level, or None at the top."""
        exp = self.enhance.get(grade, {}).get("exp", [])
        return exp[enchant] if 0 <= enchant < len(exp) else None

    def food(self, grade, enchant, exp=0):
        """The exp a card gives fed to another: its grade's base at +0 plus everything put into it."""
        table = self.enhance.get(grade, {}).get("food", [])
        return (table[min(enchant, len(table) - 1)] if table else 0) + (exp or 0)

    def feed_kinah(self, grade, enchant):
        table = self.enhance.get(grade, {}).get("kinah", [])
        return table[min(enchant, len(table) - 1)] if table else 0


@dataclass
class Card:
    stack: str
    item: int
    name: str
    kind: str
    set: str
    grade: str
    level: int                      # item level
    enchant: int
    effects: list                   # [[skill base id, level]]
    worn: bool
    stat: tuple = ("", 0)           # (god stat, value)
    exp: int = 0                    # exp put into the current level, when known
    fed: object = None              # the planner's picture of a card after eating the one you wear (_fed): that card
    real: object = None             # and the real one it pictures

    def levels(self, skill):
        return sum(lv for s, lv in self.effects if s == skill)

    @property
    def total(self):
        return sum(lv for _, lv in self.effects)


def parse_cards(body, db, update=False):
    """{stack: [item id, enchant, container, slot, effects, count, stat]} of the Arcana cards in a 1156 (or 1b56:
    count 0 = the card is gone). effects: [[skill, level]]; stat: [stat id, value]."""
    out, p, end = {}, 8, len(body) - 60
    while p < end:
        item = _u32(body, p)
        card = db.cards.get(item)
        if card is None or _u32(body, p + 4) > 1 or any(body[p + 8:p + 12]) or (_u32(body, p + 4) == 0 and not update):
            p += 1
            continue
        count, cont, pos = _u32(body, p + 4), body[p + 12], body[p + 13]
        hit = None
        for s1 in (0, 1):
            if not card["level"] <= _u32(body, p + 40 + s1) <= card["level"] + 30:
                continue
            for s2 in (0, 1):
                at = p + 44 + s1 + s2
                if body[at] == (pos if cont == WORN else 0):
                    block = _effects(body, at, db._pool_ids.get(card["type"], set()))
                    if block:
                        hit = (s1, block)
                        break
            if hit:
                break
        if hit is None:
            p += 1
            continue
        s1, (effects, stat, end_at) = hit
        enchant = body[p + 26 + s1]
        stack = f"{_u32(body, p - 8)}:{int.from_bytes(body[p - 3:p - 1], 'little')}"
        out[stack] = [item, enchant if enchant <= 10 else 0, cont, pos if cont == WORN else 0, effects, count, stat]
        p = end_at
    return out


def _effects(body, slot_at, pool):
    """([[skill, level]], [stat id, value], end) of the block after a card's slot byte, or None."""
    for start in range(slot_at + 18, slot_at + 34):
        if start + 8 >= len(body) or body[start] != 1:
            continue
        stat, value = struct.unpack_from("<HI", body, start + 1)
        if not (0 < stat < 4000 and 0 < value <= 200):
            continue
        for skip in (2, 1, 0, 3):
            q = start + 7 + skip
            if q >= len(body):
                continue
            k = body[q]
            if not 0 < k <= MAX_EFFECTS or q + 1 + 10 * k > len(body):
                continue
            first = [(_u32(body, q + 1 + 5 * i), body[q + 5 + 5 * i]) for i in range(k)]
            if not all(s in pool and 1 <= lv <= MAX_EFFECT_LEVEL for s, lv in first):
                continue
            r = q + 1 + 5 * k
            if body[r] != k:
                continue
            second = [(_u32(body, r + 1 + 5 * i), body[r + 5 + 5 * i]) for i in range(k)]
            if [s for s, _ in second] != [s for s, _ in first] or any(n >= MAX_EFFECT_LEVEL for _, n in second):
                continue
            return [[s, lv + n] for (s, lv), (_, n) in zip(first, second)], [stat, value], r + 1 + 5 * k
    return None


def cards_of(stored, db):
    """[Card] of a character's stored cards ({stack: [item, enchant, container, slot, effects, count, stat]})."""
    out = []
    for stack, (item, enchant, cont, slot, effects, *_rest) in (stored or {}).items():
        c = db.cards.get(int(item))
        if c is None:
            continue
        stat = _rest[1] if len(_rest) > 1 and _rest[1] else None
        out.append(Card(stack, int(item), c["name"], c["type"], c["set"], c["grade"], c["level"], int(enchant),
                        [list(e) for e in effects], cont == WORN, (c["stat"][0], stat[1] if stat else c["stat"][1])))
    return out


# --- what to do with them ---------------------------------------------------------------------------------------
@dataclass
class SkillNeed:
    skill: int
    name: str
    kind: str                # "active" | "passive"
    target: int
    level: int               # now (with the cards you wear)
    worn: int                # what the cards you wear add
    best: int                # what the best cards you have would add
    parts: tuple = ()        # (points, Daevanion, gear) when the game sent them (0051)
    priority: int = 0        # its place in your skill targets (1 = the first), 0 without a target

    @property
    def without(self):
        return self.level - self.worn

    @property
    def need(self):
        """Levels the cards should give."""
        return max(0, self.target - self.without) if self.target else 0

    @property
    def missing(self):
        """Still missing with the best cards you have."""
        return max(0, self.need - self.best)


@dataclass
class Feed:
    target: Card
    fodder: list             # [(Card or a Training Arcana name, exp)]
    exp: int
    kinah: int
    to: int                  # the +N it reaches
    useful: float            # expected levels it adds to skills you need (each level raises a random effect)
    waste: int = 0           # exp past the card's top (lost)


@dataclass
class Contender:
    """A card that is not the one to wear of its kind but may be (2.5.5): kept out of the food, so a card that may
    serve is not eaten before you know."""
    card: Card
    rival: Card              # the one to wear of its kind (the plan's pick)
    useful: int              # levels it adds to your skill targets, in the best mix of your cards with it
    base: int                # the same with the plan's pick
    chance: float            # the chance that, enhanced to its top, it beats the pick (each skill by its priority)
    tie: float               # the chance that it ends even
    top: int                 # its top +N
    exp: int                 # the exp to take it there
    rival_edge: list = field(default_factory=list)   # [skill] the pick gives more of, by priority
    own_edge: list = field(default_factory=list)     # [skill] it gives more of, by priority
    mix: list = field(default_factory=list)          # [Card] of other kinds its best mix wears instead of the plan's
    reach: int = 0           # the +N your food takes it to now (all of it, the one you wear aside)
    reach_chance: float = 0.0    # its chance of beating the pick there
    reach_tie: float = 0.0       # and of ending even

    @property
    def can_win(self):
        return self.chance > 0


@dataclass
class ArcanaPlan:
    cards: list = field(default_factory=list)       # [Card], worn first
    skills: list = field(default_factory=list)      # [SkillNeed], the ones with a target first
    worn: dict = field(default_factory=dict)        # kind -> Card
    best: dict = field(default_factory=dict)        # kind -> Card (the cards to wear)
    changes: list = field(default_factory=list)     # [(kind, Card worn or None, Card to wear, useful levels gained)]
    look_for: dict = field(default_factory=dict)    # kind -> [(skill, name, levels missing)]
    spare: list = field(default_factory=list)       # [Card] not worn and not the best of their kind: food
    feeds: list = field(default_factory=list)       # [Feed]
    training: list = field(default_factory=list)    # [(name, count, exp each)] in the bag
    sets: dict = field(default_factory=dict)        # set -> cards of it among the best
    pool: dict = field(default_factory=dict)        # kind -> the skills its effects can raise for this class
    transfers: dict = field(default_factory=dict)   # kind -> (the card you wear, the new one to feed it to, its exp,
    #                                                 the +N the new one reaches)
    pick: dict = field(default_factory=dict)        # kind -> the version you want (Set y versión) or, if you left it
    pick_from: dict = field(default_factory=dict)   # to the top, the one most of them wear (VERSION_PICK): "you" / "top"
    top_pick: dict = field(default_factory=dict)    # kind -> the top's version (VERSION_PICK), for the tab
    alternatives: dict = field(default_factory=dict)   # kind -> [Contender], the most likely to win first
    level_pick: Card = None          # the alternative you chose to enhance to settle it (it gets the food first)
    transfer_odds: dict = field(default_factory=dict)  # kind -> (better, even, worse): the new card once it ate the old
    #                                                    one, every way its enhances can fall, against keeping yours
    transfer_trade: dict = field(default_factory=dict)  # kind -> [(skill, levels)] it adds (+) or takes (-) on average
    trial: tuple = None              # (kind, the card it would eat, the new card, its transfer_odds, the +N it would
    #                                  reach): the risky transfer AionDex tries first with the food you have to spare
    useful_now: int = 0
    useful_best: int = 0

    @property
    def fodder_exp(self):
        return sum(f.exp for f in self.feeds)


def _useful(cards, needs, weights=None):
    """Levels the cards add to skills that need them (each skill up to what it needs), each weighed by its priority
    when weights are given."""
    got = {}
    for c in cards:
        for s, lv in c.effects:
            got[s] = got.get(s, 0) + lv
    return sum(min(n, got.get(s, 0)) * (weights.get(s, 1.0) if weights else 1) for s, n in needs.items())


def _set_bonus(cards):
    counts = {}
    for c in cards:
        counts[c.set] = counts.get(c.set, 0) + 1
    return sum((n >= 2) + (n >= 4) for n in counts.values())


def _final(card, plan):
    """The card already has every skill you still miss that a card of its kind can have (up to 4): no card of its kind
    can be better for your skill targets, so what you put in it stays (worth()'s "yes")."""
    needs = {n.skill: n for n in plan.skills}
    wanted = [s for s in plan.pool.get(card.kind, ()) if needs.get(s) and needs[s].missing]
    mine = {s for s, _ in card.effects if s in wanted}
    return bool(mine) and len(mine) >= min(MAX_EFFECTS, len(wanted))


def _fed(db, card, food, pool):
    """The card after eating another (all of its exp carries over), with its effects' expected levels: each level it
    gains adds an effect from its kind's pool (any it lacks) until it has 4, then raises one of the 4 at random."""
    exp, lvl = db.food(food.grade, food.enchant, food.exp) + card.exp, card.enchant
    while db.to_next(card.grade, lvl) is not None and exp >= db.to_next(card.grade, lvl):
        exp -= db.to_next(card.grade, lvl)
        lvl += 1
    slots = [({s: 1.0}, float(lv)) for s, lv in card.effects]
    mine = {s for s, _ in card.effects}
    for _ in range(lvl - card.enchant):
        if len(slots) < MAX_EFFECTS:
            left = [s for s in pool if s not in mine]
            if left:
                slots.append(({s: 1 / len(left) for s in left}, 1.0))
            continue
        slots = [(w, lv + 1 / len(slots)) for w, lv in slots]
    got = {}
    for w, lv in slots:
        for s, share in w.items():
            got[s] = got.get(s, 0) + lv * share
    # it keeps the eater's worn flag: between two pictures worth the same, the card you already wear stays (2.4.2)
    return Card(card.stack, card.item, card.name, card.kind, card.set, card.grade, card.level, lvl,
                [[s, v] for s, v in got.items()], card.worn, card.stat, 0, food, card)


def _outcomes(effects, raises, pool):
    """{the effects after `raises` enhances, as a sorted tuple of (skill, level): its chance}: each enhance adds an
    effect of its kind's pool (any it lacks) while it has fewer than 4, then raises one of the ones under +4 at random."""
    states = {tuple(sorted((s, lv) for s, lv in effects)): 1.0}
    for _ in range(raises):
        after = {}
        for eff, p in states.items():
            have = dict(eff)
            moves = ([(s, 1) for s in pool if s not in have] if len(have) < MAX_EFFECTS else []) or \
                [(s, lv + 1) for s, lv in eff if lv < MAX_EFFECT_LEVEL]
            for s, lv in moves or [(None, 0)]:
                key = tuple(sorted({**have, s: lv}.items())) if s is not None else eff
                after[key] = after.get(key, 0.0) + p / max(1, len(moves))
        states = after
    return states


@dataclass
class _Effects:
    effects: tuple


def _food_rank(what):
    """Lower rarity first (2.5.5): the Training Arcana, then Common, Rare, Epic, Unique."""
    return GRADE_ORDER.get(what.grade, 0) if isinstance(what, Card) else -1


def twins(plan, card, eater=None, skip=()):
    """The cards that look exactly like this one in the game's list (the same card and +N) but are not food - an
    alternative, the one to wear - other than `eater` (the card being enhanced) and the stacks in skip. The game shows
    them alike and its order among them is not known, so AionDex cannot tell which cell is which (2.5.7: a Bell of
    Magic +0 with Empyrean Lord's Grace, an alternative, was framed as food in place of one just like it)."""
    keep = {r.card.stack for rows in plan.alternatives.values() for r in rows} | {c.stack for c in plan.best.values()}
    return [c for c in plan.cards if c.stack in keep and c.stack != card.stack and c.stack not in skip and not c.worn
            and (eater is None or c.stack != eater.stack) and (c.item, c.enchant) == (card.item, card.enchant)]


def tells_apart(plan, card, others):
    """The names of the fewest of the card's effects that none of `others` (cards just like it in the game's list) has
    all of: what to look for in the game's tooltip to find it (2.5.8). All of its effects when that is not enough."""
    names = {n.skill: n.name for n in plan.skills}
    mine = [s for s, _ in card.effects]
    chosen, left = [], list(others)
    while left:
        lacking = {s: sum(1 for o in left if not o.levels(s)) for s in mine if s not in chosen}
        if not lacking or not max(lacking.values()):
            return [names.get(s, str(s)) for s in mine]
        s = max(lacking, key=lacking.get)
        chosen.append(s)
        left = [o for o in left if o.levels(s)]
    return [names.get(s, str(s)) for s in chosen]


def _enhance_value(card, needs, have, pool, db, weights=None):
    """Expected levels the card's next enhance adds to skills that still need them, each weighed by its priority: a
    new effect (from its kind's pool) while it has fewer than 4, then +1 to one of its effects at random."""
    if db.to_next(card.grade, card.enchant) is None:
        return 0.0
    w = weights or {}
    mine = {s for s, _ in card.effects}
    if len(card.effects) < MAX_EFFECTS:
        left = [s for s in pool if s not in mine]
        return sum(w.get(s, 1.0) for s in left if needs.get(s, 0) > have.get(s, 0)) / len(left) if left else 0.0
    open_ = [(s, lv) for s, lv in card.effects if lv < MAX_EFFECT_LEVEL]
    return sum(w.get(s, 1.0) for s, _ in open_ if needs.get(s, 0) > have.get(s, 0)) / len(open_) if open_ else 0.0


def build(db, prefix, cards, levels, targets, names, kinds, training=None, versions=None, final_first=False,
          picks=None):
    """The plan of one character. levels: {skill: level now} or {skill: (level, points, [Daevanion, 0, gear, Arcana])}
    (the game's skill list, with the worn cards); targets: {skill: level wanted}, in priority order (the first counts
    most: PRIORITY_DECAY; 0 / missing = no target); names / kinds: skill -> name / "active" | "passive"; training:
    [(name, count, exp)] of the Training Arcana in the bag; versions: kind -> "Vigor" | "Magic" | "any" (the version you
    want of each card; a kind left out goes by the top's, VERSION_PICK); final_first: the food goes first to the cards
    that are already the best possible of their kind for your skill targets (they never have to be replaced), then to
    the one that gives the most per exp; picks: {"level": the stack of the alternative you chose to enhance (it gets
    the food first, and never eats the one you wear), "food": [stacks of alternatives you sent back to the food],
    "direct": [stacks of new cards you chose to feed the old one to without trying them first], "hold": [kinds whose
    transfer waits while another new card is tried - set by build itself]}."""
    picks = picks or {}
    plan = ArcanaPlan()
    plan.cards = sorted(cards, key=lambda c: (not c.worn, TYPES.index(c.kind) if c.kind in TYPES else 9,
                                              -GRADE_ORDER.get(c.grade, 0), -c.enchant, -c.total))
    plan.worn = {c.kind: c for c in plan.cards if c.worn}
    plan.pool = {kind: db.pool(kind, prefix) for kind in TYPES}
    plan.top_pick = VERSION_PICK.get(prefix, {})
    for kind in TYPES:
        choice = (versions or {}).get(kind)
        if choice in ("Vigor", "Magic"):
            plan.pick[kind], plan.pick_from[kind] = choice, "you"
        elif choice != "any" and kind in plan.top_pick:
            plan.pick[kind], plan.pick_from[kind] = plan.top_pick[kind], "top"
    pool_all = {s for pool in plan.pool.values() for s in pool}
    worn_add = {}
    for c in plan.worn.values():
        for s, lv in c.effects:
            worn_add[s] = worn_add.get(s, 0) + lv
    needs = {}
    ranked = [s for s, v in targets.items() if v]
    weights = {s: PRIORITY_DECAY ** i for i, s in enumerate(ranked)}
    for s in sorted(pool_all, key=lambda s: (kinds.get(s) != "active", names.get(s, str(s)))):
        now = levels.get(s) or 0
        level, points, parts = (now[0], now[1], now[2]) if isinstance(now, (tuple, list)) else (now, None, None)
        worn = parts[3] if parts and len(parts) >= 4 else worn_add.get(s, 0)      # the game's own count first
        need = SkillNeed(s, names.get(s, str(s)), kinds.get(s, ""), int(targets.get(s) or 0), int(level or 0), worn,
                         0, (points, parts[0], parts[2]) if parts and len(parts) >= 4 else (),
                         ranked.index(s) + 1 if s in weights else 0)
        plan.skills.append(need)
        if need.need:
            needs[s] = need.need
    plan.useful_now = _useful(plan.worn.values(), needs)

    # the cards to wear: of each kind, among the ones of its highest item level (a lower grade costs item level and god
    # stat), the combination that adds the most to what you need - each card of the version you do not want costing
    # VERSION_COST (with five cards any mix gives two set bonuses: its Pantheon stat is what the version changes) -
    # then the set bonuses, the version you want, the most levels, the +N. A card also
    # competes as it would be after eating an enhanced one of its kind (all its exp carries over and its levels are
    # dealt again among the eater's effects): an enhanced card of yours does not hide a better one that shows up later.
    # The food is the one you wear when it is enhanced AND the enhanced one with the most exp: before 2.4.2 only the
    # worn one, so once you put on the new card (+0) the old +3 was no longer seen as its food and AionDex told you to
    # wear the old one again - a loop. The alternative you chose to enhance never pictures eating the one you wear: you
    # enhance it to see which of the two ends better, without breaking yours (2.5.5). Nor does one you sent to the food,
    # nor any of a kind whose transfer waits for another new card to be tried (2.5.11)
    leveling = picks.get("level")
    dropped = set(picks.get("food") or ())
    hold = set(picks.get("hold") or ())
    options = []
    for kind in TYPES:
        mine = [c for c in plan.cards if c.kind == kind]
        top = max((c.level for c in mine), default=0)
        tops = [c for c in mine if c.level == top]
        worn = plan.worn.get(kind)
        foods = [worn] if worn is not None and worn.enchant else []
        richest = max((c for c in mine if c.enchant), key=lambda c: db.food(c.grade, c.enchant, c.exp), default=None)
        if richest is not None and richest not in foods:
            foods.append(richest)
        tops += [_fed(db, c, food, plan.pool[kind]) for food in foods for c in list(tops)
                 if c is not food and c.stack != leveling and c.stack not in dropped and kind not in hold
                 and db.to_next(c.grade, c.enchant) is not None]
        options.append(tops or [None])

    def off(chosen):
        """What the cards of the version you do not want cost (VERSION_COST)."""
        return sum(VERSION_COST.get(c.kind, 0.0) for c in chosen if plan.pick.get(c.kind) not in (None, c.set))

    def score(chosen, effects=None, card=None):
        """What a mix gives your skill targets (by priority), less VERSION_COST for each card of the version you do
        not want; effects: one card's effects in place of `card` (one way its enhances can fall)."""
        cards_ = chosen + ([_Effects(effects)] if effects is not None else [])
        return _useful(cards_, needs, weights) - off(chosen + ([card] if card is not None else []))

    def best_of(opts):
        best, best_key = None, None
        for combo in itertools.product(*opts):
            chosen = [c for c in combo if c is not None]
            key = (score(chosen), _set_bonus(chosen),
                   sum(plan.pick.get(c.kind) == c.set for c in chosen), sum(c.total for c in chosen),
                   sum(GRADE_ORDER.get(c.grade, 0) * 10 + c.enchant for c in chosen), sum(c.worn for c in chosen))
            if best_key is None or key > best_key:
                best, best_key = chosen, key
        return best or [], best_key

    best, best_key = best_of(options)
    expected = {c.kind: c for c in best or []}            # with the new cards as they would be after a transfer
    plan.best = {k: c.real or c for k, c in expected.items()}
    for kind, c in expected.items():
        if c.fed is not None:
            plan.transfers[kind] = (c.fed, c.real, db.food(c.fed.grade, c.fed.enchant, c.fed.exp), c.enchant)

    def adds_of(chosen):
        out = {}
        for c in chosen:
            for s, lv in c.effects:
                out[s] = out.get(s, 0) + lv
        return out

    # what a transfer risks (2.5.11): every way the new card's enhances can fall once it ate the old one, against the
    # best mix without that transfer, each skill by its priority. «+0.0 niveles» hid a coin toss: a Chalice of Magic
    # +0 with Condemnation +1 eating a Chalice of Vigor +4 traded a level of Divine Aura for one of Condemnation on
    # average, and 31% of the time no raise fell on Condemnation - a level down, with the old card already eaten
    edges = {}                          # kind -> what its transfer adds on average, by priority (the trial goes first)
    for kind, (old, new, _, to) in plan.transfers.items():
        i = TYPES.index(kind)
        keep, keep_key = best_of(options[:i] + [[c for c in options[i] if c is None or c.fed is None]]
                                 + options[i + 1:])
        edges[kind] = best_key[0] - keep_key[0]
        others = [c for c in best if c.kind != kind]
        better = even = worse = 0.0
        for eff, p in _outcomes(new.effects, to - new.enchant, plan.pool[kind]).items():
            v = score(others, eff, new)
            if v > keep_key[0] + 1e-9:
                better += p
            elif v > keep_key[0] - 1e-9:
                even += p
            else:
                worse += p
        plan.transfer_odds[kind] = (better, even, worse)
        before, after = adds_of(keep), adds_of(best)
        trade = [(s, min(needs[s], after.get(s, 0)) - min(needs[s], before.get(s, 0)))
                 for s in sorted(needs, key=lambda s: -weights.get(s, 0))]
        plan.transfer_trade[kind] = [(s, d) for s, d in trade if abs(d) >= 0.05]
    plan.useful_best = _useful(plan.best.values(), needs)
    adds = adds_of(plan.best.values())
    for need in plan.skills:
        need.best = adds.get(need.skill, 0)
    for kind in TYPES:
        worn, chosen = plan.worn.get(kind), plan.best.get(kind)
        if chosen is not None and chosen is not worn:
            others = [c for k, c in expected.items() if k != kind]
            gain = _useful(others + [expected[kind]], needs) - _useful(others + ([worn] if worn else []), needs)
            plan.changes.append((kind, worn, chosen, gain))
    for c in plan.best.values():
        plan.sets[c.set] = plan.sets.get(c.set, 0) + 1

    # what to look for: per kind, the skills of its pool still missing levels with the best cards
    missing = {n.skill: n.missing for n in plan.skills if n.missing}
    for kind in TYPES:
        rows = [(s, names.get(s, str(s)), missing[s]) for s in db.pool(kind, prefix) if s in missing]
        if rows:
            plan.look_for[kind] = sorted(rows, key=lambda r: (-weights.get(r[0], 0), -r[2]))
    plan.look_for = dict(sorted(plan.look_for.items(), key=lambda kv: -max(weights.get(r[0], 0) for r in kv[1])))

    # the alternatives (2.5.5): of each kind, the cards of its highest item level that are not the one to wear but may
    # be - in the best mix of your cards with them (they can change what serves in the other slots too) they add as
    # many levels to your skill targets as the plan's, or enhancing them to their top beats it with MIN_CHANCE or more
    # (every way its enhances can fall, each skill by its priority). Up to MAX_CONTENDERS a kind stay out of the food,
    # and the one you chose to enhance always does
    moving = {w.stack for w, _, _, _ in plan.transfers.values()}     # fed whole to the new card (added below once)
    lead =next((c for c in plan.cards if c.stack == leveling and not c.worn and plan.best.get(c.kind) is not c
                 and db.to_next(c.grade, c.enchant) is not None), None)
    mixes = {}                          # stack -> the cards of the other kinds in its best mix

    def odds(c, raises):
        """(win, tie) of the card after `raises` enhances, against the pick."""
        win = tie = 0.0
        for eff, p in _outcomes(c.effects, raises, plan.pool[c.kind]).items():
            v = score(mixes[c.stack], eff, c)
            if v > best_key[0] + 1e-9:
                win += p
            elif v > best_key[0] - 1e-9:
                tie += p
        return win, tie

    base_raw = _useful(best, needs)
    base_got = adds_of(best)
    for i, kind in enumerate(TYPES):
        rival = plan.best.get(kind)
        if rival is None:
            continue
        rows = []
        for c in options[i]:
            if c is None or c.real is not None or c is rival or c.worn or c.stack in moving:
                continue
            chosen = c is lead
            if c.stack in dropped and not chosen:
                continue
            mix, _ = best_of(options[:i] + [[c]] + options[i + 1:])
            useful = _useful(mix, needs)
            others = mixes[c.stack] = [m for m in mix if m.kind != kind]
            top = db.max_level(c.grade)
            exp = sum(db.to_next(c.grade, lv) for lv in range(c.enchant, top)) - c.exp
            win, tie = odds(c, top - c.enchant)
            ties = useful and useful >= base_raw and off([c]) <= off([rival])    # not a tie if it pays VERSION_COST
            if not chosen and not ties and win < MIN_CHANCE:
                continue                    # an even 0 (no skill targets) is no reason to keep it
            got = {}
            for m in mix:
                for s, lv in m.effects:
                    got[s] = got.get(s, 0) + lv
            ranked_needs = sorted(needs, key=lambda s: -weights.get(s, 0))
            edge = [(s, min(needs[s], base_got.get(s, 0)) - min(needs[s], got.get(s, 0))) for s in ranked_needs]
            rows.append(Contender(c, rival, useful, base_raw, win, tie, top, max(0, exp),
                                  [s for s, d in edge if d > 0], [s for s, d in edge if d < 0],
                                  [m.real or m for m in others if (m.real or m) is not plan.best.get(m.kind)]))
        rows.sort(key=lambda r: (r.card is not lead, -r.chance, -r.useful, -r.tie))
        if rows:
            plan.alternatives[kind] = rows[:MAX_CONTENDERS + (rows[0].card is lead)]
    kept = {r.card.stack for rows in plan.alternatives.values() for r in rows}
    plan.level_pick = lead if lead is not None and lead.stack in kept else None

    # food: the cards you do not wear, that are not the best of their kind nor an alternative, fed to the best cards
    # where their next levels most likely raise a skill you need - the lower rarity first (2.5.5: the Training Arcana,
    # then Common, Rare, Epic, Unique: the higher ones stay longer in your bag, as options); the alternative you chose
    # to enhance gets the food before the rest
    plan.spare = [c for c in plan.cards if plan.best.get(c.kind) is not c and not c.worn and c.stack not in kept]
    plan.training = [t for t in training or [] if t[1]]
    food = [(c, db.food(c.grade, c.enchant, c.exp), db.feed_kinah(c.grade, c.enchant)) for c in plan.spare
            if c.stack not in moving]
    food += [((name, n), exp * n, 0) for name, n, exp in plan.training]
    food += [(w, exp, db.feed_kinah(w.grade, w.enchant)) for w, _, exp, _ in plan.transfers.values()]
    into_new = {w.stack: new.stack for w, new, _, _ in plan.transfers.values()}     # the one you wear -> the new one
    targets_ = [c for c in plan.best.values() if db.to_next(c.grade, c.enchant) is not None]
    if plan.level_pick is not None:
        targets_.append(plan.level_pick)
    pantry = sum(exp for what, exp, _ in food if not (isinstance(what, Card) and what.stack in moving))
    for rows in plan.alternatives.values():
        for r in rows:
            lvl, left = r.card.enchant, pantry + r.card.exp
            while db.to_next(r.card.grade, lvl) is not None and left >= db.to_next(r.card.grade, lvl):
                left -= db.to_next(r.card.grade, lvl)
                lvl += 1
            r.reach = lvl
            r.reach_chance, r.reach_tie = odds(r.card, lvl - r.card.enchant) if lvl < r.top else (r.chance, r.tie)
    if food and targets_:
        have = dict(adds)
        state = {c.stack: [c.enchant, c.exp] for c in targets_}

        def value(c):
            """(expected useful levels of its next enhance weighed by priority, exp it takes, the same unweighed)."""
            fake = Card(c.stack, c.item, c.name, c.kind, c.set, c.grade, c.level, state[c.stack][0], c.effects, True)
            pool = db.pool(c.kind, prefix)
            return (_enhance_value(fake, needs, have, pool, db, weights), db.to_next(c.grade, state[c.stack][0]),
                    _enhance_value(fake, needs, have, pool, db))

        plans = {c.stack: Feed(c, [], 0, 0, c.enchant, 0.0) for c in targets_}
        first_ratio = {c.stack: value(c)[0] / value(c)[1] for c in targets_}     # what its next level gives per exp
        final = {c.stack: final_first and _final(c, plan) for c in targets_}
        for what, exp, kinah in sorted(food, key=lambda f: (not (isinstance(f[0], Card) and f[0].stack in into_new),
                                                             _food_rank(f[0]), -f[1])):
            open_ = [c for c in targets_ if db.to_next(c.grade, state[c.stack][0]) is not None]
            if not open_:
                break
            forced = into_new.get(what.stack) if isinstance(what, Card) else None
            if forced is None and plan.level_pick is not None:
                forced = plan.level_pick.stack
            target = (next((c for c in open_ if c.stack == forced), None) if forced else None) or max(
                open_, key=lambda c: (final[c.stack], value(c)[0] / value(c)[1], -value(c)[1]))
            f = plans[target.stack]
            f.fodder.append((what, exp))
            f.exp += exp
            f.kinah += kinah
            lvl, into = state[target.stack]
            into += exp
            while db.to_next(target.grade, lvl) is not None and into >= db.to_next(target.grade, lvl):
                f.useful += value(target)[2]
                into -= db.to_next(target.grade, lvl)
                lvl += 1
                state[target.stack] = [lvl, into]
            if db.to_next(target.grade, lvl) is None:
                f.waste += into
                into = 0
            state[target.stack] = [lvl, into]
            f.to = lvl
        # the order to enhance them: what the next level gives in your skills (weighed by priority) per exp, the
        # same measure the food went by - Qué hacer's next step and Mejoras' steps follow it
        plan.feeds = sorted((f for f in plans.values() if f.fodder),
                            key=lambda f: (f.target is not plan.level_pick, not final[f.target.stack],
                                           -first_ratio[f.target.stack], -f.useful))

    # a transfer that can leave you worse is tried first (2.5.11): the new card gets the food you have to spare, a level
    # at a time, while the one you wear stays as it is. Its raises then show: once it is as good as yours by itself,
    # the next plan feeds it the old one with nothing at risk; if it stays behind, you keep yours and it goes back to
    # the food with all its exp. The same outcome as transferring at once when it goes well, without the risk. One at
    # a time (the other risky ones wait); not while you enhance an alternative you chose, nor for a new card you chose
    # to feed the old one to at once, nor without food to spare that raises it
    direct = set(picks.get("direct") or ())
    risky = [k for k, (_, _, worse) in plan.transfer_odds.items()
             if worse > 1e-9 and plan.transfers[k][1].stack not in direct]
    if risky and not leveling:
        first = max(risky, key=lambda k: (edges[k], plan.transfer_odds[k][0], -TYPES.index(k)))
        old, new, _, to = plan.transfers[first]
        trial = build(db, prefix, cards, levels, targets, names, kinds, training, versions, final_first,
                      {**picks, "level": new.stack, "hold": risky})
        fd = next((f for f in trial.feeds if f.target is trial.level_pick), None)
        if trial.level_pick is not None and trial.level_pick.stack == new.stack and fd and fd.to > new.enchant:
            trial.trial = (first, old, new, plan.transfer_odds[first], to)
            trial.transfer_trade.setdefault(first, plan.transfer_trade.get(first, []))
            return trial
    return plan


def feeding_pays(plan):
    """Does feeding your spare cards raise a skill you want? The one rule Arcanas, Mejoras and the next step share
    (2.5: Mejoras said «dales de comer las 12 que no usas · +0 en tus cartas» while Arcanas said to farm). The
    alternative you chose to enhance counts too: you feed it to settle which of the two serves (2.5.5)."""
    return plan is not None and any(f.useful > 0 or f.target is plan.level_pick for f in plan.feeds)


def alternative_to_try(plan):
    """When feeding the cards you wear raises nothing you want: the alternative to enhance with the food you have, the
    one most likely to beat the one you wear at the +N that food takes it to (2.5.5: improve with what you have before
    farming more), or None (then farm). Trying it costs no exp: if it loses, fed to the one you wear it passes all of
    it - only the Kinah of moving it again."""
    if plan is None or plan.level_pick is not None or feeding_pays(plan):
        return None
    # MIN_CHANCE where the food takes it (2.5.14: a Mirror with 2% at +3 was "the next step", eating the food a card
    # you wear needed to reach its next level)
    rows = [r for rows in plan.alternatives.values() for r in rows
            if r.reach > r.card.enchant and r.reach_chance >= MIN_CHANCE]
    return max(rows, key=lambda r: (r.reach_chance, r.reach_tie, r.useful), default=None)


def effects_text(card, plan):
    """«Condemnation +2, Bolt +1...» of a card, with the plan's skill names."""
    names = {n.skill: n.name for n in plan.skills}
    return ", ".join(f"{names.get(s, s)} +{lv}" for s, lv in card.effects)


@dataclass
class EffectNote:
    skill: int
    name: str
    level: int
    raised: int              # the levels its enhances put on it (each effect starts at +1)
    status: str              # "want": a skill target still missing levels with the best cards (one more helps);
                             # "full": a target the best cards reach (it counts, one more does not); "reached": a target
                             # you reach without cards; "off": not one of your skill targets
    missing: int = 0
    target: int = 0
    rises: bool = False      # the card's next enhance can raise it (4 effects, this one under its top, the card too)
    extra: bool = False      # its raises go past your target (one you reach without cards, or past it with the best)

    @property
    def serves(self):
        return self.status in ("want", "full")


def raise_luck(card, notes):
    """(levels its enhances put on effects that serve, the ones expected: each raise picks one of its 4 effects at
    random) of a card with 4 effects, or None."""
    if len(notes) < MAX_EFFECTS:
        return None
    raised = sum(e.raised for e in notes)
    return sum(e.raised for e in notes if e.serves and not e.extra), raised * sum(e.serves for e in notes) / len(notes)


def effect_notes(card, plan, db):
    """[EffectNote] of a card's effects, and (left, good): the effects its next enhance can add while it has fewer
    than 4 (its kind's pool minus the ones it has) and how many of them are skills still missing levels."""
    needs = {n.skill: n for n in plan.skills}
    top = db.to_next(card.grade, card.enchant) is None
    full = len(card.effects) >= MAX_EFFECTS
    wear = plan.best.get(card.kind) is card
    out = []
    for s, lv in card.effects:
        n = needs.get(s)
        status = ("off" if not (n and n.target) else "reached" if not n.need else "want" if n.missing else "full")
        out.append(EffectNote(s, n.name if n else str(s), lv, lv - 1, status, n.missing if n else 0,
                              n.target if n else 0, full and not top and lv < MAX_EFFECT_LEVEL,
                              lv > 1 and (status == "reached" or status == "full" and wear and n.best > n.need)))
    mine = {s for s, _ in card.effects}
    left = [s for s in plan.pool.get(card.kind, ()) if s not in mine] if not full and not top else []
    return out, (len(left), sum(1 for s in left if needs.get(s) and needs[s].missing))


@dataclass
class Worth:
    verdict: str             # "yes": it has as many of the skills you still miss as a card of its kind can; "ok": it
                             # helps, but one of its kind could have more; "ilvl": its next raises go to no skill you
                             # miss (only item level and god stat); "top": at its top
    good: int                # its next raise's outcomes (its effects under +4, or the new effects it can add while it
    of: int                  # has fewer than 4) on skills you still miss, of all of them
    mine: int                # its effects on skills you still miss
    possible: int            # how many of them a card of its kind can have (up to 4)
    absent: list             # names of the skills you miss that its kind can have and it does not, by priority
    value: float             # expected levels per enhance, weighed by priority (to rank the cards)


def worth(card, plan, db):
    """Is enhancing the card worth it: how much of each enhance goes to skills you still miss, and how far the card is
    from the best one of its kind could be for your skill targets."""
    notes, (left, good_new) = effect_notes(card, plan, db)
    needs = {n.skill: n for n in plan.skills}
    wanted = sorted((s for s in plan.pool.get(card.kind, ()) if needs.get(s) and needs[s].missing),
                    key=lambda s: needs[s].priority)
    mine = {e.skill for e in notes if e.status == "want"}
    possible = min(MAX_EFFECTS, len(wanted))
    absent = [needs[s].name for s in wanted if s not in mine]
    if db.to_next(card.grade, card.enchant) is None:
        return Worth("top", 0, 0, len(mine), possible, absent, 0.0)
    weight = {s: PRIORITY_DECAY ** (needs[s].priority - 1) for s in wanted}
    if left:
        good, of = good_new, left
        value = sum(weight.values()) - sum(weight[s] for s in mine)
        value /= left
    else:
        open_ = [e for e in notes if e.rises]
        good, of = sum(e.status == "want" for e in open_), len(open_)
        value = sum(weight.get(e.skill, 0) for e in open_ if e.status == "want") / of if of else 0.0
    verdict = "ilvl" if not good else "yes" if len(mine) >= possible else "ok"
    return Worth(verdict, good, of, len(mine), possible, absent, value)


def level_food(plan, db, card, skip=()):
    """The food for the card's next level only, out of what the plan feeds it (Training Arcana one by one): the lowest
    rarity that is enough (2.5.5: the Training Arcana, then up to Common, Rare, Epic, Unique; last the ones that look
    like a card that is not food, twins(): the game cannot tell them apart), then the least exp past
    what it needs (that carries over to the level after), then the fewest items. The card you wear, when the plan feeds
    it to this new one, goes first and whole. skip: stacks already eaten (the game may not say so until the next full
    list). -> (picks [(Card or (Training Arcana name, 1), exp, Kinah)], exp it needs, exp of the picks); the picks fall
    short when the food does."""
    need = db.to_next(card.grade, card.enchant)
    if need is None:
        return [], 0, 0
    need = max(0, need - card.exp)
    moved = next((w for w, new, _, _ in plan.transfers.values() if new.stack == card.stack), None)
    if moved is not None and moved.stack not in skip:
        exp = db.food(moved.grade, moved.enchant, moved.exp)
        return [(moved, exp, db.feed_kinah(moved.grade, moved.enchant))], need, exp
    feed = next((f for f in plan.feeds if f.target.stack == card.stack), None)
    items, mine = [], set()
    for what, exp in (feed.fodder if feed else []):
        if isinstance(what, Card):
            mine.add(what.stack)
            if what.stack not in skip:
                items.append((what, exp, db.feed_kinah(what.grade, what.enchant)))
        else:
            name, n = what
            items += [((name, 1), exp // n, 0)] * n
    if sum(e for _, e, _ in items) < need:       # not enough of its own: the rest of your food too
        items += [(c, db.food(c.grade, c.enchant, c.exp), db.feed_kinah(c.grade, c.enchant)) for c in plan.spare
                  if c.stack not in mine and c.stack not in skip and c.stack != card.stack]
        given = {w[0] for w, _, _ in items if not isinstance(w, Card)}
        for name, n, exp in plan.training:
            if name not in given:
                items += [((name, 1), exp, 0)] * n
    def rank(what):
        return 9 if isinstance(what, Card) and twins(plan, what, card, skip) else _food_rank(what)

    items.sort(key=lambda it: rank(it[0]))
    total = sum(e for _, e, _ in items)
    if total <= need:
        return items, need, total
    for top in sorted({rank(w) for w, _, _ in items}):
        tier = [it for it in items if rank(it[0]) <= top]
        if sum(e for _, e, _ in tier) >= need:
            items = tier
            break
    unit = 100                                  # every exp of the tables is a multiple of 100
    cap = -(-need // unit) + max(-(-e // unit) for _, e, _ in items)
    reach = {0: ()}
    for i, (_, exp, _) in enumerate(items):
        step = -(-exp // unit)
        for s, chosen in sorted(reach.items(), reverse=True):
            t = s + step
            if t <= cap and (t not in reach or len(reach[t]) > len(chosen) + 1):
                reach[t] = chosen + (i,)
    goal = min((s for s in reach if s * unit >= need), key=lambda s: (s, len(reach[s])))
    picks = [items[i] for i in reach[goal]]
    return picks, need, sum(e for _, e, _ in picks)


@dataclass
class Farm:
    dungeon: str
    stage: str               # the highest Transcendence stage you can enter, or the first one when you cannot yet
    il: int                  # the equipped Item Level it asks for
    can: bool
    kinds: list              # the card kinds it drops
    better: dict             # kind -> chance per clear of a card that would improve yours (a higher grade, or a
    #                          Unique when yours already is one and a better one is possible for your skills)
    food: float              # expected food exp per clear (every card it drops, at +0)
    score: float
    next_stage: tuple = None  # (stage, il) of the next one up, when there is one
    top_grade: str = ""      # the best grade it drops at this stage
    first_at: dict = field(default_factory=dict)    # grade -> (stage, il) where it first drops
    future: float = 0.0      # the same score at its last stage: what it is worth for you in the long run


def farm_options(plan, db, item_level):
    """Where to farm cards, the best for you first: per Transcendence dungeon its highest stage you can enter
    (item_level: your equipped Item Level, None when unknown - then every stage counts as open)."""
    out = []
    for dungeon, info in db.transcendence.items():
        stages = sorted(info.get("stages", {}).items(), key=lambda kv: kv[1].get("il", 0))
        if not stages:
            continue
        open_ = [kv for kv in stages if item_level is None or item_level >= kv[1].get("il", 0)]
        stage, data = (open_ or stages[:1])[-1]
        later = [kv for kv in stages if kv[1].get("il", 0) > data.get("il", 0)]
        rows = data.get("arcana", [])
        kinds, better, food, score = _farm_value(plan, db, rows)
        future = _farm_value(plan, db, stages[-1][1].get("arcana", []))[3]
        first_at = {}
        for st, d in stages:
            for _, _, grade, _ in d.get("arcana", []):
                first_at.setdefault(grade, (st, d.get("il", 0)))
        top = max((r[2] for r in rows), key=lambda g: GRADE_ORDER.get(g, -1), default="")
        out.append(Farm(dungeon, stage, data.get("il", 0), bool(open_), kinds, better, food, score,
                        (later[0][0], later[0][1].get("il", 0)) if later else None, top, first_at, future))
    return sorted(out, key=lambda f: (not f.can, -round(f.score, 6), -round(f.future, 6)))


def tied_farms(farms):
    """The dungeons as good for you as the first one (the same score now and in the long run): «da igual cuál»
    instead of picking the first of the list (2.5.10)."""
    if not farms or not farms[0].can:
        return farms[:1]
    head = farms[0]
    return [f for f in farms if f.can and round(f.score, 6) == round(head.score, 6)
            and round(f.future, 6) == round(head.future, 6)]


def _farm_value(plan, db, rows):
    """(kinds, {kind: chance per clear of a card better than yours}, food exp per clear, score) of a stage's drops."""
    kinds = [k for k in TYPES if any(r[0] == k for r in rows)]
    better, score = {}, 0.0
    for kind in kinds:
        card = plan.best.get(kind)
        mine = GRADE_ORDER.get(card.grade, -1) if card else -1
        w = worth(card, plan, db) if card else None
        # how many more of your missing skills a card of its kind could bring: also for one whose own raises no
        # longer help ("ilvl") or that is at its top - a new card can still beat it (2.5.10: a Chalice with
        # Judgment Thunder and Bolt already full counted as "the best possible", and no dungeon was for the Chalice)
        gap = max(0, w.possible - w.mine) if w else 0
        want = plan.pick.get(kind) if plan.pick_from.get(kind) == "you" else None
        if card is None or mine < GRADE_ORDER["Unique"]:
            need, weight = mine + 1, 1.0 + gap           # any higher grade: item level, its stat, 4 effects
        elif gap:
            need, weight = GRADE_ORDER["Unique"], float(gap)   # a Unique that may bring more of your skills
        else:
            continue
        miss = 1.0
        for k, version, grade, chance in rows:
            if k == kind and GRADE_ORDER.get(grade, -1) >= need and (want is None or version == want):
                miss *= 1 - chance
        if miss < 1:
            better[kind] = 1 - miss
            score += better[kind] * weight
    food = sum(chance * db.food(grade, 0) for _, _, grade, chance in rows)
    return kinds, better, food, score + food / 20000


def card_name(db, kind, version):
    """«Chalice of Vigor» of a kind and version, from the database."""
    return next((c["name"] for c in db.cards.values() if c["type"] == kind and c["set"] == version), f"{kind} ({version})")


def pantheon(db, kind, version):
    """(«Time [Siel]», what it gives) of a kind's version."""
    stat = next((c["stat"][0] for c in db.cards.values() if c["type"] == kind and c["set"] == version), "")
    return stat, PANTHEON.get(stat.split(" ")[0], "")


def short_kind(kind):
    return {"Chalice": tr("Cáliz"), "Parchment": tr("Pergamino"), "Compass": tr("Brújula"), "Bell": tr("Campana"),
            "Mirror": tr("Espejo")}.get(kind, kind)


def feed_text(f, names_of=None):
    """«Dale de comer ... a tu ...» in one line."""
    parts = []
    cards = [w for w, _ in f.fodder if isinstance(w, Card)]
    items = [w for w, _ in f.fodder if not isinstance(w, Card)]
    if cards:
        parts.append(tr("{0} arcana{1}", len(cards), "s" if len(cards) != 1 else ""))
    for name, n in items:
        parts.append(f"{n}× {name}")
    text = tr("Dale de comer {0} a tu {1} +{2}: {3:,} de exp", tr(" y ").join(parts), f.target.name, f.target.enchant,
              f.exp)
    if f.to > f.target.enchant:
        text += tr(" → +{0}", f.to)
    if f.useful >= 0.05:
        text += tr(" (≈{0:.1f} niveles en skills que te faltan)", f.useful)
    return text
