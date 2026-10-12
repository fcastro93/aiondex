"""Your market sales (Crafting -> Ventas): what sold, read from screenshots of the market's Settlement tab (kept as the
proof of each sale), what you have on sale (the List tab), and their numbers: the market's tax, what the materials
cost when it sold (gathering what you can, or buying everything, as Rentabilidad counts them), the profit, how long
each listing took to sell and how your price compared with the market's lowest.

- A sale stays in the Settlement tab until you collect it, so scanning it twice must not count it twice: a sale is
  its item, quantity, price, settlement and sale time, and a scan adds only the ones it shows more times than they
  were already recorded (two identical sales in the same minute stay two).
- A listing scanned in the List tab that later sells gives the time it took: the game lists for LISTING_HOURS and
  shows the time left, so the time it was listed is worked out from it.

Saved in crafting/sales.json; the proofs (the Settlement screenshots) in crafting/sales/ as JPEG.
"""
import json
import time
from collections import Counter
from pathlib import Path

LISTING_HOURS = 72          # a new listing shows "2d 23h" left
DAY = 86400
RECENT_DAYS = 14            # the chart and the "already recorded" check look this far back
PROOF_QUALITY = 85
MERGE_LIST_MINUTES = 10     # List scans this close are one look at the list (scrolled)


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _key(item, qty, price, net, when_text):
    return (item, int(qty or 1), int(price), int(net), " ".join((when_text or "").lower().split()))


