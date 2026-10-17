"""Build data/crafting_db.json (shipped with the app): every crafting recipe of the five professions with where each
material comes from (bought from an NPC, with its Kinah price; gathered; crafted; or found/bought elsewhere), its
success chance by how many proficiency levels you are above it, its combo, and the item names the market OCR checks
its readings against.

    python tools/build_crafting_db.py <recipes.json> <shops.json> <items.json> [<drop-rates.json> <monsters.json>]

The files are aion2data's raw data (https://aion2data.com/raw/index.json: recipes, shops, items, drop-rates), read from
the AION 2 global client and free to use with a link (credited in the app).

A material nobody sells, gathers or crafts was "market" (only bought); with drop-rates (1.63.1) the ones that drop
are told apart: "drop" (monsters: Raw Leather, Spiritstone, Lesser Manastone...), "dungeon" (a dungeon clear: Wrathful
Ego / Mind / Will, Balaur's Essence) or "chest", with the best source ("drop": [kind, source, detail, chance 0-1,
per]); a recipe's Splendent version (its combo) is "combo" ("combo_of": the recipe's product). The rest stays
"market". Every material that drops also carries "drop", whatever its source, and with monsters.json "mobs": the
monsters that drop it, regular ones first, best chance first ([name, chance %, [lowest, highest level], rank,
map]) - the best source can be a field boss or a chest (Fresh Raw Meat), not what you farm.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFESSIONS = {      # aion2data name -> (Spanish, the market categories its products are listed under)
    "Cooking": ("Cocina", "Food"),
    "Alchemy": ("Alquimia", "Potions, Scroll, Manastone, Spellbook / Orb"),
    "Blacksmithing": ("Herrería", "Greatsword, Longsword, Dagger, Mace, Guard"),
    "Armorsmithing": ("Armería", "Top, Legs, Helm, Pauldrons, Gloves, Shoes"),
    "Handicrafting": ("Artesanía", "Bow, Staff, Necklace, Earrings, Ring"),
}


def _faction(item):
    """"elyos" / "asmodian" for one side's items (Verteron Pepper, Pujery...), None for both."""
    side = (item.get("faction") or "").lower()
    return side if side in ("elyos", "asmodian") else None


def _combo(recipe):
    """After a success the craft can "combo": the same item again (food, potions) or its Splendent version (gear)."""
    combo = recipe.get("combo")
    if not combo:
        return None
    return {"chance": combo["chance"], "name": combo["name"], "same": combo["id"] == recipe["product"]["id"]}


DROP_KIND = {"monster": "drop", "dungeon clear": "dungeon", "chest": "chest"}


MOBS_KEPT = 3


def main(recipes_path, shops_path, items_path, drops_path=None, monsters_path=None):
    recipes = json.loads(Path(recipes_path).read_text(encoding="utf-8"))["records"]
    mobs = {}
    if monsters_path:
        for mo in json.loads(Path(monsters_path).read_text(encoding="utf-8"))["records"]:
            for d in mo.get("drops") or []:
                if d.get("chance"):
                    mobs.setdefault(d.get("item"), []).append(
                        (bool(mo.get("boss")), -d["chance"],
                         [mo["name"], round(d["chance"], 2), mo.get("level"), mo.get("rank"),
                          ((mo.get("spawns") or [{}])[0]).get("map")]))
        mobs = {n: [e[2] for e in sorted(v, key=lambda e: e[:2])[:MOBS_KEPT]] for n, v in mobs.items()}
    drops = {}
    if drops_path:
        for d in json.loads(Path(drops_path).read_text(encoding="utf-8"))["records"]:
            drops[d["item"]] = [d["bestSourceKind"], d["bestSource"], d.get("bestSourceDetail"), round(d["chance"], 5),
                                d["per"]]
    combos = {r["combo"]["name"]: r["product"]["name"] for r in recipes
              if r.get("combo") and r["combo"].get("id") != r["product"]["id"]}
    shops = json.loads(Path(shops_path).read_text(encoding="utf-8"))["records"]
    items = {it["id"]: it for it in json.loads(Path(items_path).read_text(encoding="utf-8"))["records"]}
    npc = {}                                     # item name -> (Kinah price, shop)
    for shop in shops:
        if shop.get("kind") != "npc":
            continue
        for good in shop.get("goods", []):
            kinah = [p["amount"] for p in good.get("price", []) if "Kina" in p.get("currency", "")]
            if kinah:
                price = min(kinah) / max(1, good.get("perPurchase") or 1)
                if good["item"] not in npc or price < npc[good["item"]][0]:
                    npc[good["item"]] = (round(price, 2), shop["name"])
    products = {r["product"]["name"] for r in recipes}
    names = {}
    ids = {}                                     # item id -> name, to recognise them in your inventory
    out = []
    for r in recipes:
        mats = []
        for m in r["materials"]:
            info = items.get(m["id"], {})
            if m["name"] in npc:
                source, price = "npc", npc[m["name"]][0]
            elif m["name"] in products:
                source, price = "craft", None
            elif info.get("category") == "Gatherable Material":
                source, price = "gather", None
            elif m["name"] in drops:                 # bought, or farmed: monsters, a dungeon, a chest
                source, price = DROP_KIND.get(drops[m["name"]][0], "market"), None
            elif m["name"] in combos:
                source, price = "combo", None        # the Splendent version a craft can give
            else:
                source, price = "market", None       # only from the market: priced from your market scans
            mat = {"name": m["name"], "count": m["count"], "source": source, "npc_price": price,
                   "faction": _faction(info)}
            if m["name"] in drops:
                mat["drop"] = drops[m["name"]]
            if m["name"] in mobs:
                mat["mobs"] = mobs[m["name"]]
            if source == "combo":
                mat["combo_of"] = combos[m["name"]]
            mats.append(mat)
            names[m["name"]] = info.get("category", "")
            ids[str(m["id"])] = m["name"]
        product = items.get(r["product"]["id"], {})
        names[r["product"]["name"]] = product.get("category", "")
        ids[str(r["product"]["id"])] = r["product"]["name"]
        out.append({"id": r["id"], "profession": r["profession"], "product": r["product"]["name"],
                    "category": product.get("category", ""), "grade": product.get("grade", ""), "count": r["count"],
                    "faction": _faction(product) or next((m["faction"] for m in mats if m["faction"]), None),
                    "proficiency": r["proficiency"], "materials": mats,
                    "success": [round(x, 1) for x in r.get("successByGap") or []],
                    "combo": _combo(r)})
    db = {
        "generated_at": int(time.time()),
        "credit": {"text": "Recetas, tiendas y objetos: aion2data.com (datos del cliente Global)",
                   "url": "https://aion2data.com/recipes"},
        "professions": {k: {"name": v[0], "market": v[1]} for k, v in PROFESSIONS.items()},
        "recipes": out,
        "names": names,                          # every product and material, for the OCR to snap to
        "ids": ids,                              # their item ids (the game's inventory lists ids)
    }
    path = ROOT / "data" / "crafting_db.json"
    path.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{path}: {len(out)} recipes, {len(names)} names, {len(ids)} item ids, "
          f"{sum(1 for n in names if n in npc)} sold by NPCs")


if __name__ == "__main__":
    main(*sys.argv[1:6])
