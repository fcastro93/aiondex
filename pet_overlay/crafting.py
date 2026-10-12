"""Crafting: is it worth crafting and selling? Market screenshots (the market's data travels encrypted, so the screen is
the only source) are read into samples with their date and time; each recipe of the chosen profession is priced from
them, two ways: gathering and crafting what you can (gathered materials free) or buying every material (at the
market's price or yours; NPC ones at their Kinah price). What it sells for after the market's 10%, times your chance of
success at your proficiency (a failed craft loses its materials), and whether it sells at all (offers that disappear
between samples).

- Shots: data/crafting/shots/*.png, waiting to be processed (with what the OCR read, in shots.json).
- Samples: data/crafting/samples.json, [{at, profession, shots, items: {name: {lowest, listings, offers}}}];
  offers are the listings screen's rows [unit price, total, quantity, hours left].
"""
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import market_ocr

log = logging.getLogger(__name__)
FEE = 0.10                      # the market keeps 10% of every sale (the screen says it: "Current Tax Rate 10%")
DEFAULT_PROFESSION = "Cooking"
TIME_SLACK = 1.5                # hours: the market shows the time left rounded ("2d 17h")
MIN_RATE_HOURS = 6              # a per-day rate needs samples spanning this long (1 offer in 1 h is not "24 a day")
MERGE_MINUTES = 15              # shots taken this close to a sample (a page read again later) join it
TOO_CHEAP = 0.5                 # an offer under half the list's lowest price is a misreading (0, 1 Kinah...)


def span_text(hours):
    """1.0 -> '1 h', 0.4 -> '24 min', 30 -> '30 h'."""
    return f"{hours * 60:.0f} min" if hours < 1 else f"{hours:.0f} h"


@dataclass
class Shot:
    file: str
    at: float
    checked: bool = True
    kind: str = None            # None = not read yet; "list" | "listings" | "unknown"
    rows: list = field(default_factory=list)
    error: str = ""
    tax: float = None

    @property
    def read(self):
        return self.kind is not None

    def caption(self):
        if not self.read:
            return "Leyendo…"
        if self.error:
            return f"No se pudo leer: {self.error}"
        if self.kind == "list":
            return f"Lista · {len(self.rows)} {'objeto' if len(self.rows) == 1 else 'objetos'}"
        if self.kind == "listings" and self.rows:
            return f"Ofertas de {self.rows[0][0]} · {len(self.rows)}"
        return "No parece el mercado"


