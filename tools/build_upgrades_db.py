"""Build data/upgrades_db.json (shipped with the app) for Mejoras: what upgrading gear costs and where gear comes from.

    python tools/build_upgrades_db.py <aion2data raw folder>

The folder holds aion2data's raw data (https://aion2data.com/raw/index.json, read from the AION 2 global client and
free to use with a link, credited in the app): items, enhance, gear-transfer, old-gear, drop-rates, odyle-energy,
substance-morph, boxes, party-content, awakening, nightmare, abyss, shops, checklist and index .json. What goes in:
- tracked: the items AionDex counts in your bag and storage for Mejoras (Kina and Kina (Bound), Enhance Stones,
  Transfer Stones, Amplify Stones and their fragments, Soul Codex, the dungeons' Gear Change Vouchers, the belts',
  amulets' and runes' Enhance Scrolls, and every currency the shops take: Abyss Points, Phantasmal Fragment,
  Centuryroot Token, Silver Medal of Merit...), id -> [kind, name, tradeable]: tradeable 1 when it can go to the
  market or another character (item-uses tradeStatus "Exchangeable", or no status, no "(Bound)" and not a currency):
  an alt spends only what is not (Mejoras: a main uses everything).
- enhance: every enhancement table by its group name, per level: [Kinah, Enhance Stones, chance %, extra items].
- decompose: what breaking a piece gives at +0, per grade and item level: [Enhance Stones, Kinah fee].
- transfer: per category (the gear table's: Top, Legs, Mace...) the item-level tiers and the Transfer Stones a
  transfer into that tier takes ([lowest, highest, stones]); no Kinah, 100%.
- drops: per gear item id its best source: [kind, source, detail, chance 0-1, per].
- cubes: every dungeon cube with a guaranteed reward: dungeon, mode, energy per cube, cubes to it, its choices.
- conversions: the Substance Morph recipes of gear (vouchers -> a selection chest or a piece, any Unique piece ->
  Transfer Stone, fragments -> Amplify Stone), and the selection chests' choices.
- pools / pool_of / props: every gear piece's soul binding stat pool ([[stat, lowest, highest]], percents x100 as
  the game sends them) and how many properties one binding gives it (items.json soulBindProperties).
- content: every activity with what it asks for, so Mejoras only sends you where you can go: the dungeon modes
  (Expedition, Transcendence, Sanctuary, Subjugation: equipped Item Level, level, players, open at the level cap and
  their reward limit), the Ascension Trial towers by difficulty, the Abyss entrances (Item Level needed, or the Item
  Level and Combat Power above which Upper Reshanta refuses you), the Daily Dungeon, the Arena, duty missions and the
  Dimensional Invasion, with what each one gives.
- nightmare: each Nightmare boss with its layer, what unlocks it and per challenge level the Suggested Item Level and
  the Phantasmal Fragments of a first and a repeat clear; awakening: the Ascension Trial's weekly reward steps and
  tower rotation.
- shops: the goods of every shop that help a character get stronger (Soul Codex, Reset, Amplify Stone Fragments,
  manastones, soulstones, Theostones, Potential Stones, Philosopher's Stones, Daevanion Crystals, Stigma Shards,
  wings, enhance scrolls, the Splendent crafts' materials) with their price, currency, limit and sale dates.
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATEGORY = {   # gear_items category -> the transfer tables' name
    "Top": "Torso", "Legs": "Pants", "Helm": "Helmet", "Pauldrons": "Shoulder", "Gloves": "Gloves", "Shoes": "Boots",
    "Cloak": "Cape", "Necklace": "Necklace", "Earrings": "Earring", "Ring": "Ring", "Bracelet": "Bracelet",
    "Guard": "Guarder", "Greatsword": "Greatsword", "Longsword": "Sword", "Dagger": "Dagger", "Bow": "Bow",
    "Spellbook": "Magicbook", "Orb": "Orb", "Mace": "Mace", "Staff": "Staff",
}
GEAR_TYPES = {"Weapons", "Armor", "Accessories"}
NOT_CURRENCY = {"Kina (All)", "Quna", "#Cash", "Duty Quest Completion Count Reset", "Soul Crystal Point"}
# shop goods worth listing: what makes a character stronger (gear, its upgrades, Daevanion, stigmas, wings)
USEFUL = re.compile(r"Soul Codex|Philosopher's Stone|Amplify Stone|Manastone|Soulstone|Theostone|Potential Stone|"
                    r"(?<!Battle )Enhance Scroll|Daevanion Crystal|Stigma Shard|Wings \(Bound\)|Wrathful (Mind|Will|Ego)|"
                    r"Refining Stone|Enhance Stone|Transfer Stone|Sync Stone")


def tracked_kind(item):
    name, category = item["name"], item.get("category") or ""
    if name == "Kina":
        return "kina"
    if name == "Kina (Bound)":
        return "kina_bound"
    if name == "Enhance Stone":
        return "enhance_stone"
    if name == "Abyss Points":
        return "abyss_points"
    if category == "Transfer Stone":
        return "transfer_stone"
    if name.startswith("Transfer Stone Fragment"):
        return "transfer_fragment"
    if name.startswith("Amplify Stone Fragment"):
        return "amplify_fragment"
    if name.startswith("Amplify Stone ("):
        return "amplify_stone"
    if name == "Soul Codex (Bound)":
        return "soul_codex"
    if name.startswith("Soul Codex: Reset"):
        return "soul_reset"
    if category == "Philosopher's Stone":
        return "philosopher"
    if name.startswith("Sync Stone Fragment"):
        return "sync_fragment"
    if category == "Manastone":
        return "soulstone" if "Soulstone" in name else "manastone"
    if category == "Theostone":
        return "theostone"
    if name.startswith("Gear Change Voucher:"):
        return "voucher"
    if name.startswith("Sync Stone"):
        return "sync_stone"
    if name.endswith("Enhance Scroll (Bound)") and category == "Miscellaneous":
        return "enhance_scroll"           # a belt's, an amulet's or a rune's: their tables take one per level
    if category == "Currencies" and name not in NOT_CURRENCY:
        return "currency"                 # what the shops take (Abyss Points, Phantasmal Fragment, Oath Coin...)
    return None


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main(folder):
    raw = Path(folder)

    def load(name):
        return json.loads((raw / f"{name}.json").read_text(encoding="utf-8"))

    items = load("items")["records"]
    trade = {r["id"]: r.get("tradeStatus") for r in load("item-uses")["records"]}

    def tradeable(i, kind):
        status = trade.get(i["id"])
        if status:
            return int(status == "Exchangeable")
        return int("(Bound)" not in i["name"] and kind not in ("currency", "kina_bound", "enhance_stone",
                                                                 "abyss_points"))

    tracked = {str(i["id"]): [kind, i["name"], tradeable(i, kind)] for i in items if (kind := tracked_kind(i))}

    enhance = {}
    for r in load("enhance")["records"]:
        levels = sorted(r["levels"], key=lambda lv: lv["from"])
        rows = []
        for lv in levels:
            if not lv["chance"]:
                break
            stones = sum(x["count"] for x in lv["items"] if x["key"] == "CoinEnchant")
            extra = sum(x["count"] for x in lv["items"] if x["key"] != "CoinEnchant")
            rows.append([lv["kinah"], stones, lv["chance"], extra])
        enhance[r["group"]] = rows

    decompose = {}
    for r in load("old-gear")["records"]:
        stones = sum(x["expected"] for x in r["atZero"] if x["item"] == "Enhance Stone")
        decompose.setdefault(r["grade"], {})[str(r["itemLevel"])] = [round(stones), r["feeKinah"]]

    transfer = {}
    names = {v: k for k, v in CATEGORY.items()}
    for r in load("gear-transfer")["records"]:
        m = re.match(r"Succession_Unique_([A-Za-z]+)(?:_LvT\d)?$", r["group"])
        if not m or m.group(1) not in names:
            continue
        own = [s for s in r["sources"] if s["group"] == r["group"]] or r["sources"]
        stones = max((x["amount"] for s in own for x in s["materials"] if "Transfer Stone" in x["name"]), default=1)
        lo, hi = r["itemLevels"]
        transfer.setdefault(names[m.group(1)], []).append([lo, hi, stones])
    for tiers in transfer.values():
        tiers.sort()

    drops, arcana = {}, []
    for r in load("drop-rates")["records"]:
        if r.get("type") in GEAR_TYPES or tracked.get(str(r["itemId"])):
            drops[str(r["itemId"])] = [r["bestSourceKind"], r["bestSource"], r.get("bestSourceDetail"),
                                       round(r["chance"], 5), r["per"]]
        elif r["item"].startswith("Arcana: ") and r["bestSourceKind"] == "dungeon clear":
            # an Arcana card (Transcendence): [card name, grade, dungeon, stage, chance per clear, other sources]
            arcana.append([re.sub(r"^Arcana: | \(Bound\)$", "", r["item"]), r["grade"], r["bestSource"],
                           r.get("bestSourceDetail"), round(r["chance"], 5), r.get("otherSources") or 0])

    cubes = []
    for r in load("odyle-energy")["records"]:
        if r.get("kind") == "cube" and r.get("ceilingCubes"):
            cubes.append({"dungeon": r["dungeon"], "mode": r["mode"], "energy": r["energyPerCube"],
                          "ceiling": r["ceilingCubes"], "choices": r.get("guaranteedReward") or [],
                          "open": bool(r.get("openAtLevelCap"))})

    conversions = []
    for r in load("substance-morph")["records"]:
        made, mats = r["makes"]["name"], r["materials"]
        voucher = any(m["name"].startswith("Gear Change Voucher") for m in mats)
        if r.get("faction") == "Elyos" or not (
                voucher and r["tab"] == "Gear · Gear" or made.startswith("Transfer Stone")
                or made.startswith("Amplify Stone (Unique)")):
            continue
        conversions.append({"makes": made, "amount": r["makes"]["amount"],
                            "materials": [[m["name"], m["amount"]] for m in mats],
                            "anyOneOf": r.get("anyOneOf")})
    chests = {}
    for r in load("boxes")["records"]:
        if r.get("kind") == "select" and r["name"] in {c["makes"] for c in conversions}:
            chests[r["name"]] = [c["name"] for part in r["contents"] for c in part.get("choose", [])]

    pools, pool_of, props, seen = [], {}, {}, {}
    gear_ids = {i for row in json.loads((ROOT / "data" / "gear_items.json").read_text(encoding="utf-8"))["items"]
                for i in row[0]}
    for i in items:
        if i["id"] not in gear_ids:
            continue
        if i.get("soulBindProperties"):
            props[str(i["id"])] = i["soulBindProperties"]
        pool = []
        for s in i.get("extraStats") or []:
            lo, hi = s["range"].replace(",", "").split("\u2013")
            pct = lo.endswith("%")
            lo, hi = float(lo.rstrip("%")), float(hi.rstrip("%"))
            scale = 100 if pct or lo != int(lo) or hi != int(hi) else 1
            pool.append([s["stat"], round(lo * scale), round(hi * scale)])
        if pool:
            key = json.dumps(pool)
            if key not in seen:
                seen[key] = len(pools)
                pools.append(pool)
            pool_of[str(i["id"])] = seen[key]

    # what each activity asks for and gives
    limits = {}
    for r in load("checklist")["records"]:
        if r.get("kind") == "limit":
            limits[r["id"]] = r
    content = []
    for r in load("party-content")["records"]:
        if r["kind"] != "mode":
            continue
        cat = r["category"]
        lim = limits.get(f"{slug(r['dungeon'])}-{r['mode']}") or limits.get(
            f"{slug(r['dungeon'])}-{cat}") or (limits.get("subjugation") if cat == "Subjugation" else None)
        content.append({"kind": "dungeon", "category": cat, "name": r["dungeon"], "mode": r["mode"],
                        "il": r["itemLevel"], "level": r["level"], "players": [r["minPlayers"], r["maxPlayers"]],
                        "open": bool(r["open"]), "limit": (lim or {}).get("limit", "")})
    trial = limits.get("ascension-trial", {}).get("limit", "")
    aw = load("awakening")["records"]
    for r in aw:
        if r["kind"] == "tier":
            content.append({"kind": "awakening", "category": "Ascension Trial", "name": r["tower"],
                            "mode": r["difficulty"], "il": r["itemLevel"], "level": 45, "players": [1, 1],
                            "open": bool(r["open"]), "limit": trial})
    abyss = load("abyss")["records"]
    hours = {t.get("map"): t["hoursPerWeek"] for t in abyss if t["kind"] == "ticket"}
    for r in abyss:
        if r["kind"] == "entrance" and r.get("map") and "Reshanta" in r["map"]:
            content.append({"kind": "abyss", "category": "Abyss", "name": r["map"], "mode": "",
                            "il": r["needItemLevel"], "il_max": r["limitItemLevel"], "cp_max": r["limitCombatPower"],
                            "level": r["minLevel"], "players": [1, r["maxPlayers"]], "open": True,
                            "limit": f"{hours.get(r['map'], 7)} h a week", "gives": ["Abyss Points"]})
    for key, kind, gives in (("daeva-bio-research-base-Unknown Fissure", "daily", ["Enhance Stone"]),
                             ("arena-single", "arena", ["Abyss Points"]),
                             ("duty-missions", "duty", ["Abyss Points", "Kina (Bound)"]),
                             ("invasion-key", "invasion", ["Centuryroot Token"])):
        r = limits.get(key)
        if r:
            content.append({"kind": kind, "category": r.get("group", ""), "name": r["name"], "mode": "", "il": 0,
                            "level": r.get("level") or 0, "players": [1, 1], "open": True,
                            "limit": r.get("limit", ""), "text": r.get("rewardText") or r.get("detail") or "",
                            "gives": gives})
    nightmare, bosses = [], {}
    for r in load("nightmare")["records"]:
        if "totals" in r:
            bosses[r["boss"]] = {"boss": r["boss"], "layer": r["layer"], "place": r["place"], "grade": r["grade"],
                                 "unlock": r.get("unlock"), "levels": []}
            nightmare.append(bosses[r["boss"]])
    for r in load("nightmare")["records"]:
        if "level" in r and r["boss"] in bosses:
            first = sum(x["count"] for x in r.get("firstClear", []) if x["item"] == "Phantasmal Fragment")
            rep = [x for x in r.get("repeatClear", []) if x["item"] == "Phantasmal Fragment"]
            bosses[r["boss"]]["levels"].append([r["level"], r["suggestedItemLevel"], first,
                                                rep[0]["min"] if rep else 0, rep[0]["max"] if rep else 0])
    for b in nightmare:
        b["levels"].sort()
    awakening = {"rewards": [[r["points"], [[x["item"], x["count"]] for x in r["rewards"]]]
                             for r in aw if r["kind"] == "reward"],
                 "weeks": [[r["from"], r["to"], r["tower"]] for r in aw if r["kind"] == "week"]}
    shops = []
    for r in load("shops")["records"]:
        if r["kind"] in ("random",) or r["name"] in ("Quna Shop", "Subscribe", "Event", "Atelier"):
            continue
        goods, seen_goods = [], set()
        for g in r.get("goods", []):
            price = [[p["currency"], p["amount"]] for p in g.get("price", [])]
            if not price or not USEFUL.search(g["item"]):
                continue
            row = [g["item"], g.get("perPurchase") or 1, price, g.get("limit") or "", g.get("requires") or [],
                   g.get("saleTo") or ""]
            key = json.dumps(row)
            if key not in seen_goods:
                seen_goods.add(key)
                goods.append(row)
        if goods:
            shops.append({"name": r["name"], "kind": r["kind"], "faction": r["faction"], "goods": goods})

    index = load("index")
    db = {
        "generated_at": int(time.time()),
        "client": index.get("clientVersion"),
        "credit": {"text": "Encantar, transfer, drops y conversiones: aion2data.com (datos del cliente Global)",
                   "url": "https://aion2data.com/what-to-do-with/old-gear"},
        "tracked": tracked, "enhance": enhance, "decompose": decompose, "transfer": transfer, "drops": drops,
        "cubes": cubes, "conversions": conversions, "chests": chests,
        "pools": pools, "pool_of": pool_of, "props": props,
        "arcana": arcana, "content": content, "nightmare": nightmare, "awakening": awakening, "shops": shops,
    }
    path = ROOT / "data" / "upgrades_db.json"
    path.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path}: {len(tracked)} tracked items, {len(enhance)} enhance tables, {len(transfer)} transfer categories, "
          f"{len(drops)} drops, {len(cubes)} cubes, {len(conversions)} conversions, {len(chests)} chests, "
          f"{len(pools)} stat pools for {len(pool_of)} pieces, {len(arcana)} Arcana drops, {len(content)} activities, "
          f"{len(nightmare)} Nightmare bosses, {sum(len(s['goods']) for s in shops)} goods in {len(shops)} shops, "
          f"{path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1])
