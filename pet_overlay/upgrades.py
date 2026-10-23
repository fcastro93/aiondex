"""Mejoras: what to do next with your gear, in order, from what AionDex knows about you.

What it reads:
- your equipped gear (settings "gear", gear.py) and every piece in your bag and storages, with its soul binding
  lines, manastones and Theostone (inventory: gear.parse_owned_gear);
- what you have to spend (inventory: Kina, Enhance Stones, Transfer Stones, Gear Change Vouchers, Soul Codex,
  Philosopher's Stones, Amplify Stones, manastones...);
- the market's gear prices (your market samples: Market > Equipo);
- the dungeons you run (the final bosses this character killed in them, fights/index.json; or the ones you pick);
- who the character is (Me): level, item level and CP (the official profile's, the item level moved by the gear
  changed since it was read: character_stats), and whether it is your main or an alt;
- the Tracker's field and Abyss bosses with what they drop;
- data/upgrades_db.json (tools/build_upgrades_db.py, aion2data's tables of the global client): what each enhancement
  level costs, what a transfer takes, where each piece drops, every dungeon cube's guaranteed reward, the Substance
  Morph recipes (3 Gear Change Vouchers: Ferocious Horn Den + 2,000,000 Kina = Nuakum Guard...), each piece's soul
  binding stat pool, and every activity with what it asks for and gives (dungeon modes, Ascension Trial, Nightmare,
  the Abyss, the Daily Dungeon, the Arena...) and the shops' goods.

What you can do: every activity a step sends you to is checked against what it asks for (can_do: the dungeon mode's
equipped Item Level and level, the Abyss entrance's Item Level or its Item Level / Combat Power limit, the Ascension
Trial tier, Nightmare's Suggested Item Level): Ferocious Horn Den (2,100) is a goal for a character at 1,539 ("te
faltan 561 de nivel de objeto"), never a place to farm. «Contenido» (plan.content) lists every activity of the game
with whether you can do it, why not, and what it gives you.

What a step raises (Step.boosts): item level (the game's sum of the pieces' item level and their +N: new gear,
enhancing, Arcana) and/or CP (everything that adds stats: soul binding, manastones, Amp - which adds no item level -,
Theostones, Potential, Daevanion, stigmas, wings).

Kina: the fees (enhancing, Substance Morph, soul binding, Theostones) take Kina and Kina (Bound) (the game's "Kina
(All)", bound first: the official note of the 2026-03-25 Korean update); the market only Kina. A main uses everything;
an alt only what is bound: its Kina (Bound) and the items no one can trade, never the market (its tradeable Kina and
items are listed for the main: plan.for_main). What is counted per server - every character of yours shares it: duty
missions and the Dimensional Invasion's keys (the client's checklist), the Daily Dungeon's entries (metabot.gg's
weekly checklist), shop goods limited "per server" - is for the main: an alt is not sent to it (PER_SERVER).

«Líneas» (plan.substats): per piece the lines to aim for, highest priority first (SUBSTATS: the Substats guide of
r/Aion2, shared by a user on 2026-10-20), which of them the piece has and the gear transfer trick of that guide. The
client's gear transfer tables (aion2data gear-transfer): the same kind of piece only (Top to Top, Mace to Mace), 100%,
no Kina; a tier 3 piece (item level 86-102) takes a donor of tiers 1-3 for 2 Transfer Stones, tier 2 (to 86) one of
tiers 1-2 for 1, tier 1 (36-70) one of tier 1; from a lower tier only while the receiving piece's transfer count is 0
(it never got one). The donor's +N and soul binding lines go over: enhancing a level 36 Unique to +10 costs 325K Kina
and 12,960 Enhance Stones, a level 86 one 775K and 30,920 (Enchant_Unique tables), and the binding costs the same.
Guides disagree on whether a donor at 100% Soul Binding passes its lines: the window says so.

Item level comes from the pieces, their +N and the Arcana (aion2data's reading of the client); some guides add
manastones, Daevanion Crystals or wings, but two of the user's characters with different manastones and Daevanion
nodes had the same official item level over that sum (431, 2026-10-17), so those stay CP.

Soul binding (kanonxo's "Aion 2 Bible", the Global PvE guide, and the game's own window): a piece's stat lines come
from its pool; each line shows value/highest and the Soul Binding Rate is the average of how far each line is from its
lowest to its highest value (6 lines at 100% and one at 32% = 90.2%; a skill effect line counts as 100%). Bind (30,000
Kina + Soul Codex, more each time) raises the values; Sync rerolls one line (a Unique piece of the same kind or a Sync
Stone); Reset rerolls them all (Soul Codex: Reset + 300,000 Kina); at 100% 2 Philosopher's Stones + 1,000,000 Kina add
one more line. A transfer keeps the +N, the Amp levels and the lines, never that extra line, the manastones, the
Theostone or Potential. Each manastone (weapons, armor) or soulstone (accessories) you use rerolls every socket of the
piece. The lines worth having per piece are the guide's (IDEAL), skill effects included.

Kinds of upgrade you switch off (CATEGORIES: Soul Binding, Market, Jefes..., per character, settings "upgrades"
"off_by") are left out the same way: no soul binding frees the pieces its Syncs were going to eat, and the shops stop
offering its Soul Codex. Steps you do not want («No quiero hacer este paso», kept per character in settings
"upgrades" "skipped_by") are left out and the plan is made again without them: a piece or a voucher you will not get no longer counts as "about to be replaced", so its
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

from .arcana import effects_text, feed_text
from .gear import CLASS_OF_JOB, POSITIONS, SLOTS
from .i18n import tr, tr_data

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
PLACES = {1: tr("bolsa"), 2: tr("almacén de cuenta"), 15: tr("almacén")}
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
# the lines to aim for per piece, highest priority first; a tuple of two: as good as each other; "skill": a skill
# effect line (passive skill levels; on rings, active skill levels). r/Aion2's Substats guide.
_WEAPON = (("Combat Speed",), ("Damage Boost",), ("Weapon Damage Boost",), ("Might", "Precision"))
SUBSTATS = {
    "weapon": _WEAPON, "guard": _WEAPON,
    "helm": (("Double Chance",), ("Attack increase",), ("skill",)),
    "chest": (("Damage Boost",), ("Perfect Chance",), ("skill",)),
    "legs": (("Attack increase",), ("Damage Tolerance",), ("Perfect Chance",), ("skill",)),
    "cloak": (("Double Chance",), ("Attack increase",), ("skill",)),
    "shoulders": (("Double Chance",), ("Critical Damage Boost",), ("skill",)),
    "gloves": (("Combat Speed",), ("Perfect Chance",), ("skill",)),
    "boots": (("Move Speed",), ("Perfect Chance",), ("skill",)),
    "earring": (("Might",), ("Precision",), ("Move Speed",), ("skill",)),
    "necklace": (("Combat Speed",), ("skill",), ("Attack", "Accuracy")),
    "ring": (("skill",), ("Might",), ("Precision",)),
}
SUBSTATS_NOTE = {"weapon": tr("Might o Precision según tu Accuracy"), "guard": tr("Might o Precision según tu Accuracy"),
                 "ring": tr("skill: niveles de habilidades activas")}
SUBSTATS_GUIDE = {"text": tr("Líneas por pieza y el truco del transfer: Substats guide de r/Aion2"),
                  "url": "https://www.reddit.com/r/Aion2/comments/1x4l6og/substats_guide_exploit_the_possibilities_of_gear/"}
GUIDE = {"text": tr("Líneas buscadas y reglas de Soul Binding: Aion 2 Bible de kanonxo"),
         "url": "https://docs.google.com/document/d/11u4wLCG1WfL-xSka2Aze0rI9vYRa7mq3N3Gp1bt0AWY/edit"}
ILVL, CP = "ilvl", "cp"
BOTH = (ILVL, CP)
BOOSTS = {"own": BOTH, "voucher": BOTH, "enhance": BOTH, "morph": BOTH, "buy": BOTH, "save": BOTH, "farm": BOTH,
          "boss": BOTH, "arcana": BOTH, "soul": (CP,), "manastone": (CP,), "soulstone": (CP,), "amp": (CP,),
          "theostone": (CP,)}
# what a shop good is for: (pattern, use, what it raises, Spanish); the first that matches
GOODS = ((r"Soul Codex: Reset", "soul_reset", (CP,), tr("el Reset de Soul Binding")),
         (r"Soul Codex", "soul_codex", (CP,), "Bind"),
         (r"Philosopher's Stone", "philosopher", (CP,), tr("la línea extra de Soul Binding")),
         (r"Amplify Stone", "amp", (CP,), tr("el Amp")),
         (r"Manastone", "manastone", (CP,), "manastones"),
         (r"Soulstone", "soulstone", (CP,), "soulstones"),
         (r"Theostone", "theostone", (CP,), tr("la Theostone")),
         (r"Potential Stone", "potential", (CP,), tr("Potencial (PvP)")),
         (r"Enhance Scroll", "scroll", BOTH, tr("encantar tu amuleto")),
         (r"Daevanion Crystal", "daevanion", (CP,), tr("los tableros Daevanion")),
         (r"Stigma Shard", "stigma", (CP,), tr("tus stigmas")),
         (r"Wings", "wings", (CP,), tr("alas (su efecto de tenerlas)")),
         (r"Wrathful|Refining Stone", "craft", BOTH, tr("los crafts Splendent")),
         (r"Enhance Stone", "enhance", BOTH, tr("encantar")),
         (r"Transfer Stone", "transfer", BOTH, "transfer"),
         (r"Sync Stone", "sync", (CP,), "Sync"))
ALWAYS = {"daevanion", "stigma"}      # every character wants them (232 Daevanion Crystals fill Azphel's board)
# currency -> the activities (content kinds) that pay it and its shops
CURRENCIES = (("Phantasmal Fragment", ("nightmare",), ("Trade Shop (nightmare)",)),
              ("Abyss Points", ("abyss", "arena", "duty"), ("Trade Shop (abyss)",)),
              ("Centuryroot Token", ("invasion",), ("Centuryroot Token Shop", "Festival Shop")),
              ("CoinDungeon1", (), ("Season Shop",)),
              ("A2 Mileage", (), ("A2 Mileage",)))
SHOP_NAME = {"Trade Shop (nightmare)": tr("Tienda de Nightmare"), "Trade Shop (abyss)": tr("Tienda del Abismo"),
             "Trade Shop (arena)": tr("Tienda de la Arena"), "Centuryroot Token Shop": tr("Tienda de Centuryroot"),
             "Festival Shop": tr("Tienda del Festival"), "Season Shop": tr("Tienda de temporada"),
             "A2 Mileage": tr("Tienda de A2 Mileage"), "Festive Bell Shop": tr("Tienda de Festive Bell (evento)"),
             "Fortune Pouch Shop": tr("Tienda de Fortune Pouch (evento)")}
CURRENCY_NAME = {"CoinDungeon1": tr("moneda de temporada")}
GROUP = {"Expedition": tr("Expedición"), "Transcendence": tr("Trascendencia"), "Sanctuary": tr("Santuario"),
         "Subjugation": "Subjugation", "Ascension Trial": tr("Ascension Trial (torres)"), "Abyss": tr("Abismo")}
ARCANA = {41: "Chalice", 42: "Parchment", 43: "Compass", 44: "Bell", 45: "Mirror"}
ARCANA_LEVEL = {"Common": 20, "Rare": 40, "Epic": 60, "Unique": 80}
FIELD_FACTION = {1010: "E", 1110: "A"}           # Verteron: Elyos, Altgard: Asmodian (the Abyss: both)
LOWER_ABYSS = "Chaotic Lower Reshanta"
PER_SERVER = {"duty", "invasion", "daily"}      # activities every character of yours shares: for the main
# the kinds of upgrade you can switch off: key, Spanish, the step kinds in it
CATEGORIES = (("own", tr("Equipar lo que tienes"), ("own",)), ("voucher", "Vouchers", ("voucher",)),
              ("enhance", tr("Encantar"), ("enhance", "morph")), ("soul", "Soul Binding", ("soul",)),
              ("stones", tr("Manastones y soulstones"), ("manastone", "soulstone")), ("amp", "Amp", ("amp",)),
              ("market", "Market", ("buy", "save")), ("farm", tr("Mazmorras"), ("farm",)), ("boss", tr("Jefes"), ("boss",)),
              ("arcana", "Arcana", ("arcana",)), ("week", tr("Actividades y tiendas"), ("content", "shop")),
              ("goal", tr("Metas"), ("goal",)), ("extra", tr("Theostones y piezas sobrantes"), ("theostone", "spare")))
CATEGORY_OF = {kind: key for key, _, kinds in CATEGORIES for kind in kinds}


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
    source: str = ""            # voucher / drop: the dungeon; boss: its name
    need: int = 0               # voucher: how many it takes
    mode: str = ""              # drop: the dungeon's mode it drops in


@dataclass
class Me:
    """What activities ask of the character."""
    level: int = 0
    item_level: int = None      # the official one, moved by the gear changed since it was read; None: unknown
    cp: int = None
    official: int = None        # the official profile's item level
    at: float = None            # when the official profile was read
    role: str = "main"          # main: uses everything | alt: only what is bound

    @property
    def alt(self):
        return self.role == "alt"


@dataclass
class Review:
    """A piece in «Líneas»: the table's lines for its slot and which it has, the others, and the transfer trick."""
    piece: object                       # Piece
    tiers: list                         # [[(stat or "skill", has it, 0..1 of its line or None)]] by priority
    others: list                        # [(stat, 0..1)] lines it has that the table does not ask for
    score: int = 0                      # how good its lines are for the slot (the table's priorities)
    donors: list = field(default_factory=list)       # [(Spare, its score, the table's lines it has, the cost to
    #                                                     bring it to your +N or None, that +N never fails)]
    stones: int = None                  # Transfer Stones a transfer into it takes
    cheap: tuple = None                 # (a level 36-70 Unique of its kind, its +0->+10 cost, yours from your +N to
    #                                     +10): only for a piece still to be enhanced
    note: str = ""