@dataclass
class MaterialCost:
    name: str
    count: int
    source: str                 # npc | gather | craft | market
    unit: float = None          # Kinah per unit if you gather and craft what you can (None = unknown)
    how: str = ""               # npc | gather | market | manual | craft | unknown
    at: float = None            # when the market price was seen
    buy: float = None           # Kinah per unit if you buy it
    buy_how: str = ""           # npc | market | manual | craft | unknown

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
            return "sin datos", "none"
        unit = "uds." if self.by_offers else "ofertas"
        one = "ud." if self.by_offers else "oferta"
        if self.sold:
            if self.hours >= MIN_RATE_HOURS:
                rate = self.per_day
                if rate >= 1:
                    return f"se vende · ~{rate:.0f} {unit}/día", "good"
            return f"se vende · {self.sold} {one if self.sold == 1 else unit} en {span_text(self.hours)}", "good"
        if self.hours >= MIN_RATE_HOURS:
            return "sin ventas vistas", "bad"
        # from the list alone, new offers can hide sales: only the offers screen shows nothing was bought
        return f"{'nada' if self.by_offers else 'sin bajas'} en {span_text(self.hours)}", "none"


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
        self.professions = db.get("professions", {})
        self.names = db.get("names", {})
        self.credit = db.get("credit")
        self.samples = [_clean_sample(s) for s in _load(self.dir / "samples.json", [])]
        self.shots = self._load_shots()
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
        """Your proficiency in a profession, or None when not set."""
        value = self.config.get("levels", {}).get(profession or self.profession)
        return value or None

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
            screen = market_ocr.read_screen(self.path(shot), list(self.names))
            shot.kind, shot.rows, shot.error = screen.kind, [list(r) for r in screen.rows], ""
            shot.tax = screen.tax
            if screen.kind == "unknown":
                shot.checked = False
        except Exception as exc:          # noqa: BLE001 - shown on the shot; the app keeps running
            log.exception("market OCR failed for %s", shot.file)
            shot.kind, shot.rows, shot.error, shot.checked = "unknown", [], str(exc) or type(exc).__name__, False
        self._save_shots()
        return shot

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
        sample = next((s for s in self.samples if s.get("profession") == self.profession and
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
            sample = {"at": taken, "profession": self.profession, "shots": len(shots), "items": items, "tax": tax}
            self.samples.append(sample)
            self.samples.sort(key=lambda s: s["at"])
        self._save_samples()
        for shot in shots:
            self.remove_shot(shot)
        return sample

    def delete_sample(self, sample):
        self.samples = [s for s in self.samples if s is not sample]
        self._save_samples()

    # --- prices and sales -------------------------------------------------------------------------------
    @property
    def fee(self):
        """The market's tax as last read from its screen, else 10%."""
        return next((s["tax"] for s in reversed(self.samples) if s.get("tax") is not None), FEE)

    def latest_prices(self):
        """name -> (lowest price, when) from the newest sample that has it."""
        out = {}
        for s in self.samples:
            for name, item in s["items"].items():
                if item.get("lowest"):
                    out[name] = (item["lowest"], s["at"])
        return out

    def history(self, name):
        """[(when, lowest, listings, offers)] of one item, oldest first."""
        return [(s["at"], s["items"][name].get("lowest"), s["items"][name].get("listings"),
                 s["items"][name].get("offers") or [])
                for s in self.samples if name in s["items"]]

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
        return max((o[3] for s in self.samples for item in s["items"].values() for o in item.get("offers") or []
                    if o[3] is not None), default=None)

    def supply(self, name):
        """(units on sale, units at the lowest price, more offers than were seen) from the newest sample with this
        item, if its offers screen was scanned then; else None."""
        s = next((s for s in reversed(self.samples) if name in s["items"]), None)
        item = s["items"][name] if s else {}
        offers = item.get("offers")
        if not offers:
            return None
        lowest = item.get("lowest") or min(o[0] for o in offers)
        listings = item.get("listings")
        return (sum(o[2] for o in offers), sum(o[2] for o in offers if o[0] == lowest),
                listings is not None and listings > len(offers))

    # --- recipes -----------------------------------------------------------------------------------------
    def recipe_rows(self, faction=None, profession=None):
        """One row per product of the profession (the recipe of your side), priced from the samples."""
        profession = profession or self.profession
        prices = self.latest_prices()
        best = {}
        for r in self.recipes:
            if r["profession"] != profession or "(Bound)" in r["product"]:      # bound items cannot be sold
                continue
            mine = faction is None or r.get("faction") in (None, faction)
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
        by_buy = self.config.get("sort") == "buy"
        rows.sort(key=lambda r: _row_order(r, by_buy))
        return rows

    def recommend(self, rows, n=3):
        """What to cook: recipes you can make that leave money gathering what you can, those seen selling first,
        each by its average profit per attempt."""
        ok = [r for r in rows if r.can_craft is not False and r.profit is not None and r.profit > 0]
        return sorted(ok, key=lambda r: (r.sales.verdict()[1] != "good", -r.profit))[:n]

    def _latest_field(self, name, key):
        for s in reversed(self.samples):
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
            if made is not None:
                cost.unit, cost.how = made, "craft"
            else:
                cost.unit, cost.how = bought, bought_how
            if bought is None:                         # not on the market: buy its materials and craft it
                bought = self._craft_cost(m["name"], prices, faction, depth + 1, buy=True)
                bought_how = "craft" if bought is not None else "unknown"
        else:                                          # only from the market (or your price)
            cost.unit, cost.how = bought, bought_how
        cost.buy, cost.buy_how = bought, bought_how
        if cost.unit is None:
            cost.how = "unknown"
        return cost

    def _craft_cost(self, product, prices, faction, depth, buy):
        """What one unit of an intermediate product costs you to craft (its materials, over its yield and its chance
        of success at your level), gathering or buying its materials."""
        recipe = next((r for r in self.recipes if r["product"] == product and r.get("faction") in (None, faction)),
                      None) or next((r for r in self.recipes if r["product"] == product), None)
        if recipe is None:
            return None
        mats = [self._material(m, prices, faction, depth) for m in recipe["materials"]]
        totals = [m.buy_total if buy else m.total for m in mats]
        if any(t is None for t in totals):
            return None
        row = RecipeRow(recipe, product, level=self.level(recipe["profession"]))
        if row.can_craft is False:
            return None
        return sum(totals) / (row.count * (row.success or 1))

    # --- storage -------------------------------------------------------------------------------------------
    def _load_shots(self):
        saved = _load(self.dir / "shots.json", {})
        shots = []
        for path in sorted(self.shots_dir.glob("*.png")) if self.shots_dir.exists() else []:
            info = saved.get(path.name, {})
            shots.append(Shot(path.name, info.get("at") or path.stat().st_mtime, info.get("checked", True),
                              info.get("kind"), info.get("rows", []), info.get("error", ""), info.get("tax")))
        return shots

    def _save_shots(self):
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            data = {s.file: {"at": s.at, "checked": s.checked, "kind": s.kind, "rows": s.rows, "error": s.error,
                             "tax": s.tax} for s in list(self.shots)}
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
