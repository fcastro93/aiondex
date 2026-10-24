"""Crafting: is it worth crafting and selling? Market screenshots (the market's data travels encrypted, so the screen is
the only source) are read into samples with their date and time; each recipe of the chosen profession is priced from
them, two ways: gathering and crafting what you can (gathered materials free) or buying every material (at the
market's price or yours; NPC ones at their Kinah price). What it sells for after the market's 10%, times your chance of
success at your proficiency (a failed craft loses its materials), and whether it sells at all (offers that disappear
between samples).

- Shots: data/crafting/shots/*.png, waiting to be processed (with what the OCR read, in shots.json).
- Samples: data/crafting/samples.json, [{at, profession, shots, items: {name: {lowest, listings, offers}}}];
  offers are the listings screen's rows [unit price, total, quantity, hours left].
- Shared samples (market_share.py): the other users' of your market, when you share yours. Prices, sales, supply and
  charts use them with yours (`pool`); a shared price far from every other reading of the item is dropped.
"""
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import market_ocr
from .inventory import Inventory
from .sales import Ledger
from .professions import KNOWN_IDS
from .i18n import N_, tr, tr_data

log = logging.getLogger(__name__)
FEE = 0.10                      # the market keeps 10% of every sale (the screen says it: "Current Tax Rate 10%")
DEFAULT_PROFESSION = "Cooking"
MATERIALS = "Materials"         # not a profession: the materials' own prices (Odyle, Wrathful Ego / Mind, ores...)
GEAR = "Gear"                   # not a profession either: the market's gear lists (Market > Equipo, for Mejoras)
GEAR_SHARE = 0.6                # a sample whose names are mostly gear is the gear one
WEEK_SECONDS = 7 * 86400
TIME_SLACK = 1.5                # hours: the market shows the time left rounded ("2d 17h")
MIN_RATE_HOURS = 6              # a per-day rate needs samples spanning this long (1 offer in 1 h is not "24 a day")
MERGE_MINUTES = 15              # shots taken this close to a sample (a page read again later) join it
THIN_LISTINGS = 2               # this few offers and no sale seen: the lowest price may be anything (6,000,235...)
NEAR_LEVELS = 10                # to level up, recipes this close below your level (far below give little experience)
FAIL_EXP = 7 / 26               # a failed craft gave 7 experience where a success gave 26 (Curry Meat Stirfry, Lv 1)
EXP_LINK = 30                   # seconds between a craft (6f8d) and its experience (7c8d)
TOO_CHEAP = 0.5                 # an offer under half the list's lowest price is a misreading (0, 1 Kinah...)
OUTLIER = 3.0                   # a shared price this many times above or below the item's other readings is dropped
OUTLIER_HOURS = 72              # ... the readings of these last hours


def span_text(hours):
    """1.0 -> '1 h', 0.4 -> '24 min', 30 -> '30 h'."""
    return f"{hours * 60:.0f} min" if hours < 1 else f"{hours:.0f} h"


@dataclass
class Shot:
    file: str
    at: float
    checked: bool = True
    kind: str = None            # None = not read yet; "list" | "listings" | "mylist" | "settlement" | "unknown"
    rows: list = field(default_factory=list)
    error: str = ""
    tax: float = None
    info: dict = field(default_factory=dict)    # the List / Settlement footers (how many, their total)

    @property
    def read(self):
        return self.kind is not None

    def caption(self):
        if not self.read:
            return tr("Leyendo…")
        if self.error:
            return tr("No se pudo leer: {0}", self.error)
        if self.kind == "list":
            return tr("Lista · {0} {1}", len(self.rows), 'objeto' if len(self.rows) == 1 else 'objetos')
        if self.kind == "listings" and self.rows:
            return tr("Ofertas de {0} · {1}", self.rows[0][0], len(self.rows))
        return tr("No parece el mercado")


@dataclass
class MaterialCost:
    name: str
    count: int
    source: str                 # npc | gather | craft | market
    unit: float = None          # Kinah per unit if you gather and craft what you can (None = unknown)
    how: str = ""               # npc | gather | market | manual | craft | unknown
    at: float = None            # when the market price was seen
    buy: float = None           # Kinah per unit if you buy it
    buy_how: str = ""           # npc | market | manual | raw (its raw materials bought, processed by you) | unknown
    market: float = None        # a processed material: its price on the market (or yours)
    raw: float = None           # ... and per unit if you buy its raw materials and process them yourself
    raw_recipe: dict = None     # the recipe that does it cheapest at your level

    @property
    def total(self):
        return None if self.unit is None else self.unit * self.count

    @property
    def buy_total(self):
        return None if self.buy is None else self.buy * self.count


@dataclass
class Sales:
    sold: int = 0               # units (or offers when only the list was scanned) gone between samples
    added: int = 0              # units newly put on sale meanwhile (offers screens only)
    hours: float = 0.0          # time those samples cover
    observations: int = 0
    by_offers: bool = False     # counted from the offers screen (units) rather than the list (offers)

    @property
    def per_day(self):
        return self.sold * 24 / self.hours if self.hours > 0 else None

    def verdict(self):
        """(short text, kind) with kind "good" | "bad" | "none"."""
        if self.observations < 2:
            return tr("sin datos"), "none"
        unit = "uds." if self.by_offers else "ofertas"
        one = "ud." if self.by_offers else "oferta"
        if self.sold:
            if self.hours >= MIN_RATE_HOURS:
                rate = self.per_day
                if rate >= 1:
                    return tr("se vende · ~{0:.0f} {1}/día", rate, unit), "good"
            return tr("se vende · {0} {1} en {2}", self.sold, one if self.sold == 1 else unit, span_text(self.hours)), "good"
        if self.hours >= MIN_RATE_HOURS:
            return tr("sin ventas vistas"), "bad"
        # from the list alone, new offers can hide sales: only the offers screen shows nothing was bought
        return tr("{0} en {1}", 'nada' if self.by_offers else 'sin bajas', span_text(self.hours)), "none"