@dataclass
class Activity:
    """A row of «Contenido»: an activity, whether you can do it and what it gives you."""
    group: str                  # Expedición, Trascendencia, Nightmare, Jefes de campo, Tiendas...
    name: str
    mode: str = ""
    needs: str = ""             # "Nv. 45 · 2,100 de nivel de objeto · 1–5 jugadores"
    reason: str = None          # None: you can do it; else why not ("te faltan 561 de nivel de objeto")
    closed: bool = False        # not open on the global servers yet
    gives: str = ""             # what it gives you
    boosts: tuple = ()
    useful: int = 0             # how many things in it improve you


@dataclass
class Step:
    kind: str       # own | voucher | enhance | soul | manastone | amp | morph | buy | save | farm | boss | arcana |
    #                 content (an activity you can do) | shop | goal (one you cannot do yet) | spare | theostone
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
    boosts: tuple = ()          # what it raises: ILVL and / or CP (BOOSTS by kind when empty)
    uses: tuple = ()            # what it spends (soul_codex, amp, manastone...): the shops' goods worth buying


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
    soul: object = None         # its Soul (its lines: a donor for a transfer, «Líneas»)

    @property
    def place(self):
        return PLACES.get(self.container, tr("almacén"))

    @property
    def text(self):
        return f"{self.item.name} +{self.enchant} ({self.place})"


@dataclass
class Plan:
    character: str
    resources: dict = field(default_factory=dict)     # kind -> count (kina, enhance_stone, transfer_stone...)
    arcana_plan: object = None                         # arcana.ArcanaPlan of the character (Personaje › Arcanas)
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
    off: set = field(default_factory=set)             # the CATEGORIES you switched off
    counts: dict = field(default_factory=dict)        # category -> its steps (switched off ones too)
    item_level: int = 0
    read_at: float = None                             # when your items were read
    me: Me = None
    currencies: dict = field(default_factory=dict)    # Phantasmal Fragment, Abyss Points, Oath Coin... -> count
    for_main: dict = field(default_factory=dict)      # an alt's tradeable Kina and items: for the main
    alts_kina: int = 0                                # a main: the tradeable Kina your alts hold
    locked: dict = field(default_factory=dict)        # (dungeon, mode) -> [Option] of a mode you cannot enter yet
    gains: dict = field(default_factory=dict)         # (dungeon, mode) -> [Option] of every mode (Contenido)
    bosses: dict = field(default_factory=dict)        # field / Abyss boss name -> its record (from the Tracker)
    content: list = field(default_factory=list)       # Activity rows of «Contenido»
    substats: list = field(default_factory=list)      # Review per piece of «Líneas»

    def have(self, kind):
        return self.resources.get(kind, 0)


