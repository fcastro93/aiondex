"""Build data/upgrades_db.json (shipped with the app) for Mejoras: what upgrading gear costs and where gear comes from.

    python tools/build_upgrades_db.py <aion2data raw folder>

The folder holds aion2data's raw data (https://aion2data.com/raw/index.json, read from the AION 2 global client and
free to use with a link, credited in the app): items, enhance, gear-transfer, old-gear, drop-rates, odyle-energy,
substance-morph, boxes and index .json. What goes in:
- tracked: the items AionDex counts in your bag and storage for Mejoras (Kina, Enhance Stones, Transfer Stones,
  Amplify Stones and their fragments, Soul Codex, the dungeons' Gear Change Vouchers, the belts', amulets' and runes'
  Enhance Scrolls), id -> [kind, name].
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
    return None


def main(folder):
    raw = Path(folder)

    def load(name):
        return json.loads((raw / f"{name}.json").read_text(encoding="utf-8"))

    items = load("items")["records"]
    tracked = {str(i["id"]): [kind, i["name"]] for i in items if (kind := tracked_kind(i))}

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

    drops = {}
    for r in load("drop-rates")["records"]:
        if r.get("type") in GEAR_TYPES or tracked.get(str(r["itemId"])):
            drops[str(r["itemId"])] = [r["bestSourceKind"], r["bestSource"], r.get("bestSourceDetail"),
                                       round(r["chance"], 5), r["per"]]

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

    index = load("index")
    db = {
        "generated_at": int(time.time()),
        "client": index.get("clientVersion"),
        "credit": {"text": "Encantar, transfer, drops y conversiones: aion2data.com (datos del cliente Global)",
                   "url": "https://aion2data.com/what-to-do-with/old-gear"},
        "tracked": tracked, "enhance": enhance, "decompose": decompose, "transfer": transfer, "drops": drops,
        "cubes": cubes, "conversions": conversions, "chests": chests,
        "pools": pools, "pool_of": pool_of, "props": props,
    }
    path = ROOT / "data" / "upgrades_db.json"
    path.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path}: {len(tracked)} tracked items, {len(enhance)} enhance tables, {len(transfer)} transfer categories, "
          f"{len(drops)} drops, {len(cubes)} cubes, {len(conversions)} conversions, {len(chests)} chests, "
          f"{len(pools)} stat pools for {len(pool_of)} pieces, "
          f"{path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1])