@dataclass
class RecipeRow:
    recipe: dict
    product: str
    sell: int = None            # lowest price on the market
    sell_at: float = None
    listings: int = None
    materials: list = field(default_factory=list)
    sales: Sales = field(default_factory=Sales)
    fee: float = FEE
    supply: tuple = None        # (units on sale, units at the lowest price, more offers than were seen) or None
    level: int = None           # your proficiency in the profession (None = not set)

    @property
    def count(self):
        return self.recipe.get("count") or 1

    @property
    def thin(self):
        """Too few offers to trust the price (and none seen selling)."""
        return (self.listings is not None and self.listings <= THIN_LISTINGS
                and self.sales.verdict()[1] != "good")

    @property
    def needs(self):
        return self.recipe.get("proficiency") or 0

    @property
    def can_craft(self):
        """True / False with your level set, None without it."""
        return None if self.level is None else self.level >= self.needs

    @property
    def success(self):
        """Chance a craft works (0-1) at your level; at the recipe's own level when yours is not set or is below it
        (what you would get on reaching it); None when the data has none (then it counts as 1)."""
        chances = self.recipe.get("success") or []
        if not chances:
            return None
        gap = 0 if self.level is None else max(0, self.level - self.needs)
        return chances[min(gap, len(chances) - 1)] / 100

    @property
    def min_fail(self):
        """The lowest chance of failing this recipe can have, and the proficiency where it is reached (the
        proficiency cap keeps high recipes from getting as far above them: 9% at best in cooking up to level 60,
        13% for a level 90 recipe)."""
        chances = self.recipe.get("success") or []
        return (1 - chances[-1] / 100, self.needs + len(chances) - 1) if chances else None

    def fail_curve(self):
        """[(proficiency, chance of failing)] at the recipe's level, +5, +10, +20, +30 and the best."""
        chances = self.recipe.get("success") or []
        gaps = sorted({g for g in (0, 5, 10, 20, 30) if g < len(chances)} | ({len(chances) - 1} if chances else set()))
        return [(self.needs + g, 1 - chances[g] / 100) for g in gaps]

    @property
    def net(self):
        """What the market pays you for one craft that works, after its tax."""
        return None if self.sell is None else self.sell * self.count * (1 - self.fee)

    @property
    def expected(self):
        """What one attempt brings on average (a failed craft brings nothing)."""
        return None if self.net is None else self.net * (self.success if self.success is not None else 1)

    @property
    def cost(self):
        """Materials of one attempt if you gather and craft what you can."""
        totals = [m.total for m in self.materials]
        return None if any(t is None for t in totals) else sum(totals)

    @property
    def buy_cost(self):
        """Materials of one attempt if you buy them all."""
        totals = [m.buy_total for m in self.materials]
        return None if any(t is None for t in totals) else sum(totals)

    @property
    def known_cost(self):
        return sum(m.total or 0 for m in self.materials)

    @property
    def known_buy_cost(self):
        return sum(m.buy_total or 0 for m in self.materials)

    @property
    def missing(self):
        return [m.name for m in self.materials if m.unit is None]

    @property
    def missing_buy(self):
        return [m.name for m in self.materials if m.buy is None]

    @property
    def profit(self):
        """Average profit per attempt gathering and crafting what you can."""
        return None if self.expected is None or self.cost is None else self.expected - self.cost

    @property
    def buy_profit(self):
        """Average profit per attempt buying every material."""
        return None if self.expected is None or self.buy_cost is None else self.expected - self.buy_cost

    @property
    def margin(self):
        return None if self.profit is None or not self.cost else self.profit / self.cost