class Ledger:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.proofs_dir = self.dir / "sales"
        data = _load(self.dir / "sales.json", {})
        self.sales = data.get("sales", [])
        self.listings = data.get("listings")     # the last List scan: {"at", "rows", "listed", "slots", "total"}

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "sales.json").write_text(json.dumps({"sales": self.sales, "listings": self.listings},
                                                        ensure_ascii=False), encoding="utf-8")

    # --- scans ---
    def on_listings(self, at, rows, info):
        """A List tab scan: what you have on sale now. rows: (name, qty, total, unit, hours left). Scans a few
        minutes apart (the list scrolled to see the rest) add up: of the same listing, the most either one shows."""
        out = []
        for name, qty, total, unit, left in rows:
            listed = at - (LISTING_HOURS - left) * 3600 if left is not None and left <= LISTING_HOURS else None
            out.append([name, qty, total, unit, left, listed])
        previous = self.listings
        if previous and 0 <= at - previous["at"] <= MERGE_LIST_MINUTES * 60:
            def key(r):
                return (r[0], r[1], r[2], round((r[5] or 0) / 3600))
            mine, theirs = Counter(key(r) for r in out), Counter(key(r) for r in previous.get("rows", []))
            for r in previous.get("rows", []):
                k = key(r)
                if theirs[k] > mine[k]:
                    out.append(r)
                    mine[k] += 1
        self.listings = {"at": at, "rows": out, **{k: v for k, v in info.items() if v is not None}}
        self.save()

    def keep_proof(self, image_path, at):
        """The Settlement screenshot as a JPEG in crafting/sales/ -> its file name (None if it could not be saved)."""
        from PIL import Image
        self.proofs_dir.mkdir(parents=True, exist_ok=True)
        name = time.strftime("venta-%Y%m%d-%H%M%S", time.localtime(at))
        path, n = self.proofs_dir / f"{name}.jpg", 1
        while path.exists():
            n += 1
            path = self.proofs_dir / f"{name}-{n}.jpg"
        try:
            Image.open(image_path).convert("RGB").save(path, "JPEG", quality=PROOF_QUALITY)
        except OSError:
            return None
        return path.name

    def proof_path(self, sale):
        return self.proofs_dir / sale["proof"] if sale.get("proof") else None

    def on_settlement(self, at, rows, proof, cost_of, market_of):
        """A Settlement tab scan -> the sales not recorded yet. rows: (name, qty, price, unit, settlement, sale time
        text, sale time or None). cost_of(name, when) -> {"crafted", "cost", "buy"} per unit with the prices of
        that time; market_of(name, when) -> the lowest price on the market then (or None)."""
        recorded = Counter(_key(s["item"], s["qty"], s["price"], s["net"], s.get("when_text"))
                           for s in self.sales if at - s.get("scanned", s["at"]) <= RECENT_DAYS * DAY)
        in_scan = Counter()
        new = []
        for i, (name, qty, price, unit, net, when_text, when) in enumerate(rows):
            key = _key(name, qty, price, net, when_text)
            in_scan[key] += 1
            if in_scan[key] <= recorded[key]:
                continue                          # already recorded from an earlier scan
            sold_at = when or at
            cost = cost_of(name, sold_at) or {"crafted": False}
            listed = self._take_listing(name, qty, price)
            sale = {"id": f"{int(at * 1000)}-{i}", "at": when or at, "time_known": when is not None, "scanned": at,
                    "item": name, "qty": int(qty or 1), "price": int(price), "unit": unit, "net": int(net),
                    "tax": int(price) - int(net), "when_text": when_text, "crafted": bool(cost.get("crafted")),
                    "cost": None if cost.get("cost") is None else round(cost["cost"] * (qty or 1)),
                    "buy_cost": None if cost.get("buy") is None else round(cost["buy"] * (qty or 1)),
                    "market": market_of(name, sold_at), "listed_at": listed, "proof": proof}
            self.sales.append(sale)
            new.append(sale)
        if new:
            self.sales.sort(key=lambda s: s["at"])
            self.save()
        return new

    def _take_listing(self, name, qty, price):
        """The listing this sale was (removed from what is on sale) -> when it was listed, or None."""
        if not self.listings:
            return None
        rows = self.listings.get("rows", [])
        for row in rows:
            if row[0] == name and int(row[1] or 1) == int(qty or 1) and abs(int(row[2]) - int(price)) <= 1:
                rows.remove(row)
                return row[5]
        return None

    def remove(self, sale_id):
        self.sales = [s for s in self.sales if s["id"] != sale_id]
        self.save()

    # --- numbers ---
    @staticmethod
    def profit(sale, buying=False):
        """What the sale left after the tax and the materials: None when the materials' price is not known."""
        if not sale.get("crafted"):
            return sale["net"]                    # dropped / bought items: no materials to pay
        cost = sale.get("buy_cost" if buying else "cost")
        return None if cost is None else sale["net"] - cost

    @staticmethod
    def hours_to_sell(sale):
        listed = sale.get("listed_at")
        return (sale["at"] - listed) / 3600 if listed and sale.get("time_known") and sale["at"] >= listed else None

    def totals(self, sales=None):
        sales = self.sales if sales is None else sales
        known = [s for s in sales if self.profit(s) is not None]
        net = sum(s["net"] for s in sales)
        profit = sum(self.profit(s) for s in known)
        buy_known = [s for s in sales if self.profit(s, True) is not None]
        return {"count": len(sales), "units": sum(s["qty"] for s in sales), "gross": sum(s["price"] for s in sales),
                "tax": sum(s["tax"] for s in sales), "net": net, "profit": profit,
                "cost": sum(s["cost"] for s in known if s.get("crafted") and s.get("cost") is not None),
                "buy_profit": sum(self.profit(s, True) for s in buy_known),
                "unknown": len(sales) - len(known),
                "margin": profit / sum(s["net"] for s in known) if known and sum(s["net"] for s in known) else None}

    def by_product(self):
        """[(item, totals, average unit price, price vs the market's lowest, average hours to sell)], most profit
        first."""
        groups = {}
        for s in self.sales:
            groups.setdefault(s["item"], []).append(s)
        out = []
        for item, sales in groups.items():
            t = self.totals(sales)
            unit = t["gross"] / t["units"] if t["units"] else None
            vs = [s["price"] / s["qty"] / s["market"] - 1 for s in sales if s.get("market")]
            hours = [h for h in (self.hours_to_sell(s) for s in sales) if h is not None]
            out.append((item, t, unit, sum(vs) / len(vs) if vs else None, sum(hours) / len(hours) if hours else None))
        return sorted(out, key=lambda e: -(e[1]["profit"] or 0))

    def by_day(self, now=None, days=RECENT_DAYS):
        """[(day start, net, profit, sales)] of the last `days` days, oldest first (days without sales too)."""
        now = now or time.time()
        local = time.localtime(now)
        today = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
        out = []
        for d in range(days - 1, -1, -1):
            start = time.mktime(time.localtime(today - d * DAY)[:3] + (0, 0, 0, 0, 0, -1))
            end = start + DAY
            sales = [s for s in self.sales if start <= s["at"] < end]
            t = self.totals(sales)
            out.append((start, t["net"], t["profit"], t["count"]))
        return out

    def on_sale(self, fee):
        """(rows on sale, their total, what you would collect after the tax) from the last List scan."""
        if not self.listings:
            return [], 0, 0
        rows = self.listings.get("rows", [])
        total = sum(r[2] for r in rows)
        return rows, total, round(total * (1 - fee))