class UpgradesDB:
    def __init__(self, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.credit = tr_data("upgrades_db.json", data).get("credit") or {"text": "aion2data.com", "url": "https://aion2data.com"}
        self.tracked = {int(k): tuple(v) + (0,) * (3 - len(v)) for k, v in data.get("tracked", {}).items()}
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
        self.content = data.get("content", [])
        self.nightmare = data.get("nightmare", [])
        self.awakening = data.get("awakening", {})
        self.shops = {s["name"]: s for s in data.get("shops", [])}
        self.arcana = data.get("arcana", [])
        self._tables = {}
        for group in self.enhance:
            m = re.match(r"Enchant_(\w+?)_(\d+)$", group)
            if m:
                self._tables.setdefault(m.group(1), []).append(int(m.group(2)))
        for levels in self._tables.values():
            levels.sort()

    def tracked_names(self):
        return {i: v[1] for i, v in self.tracked.items()}

    def mode(self, dungeon, mode):
        """The content record of a dungeon's mode (what it asks for), or None."""
        return next((c for c in self.content if c["kind"] == "dungeon" and c["name"] == dungeon and c["mode"] == mode),
                    None)

    def entry(self, kind, name):
        return next((c for c in self.content if c["kind"] == kind and c["name"] == name), None)

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


def character_stats(gear, profile, gear_db, role="main", live_cp=None):
    """Me of a character: its level, its official item level and CP (armory.Profile) and, when its gear changed since
    the profile was read, that item level moved by the change (each piece's item level and +N, the same reading on
    both sides: the official number is more than that plain sum, so only the difference is used)."""
    me = Me(level=(gear or {}).get("level") or (profile.level if profile else 0) or 0, role=role)
    if profile is not None and profile.item_level:
        def measure(rows):
            return sum((gear_db.by_id[i].level if i in gear_db.by_id else 0) + (n or 0) for i, n in rows)

        me.official, me.at, me.cp = profile.item_level, profile.at, profile.cp
        me.item_level = profile.item_level
        if gear and (gear.get("at") or 0) > (profile.at or 0):
            now = measure((raw[0], raw[1]) for raw in gear.get("slots", {}).values())
            then = measure((e.get("id"), (e.get("enchantLevel") or 0) + (e.get("exceedLevel") or 0))
                           for e in profile.items.values() if e.get("id"))
            me.item_level += now - then
    if live_cp:
        me.cp = live_cp
    return me


def can_do(entry, me):
    """None when the character can do this activity, else why not, in Spanish. An unknown item level passes (the
    window says it does not know it)."""
    if not entry:
        return None
    if not entry.get("open", True):
        return tr("aún no abre en Global")
    if entry.get("level") and me.level and me.level < entry["level"]:
        return tr("pide nivel {0}", entry['level'])
    il = me.item_level
    if il is not None:
        if entry.get("il") and il < entry["il"]:
            return tr("te faltan {0:,} de nivel de objeto (pide {1:,})", entry['il'] - il, entry['il'])
        if entry.get("il_max") and il > entry["il_max"]:
            return tr("tu nivel de objeto pasa su límite ({0:,})", entry['il_max'])
    if entry.get("cp_max") and me.cp and me.cp > entry["cp_max"]:
        return tr("tu CP pasa su límite ({0:,})", entry['cp_max'])
    return None


def needs_text(entry):
    """What an activity asks for, in one line."""
    bits = []
    if entry.get("level"):
        bits.append(tr("Nv. {0}", entry['level']) if entry["level"] < 99 else tr("nivel por encima del máximo actual"))
    if entry.get("il"):
        bits.append(tr("{0:,} de nivel de objeto", entry['il']))
    if entry.get("il_max"):
        bits.append(tr("hasta {0:,} de nivel de objeto", entry['il_max']))
    if entry.get("cp_max"):
        bits.append(tr("hasta {0:,} de CP", entry['cp_max']))
    lo, hi = entry.get("players") or (1, 1)
    if 1 < hi <= 10:
        bits.append(tr("{0}–{1} jugadores", lo, hi) if lo != hi else tr("{0} jugadores", hi))
    return " · ".join(bits) or tr("sin requisitos")


def good_use(name):
    """(use, what it raises, Spanish) of a shop good, or None."""
    for pattern, use, boosts, text in GOODS:
        if re.search(pattern, name):
            return use, boosts, text
    return None


def limit_es(text):
    """A shop limit in Spanish ("5 per character, weekly" -> "5 por personaje a la semana")."""
    if not text:
        return ""
    out = text.replace(" per character", tr(" por personaje")).replace(" per server", tr(" por servidor"))
    for en, es in ((", weekly", tr(" a la semana")), (", daily", tr(" al día")), (", monthly", tr(" al mes"))):
        out = out.replace(en, es)
    return out


def missing_il(reason):
    """The item level a reason says you lack (for sorting the goals), else a big number."""
    m = re.match(r"te faltan ([\d,]+) de nivel", reason or "")
    return int(m.group(1).replace(",", "")) if m else 10 ** 6


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
               skipped=None, me=None, bosses=None, alts_kina=0, now=None, off=None, arcana=None):
    """The Plan for a character. gear: settings["gear"][name]; inventory: the app's Inventory (bag and storage);
    prices: {item name: (lowest, when)} of the market; dungeons: the ones you run; levels: {profession: level};
    recipes: crafting_db's (the Dragon Lord pieces' Splendent step); skipped: the keys of the steps you do not want;
    me: Me (level, item level, CP, main or alt); bosses: the Tracker's field and Abyss bosses [{"name", "where",
    "level", "abyss", "map_id", "items": [(piece, chance %)], "respawn"}]; alts_kina: a main's alts' tradeable Kina;
    off: the CATEGORIES you switched off; arcana: the character's arcana.ArcanaPlan (the cards to wear and to feed)."""
    gear = gear or {}
    plan = Plan(name, arcana_plan=arcana)
    plan.skipped = set(skipped or ())
    plan.off = set(off or ())
    plan.dungeons = list(dungeons or [])
    plan.read_at = inventory.read_at(name) if inventory else None
    plan.me = me = me or Me(level=gear.get("level") or 0)
    plan.alts_kina = 0 if me.alt else alts_kina
    faction = gear.get("faction")
    counted = set()
    for item_id, (kind, item_name, trade) in db.tracked.items():
        if item_name in counted:                    # one name, several ids (owned() counts them all)
            continue
        counted.add(item_name)
        have = inventory.owned(name, item_name) if inventory else None
        if not have or not have[0]:
            continue
        if me.alt and trade:                        # an alt keeps what can be traded for the main
            plan.for_main[item_name] = have[0]
            continue
        if kind in ("currency", "abyss_points"):
            plan.currencies[item_name] = have[0]
            if kind == "currency":
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
    kina, bound = plan.have("kina"), plan.have("kina_bound")
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
                Option("own", piece.pos, item, level - piece.level, 0, tr("La tienes guardada (+{0}, {1}).", enchant, level),
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
                               tr("{0} Gear Change Voucher de {1} + {2} Kinah (Substance Morph); tienes {3}.", need, dungeon, kinah_text(fee), count), source=dungeon, need=need))

    # the market (a main's: an alt spends only what is bound)
    for item_name, (lowest, _) in ({} if me.alt else prices or {}).items():
        item = gear_db.named(item_name, gear.get("faction"))
        if item is None or not _wearable(item, gear) or item.slot not in POSITIONS:
            continue
        piece, gain = gain_over(item)
        if piece and gain > 0:
            ladder = bool(LADDER.match(item.name))
            text = tr("En el market a {0}", kinah_text(lowest))
            step = splendent_step(item.name, recipes) if ladder else None
            if step:
                nxt = gear_db.named(step[0], gear.get("faction"))
                text += (tr(" · con transfer crafting pasa a {0} ({1}): ", step[0], nxt.level if nxt else '?')
                         + ", ".join(f"{n}× {m}" for m, n in step[1]))
            elif ladder:
                text += tr(" · sube hasta 102 con transfer crafting")
            plan.options.setdefault(piece.pos, []).append(Option("market", piece.pos, item, gain, lowest, text,
                                                                 ladder=ladder))

    # every dungeon mode: the ones you run and can enter give options; one you cannot enter yet, a goal
    for item_id, (kind, source, detail, chance, per) in db.drops.items():
        if kind != "dungeon clear":
            continue
        item = gear_db.by_id.get(item_id)
        if item is None or not _wearable(item, gear) or item.slot not in POSITIONS:
            continue
        piece, gain = gain_over(item)
        if not piece or gain <= 0:
            continue
        pity = db.cube_for(source, item.name)
        text = tr("{0} ({1}): {2:.1f}% por {3}", source, detail, chance * 100, 'clear' if per == 'clear' else per)
        if pity:
            text += tr(" · garantizado: ") + ", ".join(tr("{0} cada {1} cubos", m, n) for m, n, _ in
                                                   sorted(pity, key=lambda x: x[1]))
        o = Option("drop", piece.pos, item, gain, None, text, chance=chance, source=source, mode=detail)
        plan.gains.setdefault((source, detail), []).append(o)
        if can_do(db.mode(source, detail), me):
            plan.locked.setdefault((source, detail), []).append(o)
        elif source in plan.dungeons:
            plan.options.setdefault(piece.pos, []).append(o)
    # the Tracker's field and Abyss bosses you can reach (an Abyss one: the Lower Abyss's entrance; a field one: your
    # faction's map)
    for b in bosses or []:
        b["reason"] = boss_reason(b, me, db, faction)
        known = plan.bosses.get(b["name"])            # one name on both factions' maps (Silent Dartan): yours wins
        if known is None or known["reason"] and not b["reason"]:
            plan.bosses[b["name"]] = b
        for item_name, chance in b.get("items", []):
            item = gear_db.named(item_name, faction)
            if item is None or not _wearable(item, gear) or item.slot not in POSITIONS:
                continue
            piece, gain = gain_over(item)
            if piece and gain > 0:
                o = Option("boss", piece.pos, item, gain, None, tr("{0}: {1:.1f}% por muerte", b['name'], chance),
                           chance=chance / 100, source=b["name"])
                b.setdefault("useful", []).append(o)
                if not b["reason"]:
                    plan.options.setdefault(piece.pos, []).append(o)
    order = {"own": 0, "voucher": 1, "drop": 2, "boss": 2, "market": 3}
    skip = plan.skipped
    for pos, opts in plan.options.items():
        best = {}
        for o in opts:                                          # one option per piece and kind (the cheapest)
            if option_key(o) in skip or o.kind == "voucher" and f"voucher-dungeon:{o.source}" in skip or \
                    o.kind == "drop" and f"farm-dungeon:{o.source}" in skip or \
                    o.kind == "boss" and f"boss:{o.source}" in skip:
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
        plan.spare.append(Spare(item, enchant, level, cont, s.rate if s and s.lines else 0.0, soul=s))
    plan.spare.sort(key=lambda x: (x.enchant, x.level, x.rate))

    arcana = _arcana(plan, gear, gear_db, db, me)
    plan.steps = _steps(plan, by_pos, kina, bound, stones, db, levels or {}, prices or {}, recipes, gear_db,
                        faction, arcana, now or time.time())
    plan.content = _content(plan, db, me, arcana, faction, now or time.time())
    plan.substats = _substats(plan, db, gear_db, faction)
    return plan


def line_score(key, soul):
    """(score, the table's lines it has) of a piece's lines for its slot: each line of the table counts for its
    priority (the first one most); a skill effect line counts for "skill"."""
    tiers = SUBSTATS.get(key)
    if not tiers or soul is None:
        return 0, []
    have = {x[0] for x in soul.lines if x[0]}
    score, found = 0, []
    for i, tier in enumerate(tiers):
        for stat in tier:
            if stat == "skill" and soul.skills or stat in have:
                score += len(tiers) - i
                found.append(stat)
    return score, found


def _tier(db, item, lowest):
    """Index of a piece's gear transfer tier (its kind's tiers by item level): the lowest one it is in for a donor,
    the highest for the piece receiving."""
    tiers = sorted(db.transfer.get(item.category, []))
    hits = [i for i, (lo, hi, _) in enumerate(tiers) if lo <= item.level <= hi]
    return (hits[0] if lowest else hits[-1]) if hits else None


