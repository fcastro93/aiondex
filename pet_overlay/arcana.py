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
PRIORITY_DECAY = 0.75               # a skill target's weight is this to the power of its place in your list (1, 0.75,
#                                     0.56...): the first one counts most, but a lot of the next ones can still win


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
    pick: dict = field(default_factory=dict)        # kind -> the version most of the top wear (VERSION_PICK)
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


def build(db, prefix, cards, levels, targets, names, kinds, training=None):
    """The plan of one character. levels: {skill: level now} or {skill: (level, points, [Daevanion, 0, gear, Arcana])}
    (the game's skill list, with the worn cards); targets: {skill: level wanted}, in priority order (the first counts
    most: PRIORITY_DECAY; 0 / missing = no target); names / kinds: skill -> name / "active" | "passive"; training:
    [(name, count, exp)] of the Training Arcana in the bag."""
    plan = ArcanaPlan()
    plan.cards = sorted(cards, key=lambda c: (not c.worn, TYPES.index(c.kind) if c.kind in TYPES else 9,
                                              -GRADE_ORDER.get(c.grade, 0), -c.enchant, -c.total))
    plan.worn = {c.kind: c for c in plan.cards if c.worn}
    plan.pool = {kind: db.pool(kind, prefix) for kind in TYPES}
    plan.pick = VERSION_PICK.get(prefix, {})
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
    # stat), the combination that adds the most to what you need, then the set bonuses, the version most of the top wear
    # (with five cards any mix gives two bonuses: its Pantheon stat decides), the most levels, the +N
    options = []
    for kind in TYPES:
        mine = [c for c in plan.cards if c.kind == kind]
        top = max((c.level for c in mine), default=0)
        options.append([c for c in mine if c.level == top] or [None])
    best, best_key = None, None
    for combo in itertools.product(*options):
        chosen = [c for c in combo if c is not None]
        key = (_useful(chosen, needs, weights), _set_bonus(chosen), sum(plan.pick.get(c.kind) == c.set for c in chosen),
               sum(c.total for c in chosen),
               sum(GRADE_ORDER.get(c.grade, 0) * 10 + c.enchant for c in chosen), sum(c.worn for c in chosen))
        if best_key is None or key > best_key:
            best, best_key = chosen, key
    plan.best = {c.kind: c for c in best or []}
    plan.useful_best = _useful(plan.best.values(), needs)
    adds = {}
    for c in plan.best.values():
        for s, lv in c.effects:
            adds[s] = adds.get(s, 0) + lv
    for need in plan.skills:
        need.best = adds.get(need.skill, 0)
    for kind in TYPES:
        worn, chosen = plan.worn.get(kind), plan.best.get(kind)
        if chosen is not None and chosen is not worn:
            others = [c for k, c in plan.best.items() if k != kind]
            gain = _useful(others + [chosen], needs) - _useful(others + ([worn] if worn else []), needs)
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

    # food: the cards you do not wear and that are not the best of their kind, fed to the best cards where their next
    # levels most likely raise a skill you need
    plan.spare = [c for c in plan.cards if plan.best.get(c.kind) is not c and not c.worn]
    plan.training = [t for t in training or [] if t[1]]
    food = sorted([(c, db.food(c.grade, c.enchant, c.exp), db.feed_kinah(c.grade, c.enchant)) for c in plan.spare],
                  key=lambda f: f[1])
    food += [((name, n), exp * n, 0) for name, n, exp in plan.training]
    targets_ = [c for c in plan.best.values() if db.to_next(c.grade, c.enchant) is not None]
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
        for what, exp, kinah in sorted(food, key=lambda f: -f[1]):
            open_ = [c for c in targets_ if db.to_next(c.grade, state[c.stack][0]) is not None]
            if not open_:
                break
            target = max(open_, key=lambda c: (value(c)[0] / value(c)[1], -value(c)[1]))
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
            state[target.stack] = [lvl, into]
            f.to = lvl
        plan.feeds = sorted((f for f in plans.values() if f.fodder),
                            key=lambda f: -max((weights.get(s, 0) for s, _ in f.target.effects), default=0) - f.useful)
    return plan


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