class Crafting:
    def __init__(self, data_dir, settings):
        self.data_dir = Path(data_dir)
        self.dir = self.data_dir / "crafting"
        self.shots_dir = self.dir / "shots"
        self.settings = settings
        db = _load(self.data_dir / "crafting_db.json", {})
        self.recipes = db.get("recipes", [])
        self.professions = {**db.get("professions", {}),
                            MATERIALS: {"name": tr("Materiales"),
                                        "market": tr("materiales (Odyle, Ego, Mind, minerales, plantas…)")}}
        self.names = db.get("names", {})
        self.gear_names = set()               # every gear piece's name (the app's GearDB): the OCR snaps to them too
        self.inventory = Inventory(settings, db.get("ids", {}))      # your bag and storage, from the game
        self.credit = tr_data("crafting_db.json", db).get("credit")
        self.character = None            # who you are playing: profession levels are per character
        self._last_craft = None          # (when, recipe id, success) until its experience arrives
        self._synced = set()             # (character, profession id) whose level the game confirmed this session
        self.samples = [_clean_sample(s) for s in _load(self.dir / "samples.json", [])]
        self.shared = []                      # the other users' samples of your market (set by the app)
        self._pool = None
        self.shots = self._load_shots()
        self.ledger = Ledger(self.dir)        # your sales: Settlement scans (kept as proof) and List scans
        self.ledger.cost_fn = self.real_sale_cost
        self.ledger.buy_fn = self.buy_sale_cost
        self.faction = None                   # your side's recipes for the sales of old scans (set by the app)
        self._day_cache = {}                  # day -> its average prices, while the day is not over
        self._lock = threading.Lock()        # the OCR thread saves the shots too

    # --- settings --------------------------------------------------------------------------------------
    @property
    def config(self):
        return self.settings.setdefault("crafting", {})

    @property
    def profession(self):
        key = self.config.get("profession") or DEFAULT_PROFESSION
        return key if key in self.professions or not self.professions else next(iter(self.professions))

    def set_option(self, key, value):
        self.config[key] = value
        self.settings.save()

    def level(self, profession=None):
        """Your proficiency in a profession: the game's (read at login and kept up while you craft) for the character
        you play, else the one you typed, else None."""
        trying = self.config.get("level_try", {}).get(profession or self.profession)
        if trying:
            return trying
        game = self.game_level(profession)
        if game is not None:
            return game
        value = self.config.get("levels", {}).get(profession or self.profession)
        return value or None

    def level_source(self, profession=None):
        """"try" (a level you are trying out over the game's) | "game" | "manual" | None."""
        if self.config.get("level_try", {}).get(profession or self.profession):
            return "try"
        if self.game_level(profession) is not None:
            return "game"
        return "manual" if self.config.get("levels", {}).get(profession or self.profession) else None

    # --- levels from the game ---
    def _ids(self):
        """profession id -> profession: Cooking = 5, the others as learnt from a craft."""
        learnt = {int(k): v for k, v in self.config.get("profession_ids", {}).items()}
        return {**learnt, **KNOWN_IDS}

    @property
    def levels_character(self):
        """Whose levels the numbers use: the one picked in the window, else the one you play."""
        return self.config.get("view_character") or self.character

    def owned(self, item):
        """(how many you have, {"Bolsa": n, "Almacén": n}) of the character the numbers use, or None if AionDex has
        not read their items yet."""
        return self.inventory.owned(self.levels_character, item)

    def makes(self, row):
        """(times you can make this recipe with what you have, the material that limits it) or None."""
        return self.inventory.makes(self.levels_character, [(m.name, m.count) for m in row.materials])

    def known_characters(self):
        """Characters whose profession levels AionDex has read from the game."""
        return sorted(name for name, levels in self.config.get("game_levels", {}).items() if levels)

    def _game_entry(self, character=None):
        return self.config.get("game_levels", {}).get(character or self.levels_character or "", {})

    def game_level(self, profession=None, character=None):
        profession = profession or self.profession
        pid = next((i for i, p in self._ids().items() if p == profession), None)
        entry = self._game_entry(character).get(str(pid)) if pid is not None else None
        return entry[0] if entry else None

    def game_exp(self, profession=None):
        profession = profession or self.profession
        pid = next((i for i, p in self._ids().items() if p == profession), None)
        entry = self._game_entry().get(str(pid)) if pid is not None else None
        return entry[1] if entry else None

    def set_game_levels(self, character, levels):
        """The 3336 list: {profession id: (level, experience)} for this character."""
        stored = self.config.setdefault("game_levels", {}).setdefault(character, {})
        stored.update({str(pid): [lvl, exp] for pid, (lvl, exp) in levels.items()})
        self._synced |= {(character, pid) for pid in levels}
        self.settings.save()

    def note_craft(self, recipe_id, success):
        """A craft just started (6f8d): its experience comes in the next 7c8d, which names the profession."""
        self._last_craft = (time.time(), recipe_id, success)

    def exp_seen(self, recipe_id):
        """{"ok": [...], "fail": [...]}: the experience each craft of this recipe gave you."""
        return self.config.get("craft_exp", {}).get(str(recipe_id), {})

    def on_exp(self, pid, exp, gained, character=None):
        """A craft's experience (7c8d): experience in the level after it, and what it gave. Less than before plus
        the gain means a level up. -> (profession, new level) on a level up, else None."""
        last = self._last_craft
        if last and time.time() - last[0] <= EXP_LINK and gained > 0:
            self.learn_profession_id(pid, last[1])
            seen = self.config.setdefault("craft_exp", {}).setdefault(str(last[1]), {})
            values = seen.setdefault("ok" if last[2] else "fail", [])
            values.append(int(gained))
            del values[:-5]
            self._last_craft = None
        who = character or self.character or ""
        stored = self.config.setdefault("game_levels", {}).setdefault(who, {})
        entry = stored.get(str(pid))
        if entry is None:
            return None                  # level unknown until the game sends it (login list or 7d8d)
        level, before = entry
        if exp < before + gained:
            profession = self._ids().get(pid)
            if profession and gained > 0 and (who, pid) in self._synced:     # what that level asked for
                self.config.setdefault("level_exp", {})[f"{profession}|{level}"] = int(before + gained - exp)
            level += 1
        stored[str(pid)] = [level, exp]
        self.settings.save()
        profession = self._ids().get(pid)
        # only from a level the game confirmed this session (a stored one can be behind: AionDex was closed...)
        return (profession, level) if level != entry[0] and profession and (who, pid) in self._synced else None

    def on_level(self, pid, level, exp, character=None):
        """The game's own level of a profession (7d8d). -> (profession, new level) when it went up, else None."""
        who = character or self.character or ""
        stored = self.config.setdefault("game_levels", {}).setdefault(who, {})
        entry = stored.get(str(pid))
        stored[str(pid)] = [level, exp]
        self.settings.save()
        synced = (who, pid) in self._synced
        self._synced.add((who, pid))
        profession = self._ids().get(pid)
        # one level more than a confirmed one: a level up (else AionDex was out of step: it just catches up)
        return (profession, level) if synced and entry and level == entry[0] + 1 and profession else None

    def learn_profession_id(self, pid, recipe_id):
        """A craft (6f8d) names its profession id and recipe: that tells which profession the id is."""
        recipe = next((r for r in self.recipes if r["id"] == recipe_id), None)
        if recipe is None or pid in KNOWN_IDS:
            return
        ids = self.config.setdefault("profession_ids", {})
        if ids.get(str(pid)) != recipe["profession"]:
            ids[str(pid)] = recipe["profession"]
            self.settings.save()

    def try_level(self, value, profession=None):
        """See the numbers at another level than the game's (None = back to the game's)."""
        trying = self.config.setdefault("level_try", {})
        if value and value != self.game_level(profession):
            trying[profession or self.profession] = int(value)
        else:
            trying.pop(profession or self.profession, None)
        self.settings.save()

    def set_level(self, value, profession=None):
        levels = self.config.setdefault("levels", {})
        if value:
            levels[profession or self.profession] = int(value)
        else:
            levels.pop(profession or self.profession, None)
        self.settings.save()

    def manual_price(self, name):
        return self.config.get("prices", {}).get(name)

    def set_manual_price(self, name, price):
        prices = self.config.setdefault("prices", {})
        if price:
            prices[name] = int(price)
        else:
            prices.pop(name, None)
        self.settings.save()

    # --- shots -----------------------------------------------------------------------------------------
    def new_shot_path(self):
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path, n = self.shots_dir / f"{stamp}.png", 1
        while path.exists():
            n += 1
            path = self.shots_dir / f"{stamp}-{n}.png"
        return path

    def add_shot(self, path):
        shot = Shot(Path(path).name, time.time())
        self.shots.append(shot)
        self._save_shots()
        return shot

    def path(self, shot):
        return self.shots_dir / shot.file

    def read_shot(self, shot):
        """OCR of one shot (slow: run it off the UI thread)."""
        try:
            screen = market_ocr.read_screen(self.path(shot), list(self.names) + sorted(self.gear_names - set(self.names)))
            shot.kind, shot.rows, shot.error = screen.kind, [list(r) for r in screen.rows], ""
            shot.tax, shot.info = screen.tax, dict(screen.info)
            if screen.kind == "unknown":
                shot.checked = False
        except Exception as exc:          # noqa: BLE001 - shown on the shot; the app keeps running
            log.exception("market OCR failed for %s", shot.file)
            shot.kind, shot.rows, shot.error, shot.checked = "unknown", [], str(exc) or type(exc).__name__, False
        self._save_shots()
        return shot

    # --- sales ---
    def take_sales_shot(self, shot, faction=None):
        """A screenshot of your List or Settlement tab goes to the sales ledger (the Settlement one kept as the proof
        of its sales) instead of waiting with the market shots. -> the new sales (Settlement) or None (List)."""
        new = None
        if shot.kind == "mylist":
            self.ledger.on_listings(shot.at, shot.rows, shot.info)
        elif shot.kind == "settlement":
            proof = self.ledger.keep_proof(self.path(shot), shot.at) if shot.rows else None
            new = self.ledger.on_settlement(shot.at, shot.rows, proof,
                                            lambda n, when: self.unit_cost(n, faction, when), self.market_price)
            if new:
                for sale in new:
                    sale["faction"] = faction
                    recipe = self._recipe_for(sale["item"], faction)
                    if recipe is not None and sale.get("crafted"):
                        # units an attempt gives at the level of who is playing: the sale's cost never changes when
                        # you switch to a character with another level
                        row = RecipeRow(recipe, sale["item"], level=self.level(recipe["profession"]))
                        sale["yield"] = row.count * (row.success or 1)
                self.ledger.save()
            if not new and proof:
                (self.ledger.proofs_dir / proof).unlink(missing_ok=True)     # nothing new: no proof needed
        self.remove_shot(shot)
        return new

    # --- what you bought and what you farmed (Ventas: the real profit) ------------------------------------------
    def material_info(self, name):
        """A material's entry in the recipes (source, and where it drops: "drop", "mobs", "combo_of"), or {}."""
        if not hasattr(self, "_mat_info"):
            self._mat_info = {}
            for r in self.recipes:
                for m in r["materials"]:
                    self._mat_info.setdefault(m["name"], m)
        return self._mat_info.get(name, {})

    def source_text(self, name):
        """Where a material comes from besides the market, in a line: the monsters that drop it, the dungeon, the
        chest or the recipe it is the combo of (crafting_db, from aion2data's drop tables), or ""."""
        m = self.material_info(name)
        parts = []
        if m.get("source") == "combo" and m.get("combo_of"):
            parts.append(tr("Sale como combo (25%) al craftear {0}.", m['combo_of']))
        mobs = m.get("mobs") or []
        if mobs:
            parts.append(tr("Lo sueltan: ") + "; ".join(
                f"{n} ({chance:g}%" + (tr(", nivel {0}–{1}", lv[0], lv[1]) if lv else "") + (f", {where}" if where else "") + ")"
                for n, chance, lv, _, where in mobs) + ".")
        drop = m.get("drop")
        if drop and not (drop[0] == "monster" and mobs):
            kind, source, detail, chance, per = drop
            what = {"monster": tr("Lo suelta"), "dungeon clear": "Sale en", "chest": "Sale de"}.get(kind, "Sale de")
            per = {"kill": tr("por kill"), "clear": tr("por clear"), "opening": tr("por cofre")}.get(per, per)
            parts.append(f"{what} {source}{f' ({detail})' if detail else ''}: {chance * 100:g}% {per}.")
        return " ".join(parts)

    def mat_choice(self, name, source):
        """"buy" or "farm" for a material, as you marked it. Not marked: what is gathered or crafted is yours, and
        the rest (monster drops, market only) is bought. NPC ingredients are always bought."""
        if source == "npc":
            return "buy"
        mark = self.config.get("mat_marks", {}).get(name)
        if mark in ("buy", "farm"):
            return mark
        return "farm" if source in ("gather", "craft") else "buy"

    def set_mat_choice(self, name, mark):
        self.config.setdefault("mat_marks", {})[name] = mark
        self.settings.save()

    def sold_materials(self):
        """The materials of what you sold (and of the ones you make yourself, their own): [(name, source, [products],
        depth)], each once, NPC ingredients last."""
        out, seen = [], set()

        def walk(product, depth, top):
            recipe = self._recipe_for(product, self.faction)
            if recipe is None or depth > 3:
                return
            for m in recipe["materials"]:
                if m["name"] not in seen:
                    seen.add(m["name"])
                    out.append([m["name"], m["source"], [top], depth])
                else:
                    row = next(r for r in out if r[0] == m["name"])
                    if top not in row[2]:
                        row[2].append(top)
                if m["source"] == "craft" and self.mat_choice(m["name"], "craft") == "farm":
                    walk(m["name"], depth + 1, top)

        for product in dict.fromkeys(s["item"] for s in self.ledger.sales if s.get("crafted")):
            walk(product, 0, product)
        return sorted(out, key=lambda r: r[1] == "npc")

    def real_sale_cost(self, sale):
        """What a sale's materials cost you with your marks: bought ones at that day's average market price (NPC ones
        at the NPC's), farmed and gathered ones free, the ones you make from their own materials; failed attempts
        included, at the level the sale was crafted with. None when a bought material has no price."""
        return self._sale_cost(sale, buying=False)

    def buy_sale_cost(self, sale):
        """The same buying every material (at that day's average prices)."""
        return self._sale_cost(sale, buying=True)

    def _sale_cost(self, sale, buying):
        faction = sale.get("faction") or self.faction
        recipe = self._recipe_for(sale["item"], faction)
        if recipe is None:
            return None
        prices = self.day_prices(sale["at"])
        if buying:
            totals = [self._material(m, prices, faction, 0).buy_total for m in recipe["materials"]]
            attempt = None if None in totals else sum(totals)
        else:
            attempt = self._attempt_real(recipe, prices, faction, 0)
        units = self.sale_yield(sale, recipe, faction)
        return None if attempt is None or not units else round(attempt * (sale.get("qty") or 1) / units)

    def sale_yield(self, sale, recipe, faction):
        """Units an attempt gave when the sale was crafted (its success chance at that level): kept with each sale;
        for the first sales, read from the cost they recorded (keep_yields() then keeps it); else at your level."""
        if sale.get("yield"):
            return sale["yield"]
        if sale.get("cost"):
            prices = self.latest_prices(sale["at"])          # the prices that recorded cost was worked out with
            gathered = [self._material(m, prices, faction, 0).total for m in recipe["materials"]]
            if None not in gathered and sum(gathered) > 0:
                return sum(gathered) / (sale["cost"] / (sale.get("qty") or 1))
        row = RecipeRow(recipe, sale["item"], level=self.level(recipe["profession"]))
        return row.count * (row.success or 1)

    def keep_yields(self):
        """Store the yield of the sales recorded before it was kept with them, so nothing about a sale moves later."""
        changed = False
        for sale in self.ledger.sales:
            if sale.get("crafted") and not sale.get("yield") and sale.get("cost"):
                faction = sale.get("faction") or self.faction
                recipe = self._recipe_for(sale["item"], faction)
                if recipe is not None:
                    sale["yield"] = self.sale_yield(sale, recipe, faction)
                    changed = True
        if changed:
            self.ledger.save()

    def day_prices(self, at):
        """{item: (average lowest price, day start)} of the local day of `at` (00:00 to 23:59): the average of that
        day's samples; an item not seen that day keeps the last price seen before the day ended. Once the day is
        over its prices are kept in the ledger, so later market moves never change that day's sales."""
        local = time.localtime(at)
        start = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
        following = time.localtime(start + 36 * 3600)
        end = time.mktime((following.tm_year, following.tm_mon, following.tm_mday, 0, 0, 0, 0, 0, -1))
        key = time.strftime("%Y-%m-%d", local)
        kept = self.ledger.day_prices.get(key)
        if kept is not None:
            return {name: (price, start) for name, price in kept.items()}
        if key in self._day_cache:
            return self._day_cache[key]
        seen = {}
        for s in self.pool:
            if start <= s["at"] < end:
                for name, item in s["items"].items():
                    if item.get("lowest"):
                        seen.setdefault(name, []).append(item["lowest"])
        out = {name: (sum(v) / len(v), start) for name, v in seen.items()}
        for name, found in self.latest_prices(end).items():
            out.setdefault(name, found)
        if time.time() >= end:
            self.ledger.day_prices[key] = {name: round(price, 2) for name, (price, _) in out.items()}
            self.ledger.save()
        self._day_cache[key] = out
        return out

    def forget_costs(self):
        """Prices of the day going on, levels or marks may have changed."""
        self._day_cache.clear()

    def sale_prices_text(self, sale):
        """'Fresh Raw Meat 2,291' for the bought materials of a sale, at its day's average."""
        faction = sale.get("faction") or self.faction
        recipe = self._recipe_for(sale["item"], faction)
        if recipe is None:
            return ""
        prices = self.day_prices(sale["at"])
        parts = []
        for m in recipe["materials"]:
            if m["source"] != "npc" and self.mat_choice(m["name"], m["source"]) == "buy":
                found = prices.get(m["name"])
                parts.append(f"{m['name']} {found[0]:,.0f}" if found else tr("{0} sin precio", m['name']))
        return ", ".join(parts)

    def _recipe_for(self, product, faction):
        recipes = [r for r in self.recipes if r["product"] == product and r.get("faction") in (None, faction)] or \
            [r for r in self.recipes if r["product"] == product]
        return recipes[0] if recipes else None

    def _real_cost(self, product, prices, faction, depth):
        """One unit of something you make yourself, with your marks, at your level."""
        recipe = self._recipe_for(product, faction)
        if recipe is None:
            return None
        real = self._attempt_real(recipe, prices, faction, depth)
        row = RecipeRow(recipe, product, level=self.level(recipe["profession"]))
        return None if real is None else real / (row.count * (row.success or 1))

    def _attempt_real(self, recipe, prices, faction, depth):
        total = 0
        for m in recipe["materials"]:
            if m["source"] == "npc":
                unit = m["npc_price"]
            elif self.mat_choice(m["name"], m["source"]) == "farm":
                unit = self._real_cost(m["name"], prices, faction, depth + 1) if m["source"] == "craft" and depth < 3 \
                    else 0
            else:
                market = prices.get(m["name"])
                unit = market[0] if market else self.manual_price(m["name"])
            if unit is None:
                return None
            total += unit * m["count"]
        return total

    def unit_cost(self, product, faction=None, at=None):
        """What one unit of a product costs you in materials (with the prices before `at`: a sale's own time),
        failures included: {"crafted": False} for what no recipe makes, else {"crafted": True, "cost": gathering what
        you can, "buy": buying everything} (None when a material has no price)."""
        recipe = next((r for r in self.recipes if r["product"] == product), None)
        if recipe is None:
            return {"crafted": False}
        row = next((r for r in self.recipe_rows(faction, recipe["profession"], at) if r.product == product), None)
        if row is None:
            return {"crafted": True, "cost": None, "buy": None}
        per = (row.success or 1) * row.count
        return {"crafted": True, "cost": None if row.cost is None else row.cost / per,
                "buy": None if row.buy_cost is None else row.buy_cost / per}

    def market_price(self, name, at=None):
        """The lowest price of an item in your newest sample that has it (taken before `at`), or None."""
        found = self.latest_prices(at).get(name)
        return found[0] if found else None

    def set_checked(self, shot, on):
        shot.checked = on
        self._save_shots()

    def remove_shot(self, shot):
        try:
            self.path(shot).unlink()
        except OSError:
            pass
        self.shots = [s for s in self.shots if s is not shot]
        self._save_shots()

    def ready(self):
        return [s for s in self.shots if s.checked and s.read and s.kind in ("list", "listings") and s.rows]

    def reread(self, shot):
        """Forget what the OCR read from a shot, so it is read again."""
        shot.kind, shot.rows, shot.error, shot.tax, shot.checked = None, [], "", None, True
        self._save_shots()

    def process(self, now=None):
        """The checked shots -> a sample dated when they were taken (their files are then deleted); shots taken
        within MERGE_MINUTES of an existing sample of the profession join it instead. None if nothing to keep."""
        shots = self.ready()
        if not shots:
            return None
        taken = min(s.at for s in shots) if now is None else now
        names = [r[0] for s in shots for r in s.rows]
        gear = self.gear_names and names and sum(1 for n in names if n in self.gear_names) >= GEAR_SHARE * len(names)
        profession = GEAR if gear else self.profession          # the gear lists are a sample of their own
        sample = next((s for s in self.samples if s.get("profession") == profession and
                       abs(s["at"] - taken) <= MERGE_MINUTES * 60), None)
        items = json.loads(json.dumps(sample["items"])) if sample else {}
        for shot in sorted(shots, key=lambda s: s.at):
            if shot.kind == "list":
                for name, listings, lowest in shot.rows:
                    item = items.setdefault(name, {})
                    if "lowest" not in item or lowest < item["lowest"]:
                        item["lowest"], item["listings"] = lowest, listings
            else:
                by_name = {}
                for name, unit, total, qty, hours in shot.rows:
                    by_name.setdefault(name, []).append([unit, total, qty, hours])
                for name, offers in by_name.items():
                    item = items.setdefault(name, {})
                    item["offers"] = _union(item.get("offers", []), offers)
        for name, item in items.items():
            items[name] = item = _clean_item(item)
            if "lowest" not in item and item.get("offers"):
                item["lowest"] = min(o[0] for o in item["offers"])
        tax = next((s.tax for s in shots if s.tax is not None), None)
        if sample:
            sample["items"] = items
            sample["shots"] = sample.get("shots", 0) + len(shots)
            sample["tax"] = sample.get("tax") if tax is None else tax
            sample["merged"] = True             # for the notice: "added to the sample of 00:56"
        else:
            sample = {"at": taken, "profession": profession, "shots": len(shots), "items": items, "tax": tax}
            self.samples.append(sample)
            self.samples.sort(key=lambda s: s["at"])
        self._pool = None
        self._save_samples()
        for shot in shots:
            self.remove_shot(shot)
        return sample

    def delete_sample(self, sample):
        self.samples = [s for s in self.samples if s is not sample]
        self._pool = None
        self._save_samples()

    def set_shared(self, samples):
        """The other users' samples of your market ([] when you do not share)."""
        self.shared = samples
        self._pool = None
        self._day_cache.clear()

    @property
    def pool(self):
        """Your samples and the other users' of your market, oldest first."""
        if not self.shared:
            return self.samples
        if self._pool is None:
            self._pool = sorted(self.samples + _agreeing(self.samples, self.shared), key=lambda s: s["at"])
        return self._pool

    @property
    def shared_count(self):
        return len(self.pool) - len(self.samples)

    def price_is_shared(self, name, at=None):
        """True when the price used for an item comes from another user's sample."""
        for s in reversed(self.pool):
            if (at is None or s["at"] <= at) and s["items"].get(name, {}).get("lowest"):
                return bool(s.get("shared"))
        return False

    # --- prices and sales -------------------------------------------------------------------------------
    @property
    def fee(self):
        """The market's tax as last read from its screen, else 10%."""
        return next((s["tax"] for s in reversed(self.samples + self.shared) if s.get("tax") is not None), FEE)

    def latest_prices(self, at=None):
        """name -> (lowest price, when) from the newest sample that has it (with `at`: the newest one taken before
        that moment, so a sale is costed with the prices of its time; the oldest samples when none is older)."""
        out = {}
        samples = self.pool
        if at is not None:
            samples = [s for s in samples if s["at"] <= at] or samples[:1]
        for s in samples:
            for name, item in s["items"].items():
                if item.get("lowest"):
                    out[name] = (item["lowest"], s["at"])
        return out

    def history(self, name):
        """[(when, lowest, listings, offers)] of one item, oldest first."""
        return [(s["at"], s["items"][name].get("lowest"), s["items"][name].get("listings"),
                 s["items"][name].get("offers") or [])
                for s in self.pool if name in s["items"]]

    def sales(self, name):
        """What disappeared from the market between consecutive samples of this item: sold (or taken down)."""
        hist = self.history(name)
        units = Sales(observations=len(hist), by_offers=True)      # from the offers screens (units)
        offers = Sales(observations=len(hist))                      # from the list's offer counts
        duration = self.offer_duration()
        for (t1, _, n1, o1), (t2, _, n2, o2) in zip(hist, hist[1:]):
            hours = (t2 - t1) / 3600
            if o1 and o2:
                gone, added = _sold_between(o1, o2, hours, before_all=n1 is not None and len(o1) >= n1,
                                            after_all=n2 is not None and len(o2) >= n2, duration=duration)
                units.sold += gone
                units.added += added
                units.hours += hours
            if n1 is not None and n2 is not None:
                offers.sold += max(0, n1 - n2)
                offers.hours += hours
        return units if units.hours else offers

    def offer_duration(self):
        """How long an offer stays on the market: the longest time left seen on any offers screen (None before
        any)."""
        return max((o[3] for s in self.pool for item in s["items"].values() for o in item.get("offers") or []
                    if o[3] is not None), default=None)

    def supply(self, name):
        """(units on sale, units at the lowest price, more offers than were seen) from the newest sample with this
        item, if its offers screen was scanned then; else None."""
        s = next((s for s in reversed(self.pool) if name in s["items"]), None)
        item = s["items"][name] if s else {}
        offers = item.get("offers")
        if not offers:
            return None
        lowest = item.get("lowest") or min(o[0] for o in offers)
        listings = item.get("listings")
        return (sum(o[2] for o in offers), sum(o[2] for o in offers if o[0] == lowest),
                listings is not None and listings > len(offers))

    # --- recipes -----------------------------------------------------------------------------------------
    def recipe_rows(self, faction=None, profession=None, at=None, bound=False):
        """One row per product of the profession (the recipe of your side), priced from the samples (the ones before
        `at` when given). bound: also the bound products (they cannot be sold, but you can craft them)."""
        profession = profession or self.profession
        prices = self.latest_prices(at)
        best = {}
        for r in self.recipes:
            if r["profession"] != profession or ("(Bound)" in r["product"] and not bound):
                continue
            mine = faction is None or r.get("faction") in (None, faction)
            if faction and not mine and not any(x["product"] == r["product"] and x.get("faction") in (None, faction)
                                                for x in self.recipes):
                continue                        # the other side's dish (White Dragon's for an Asmodian)
            old = best.get(r["product"])
            if old is None or (mine and old[0] is False):
                best[r["product"]] = (mine, r)
        rows = []
        level = self.level(profession)
        for product, (_, recipe) in best.items():
            row = RecipeRow(recipe, product, fee=self.fee, level=level)
            if product in prices:
                row.sell, row.sell_at = prices[product]
                row.listings = self._latest_field(product, "listings")
            row.materials = [self._material(m, prices, faction, 0) for m in recipe["materials"]]
            row.sales = self.sales(product)
            row.supply = self.supply(product)
            rows.append(row)
        sort = self.config.get("sort") or "gather"
        if sort in ("gather", "buy") and not self.config.get("sort_flip"):
            rows.sort(key=lambda r: _row_order(r, sort == "buy"))      # the default: what you can make first
        else:
            rows = _sorted_by(rows, sort, self.config.get("sort_flip", False))
        return rows

    def set_sort(self, key):
        """Click on a column: sort by it; a second click on the same one reverses the order."""
        if (self.config.get("sort") or "gather") == key:
            self.config["sort_flip"] = not self.config.get("sort_flip", False)
        else:
            self.config["sort"], self.config["sort_flip"] = key, False
        self.settings.save()

    def material_rows(self, profession=None, with_unpriced=False):
        """The materials the profession's recipes use that have a price in your samples (Lesser Manastone,
        Pujery...): [(name, source, lowest, when, listings, Sales, supply, recipes using it)], by name. For
        "Materiales" every profession's; with_unpriced, also those with no price yet (lowest None)."""
        profession = profession or self.profession
        prices = self.latest_prices()
        uses = {}
        for r in self.recipes:
            if r["profession"] == profession or profession == MATERIALS:
                for m in r["materials"]:
                    if m["source"] != "npc" and "(Bound)" not in m["name"]:
                        uses.setdefault(m["name"], [m["source"], set()])[1].add(r["product"])
        out = []
        for name, (source, products) in sorted(uses.items()):
            if name in prices:
                out.append((name, source, prices[name][0], prices[name][1], self._latest_field(name, "listings"),
                            self.sales(name), self.supply(name), sorted(products)))
            elif with_unpriced:
                out.append((name, source, None, None, None, Sales(), None, sorted(products)))
        return out

    def week_average(self, name, now=None):
        """(average lowest price of the last 7 days, readings) of an item, or (None, 0)."""
        now = now or time.time()
        seen = [low for at, low, _, _ in self.history(name) if low and now - at <= WEEK_SECONDS]
        return (sum(seen) / len(seen), len(seen)) if seen else (None, 0)

    def leveling_pick(self, rows):
        """The recipe to level the profession up with: one you can make near your level (far below give little
        experience), costing the least per point of experience when AionDex has seen what it gives, else per attempt.
        None without your level."""
        level = self.level()
        if level is None:
            return None
        able = [r for r in rows if r.can_craft and r.cost is not None]
        near = [r for r in able if r.needs >= level - NEAR_LEVELS]
        if not near and able:
            top = max(r.needs for r in able)
            near = [r for r in able if r.needs == top]
        if not near:
            return None
        for r in near:
            r.exp = self.expected_exp(r)
        value = lambda r: r.profit if r.profit is not None else -r.cost      # unsold: what it costs you
        known = [r for r in near if r.exp]
        if known:
            return max(known, key=lambda r: (value(r) / r.exp, r.needs))     # least Kinah lost per experience
        return max(near, key=lambda r: (value(r), r.needs))

    def expected_exp(self, row):
        """Experience per attempt from what the recipe gave you (a fail gives about 27% of a success), or None."""
        seen = self.exp_seen(row.recipe["id"])
        ok, fail = seen.get("ok"), seen.get("fail")
        if not ok and not fail:
            return None
        ok_avg = sum(ok) / len(ok) if ok else sum(fail) / len(fail) / FAIL_EXP
        fail_avg = sum(fail) / len(fail) if fail else ok_avg * FAIL_EXP
        s = row.success if row.success is not None else 1
        return s * ok_avg + (1 - s) * fail_avg

    def level_exp(self, profession=None, level=None):
        """Experience a level of a profession asks for (learnt when you went past it), or None."""
        profession = profession or self.profession
        level = level if level is not None else self.level(profession)
        return self.config.get("level_exp", {}).get(f"{profession}|{level}")

    def exp_estimate(self, row, rows):
        """(experience per attempt, "visto" | "estimado") for a recipe: what it gave you, else what the recipes you
        crafted closest to its level give (median, within 10 levels), else (None, None)."""
        exp = self.expected_exp(row)
        if exp:
            return exp, N_("visto")
        near = []
        for other in rows:
            if other is row or abs(other.needs - row.needs) > NEAR_LEVELS:
                continue
            seen = self.exp_seen(other.recipe["id"])
            ok = seen.get("ok") or [v / FAIL_EXP for v in seen.get("fail") or []]
            if ok:
                near.append((abs(other.needs - row.needs), sum(ok) / len(ok)))
        if not near:
            return None, None
        near.sort()
        values = sorted(v for _, v in near[:5])
        ok = values[len(values) // 2]
        s = row.success if row.success is not None else 1
        return s * ok + (1 - s) * ok * FAIL_EXP, N_("estimado")

    def leveling_plan(self, rows):
        """The recipes to level up with, best first: [(row, exp per attempt, "visto"/"estimado"/None, Kinah per
        attempt (+ you earn, - you lose), Kinah per experience or None)]. The ones you can make near your level (far
        below give little experience); cheapest per point of experience first, the ones whose experience is unknown
        after them (cheapest per attempt)."""
        level = self.level()
        if level is None:
            return []
        able = [r for r in rows if r.can_craft and r.cost is not None]
        near = [r for r in able if r.needs >= level - NEAR_LEVELS]
        if not near and able:
            top = max(r.needs for r in able)
            near = [r for r in able if r.needs == top]
        plan = []
        for r in near:
            exp, how = self.exp_estimate(r, rows)
            value = r.profit if r.profit is not None else -r.cost
            plan.append((r, exp, how, value, value / exp if exp else None))
        plan.sort(key=lambda e: (e[4] is None, -(e[4] if e[4] is not None else e[3]), -e[0].needs))
        return plan

    def recommend(self, rows, n=3):
        """What to cook: recipes you can make that leave money gathering what you can, those seen selling first,
        each by its average profit per attempt."""
        ok = [r for r in rows if r.can_craft is not False and r.profit is not None and r.profit > 0 and not r.thin]
        return sorted(ok, key=lambda r: (r.sales.verdict()[1] != "good", -r.profit))[:n]

    def _latest_field(self, name, key):
        for s in reversed(self.pool):
            if name in s["items"] and s["items"][name].get(key) is not None:
                return s["items"][name][key]
        return None

    def _material(self, m, prices, faction, depth):
        """A material's price both ways: gathering and crafting what you can, or buying it (NPC, else the market's
        price, else yours)."""
        cost = MaterialCost(m["name"], m["count"], m["source"])
        market = prices.get(m["name"])
        manual = self.manual_price(m["name"])
        if market:
            bought, bought_how, cost.at = market[0], "market", market[1]
        elif manual:
            bought, bought_how = manual, "manual"
        else:
            bought, bought_how = None, "unknown"
        if m["source"] == "npc":
            cost.unit = cost.buy = m["npc_price"]
            cost.how = cost.buy_how = "npc"
            return cost
        if m["source"] == "gather":
            cost.unit, cost.how = 0, "gather"
        elif m["source"] == "craft" and depth < 3:
            made = self._craft_cost(m["name"], prices, faction, depth + 1, buy=False)
            if made is not None and (bought is None or made[0] <= bought):
                cost.unit, cost.how = made[0], "craft"
            else:                                      # buying it processed is cheaper (or it cannot be made)
                cost.unit, cost.how = bought, bought_how
            # buying: the processed material, or its raw materials processed by you, whichever is cheaper
            raw = self._craft_cost(m["name"], prices, faction, depth + 1, buy=True)
            cost.market = bought
            if raw is not None:
                cost.raw, cost.raw_recipe = raw
                if bought is None or raw[0] < bought:
                    bought, bought_how = raw[0], "raw"
        else:                                          # only from the market (or your price)
            cost.unit, cost.how = bought, bought_how
        cost.buy, cost.buy_how = bought, bought_how
        if cost.unit is None:
            cost.how = "unknown"
        return cost

    def _craft_cost(self, product, prices, faction, depth, buy):
        """(cost of one unit, recipe) of an intermediate product made by you, gathering or buying its materials: over
        its yield and its chance of success at your level in that profession, with the cheapest of its recipes you
        can make (1 gem -> 3 powders at level 20, 10 -> 30 at 45...). None if none can be priced or made."""
        recipes = [r for r in self.recipes if r["product"] == product and r.get("faction") in (None, faction)] or \
            [r for r in self.recipes if r["product"] == product]
        best = None
        for recipe in recipes:
            row = RecipeRow(recipe, product, level=self.level(recipe["profession"]))
            if row.can_craft is False:
                continue
            mats = [self._material(m, prices, faction, depth) for m in recipe["materials"]]
            totals = [m.buy_total if buy else m.total for m in mats]
            if any(t is None for t in totals):
                continue
            unit = sum(totals) / (row.count * (row.success or 1))
            if best is None or unit < best[0]:
                best = (unit, recipe)
        return best

    # --- storage -------------------------------------------------------------------------------------------
    def _load_shots(self):
        saved = _load(self.dir / "shots.json", {})
        shots = []
        for path in sorted(self.shots_dir.glob("*.png")) if self.shots_dir.exists() else []:
            info = saved.get(path.name, {})
            shots.append(Shot(path.name, info.get("at") or path.stat().st_mtime, info.get("checked", True),
                              info.get("kind"), info.get("rows", []), info.get("error", ""), info.get("tax"),
                              info.get("info", {})))
        return shots

    def _save_shots(self):
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            data = {s.file: {"at": s.at, "checked": s.checked, "kind": s.kind, "rows": s.rows, "error": s.error,
                             "tax": s.tax, "info": s.info} for s in list(self.shots)}
            (self.dir / "shots.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _save_samples(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "samples.json").write_text(json.dumps(self.samples, ensure_ascii=False), encoding="utf-8")


def _clean_item(item):
    """An item without the offers that cannot be right: a total that is not a whole number of units, or a unit price
    far under the lowest the list showed (misreadings, some from versions before 1.23.3)."""
    offers = item.get("offers")
    if not offers:
        return item
    floor = item["lowest"] * TOO_CHEAP if item.get("listings") is not None and item.get("lowest") else 0
    good = [o for o in offers if o[0] >= 1 and o[2] >= 1 and abs(o[1] - o[0] * o[2]) <= o[0] * 0.05
            and o[0] >= floor]
    if len(good) == len(offers):
        return item
    item = dict(item, offers=good)
    if item.get("listings") is None:             # the lowest came from the offers themselves
        if good:
            item["lowest"] = min(o[0] for o in good)
        else:
            item.pop("lowest", None)
    return item


def _clean_sample(sample):
    sample["items"] = {name: _clean_item(item) for name, item in sample.get("items", {}).items()}
    return sample


def _agreeing(own, shared):
    """The other users' samples without the prices that disagree a lot with the item's other readings: over OUTLIER
    times above or below their median in the last OUTLIER_HOURS (a misreading like 911,950 for 11,950, or a fake).
    An item needs 3 readings for this; with fewer, a shared price is kept."""
    cut = max((s["at"] for s in own + shared), default=0) - 2 * OUTLIER_HOURS * 3600
    seen = {}
    for s in own + shared:
        if s["at"] >= cut:
            for name, item in s.get("items", {}).items():
                if item.get("lowest"):
                    seen.setdefault(name, []).append(item["lowest"])
    middle = {name: sorted(v)[len(v) // 2] for name, v in seen.items() if len(v) >= 3}
    out = []
    for s in shared:
        items = {}
        for name, item in s.get("items", {}).items():
            item = _clean_item(dict(item))
            low, mid = item.get("lowest"), middle.get(name)
            if low and mid and (low > mid * OUTLIER or low * OUTLIER < mid):
                continue
            if item.get("lowest") or item.get("offers"):
                items[name] = item
        if items:
            out.append(dict(s, items=items))
    return out


SORT_KEYS = {          # column -> (value of a row, best first by default)
    "name": (lambda r: r.product.lower(), False),
    "price": (lambda r: r.sell, True),
    "net": (lambda r: r.net, True),
    "fail": (lambda r: None if r.success is None else 1 - r.success, False),
    "gather": (lambda r: r.profit, True),
    "buy": (lambda r: r.buy_profit, True),
    "sales": (lambda r: (r.sales.sold / r.sales.hours) if r.sales.hours else None, True),
}


def _sorted_by(rows, key, flip):
    """Rows by one column (unknown values always last); flip reverses the column's usual order."""
    value, descending = SORT_KEYS.get(key, SORT_KEYS["gather"])
    known = [r for r in rows if value(r) is not None]
    unknown = [r for r in rows if value(r) is None]
    known.sort(key=lambda r: (value(r), r.product), reverse=descending != flip)
    return known + sorted(unknown, key=lambda r: r.product)


def _row_order(row, by_buy=False):
    """What you can craft before what is above your level; within each, known profit first (highest first, gathering
    or buying), then priced but missing a material's price, then the rest."""
    above = row.can_craft is False
    profit = row.buy_profit if by_buy else row.profit
    if profit is not None:
        return above, 0, -profit
    if row.sell is not None:
        return above, 1, -(row.net or 0)
    return above, 2, row.needs


def _union(a, b):
    """Offers from two shots of the same item (overlapping scrolls): each distinct offer as many times as the shot
    that shows it most."""
    out = [list(o) for o in a]
    for offer in b:
        key = list(offer)
        if out.count(key) < b.count(offer):
            out.append(key)
    return sorted(out, key=lambda o: (o[0], -o[2]))


def _in_view(offer, shown, everything):
    """Would this offer be on a screen that showed `shown`? Yes if that screen showed every offer (the list counted
    no more); else only if it comes strictly before the last one shown, by total (the screen's usual order) and by
    unit price: at a tie with the last one it may be just out of view."""
    return everything or (offer[0] < max(o[0] for o in shown) and offer[1] < max(o[1] for o in shown))


def _sold_between(before, after, hours, before_all=False, after_all=False, duration=None):
    """-> (units gone, units new) between two offers screens of one item.

    Gone: earlier offers missing later (matched by price, same or fewer units, and time left lowered by the time
    passed): bought, or taken down. New: later offers that were not there before. An offer that would not have been
    in view on the other screen counts neither way (_in_view), nor does one that expired meanwhile. A later offer
    with less time left than one put up after the earlier screen would have (duration = how long offers stay up)
    was already there, out of view, so it is not new."""
    pool = [list(o) for o in after]
    gone = 0
    for unit, total, qty, left in sorted(before, key=lambda o: o[0]):
        expected = None if left is None else left - hours
        same = [o for o in pool if o[0] == unit and o[2] <= qty and
                (expected is None or o[3] is None or abs(o[3] - expected) <= TIME_SLACK)]
        if same:
            match = max(same, key=lambda o: o[2])          # the whole offer, else what is left of it
            gone += qty - match[2]
            pool.remove(match)
        elif expected is not None and expected <= 0.5:
            continue                                       # expired
        elif _in_view((unit, total), after, after_all):
            gone += qty
    new = sum(o[2] for o in pool if _in_view(o, before, before_all) and
              (duration is None or o[3] is None or o[3] >= duration - hours - TIME_SLACK))
    return gone, new


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class ItemIcons:
    """The items' pictures for Profesiones (recipes, materials, market goods), from metabot's item pages: each name
    is looked up once (its page's og:image, an icon under /web/aion2/items/) and remembered in
    data/crafting/item_icons.json; the picture itself is kept with the Tracker's in data/tracker_icons/. A name
    metabot has no page for is tried again after a week. want() queues the unknown names (the ones on screen first)
    for a background thread, and on_done is called, from that thread, each time a few more pictures are on disk."""
    VERSION = 2                 # 1 (unreleased) took the most repeated icon: wrong for gear pages
    BATCH = 6
    RETRY_SECONDS = 600         # after a network failure, no lookups for a while
    MISS_DAYS = 7               # a name with no page is looked up again after this

    def __init__(self, data_dir, online=True):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "crafting" / "item_icons.json"
        self.online = online
        saved = _load(self.path, {})
        if not isinstance(saved, dict) or saved.get("v") != self.VERSION:
            saved = {}
        self.map = {k: v for k, v in (saved.get("icons") or {}).items() if isinstance(v, str) and v}
        now = time.time()
        self.miss = {k: t for k, t in (saved.get("miss") or {}).items()
                     if isinstance(t, (int, float)) and now - t < self.MISS_DAYS * 86400}
        self.on_done = None
        self._queue = []
        self._queued = set()
        self._lock = threading.Lock()
        self._thread = None
        self._failed_at = 0.0

    @staticmethod
    def key(name):
        """One entry per item: the bound copy of a product has the same picture."""
        return (name or "").replace(" (Bound)", "").strip()

    def file(self, name):
        """The PNG of an item's icon, or None (not looked up yet, metabot has none, or not downloaded)."""
        from .tracker import image_file
        path = self.map.get(self.key(name))
        if not path:
            return None
        file = image_file(self.data_dir, path)
        return file if file.exists() else None

    def want(self, names):
        """Look up (and download) the icons of these names that are not known yet, in the background."""
        if not self.online or time.time() - self._failed_at < self.RETRY_SECONDS:
            return
        with self._lock:
            for name in names:
                key = self.key(name)
                if key and key not in self.map and key not in self.miss and key not in self._queued:
                    self._queue.append(key)
                    self._queued.add(key)
            if self._queue and self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="item-icons")
                self._thread.start()

    def _lookup(self, key):
        """The icon path of one item from its metabot page ("" when it has none). Raises OSError when metabot
        cannot be reached (or turns us away)."""
        import urllib.error
        from . import metabot
        for slug in (metabot.slugify(key), metabot.slugify(key + " (Bound)")):   # some only have the bound page
            try:
                page = metabot.fetch(f"/en/aion-2/items/{slug}").decode("utf-8", "ignore")
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue                                            # no such page
                raise OSError(f"metabot answered {exc.code}") from exc
            except Exception as exc:                                    # noqa: BLE001 - the network is down
                raise OSError(str(exc)) from exc
            path = metabot.parse_item_icon(page)
            if path:
                return path
        return ""

    def _run(self):
        from . import dbupdate
        while True:
            with self._lock:
                batch, self._queue = self._queue[:self.BATCH], self._queue[self.BATCH:]
                if not batch:
                    self._thread = None
                    return
            paths = []
            for key in batch:
                try:
                    path = self._lookup(key)
                except OSError as exc:
                    log.info("item icons: metabot unreachable (%s), giving up for a while", exc)
                    with self._lock:
                        self._queue, self._queued, self._thread = [], set(), None
                    self._failed_at = time.time()
                    self._save()
                    return
                if path:
                    self.map[key] = path
                    paths.append(path)
                else:
                    self.miss[key] = time.time()
            if paths:
                try:
                    dbupdate.download_tracker_images(self.data_dir, paths)
                except Exception:                                       # noqa: BLE001
                    log.exception("item icons: download")
            with self._lock:
                self._queued.difference_update(batch)
            self._save()
            if self.on_done:
                self.on_done()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"v": self.VERSION, "icons": dict(self.map), "miss": dict(self.miss)}
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        except OSError:
            log.exception("item icons: save")