def _substats(plan, db, gear_db, faction):
    """«Líneas»: per worn piece with a row in the table, its lines against the table, the spare pieces of its kind
    with better lines (a transfer donor), and what making the lines and the +N on a tier 1 piece saves."""
    out = []
    for piece in plan.pieces:
        tiers = SUBSTATS.get(piece.key)
        if not tiers or not piece.item:
            continue
        s = piece.soul
        have = {x[0]: x[4] for x in s.lines if x[0]} if s else {}
        rows = [[(stat, bool(s and s.skills) if stat == "skill" else stat in have,
                  None if stat == "skill" else have.get(stat)) for stat in tier] for tier in tiers]
        wanted = {stat for tier in tiers for stat in tier}
        others = [(n, p) for n, p in have.items() if n not in wanted]
        score, _ = line_score(piece.key, s)
        review = Review(piece, rows, others, score, stones=db.transfer_stones(piece.item),
                        note=SUBSTATS_NOTE.get(piece.key, tr("skill: niveles de habilidades pasivas")))
        target = _tier(db, piece.item, lowest=False)
        for x in plan.spare:
            if x.item.category != piece.item.category or x.item.grade != "Unique" or not x.soul:
                continue
            donor = _tier(db, x.item, lowest=True)
            better, found = line_score(piece.key, x.soul)
            worth = better - score >= 2 or any(stat in found for stat in tiers[0])    # not for the last line alone
            if target is not None and donor is not None and donor <= target and better > score and worth:
                goal = min(piece.enchant, db.top_level(x.item) or piece.enchant)      # the transfer takes its +N
                review.donors.append((x, better, found, db.enhance_cost(x.item, x.enchant, goal),
                                      goal <= db.safe_level(x.item)))
        review.donors.sort(key=lambda d: (-d[1], -d[0].enchant))
        if target is not None and target > 0 and piece.item.grade == "Unique" and piece.enchant < piece.safe:
            cheap = [i for i in gear_db.by_id.values() if i.category == piece.item.category and i.grade == "Unique"
                     and i.faction in ("B", faction or i.faction) and _tier(db, i, lowest=True) == 0]
            if cheap:
                low = min(cheap, key=lambda i: (i.level, i.name))
                theirs = db.enhance_cost(low, 0, db.safe_level(low))
                mine = db.enhance_cost(piece.item, piece.enchant, piece.safe)
                if theirs and mine:
                    review.cheap = (low, theirs, mine)
        out.append(review)
    return out


def boss_reason(b, me, db, faction):
    """None when you can go to this boss, else why not."""
    if b.get("abyss"):
        return can_do(db.entry("abyss", b.get("where") or LOWER_ABYSS) or db.entry("abyss", LOWER_ABYSS), me)
    side = FIELD_FACTION.get(b.get("map_id"))
    if side and faction and side != faction:
        return tr("es del mapa del otro bando")
    return None


def _arcana(plan, gear, gear_db, db, me):
    """Per Arcana slot (Transcendence's cards): (pos, the card you wear or None, its item level, [(card name, grade,
    item level, dungeon, stage, chance, reason)] of the better ones, the ones you can get first)."""
    out = []
    slots = gear.get("slots", {})
    for pos, kind in ARCANA.items():
        raw = slots.get(str(pos))
        item = gear_db.by_id.get(raw[0]) if raw else None
        level = item.level if item else 0
        better = []
        for card, grade, dungeon, stage, chance, _ in db.arcana:
            lv = ARCANA_LEVEL.get(grade, 0)
            if card.startswith(kind + " ") and lv > level:
                better.append((card, grade, lv, dungeon, stage, chance, can_do(db.mode(dungeon, stage), me)))
        better.sort(key=lambda c: (c[6] is not None, -c[2], -c[5]))
        out.append((pos, item, level, better))
    return out


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
    keep = tr("tu +{0}", piece.enchant) + (tr(" y su Soul Binding ({0:.0%})", s.rate) if s.lines else "")
    lost = [w for w, has in ((tr("las manastones"), any(x[0] for x in s.sockets)), (tr("la Theostone"), s.theostone))
            if has]
    return tr(" Con transfer pasan {0}", keep) + (tr("; se pierden {0}", tr(" y ").join(lost)) if lost else "") + "."


def _steps(plan, by_pos, kina, bound, stones, db, levels, prices, recipes, gear_db, faction, arcana, now):
    steps = []
    fees = kina + bound                 # enhancing, Substance Morph, soul binding: Kina and Kina (Bound)
    transfer = plan.have("transfer_stone")
    # 1. wear what you have
    for pos, opts in plan.options.items():
        for o in opts:
            if o.kind == "own":
                piece = by_pos[pos]
                steps.append(Step("own", tr("Ponte {0} +{1}", o.item.name, o.enchant),
                                  tr("La tienes en la bolsa o el almacén: {0} de base contra tu {1} ({2} ahora).", o.item.level, piece.item.name if piece.item else 'hueco', piece.level),
                                  o.gain, 0, pos=pos, key=option_key(o),
                                  summary=tr("Guardada: {0} de base contra {1}", o.item.level, piece.base)))
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
            parts.append(tr("con 1 voucher + {0} cada pieza: ", kinah_text(cheap[0].kinah)) +
                         ", ".join(f"{o.item.name} (+{o.gain})" for o in cheap[:max(n, 1)]))
        conquest = can_do(db.mode(dungeon, "Conquest"), plan.me)
        for o in big:
            missing = o.need - count
            parts.append(tr("con {0} vouchers + {1}: {2} (+{3})", o.need, kinah_text(o.kinah), o.item.name, o.gain) +
                         (tr(" · te falta{0} {1}: el garantizado de Conquest de {2} ofrece el voucher", 'n' if missing > 1 else '', missing, dungeon) + (tr(" (aún no puedes entrar: {0})", conquest) if conquest else "")
                          if missing > 0 else ""))
        best = max(picks, key=lambda o: o.gain if o.need <= count else -1)
        ready = best.need <= count and fees >= (best.kinah or 0)
        steps.append(Step("voucher", tr("Tus {0} Gear Change Voucher de {1}", count, dungeon),
                          "; ".join(parts) + "." + _lost_on_transfer(by_pos[best.pos]), best.gain, best.kinah,
                          ready=ready, pos=best.pos, key=f"voucher-dungeon:{dungeon}",
                          summary=" · ".join(
                              [tr("{0} por {1}", cheap[0].item.name, kinah_text(cheap[0].kinah))] if cheap else [])
                          + "".join(f" · {o.need} = {o.item.name}" + (tr(" (te falta{0} {1})", 'n' if o.need - count > 1 else '', o.need - count) if o.need > count else "")
                                    for o in big[:1]),
                          parts=[(option_key(o), tr("{0} (+{1}) con {2} voucher{3}", o.item.name, o.gain, o.need, 's' if o.need > 1 else '')) for o in picks]))
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
            text = (tr("de +{0} a +{1} (no falla): {2} Kinah y {3:,} Enhance Stones", piece.enchant, piece.safe, kinah_text(cost_k), cost_s) + (tr(" y {0} {1} (tienes {2})", extra, scroll.replace(' (Bound)', ''), have)
                                                     if extra else ""))
            enh.append(Step("enhance", tr("Encanta {0}", piece.item.name), text, n, cost_k, cost_s,
                            ready=fees >= cost_k and stones >= cost_s and have >= extra, pos=piece.pos,
                            key=f"enhance:{piece.item.name}:{piece.pos}",
                            uses=("enhance", f"scroll:{piece.item.name}") if extra else ("enhance",),
                            summary=tr("+{0} → +{1} · {2} Kinah · {3:,} piedras", piece.enchant, piece.safe, kinah_text(cost_k), cost_s)))
    enh.sort(key=lambda s: s.kinah / max(1, s.gain))
    if len(enh) > 2:                     # one card: every piece, cheapest per level first, and the total
        total_k, total_s = sum(s.kinah for s in enh), sum(s.stones for s in enh)
        steps.append(Step("enhance", tr("Encanta {0} piezas hasta donde no falla", len(enh)),
                          tr("Sin riesgo (cada intento sale): ") + "; ".join(
                              tr("{0} +{1}→+{2} ({3} Kinah, {4:,} piedras", by_pos[s.pos].item.name, by_pos[s.pos].enchant, by_pos[s.pos].safe, kinah_text(s.kinah), s.stones)
                              + (tr(", {0} pergaminos: tienes {1}", by_pos[s.pos].to_safe[2], plan.scrolls.get(by_pos[s.pos].item.name + ' Enhance Scroll (Bound)', 0))
                                 if by_pos[s.pos].to_safe[2] else "") + ")" for s in enh)
                          + tr(". En total {0} Kinah y {1:,} Enhance Stones (tienes {2} de Kinah, ligada o no, y "
                               "{3:,}); de arriba abajo si no te alcanza.", kinah_text(total_k), total_s, kinah_text(fees), stones),
                          sum(s.gain for s in enh), total_k, total_s, ready=enh[0].ready, pos=enh[0].pos,
                          key="enhance-group", uses=tuple({u for s in enh for u in s.uses}),
                          summary=tr("{0} piezas a +10 · {1} Kinah · {2:,} piedras", len(enh), kinah_text(total_k), total_s),
                          parts=[(s.key, f"{by_pos[s.pos].item.name} +{by_pos[s.pos].enchant}→+{by_pos[s.pos].safe}")
                                 for s in enh]))
    else:
        steps.extend(enh)
    # belts and amulets at +10: Substance Morph into the next grade (the guide)
    for piece in plan.pieces:
        if piece.item and piece.item.category in ("Belt", "Amulet") and piece.enchant >= piece.top > 0:
            steps.append(Step("morph", tr("Sube de grado {0}", piece.item.name),
                              tr("Ya está a +{0}: con Substance Morph pasa al grado siguiente (Heroic es el máximo) "
                                 "y vuelve a encantarse.", piece.enchant), 0, ready=True, pos=piece.pos,
                              key=f"morph:{piece.item.name}:{piece.pos}",
                              summary=tr("Al máximo: pásalo al grado siguiente")))
    # 4. soul binding, on the pieces you keep
    codex, resets, sync = plan.have("soul_codex"), plan.have("soul_reset"), plan.have("sync_stone")

    def food(piece, n):
        """Up to n spare pieces of the same kind and grade to eat in Syncs of this piece, the cheapest first."""
        out = [x for x in plan.spare if not x.use and x.item.category == piece.item.category
               and x.item.grade == piece.item.grade][:n]
        for x in out:
            x.use = tr("Sync de tu {0}", piece.item.name)
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
            soul.append((0, Step("soul", tr("Línea extra en {0}", piece.item.name),
                                 tr("Soul Binding al 100%: {0} Philosopher's Stones + {1} le dan una línea más "
                                    "(tienes {2}); esa línea no pasa con un transfer.", FUSE_STONES, kinah_text(FUSE_KINA), fuse),
                                 1, FUSE_KINA, ready=fuse >= FUSE_STONES and fees >= FUSE_KINA, pos=piece.pos,
                                 uses=("philosopher",),
                                 summary=tr("Al 100% · {0} Philosopher's Stones + {1} (tienes {2})", FUSE_STONES, kinah_text(FUSE_KINA), fuse))))
            continue
        good = len(s.lines) - len(s.bad) + s.skills
        if len(s.bad) >= 2 and good < len(s.bad):       # more to throw away than to keep: reroll them all
            soul.append((1, Step("soul", tr("Reset de líneas: {0}", piece.item.name),
                                 tr("Soul Binding {0:.0%} ({1}): {2} líneas que no son las buscadas ({3}). Reset "
                                    "las vuelve a tirar todas: 1 Soul Codex: Reset + {4} Kinah (tienes {5}). "
                                    "Después, Bind.", s.rate, lines, len(s.bad), ', '.join(s.bad), kinah_text(RESET_KINA), resets),
                                 len(s.bad), RESET_KINA, ready=resets >= 1 and fees >= RESET_KINA, pos=piece.pos,
                                 uses=("soul_reset", "soul_codex"),
                                 summary=tr("{0:.0%} · {1} líneas a cambiar · 1 Reset + {2}", s.rate, len(s.bad), kinah_text(RESET_KINA)))))
        elif s.bad:                                       # keep the good lines: reroll the bad ones one by one
            eat = food(piece, len(s.bad))
            if eat:
                how = tr("Usa como comida: ") + ", ".join(x.text for x in eat) + "."
            else:                                         # no piece of its kind: a Sync Stone from any spare Unique
                stone = [x for x in plan.spare if not x.use and x.item.grade == "Unique"][:max(0, len(s.bad) - sync)]
                for x in stone:
                    x.use = tr("Sync Stone para tu {0}", piece.item.name)
                how = (tr("No tienes otra pieza {0} del mismo tipo: usa una Sync Stone (tienes {1})", piece.item.grade, sync)
                       + (tr(". Conviértela en Substance Morph (1 pieza Unique = 1 Sync Stone): ")
                          + ", ".join(x.text for x in stone) + "." if stone else "."))
            soul.append((2, Step("soul", tr("Sync de {0}: {1}", tr(" y ").join(s.bad), piece.item.name),
                                 tr("Soul Binding {0:.0%} ({1}). Sync vuelve a tirar una línea (las demás se "
                                    "quedan) comiéndose una pieza del mismo tipo y grado, o una Sync Stone, por "
                                    "intento. {2} Después, Bind.", s.rate, lines, how), len(s.bad),
                                 ready=bool(eat or sync or any(x.item.grade == "Unique" for x in plan.spare)),
                                 pos=piece.pos, uses=("sync", "soul_codex"),
                                 summary=tr("{0:.0%} · cambiar {1} · comida: ", s.rate, ', '.join(s.bad))
                                         + (", ".join(f"{x.item.name} +{x.enchant}" for x in eat) if eat
                                            else tr("una Sync Stone")))))
        else:
            soul.append((3, Step("soul", f"Bind: {piece.item.name}",
                                 tr("Soul Binding {0:.0%} ({1}): sus líneas ya son de las buscadas. Bind las sube: "
                                    "{2} Kinah + Soul Codex por intento, más cada vez (tienes {3}).", s.rate, lines, kinah_text(BIND_KINA), codex), round((1 - s.rate) * 10), BIND_KINA,
                                 ready=codex > 0 and fees >= BIND_KINA, pos=piece.pos, uses=("soul_codex",),
                                 summary=tr("{0:.0%} · líneas buenas: súbelas con Bind (tienes {1} Soul Codex)", s.rate, codex))))
    soul.sort(key=lambda x: (x[0], x[1].pos not in (1, 2), -x[1].gain))
    for _, s in soul:
        s.key = f"soul:{by_pos[s.pos].item.name}:{s.pos}"
        steps.append(s)
    if "soul" in plan.off:               # no soul binding for now: the pieces its Syncs were going to eat are free
        for x in plan.spare:
            if x.use.startswith("Sync"):
                x.use = ""
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
        steps.append(Step(kind, tr("{0} huecos de {1} vacíos", total, word.lower()), key=kind, detail=
                          ", ".join(f"{p.item.name} ({n})" for p, n in empty)
                          + tr(". Tienes {0} {1} Superior y {2} de otros grados. Cada piedra que pones vuelve a "
                               "tirar todos los huecos de la pieza: ponlas en las que vas a conservar (se pierden "
                               "con un transfer).", sup, word, sum(have.values()) - sup), gain=total, ready=sup > 0 or bool(have),
                          uses=(kind,),
                          summary=tr("{0} huecos en {1} piezas · tienes {2} {3} Superior", total, len(empty), sup, word)))
    # 6. Amp the weapon (+16..+20 of a Unique piece)
    weapon = by_pos.get(1)
    if weapon and weapon.item and weapon.item.grade == "Unique" and weapon.enchant >= 15 and 1 not in plan.replace:
        amp, top = weapon.enchant - 15, amp_max(weapon.item)
        if amp < top:
            frags = plan.have("amplify_fragment")
            make = min(frags // AMPLIFY_RECIPE[0], stones // AMPLIFY_RECIPE[1])
            steps.append(Step("amp", tr("Amp de {0}: {1}/{2}", weapon.item.name, amp, top),
                              tr("Cada nivel de Amp (pasado +15) da Attack y Attack increase. Tienes {0} Amplify "
                                 "Stone (Unique) y con tus {1} fragmentos haces {2} más ({3} fragmentos + {4:,} "
                                 "Enhance Stones cada una, Substance Morph). El Amp pasa con un transfer.", plan.have('amplify_stone'), frags, make, AMPLIFY_RECIPE[0], AMPLIFY_RECIPE[1]), top - amp,
                              ready=plan.have("amplify_stone") > 0 or make > 0, pos=1, key="amp", uses=("amp",),
                              summary=tr("Amp {0}/{1} · {2} Amplify Stone (+{3} con tus fragmentos)", amp, top, plan.have('amplify_stone'), make)))
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
            detail = (tr("{0} de base contra {1} ({2}) por {3}: {4} por nivel.", o.item.level, piece.item.name if piece.item else tr("nada"), piece.base, kinah_text(o.kinah), kinah_text(o.kinah / o.gain)))
            if piece.enchant >= TRANSFER_WORTH and stones_needed:
                detail += (tr(" Transfer: {0} Transfer Stone{1} (tienes {2}).", stones_needed, 's' if stones_needed > 1 else '', transfer)) + _lost_on_transfer(piece)
            if o.ladder:
                detail += tr(" Es de la línea Dragon Lord: con transfer crafting sigue subiendo hasta 102.")
            steps.append(Step("buy", tr("Compra {0}", o.item.name), detail, o.gain, o.kinah, ready=kina >= o.kinah,
                              pos=o.pos, key=option_key(o),
                              summary=tr("{0} contra {1} · {2} por nivel", o.item.level, piece.base, kinah_text(o.kinah / o.gain))
                                      + (tr(" · línea Dragon Lord") if o.ladder else "")))
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
                how = (tr("Compra {0} ({1}) y pásalo a {2} ({3}) en la mesa de Artesanía con transfer crafting (no "
                          "pide nivel y no falla): ", o.item.name, kinah_text(o.kinah), nxt.name, nxt.level)
                       + ", ".join(f"{n}× {m}" for m, n in step[1])
                       + (tr(" ({0}: los haces tú con Alquimia)", ', '.join(own)) if own else "") + ".")
                routes.append((nxt.level, o.kinah + known, o, how))
        if routes:
            top_level = max(r[0] for r in routes)
            reach, cost, o, how = min((r for r in routes if r[0] == top_level), key=lambda r: r[1])
            if o not in value[:3] or how:
                piece = by_pos[o.pos]
                steps.append(Step(
                    "save" if kina < cost else "buy",
                    tr("Ahorra para {0}", o.item.name) if kina < cost else tr("Compra {0}", o.item.name),
                    (how or tr("Compra {0} ({1}).", o.item.name, kinah_text(o.kinah)))
                    + tr(" La que más te sirve a futuro: {0} contra tu {1} ({2}) y, con transfer crafting, sigue "
                         "hasta {3} sin perder el encantamiento.", reach, piece.item.name if piece.item else 'hueco', piece.base, LADDER_TOP)
                    + (tr(" Te faltan {0}.", kinah_text(cost - kina)) if kina < cost else ""),
                    reach - piece.base, cost, ready=kina >= cost, pos=o.pos, key=option_key(o),
                    uses=("craft",) if how else (),
                    summary=tr("{0} hoy y hasta {1} con transfer crafting", reach, LADDER_TOP)
                            + (tr(" · te faltan {0}", kinah_text(cost - kina)) if kina < cost else "")))
                if kina < cost and plan.alts_kina:
                    steps[-1].detail += (tr(" Tus alts tienen {0} de Kinah comerciable que pueden pasarte.", kinah_text(plan.alts_kina)))
    # 8. farm: what your dungeons give for your weakest pieces, one card per dungeon
    drops = sorted((o for opts in plan.options.values() for o in opts if o.kind == "drop"), key=lambda o: -o.gain)
    by_dungeon, seen = {}, set()
    for o in drops:
        if o.pos not in seen:
            seen.add(o.pos)
            by_dungeon.setdefault(o.source, []).append(o)
    for dungeon, opts in by_dungeon.items():
        pity = sorted({(m, n) for o in opts for m, n, _ in db.cube_for(dungeon, o.item.name)}, key=lambda x: x[1])
        steps.append(Step("farm", tr("Farmea en {0}", dungeon),
                          ", ".join(tr("{0} (+{1}, {2:.1f}% por clear)", o.item.name, o.gain, o.chance * 100) for o in opts)
                          + (tr(". Garantizado (eliges una): ") + ", ".join(tr("{0} cada {1} cubos", m, n) for m, n in pity)
                             if pity else "") + ".", opts[0].gain, ready=True, pos=opts[0].pos,
                          key=f"farm-dungeon:{dungeon}",
                          summary=tr("{0} piezas que te sirven", len(opts))
                                  + (tr(" · garantizado cada {0} cubos ({1})", pity[0][1], pity[0][0]) if pity else ""),
                          parts=[(option_key(o), f"{o.item.name} (+{o.gain})") for o in opts]))
    # field and Abyss bosses you can reach whose loot improves you, the best first
    by_boss = {}
    for o in sorted((o for opts in plan.options.values() for o in opts if o.kind == "boss"), key=lambda o: -o.gain):
        if all(x.item.name != o.item.name for x in by_boss.get(o.source, [])):
            by_boss.setdefault(o.source, []).append(o)
    for boss, opts in sorted(by_boss.items(), key=lambda x: (-x[1][0].gain, -len(x[1])))[:4]:
        b = plan.bosses.get(boss, {})
        where = b.get("where") or ""
        steps.append(Step("boss", tr("Jefe: {0}", boss),
                          (tr("Jefe del Abismo") if b.get('abyss') else tr("Jefe de campo")) + (tr(" en {0}", where) if where else "")
                          + (tr(", nivel {0}", b['level']) if b.get("level") else "") + tr(". Suelta: ")
                          + ", ".join(tr("{0} (+{1}, {2:.1f}% por muerte)", o.item.name, o.gain, o.chance * 100) for o in opts)
                          + (f". {b['respawn']}" if b.get("respawn") else "") + tr(". El Tracker te avisa cuándo "
                                                                                   "sale y el Mapa del mundo te "
                                                                                   "dice dónde."), opts[0].gain, ready=True, pos=opts[0].pos,
                          key=f"boss:{boss}",
                          summary=tr("{0} pieza{1} que te sirve{2} · la mejor {3} (+{4})", len(opts), 's' if len(opts) > 1 else '', 'n' if len(opts) > 1 else '', opts[0].item.name, opts[0].gain),
                          parts=[(option_key(o), f"{o.item.name} (+{o.gain})") for o in opts]))
    # Arcana: Transcendence's cards (item level), the better ones you can get
    up = [(pos, item, level, [c for c in better if c[6] is None]) for pos, item, level, better in arcana]
    up = [(pos, item, level, cards) for pos, item, level, cards in up if cards]
    if up and "arcana" not in plan.skipped:
        gain = sum(cards[0][2] - level for _, _, level, cards in up)
        steps.append(Step("arcana", tr("Arcana: {0} hueco{1} a mejorar en Trascendencia", len(up), 's' if len(up) > 1 else ''),
                          "; ".join(tr("{0}: {1} {2} ({3}) contra ", ARCANA[pos], cards[0][0], cards[0][1], cards[0][2])
                                    + (tr("tu {0} {1} ({2})", item.name, item.grade, level) if item else tr("el hueco vacío"))
                                    + tr(" — {0}, {1} ({2:.0f}% por clear)", cards[0][3], cards[0][4], cards[0][5] * 100)
                                    for pos, item, level, cards in up)
                          + tr(". Cada Arcana suma su nivel de objeto; dos y cuatro del mismo tipo (Vigor o Magic) "
                               "dan su bonus de set."), gain, ready=True, pos=up[0][0], key="arcana",
                          summary=tr("+{0} de nivel de objeto en {1}", gain, ', '.join(ARCANA[p] for p, *_ in up))))
    steps += _activities(plan, steps, db, faction, now)
    steps += _goals(plan, arcana, db)
    # 9. what the rest of your spare pieces are worth: a Sync Stone or a Transfer Stone each
    rest = [x for x in plan.spare if not x.use and x.item.grade == "Unique"]
    if rest:
        need = sum(db.transfer_stones(o.item) or 1 for o in (
            o for opts in plan.options.values() for o in opts[:1] if o.kind in ("market", "voucher")))
        for x in rest:
            x.use = tr("Transfer Stone o Sync Stone")
        steps.append(Step("spare", tr("{0} piezas Unique que no usas → Transfer o Sync Stones", len(rest)),
                          tr("Cada una se convierte en 1 Transfer Stone (Unique) o 1 Sync Stone (Unique) en "
                             "Substance Morph (la que elijas): {0}{1}. Tienes {2} Transfer Stones y {3} Sync "
                             "Stones; un transfer a nivel 86+ pide 2. Primero las +0: las encantadas devuelven "
                             "Enhance Stones si las rompes.", ', '.join(x.text for x in rest[:5]), '…' if len(rest) > 5 else '', transfer, plan.have('sync_stone')) + (tr(" Si haces con transfer todos los cambios que te "
                                                                 "recomiendo, te harían falta unas {0}.", need) if need
                                                              else ""), 0, ready=True, key="spare",
                          summary=tr("{0} piezas → 1 Transfer Stone o 1 Sync Stone cada una", len(rest))))
    # Arcana cards (arcana.py, Personaje › Arcanas): the ones to wear, and the ones you do not use fed to them
    cards = plan.arcana_plan
    if cards is not None:
        wear = [(k, w, c, g) for k, w, c, g in cards.changes if g > 0]
        if wear:
            steps.append(Step("arcana", tr("Arcanas: ponte {0}", ", ".join(c.name for _, _, c, _ in wear)),
                              "; ".join(tr("{0} {1} +{2} ({3}) en lugar de la que llevas ({4}): +{5} nivel{6} en "
                                           "skills que te faltan", c.name, c.grade, c.enchant, effects_text(c, cards),
                                           effects_text(w, cards) if w else tr("el hueco vacío"), g,
                                           "es" if g != 1 else "") for _, w, c, g in wear)
                              + tr(". Qué buscar en cada carta y el resto, en Personaje › Arcanas."), 0, ready=True,
                              key="arcana-wear", boosts=(CP,),
                              summary=tr("+{0} niveles de skill que te faltan", sum(g for *_, g in wear))))
        if cards.feeds:
            kinah = sum(f.kinah for f in cards.feeds)
            ups = sum(f.to - f.target.enchant for f in cards.feeds)
            n = sum(1 for f in cards.feeds for w, _ in f.fodder if not isinstance(w, tuple))
            steps.append(Step("arcana", tr("Arcanas: dales de comer las {0} que no usas", n) if n else
                              tr("Arcanas: usa tus Training Arcana"),
                              " ".join(feed_text(f) + "." for f in cards.feeds)
                              + tr(" Una carta que das de comer pasa la exp de su grado más toda la que tenía (una "
                                   "Unique +2 da 2,000 + 9,000): las que no usas, aunque estén mejoradas, valen como "
                                   "comida. Cada +1 sube 1 el nivel de objeto y el stat de la carta, y añade un efecto "
                                   "de skill o sube uno al azar."), ups, kinah or None, ready=fees >= kinah,
                              key="arcana-feed", summary=tr("{0:,} de exp · +{1} en tus cartas", cards.fodder_exp, ups)))
    # 10. Theostones: weapons and guards, under 1% of the damage (the guide): last
    for pos in (1, 2):
        piece = by_pos.get(pos)
        if piece and piece.item and piece.soul and not piece.soul.theostone and pos not in plan.replace:
            steps.append(Step("theostone", tr("Theostone para {0}", piece.item.name),
                              tr("No tiene ninguna: grabar una cuesta {0} Kinah (tienes {1}). Aporta menos del 1% "
                                 "del daño y se pierde con un transfer.", kinah_text(THEOSTONE_KINA), plan.have('theostone')),
                              0, THEOSTONE_KINA, ready=plan.have("theostone") > 0 and fees >= THEOSTONE_KINA, pos=pos,
                              key=f"theostone:{pos}", uses=("theostone",),
                              summary=tr("Sin Theostone · {0} Kinah · menos del 1% del daño", kinah_text(THEOSTONE_KINA))))
    for s in steps:
        s.boosts = s.boosts or BOOSTS.get(s.kind, ())
        plan.counts[CATEGORY_OF.get(s.kind)] = plan.counts.get(CATEGORY_OF.get(s.kind), 0) + 1
    return [s for s in steps if s.key not in plan.skipped and CATEGORY_OF.get(s.kind) not in plan.off]


def _goods(plan, db, shops, currency, needs, faction, now):
    """[(needed, item, per purchase, price, limit, (use, boosts, text))] of the shops' goods paid with a currency that
    help you, the ones your plan uses first, then the cheapest; one row per good (its cheapest price). An alt leaves
    the goods limited per server to the main."""
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    best = {}
    for shop in shops:
        s = db.shops.get(shop)
        if not s or s["faction"] not in ("both", {"A": "Asmodian", "E": "Elyos"}.get(faction, s["faction"])):
            continue
        for item, per, price, limit, requires, sale_to in s["goods"]:
            use = good_use(item)
            cost = [a for c, a in price if c == currency]
            if not use or not cost or len(price) > 1 or sale_to and sale_to < today or requires or \
                    plan.me.alt and "per server" in limit:
                continue
            needed = use[0] in needs or use[0] in ALWAYS or use[0] == "scroll" and any(
                n.startswith("scroll:") and item.startswith(n[7:]) for n in needs)
            if item not in best or cost[0] < best[item][3]:
                best[item] = (needed, item, per, cost[0], limit, use)
    return sorted(best.values(), key=lambda g: (not g[0], g[5][0] in ALWAYS, g[3]))


def _goods_text(goods, currency, n=6):
    name = CURRENCY_NAME.get(currency, currency)
    return "; ".join(tr("{0}{1} por {2:,} {3}", g[1].replace(' (Bound)', ''), f' ×{g[2]}' if g[2] > 1 else '', g[3], name)
                     + (f" ({limit_es(g[4])})" if g[4] else "") + tr(", para {0}", g[5][2]) for g in goods[:n])


def _activities(plan, steps, db, faction, now):
    """What you can do each week and what it gives you: Nightmare, the Abyss / Arena / duty missions (Abyss Points),
    the Dimensional Invasion (Centuryroot Tokens), the season's shop, the Ascension Trial and the Daily Dungeon
    (Enhance Stones, Amplify Stone Fragments), each with the shop goods your plan uses; a shop step when you already
    have the currency for one of them."""
    me, out = plan.me, []
    needs = {u for s in steps if CATEGORY_OF.get(s.kind) not in plan.off for u in s.uses}
    for currency, kinds, shops in CURRENCIES:
        goods = _goods(plan, db, shops, currency, needs, faction, now)
        if not goods:
            continue
        have = plan.currencies.get(currency)
        name = CURRENCY_NAME.get(currency, currency)
        shop = " / ".join(SHOP_NAME.get(s, s) for s in shops)
        boosts = tuple(sorted({b for g in goods if g[0] for b in g[5][1]}, key=lambda b: b != ILVL))
        cheapest = min((g[3] for g in goods if g[0]), default=None)
        if have and cheapest and have >= cheapest:
            buy = [g for g in goods if g[0] and g[3] <= have]
            out.append(Step("shop", tr("Gasta tus {0:,} {1}", have, name),
                            tr("En la {0}: ", shop) + _goods_text(buy, currency) + ".", 0, ready=True,
                            key=f"shop:{currency}", boosts=boosts,
                            summary=f"{SHOP_NAME.get(shops[0], shops[0])}: "
                                    + ", ".join(g[1].replace(" (Bound)", "") for g in buy[:3])))
        what = tr("En la {0}: {1}.", shop, _goods_text(goods, currency)) + (tr(" Tienes {0:,}.", have) if have is not None else "")
        if currency == "Phantasmal Fragment":
            step = _nightmare(plan, db, what, boosts)
            if step:
                out.append(step)
            continue
        acts = [c for c in db.content if c["kind"] in kinds and not (me.alt and c["kind"] in PER_SERVER)]
        ok = [c for c in acts if not can_do(c, me)]
        if not acts:
            if any(g[0] for g in goods) and (have or currency == "CoinDungeon1"):
                out.append(Step("content", SHOP_NAME.get(shops[0], shops[0]),
                                what + (tr(" Con la moneda de la temporada, mientras dure.") if currency == "CoinDungeon1"
                                        else ""), 0, ready=bool(have), key=f"content:{currency}", boosts=boosts,
                                summary=", ".join(g[1].replace(" (Bound)", "") for g in goods if g[0])[:90]))
            continue
        if not ok:
            continue
        where = "; ".join(_act_text(c) for c in ok)
        out.append(Step("content", f"{name}: " + ", ".join(_act_name(c) for c in ok),
                        f"{where}. {what}", 0, ready=True, key=f"content:{currency}", boosts=boosts,
                        summary=tr("para {0}", ', '.join(dict.fromkeys(g[5][2] for g in goods if g[0])))[:100]))
    trial = _trial(plan, db, now)
    if trial:
        out.append(trial)
    daily = db.entry("daily", "Daeva Bio-Research Base: Unknown Fissure")
    if daily and not can_do(daily, me) and not me.alt and "content:daily" not in plan.skipped:
        out.append(Step("content", tr("Mazmorra diaria: Unknown Fissure"),
                        tr("14 entradas a la semana, compartidas con las demás mazmorras diarias y entre tus "
                           "personajes (por servidor): 3,000 Enhance Stones con 100 puntos y hasta 10,000 con "
                           "10,000, para encantar."),
                        0, ready=True, key="content:daily", boosts=BOTH,
                        summary=tr("Enhance Stones: 3,000 a los 100 puntos, 10,000 a los 10,000")))
    return out


def _act_name(c):
    return {"abyss": c["name"].replace("Chaotic ", ""), "arena": "Arena", "duty": tr("misiones de deber"),
            "invasion": tr("Invasión dimensional")}.get(c["kind"], c["name"])


def _act_text(c):
    if c["kind"] == "abyss":
        return f"{c['name']} ({c['limit'].replace('h a week', 'h a la semana')})"
    if c["kind"] == "duty":
        return tr("misiones de deber: 1,000 Abyss Points y 50,000 Kina (Bound) cada una, 5 al día")
    if c["kind"] == "arena":
        return tr("Arena: premio por las primeras 30 victorias y 10 partidas de la semana")
    if c["kind"] == "invasion":
        return tr("Invasión dimensional: 1 llave al día (hasta 7)")
    return c["name"]


def _nightmare(plan, db, shop_text, boosts):
    """Nightmare: the highest challenge level whose Suggested Item Level you reach, on the Layer I bosses that need
    nothing to open (the rest open with level 10 of the ones before)."""
    il = plan.me.item_level
    free = [b for b in db.nightmare if not b.get("unlock")]
    if not free or "content:Phantasmal Fragment" in plan.skipped:
        return None
    first = min(lv[1] for b in free for lv in b["levels"])
    if il is not None and il < first:
        return None                          # a goal (_goals)
    best = []
    for b in free:
        reach = [lv for lv in b["levels"] if il is None or lv[1] <= il]
        if reach:
            best.append((b, reach[-1]))
    level = min(lv[0] for _, lv in best)
    lo = sum(lv[3] for _, lv in best) // len(best)
    hi = sum(lv[4] for _, lv in best) // len(best)
    names = ", ".join(b["boss"] for b, _ in best)
    return Step("content", tr("Nightmare hasta el nivel {0}", level),
                tr("{0} (Layer I, en el nivel {1} sugieren {2:,} de nivel de objeto): {3:,}–{4:,} Phantasmal "
                   "Fragment por intento según la nota, más el primer clear de cada nivel. 2 intentos al día, hasta "
                   "14 guardados. Con el nivel 10 de dos de ellos se abren Notun y Gerod. ", names, level, max(lv[1] for _, lv in best), lo, hi)
                + shop_text, 0, ready=True, key="content:Phantasmal Fragment", boosts=boosts,
                summary=tr("{0:,}–{1:,} Phantasmal Fragment por intento · 2 intentos al día", lo, hi))


def _trial(plan, db, now):
    """The Ascension Trial (the towers): the hardest tier you can do, this week's tower, its weekly rewards."""
    if "content:trial" in plan.skipped:
        return None
    tiers = [c for c in db.content if c["kind"] == "awakening"]
    ok = [c for c in tiers if not can_do(c, plan.me)]
    if not ok:
        return None
    top = max(ok, key=lambda c: c["il"])
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    week = next((t for f, to, t in db.awakening.get("weeks", []) if f <= stamp < to), None)
    rewards = db.awakening.get("rewards", [])
    top_reward = rewards[-1] if rewards else None
    text = (tr("Hasta {0} ({1:,}); {2}", top['mode'], top['il'], top['limit'].replace('3 entries a week, shared by all 6 dungeons', tr("3 entradas a la semana "
                                                                                                                                       "entre las 6 torres")))
            + (tr("; esta semana: {0}", week) if week else "") + tr(". Premios de la semana por puntos: ")
            + (", ".join(f"{n:,} {i.replace(' (Bound)', '')}" for i, n in top_reward[1]) + tr(" con {0:,} puntos", top_reward[0]) if top_reward else "Enhance Stones y Amplify Stone Fragments") + ".")
    return Step("content", f"Ascension Trial: {top['mode']}" + (tr(" en {0}", week) if week else ""), text, 0, ready=True,
                key="content:trial", boosts=BOTH, summary=tr("Enhance Stones y Amplify Stone Fragments (Unique) "
                                                             "cada semana"))


def _goals(plan, arcana, db):
    """What you cannot do yet and would improve you: the item level you lack for each, the closest first."""
    me, goals = plan.me, []
    by_dungeon = {}
    for (dungeon, mode), opts in plan.locked.items():
        reason = can_do(db.mode(dungeon, mode), me)
        if reason and db.mode(dungeon, mode).get("open"):
            by_dungeon.setdefault(dungeon, []).append((mode, reason, opts))
    for dungeon, modes in by_dungeon.items():
        mode, reason, _ = min(modes, key=lambda m: missing_il(m[1]))
        opts = {o.item.name: o for _, _, os in modes for o in os}
        best = max(opts.values(), key=lambda o: o.gain)
        goals.append((missing_il(reason), Step(
            "goal", f"{dungeon}: {reason}",
            tr("{0} ({1}) te daría {2} pieza{3}: ", dungeon, mode, len(opts), 's' if len(opts) > 1 else '')
            + ", ".join(f"{o.item.name} (+{o.gain})" for o in sorted(opts.values(), key=lambda o: -o.gain)[:6])
            + tr(". Hasta entonces, sube tu nivel de objeto con los pasos de arriba."), best.gain, ready=False,
            key=f"goal:{dungeon}", boosts=BOTH,
            summary=tr("{0} piezas que te sirven · la mejor {1} (+{2})", len(opts), best.item.name, best.gain))))
    cards = [(pos, item, level, better) for pos, item, level, better in arcana
             if better and not any(c[6] is None for c in better)]
    if cards:
        reason = min((c[6] for *_, cs in cards for c in cs), key=missing_il)
        first = can_do(db.mode("Deus Research Base", "Transcendence Stage 1"), me)
        goals.append((missing_il(reason), Step(
            "goal", tr("Arcana en Trascendencia: {0}", reason),
            tr("Los Arcana salen en Deus Research Base (Chalice, Bell, Compass) y Shattered Arkanis (Mirror, "
               "Parchment), Common desde Stage 2, Rare en Stage 3, Epic y Unique en Stage 4")
            + (tr(" (Stage 1 pide 1,600: {0})", first) if first else tr(" (ya entras en Stage 1)")) + ": "
            + ", ".join(f"{ARCANA[pos]} ({tr('vacío') if not item else f'{item.grade} {level}'})" for pos, item, level, _ in
                        cards) + tr(" suben hasta 80 de nivel de objeto cada uno."), 0, ready=False, key="goal:arcana",
            boosts=BOTH, summary=tr("Arcana: hasta 80 de nivel de objeto por hueco"))))
    lower = db.entry("abyss", LOWER_ABYSS)
    reason = can_do(lower, me)
    if reason:
        goals.append((missing_il(reason), Step(
            "goal", tr("Abismo: {0}", reason), tr("Chaotic Lower Reshanta: Abyss Points (manastones, soulstones, "
                                            "Theostones y Potencial en su tienda) y los jefes del Abismo."), 0, ready=False, key="goal:abyss", boosts=BOTH,
            summary=tr("Abyss Points y los jefes del Abismo"))))
    return [s for _, s in sorted(goals, key=lambda g: g[0])[:5] if s.key not in plan.skipped]


def _content(plan, db, me, arcana, faction, now):
    """Every activity of «Contenido»: whether you can do it, why not, and what it gives you."""
    rows = []
    for c in db.content:
        if c["kind"] != "dungeon":
            continue
        reason = can_do(c, me)
        opts = {o.item.name: o for o in plan.gains.get((c["name"], c["mode"]), [])}
        gives, boosts = [], ()
        if opts:
            best = max(opts.values(), key=lambda o: o.gain)
            gives.append(tr("{0} pieza{1} que te sirve{2} (la mejor {3} +{4})", len(opts), 's' if len(opts) > 1 else '', 'n' if len(opts) > 1 else '', best.item.name, best.gain))
            boosts = BOTH
        pity = [x for x in db.cubes if x["dungeon"] == c["name"] and x["mode"] == c["mode"]]
        if pity and pity[0]["choices"]:
            gives.append(tr("garantizado cada {0} cubos: ", pity[0]['ceiling']) + ", ".join(pity[0]["choices"][:3])
                         + ("…" if len(pity[0]["choices"]) > 3 else ""))
        if c["category"] == "Transcendence":
            cards = sorted({f"{card} {grade}" for card, grade, d, stage, *_ in db.arcana
                            if d == c["name"] and stage == c["mode"]})
            if cards:
                gives.append("Arcana: " + ", ".join(cards))
                boosts = BOTH
        if c["category"] == "Subjugation" and not gives:
            gives.append(tr("sus premios no están en las tablas del cliente"))
        rows.append(Activity(GROUP.get(c["category"], c["category"]), c["name"], c["mode"], needs_text(c), reason,
                             not c["open"], " · ".join(gives), boosts, len(opts)))
    tiers = {}
    for c in db.content:
        if c["kind"] == "awakening":
            tiers.setdefault(c["mode"], c)
    for mode, c in tiers.items():
        rows.append(Activity(GROUP["Ascension Trial"], tr("6 torres (una por semana)"), mode, needs_text(c),
                             can_do(c, me), not c["open"], tr("Enhance Stones, Amplify Stone Fragments (Unique), "
                                                              "cofres de mascota; 3 entradas a la semana"), BOTH))
    for b in db.nightmare:
        levels = b["levels"]
        il = me.item_level
        reach = [lv for lv in levels if il is None or lv[1] <= il]
        unlock = b.get("unlock")
        needs = tr("sugerido {0:,}–{1:,}", levels[0][1], levels[-1][1]) + (
            tr(" · se abre con el nivel {0} de {1}", unlock['level'], tr(" y ").join(unlock['bosses'])) if unlock else "")
        reason = None if reach else tr("te faltan {0:,} para el nivel 1 (sugerido)", levels[0][1] - il)
        lv = reach[-1] if reach else levels[0]
        rows.append(Activity("Nightmare", b["boss"], b["layer"], needs, reason, False,
                             (tr("hasta el nivel {0}: ", lv[0]) if reach else "") + tr("{0:,}–{1:,} Phantasmal Fragment "
                                                                                 "por intento (Soul Codex, Reset y "
                                                                                 "Amplify Stone Fragments en su "
                                                                                 "tienda)", lv[3], lv[4]), (CP,)))
    for c in db.content:
        if c["kind"] in ("abyss", "daily", "arena", "duty", "invasion"):
            gives = {"abyss": tr("Abyss Points (manastones, soulstones, Theostones, Potencial y Daevanion: Azphel "
                                 "en su tienda) y los jefes del Abismo"),
                     "daily": tr("Enhance Stones: 3,000 con 100 puntos y hasta 10,000 con 10,000; 14 entradas a la "
                                 "semana"), "arena": "Abyss Points", "duty": tr("Abyss Points y Kina (Bound)"),
                     "invasion": tr("Centuryroot Tokens (Soul Codex, Daevanion Crystals y stigmas en sus tiendas)")}[c["kind"]]
            group = {"abyss": tr("Abismo"), "daily": tr("Diario"), "arena": "PvP", "duty": tr("Diario"),
                     "invasion": tr("Eventos")}[c["kind"]]
            shared = c["kind"] in PER_SERVER
            rows.append(Activity(group, _act_name(c) if c["kind"] != "abyss" else c["name"], "", needs_text(c),
                                 can_do(c, me), False, gives + (
                                     tr(" · por servidor (lo comparten tus personajes): ") + (
                                         tr("mejor para tu main") if me.alt else tr("úsalo en tu main")) if shared else ""),
                                 BOTH if c["kind"] == "daily" else (CP,)))
    useful = [b for b in plan.bosses.values() if b.get("useful")]
    for b in sorted(useful, key=lambda b: -max(o.gain for o in b["useful"])):
        opts = {o.item.name: o for o in b["useful"]}
        best = max(opts.values(), key=lambda o: o.gain)
        rows.append(Activity(tr("Jefes del Abismo") if b.get("abyss") else tr("Jefes de campo"), b["name"], b.get("where", ""),
                             tr("Nv. {0}", b['level']) if b.get("level") else "", b.get("reason"), False,
                             tr("{0} pieza{1} que te sirve{2} (la mejor {3} +{4})", len(opts), 's' if len(opts) > 1 else '', 'n' if len(opts) > 1 else '', best.item.name, best.gain), BOTH, len(opts)))
    rest = len(plan.bosses) - len(useful)
    if rest:
        rows.append(Activity(tr("Jefes de campo"), tr("Otros {0} jefes del Tracker", rest), "", "", None, False,
                             tr("nada de lo que sueltan mejora tu equipo"), ()))
    needs = {u for s in plan.steps for u in s.uses}
    for name, s in db.shops.items():
        currencies = sorted({c for g in s["goods"] for c, _ in g[2]})
        cur = next((c for c in currencies if c != "Silver Medal of Merit"), currencies[0])
        goods = _goods(plan, db, [name], cur, needs, faction, now)
        if not goods:
            continue
        have = plan.currencies.get(cur)
        rows.append(Activity(tr("Tiendas"), SHOP_NAME.get(name, name), CURRENCY_NAME.get(cur, cur),
                             tr("tienes {0:,}", have) if have is not None else "", None, False,
                             ", ".join(dict.fromkeys(g[1].replace(" (Bound)", "") for g in goods))[:160],
                             tuple(sorted({b for g in goods for b in g[5][1]}, key=lambda b: b != ILVL)),
                             sum(1 for g in goods if g[0])))
    return rows


def next_steps(plan, n=3):
    """The first n steps you can do now (then the rest), for the module's panel."""
    ready = [s for s in plan.steps if s.ready and s.kind not in ("farm", "boss", "content", "goal", "theostone",
                                                                  "spare")]
    rest = [s for s in plan.steps if s not in ready]
    return (ready + rest)[:n]
